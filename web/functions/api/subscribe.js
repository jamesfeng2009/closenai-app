/**
 * POST /api/subscribe
 * 收集内测名单，存到本项目自己的 KV（binding: WAITLIST）。
 * 不依赖任何第三方服务，数据留在你自己的 Cloudflare 账号里。
 *
 * 限流走 D1（binding: RATE_LIMIT_DB）的单条 `INSERT … ON CONFLICT … RETURNING`：
 * KV 只有 get/put，并发请求会在「读到 2」与「写回 3」之间互相覆盖，每分钟三次的上限
 * 因此可被并发绕过。Pages Functions 不支持 Workers 的 rate-limit 绑定
 * （官方绑定清单只有 KV/DO/R2/D1/…），而 D1 的单语句在 SQLite 里天然原子，
 * 于是这里要的是「一条语句既加计数又返回结果」，不是「先读后写」。
 *
 * 故障方向：D1 缺绑定或查询报错 ⇒ 503 `rate_limit_unavailable`，**不**退回 KV 计数、
 * 也不放行。数据面（WAITLIST）仍然只存邮箱等订阅记录；限流表只存 IP 的截断哈希。
 */

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

const RATE_LIMIT_MAX = 3;
const RATE_LIMIT_WINDOW_SECONDS = 60;
// 旧窗口再多留一个窗口的余量，随后在下一个窗口的首个请求里清掉。
const RATE_LIMIT_RETENTION_SECONDS = RATE_LIMIT_WINDOW_SECONDS * 2;

const RATE_LIMIT_UPSERT = `
INSERT INTO subscribe_rate_limits (bucket_key, window_start, count)
VALUES (?1, ?2, 1)
ON CONFLICT(bucket_key) DO UPDATE SET
  count = CASE
    WHEN subscribe_rate_limits.window_start = excluded.window_start
      THEN subscribe_rate_limits.count + 1
    ELSE 1
  END,
  window_start = excluded.window_start
RETURNING count, window_start
`;

const RATE_LIMIT_PURGE =
  'DELETE FROM subscribe_rate_limits WHERE window_start < ?1';

const CORS = {
  'access-control-allow-origin': 'https://closenai.app',
  'access-control-allow-methods': 'POST, OPTIONS',
  'access-control-allow-headers': 'content-type',
};

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
      ...CORS,
    },
  });
}

async function sha256(text) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: CORS });
}

export async function onRequestPost({ request, env }) {
  if (!env.WAITLIST) {
    return json({ ok: false, error: 'storage_not_configured' }, 500);
  }

  let body = {};
  const ct = request.headers.get('content-type') || '';
  try {
    if (ct.includes('application/json')) {
      body = (await request.json()) || {};
    } else if (ct.includes('form')) {
      body = Object.fromEntries(await request.formData());
    } else {
      return json({ ok: false, error: 'unsupported_content_type' }, 415);
    }
  } catch {
    return json({ ok: false, error: 'bad_request' }, 400);
  }

  // Honeypot：机器人会填，人类看不到。静默返回 ok，不写入。
  if (body.website) {
    return json({ ok: true, duplicate: false });
  }

  const email = String(body.email || '').trim().toLowerCase();
  if (!email || email.length > 254 || !EMAIL_RE.test(email)) {
    return json({ ok: false, error: 'invalid_email' }, 400);
  }

  // Cloudflare 边缘必带这个头；缺失说明请求没走边缘，按故障方向拒绝而不是放行。
  const ip = (request.headers.get('cf-connecting-ip') || '').trim();
  if (!ip) {
    return json({ ok: false, error: 'missing_client_ip' }, 400);
  }
  if (!env.RATE_LIMIT_DB) {
    return json({ ok: false, error: 'rate_limit_unavailable' }, 503);
  }

  const now = Math.floor(Date.now() / 1000);
  const windowStart = now - (now % RATE_LIMIT_WINDOW_SECONDS);
  const bucket = 'ip:' + (await sha256(ip)).slice(0, 32);

  let row;
  try {
    row = await env.RATE_LIMIT_DB.prepare(RATE_LIMIT_UPSERT)
      .bind(bucket, windowStart)
      .first();
  } catch {
    return json({ ok: false, error: 'rate_limit_unavailable' }, 503);
  }

  const count = Number(row && row.count);
  if (!Number.isInteger(count) || count < 1) {
    // 语句成功却没回一行，说明绑定指向的不是这张表：宁可 503，不许当成 0 次放行。
    return json({ ok: false, error: 'rate_limit_unavailable' }, 503);
  }
  if (count > RATE_LIMIT_MAX) {
    return json({ ok: false, error: 'rate_limited' }, 429);
  }
  if (count === 1) {
    // 尽力而为：清理失败不影响本次已经过闸的事实，也不会把旧行当成新一轮计数。
    try {
      await env.RATE_LIMIT_DB.prepare(RATE_LIMIT_PURGE)
        .bind(windowStart - RATE_LIMIT_RETENTION_SECONDS)
        .run();
    } catch {
      // 忽略；保留期在下一个窗口的首个请求再收。
    }
  }

  const key = 'sub:' + (await sha256(email)).slice(0, 40);

  if (await env.WAITLIST.get(key)) {
    return json({ ok: true, duplicate: true });
  }

  const cf = request.cf || {};
  await env.WAITLIST.put(
    key,
    JSON.stringify({
      email,
      at: new Date().toISOString(),
      country: cf.country || null,
      city: cf.city || null,
      ua: (request.headers.get('user-agent') || '').slice(0, 200),
      ref: (request.headers.get('referer') || '').slice(0, 300),
    })
  );

  return json({ ok: true, duplicate: false });
}

export async function onRequestGet() {
  return json({ ok: false, error: 'method_not_allowed' }, 405);
}
