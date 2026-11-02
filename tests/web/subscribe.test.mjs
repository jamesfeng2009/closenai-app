/**
 * T7-13：订阅接口的限流闸（Node 内置 test runner，无第三方依赖）。
 *
 * 跑法：node --test tests/web/subscribe.test.mjs
 * （Node 20 的 test runner 不把目录参数当目录展开，必须点文件；变异脚本同此。）
 *
 * 这份闸刻意不“相信代码自述”：
 *   · 并发用例真的并发起十个请求，按响应码数通过数；
 *   · 原子性无法在单进程 fake 里证明，所以另有一条静态闸钉住 SQL 形状
 *     （单条 ON CONFLICT … RETURNING），并钉住「不再用 KV 先读后写」；
 *   · 故障方向、蜜罐、重复邮箱、无 IP、窗口过期各有一条，拒绝路径还必须零写名单。
 *
 * 数据全部用 example.com 与 TEST-NET 网段，避免把真实邮箱／IP 写进任何日志。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  onRequestGet,
  onRequestOptions,
  onRequestPost,
} from '../../web/functions/api/subscribe.js';

const SOURCE = readFileSync(
  new URL('../../web/functions/api/subscribe.js', import.meta.url),
  'utf8'
);

class FakeKv {
  constructor() {
    this.map = new Map();
    this.gets = [];
    this.puts = [];
  }

  async get(key) {
    this.gets.push(key);
    return this.map.has(key) ? this.map.get(key) : null;
  }

  async put(key, value) {
    this.puts.push([key, value]);
    this.map.set(key, value);
  }
}

class FakeD1 {
  constructor({ failAlways = false } = {}) {
    this.rows = new Map();
    this.statements = [];
    this.binds = [];
    this.failAlways = failAlways;
  }

  prepare(sql) {
    this.statements.push(sql);
    const db = this;
    return {
      bind(...args) {
        db.binds.push(args);
        return {
          async first() {
            if (db.failAlways) throw new Error('d1 unavailable');
            if (!sql.includes('ON CONFLICT')) {
              throw new Error('first() 只能落在原子自增语句上：' + sql);
            }
            const [key, windowStart] = args;
            const prev = db.rows.get(key);
            const count =
              !prev || prev.window_start !== windowStart ? 1 : prev.count + 1;
            db.rows.set(key, { window_start: windowStart, count });
            return { count, window_start: windowStart };
          },
          async run() {
            if (db.failAlways) throw new Error('d1 unavailable');
            return { success: true };
          },
        };
      },
    };
  }
}

function makeEnv({ d1 = new FakeD1(), kv = new FakeKv(), withLimiter = true } = {}) {
  const env = { WAITLIST: kv };
  if (withLimiter) env.RATE_LIMIT_DB = d1;
  return { env, kv, d1 };
}

function makePost({
  ip = '203.0.113.10',
  email = 'probe@example.com',
  extra = {},
  contentType = 'application/json',
  headers = {},
} = {}) {
  const h = new Headers({ 'content-type': contentType, ...headers });
  let body;
  if (ip !== null) h.set('cf-connecting-ip', ip);
  if (contentType.includes('form')) {
    body = new URLSearchParams({ email, ...extra }).toString();
  } else {
    body = JSON.stringify({ email, ...extra });
  }
  return new Request('https://closenai.app/api/subscribe', {
    method: 'POST',
    headers: h,
    body,
  });
}

async function post(env, options) {
  const res = await onRequestPost({ request: makePost(options), env });
  return { res, data: await res.json() };
}

function freezeClock(seconds) {
  const real = Date.now;
  Date.now = () => seconds * 1000;
  return () => {
    Date.now = real;
  };
}

const statuses = (xs) => xs.map((x) => x.res.status).sort((a, b) => a - b);

test('限流是单条原子 upsert，不再走 KV 先读后写', () => {
  assert.match(SOURCE, /ON CONFLICT\(bucket_key\) DO UPDATE SET/);
  assert.match(SOURCE, /RETURNING count, window_start/);
  assert.doesNotMatch(SOURCE, /WAITLIST\.get\(\s*['"]rl:/);
  assert.doesNotMatch(SOURCE, /WAITLIST\.put\(\s*ipKey/);
  assert.match(SOURCE, /count = CASE[\s\S]*ELSE 1[\s\S]*END/);
});

test('并发十个请求只有三个通过，拒绝的请求不写名单', async () => {
  const restore = freezeClock(1_800_000_000);
  try {
    const { env, kv, d1 } = makeEnv();
    const results = await Promise.all(
      Array.from({ length: 10 }, (_, i) =>
        post(env, { email: `burst-${i}@example.com` })
      )
    );

    assert.deepEqual(statuses(results), [200, 200, 200, 429, 429, 429, 429, 429, 429, 429]);
    assert.equal(results.filter((r) => r.res.status === 200).length, 3);
    assert.equal(kv.puts.length, 3, '只有通过限流的请求才允许写名单');
    const row = [...d1.rows.values()][0];
    assert.equal(row.count, 10, '被拒的请求也必须计入同一窗口的原子计数');
  } finally {
    restore();
  }
});

test('窗口过期后同一 IP 恢复通过，且窗口起点前移', async () => {
  const restore = freezeClock(1_800_000_000);
  try {
    const { env, d1 } = makeEnv();
    for (let i = 0; i < 3; i += 1) {
      const { res } = await post(env, { email: `window-${i}@example.com` });
      assert.equal(res.status, 200);
    }
    const blocked = await post(env, { email: 'window-blocked@example.com' });
    assert.equal(blocked.res.status, 429);

    Date.now = () => 1_800_000_060 * 1000;
    const after = await post(env, { email: 'window-after@example.com' });
    assert.equal(after.res.status, 200);

    const row = [...d1.rows.values()][0];
    assert.equal(row.window_start, 1_800_000_060);
    assert.equal(row.count, 1);
  } finally {
    restore();
  }
});

test('缺少 cf-connecting-ip 时拒绝且不写任何存储', async () => {
  const { env, kv, d1 } = makeEnv();
  const { res, data } = await post(env, { ip: null });
  assert.equal(res.status, 400);
  assert.equal(data.error, 'missing_client_ip');
  assert.equal(d1.statements.length, 0);
  assert.equal(kv.puts.length, 0);
});

test('D1 故障时失败关闭（503），不退回 KV 计数', async () => {
  const { env, kv } = makeEnv({ d1: new FakeD1({ failAlways: true }) });
  const { res, data } = await post(env, { email: 'fail-closed@example.com' });
  assert.equal(res.status, 503);
  assert.equal(data.error, 'rate_limit_unavailable');
  assert.equal(kv.puts.length, 0);
});

test('未绑定限流库时同样失败关闭（503）', async () => {
  const { env, kv } = makeEnv({ withLimiter: false });
  const { res, data } = await post(env, { email: 'no-binding@example.com' });
  assert.equal(res.status, 503);
  assert.equal(data.error, 'rate_limit_unavailable');
  assert.equal(kv.puts.length, 0);
});

test('蜜罐命中时静默成功，不碰限流表也不写名单', async () => {
  const { env, kv, d1 } = makeEnv();
  const { res, data } = await post(env, {
    email: 'bot@example.com',
    extra: { website: 'https://spam.example' },
  });
  assert.equal(res.status, 200);
  assert.deepEqual(data, { ok: true, duplicate: false });
  assert.equal(d1.statements.length, 0);
  assert.equal(kv.puts.length, 0);
});

test('非法邮箱在限流之前拒绝', async () => {
  const { env, kv, d1 } = makeEnv();
  const { res, data } = await post(env, { email: 'not-an-email' });
  assert.equal(res.status, 400);
  assert.equal(data.error, 'invalid_email');
  assert.equal(d1.statements.length, 0);
  assert.equal(kv.puts.length, 0);
});

test('重复邮箱仍返回 duplicate 且只写一次名单', async () => {
  const restore = freezeClock(1_800_000_000);
  try {
    const { env, kv } = makeEnv();
    const first = await post(env, { email: 'Dup@Example.com' });
    const second = await post(env, { email: 'dup@example.com' });
    assert.equal(first.res.status, 200);
    assert.equal(first.data.duplicate, false);
    assert.equal(second.res.status, 200);
    assert.equal(second.data.duplicate, true);
    assert.equal(kv.puts.length, 1);
    const stored = JSON.parse([...kv.map.values()][0]);
    assert.equal(stored.email, 'dup@example.com');
  } finally {
    restore();
  }
});

test('表单提交保持可用', async () => {
  const { env, kv } = makeEnv();
  const { res, data } = await post(env, {
    email: 'form-probe@example.com',
    contentType: 'application/x-www-form-urlencoded',
  });
  assert.equal(res.status, 200);
  assert.equal(data.duplicate, false);
  assert.equal(kv.puts.length, 1);
  assert.equal(JSON.parse([...kv.map.values()][0]).email, 'form-probe@example.com');
});

test('限流表只落截断哈希、窗口与计数', async () => {
  const restore = freezeClock(1_800_000_000);
  try {
    const { env, d1 } = makeEnv();
    await post(env, { ip: '198.51.100.42', email: 'privacy@example.com' });

    const keys = [...d1.rows.keys()];
    assert.equal(keys.length, 1);
    assert.match(keys[0], /^ip:[0-9a-f]{32}$/);
    assert.ok(!keys[0].includes('198.51.100.42'), '限流键不许含原始 IP');

    const flat = JSON.stringify(d1.binds);
    assert.ok(!flat.includes('198.51.100.42'), '绑定参数不许含原始 IP');
    assert.ok(!flat.includes('privacy@example.com'), '限流表不许碰邮箱');
    const row = [...d1.rows.values()][0];
    assert.deepEqual(Object.keys(row).sort(), ['count', 'window_start']);
  } finally {
    restore();
  }
});

test('GET 405 / OPTIONS 204 且带 CORS', async () => {
  const getRes = await onRequestGet();
  assert.equal(getRes.status, 405);
  assert.equal(getRes.headers.get('access-control-allow-origin'), 'https://closenai.app');

  const optRes = await onRequestOptions();
  assert.equal(optRes.status, 204);
  assert.equal(optRes.headers.get('access-control-allow-origin'), 'https://closenai.app');
  assert.match(optRes.headers.get('access-control-allow-methods'), /POST/);
});

test('限流命中 429 也带 CORS，且不写名单', async () => {
  const restore = freezeClock(1_800_000_000);
  try {
    const { env, kv } = makeEnv();
    for (let i = 0; i < 3; i += 1) {
      await post(env, { email: `cors-${i}@example.com` });
    }
    const { res, data } = await post(env, { email: 'cors-blocked@example.com' });
    assert.equal(res.status, 429);
    assert.equal(data.error, 'rate_limited');
    assert.equal(res.headers.get('access-control-allow-origin'), 'https://closenai.app');
    assert.equal(kv.puts.length, 3);
  } finally {
    restore();
  }
});
