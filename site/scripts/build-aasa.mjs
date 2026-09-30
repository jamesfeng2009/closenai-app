#!/usr/bin/env node
/**
 * 生成 AASA（Universal Links 关联文件，#38 把它从手改占位变成参数化）。
 *
 * 为什么用脚本而不是在仓库里写死真实 Team ID：
 * Apple 抓取 /.well-known/apple-app-site-association 时按 `TeamID(bundleId)` 精确匹配，
 * 占位串 `TEAMID.com.closenai.app` 的表现是**静默不唤起**（点了分享卡只在 Safari 开网页），
 * 而这类失败没有任何一端会报错。把 Team ID 变成构建期注入，部署流水线里缺它就当场失败。
 *
 * 用法：
 *   APPLE_TEAM_ID=XXXXXXXXXX node site/scripts/build-aasa.mjs          # 写回 site/public/.well-known/...
 *   APPLE_TEAM_ID=XXXXXXXXXX node site/scripts/build-aasa.mjs --out /tmp/aasa.json
 *   node site/scripts/build-aasa.mjs --check                            # 只校验现有文件已渲染
 *
 * Cloudflare Pages：Build command 里先跑这一条（环境变量在 Pages 的 Settings →
 * Environment variables 配 `APPLE_TEAM_ID`），再 `npm run build`，output 目录指向 `site/dist`
 *（public/ 会被 Astro 原样拷进 dist/，AASA 随之落地）。
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SITE_ROOT = path.resolve(HERE, '..');
const DEFAULT_OUT = path.join(SITE_ROOT, 'public', '.well-known', 'apple-app-site-association');

const TEAM_ID_RE = /^[A-Z0-9]{10}$/;
const BUNDLE_ID_RE = /^[a-z0-9]+(\.[a-z0-9-]+)+$/i;

function arg(name) {
  const i = process.argv.indexOf(name);
  return i === -1 ? null : process.argv[i + 1];
}

function build(teamId, bundleId) {
  return {
    applinks: {
      details: [
        {
          appIDs: [`${teamId}.${bundleId}`],
          components: [
            {
              '/': '/r/*',
              comment:
                '分享深链唯一接管路径（M4）：/r/{referral_code}。App 内由 Linking 取码，见 frontend/src/services/referral.ts。刻意不写 "/": "*"——那会把品牌站整站都变成唤起 App，落地页反而打不开。',
            },
          ],
        },
      ],
    },
  };
}

function main() {
  const check = process.argv.includes('--check');
  const out = arg('--out') || DEFAULT_OUT;
  const bundleId = process.env.APPLE_BUNDLE_ID || 'com.closenai.app';
  const teamId = process.env.APPLE_TEAM_ID || '';

  if (!BUNDLE_ID_RE.test(bundleId)) {
    console.error(`[build-aasa] APPLE_BUNDLE_ID 非法: ${bundleId}`);
    process.exit(2);
  }

  if (check) {
    const existing = fs.readFileSync(out, 'utf8');
    const parsed = JSON.parse(existing);
    const appIds = parsed?.applinks?.details?.[0]?.appIDs ?? [];
    const unresolved = appIds.filter((v) => !TEAM_ID_RE.test(v.split('.')[0] ?? ''));
    if (!appIds.length || unresolved.length) {
      console.error(
        `[build-aasa] AASA 仍是占位（appIDs=${JSON.stringify(appIds)}）——Universal Links 会静默失效`
      );
      process.exit(1);
    }
    console.log(`[build-aasa] OK: ${out} appIDs=${appIds.join(',')}`);
    return;
  }

  if (!TEAM_ID_RE.test(teamId)) {
    console.error(
      `[build-aasa] 缺少合法的 APPLE_TEAM_ID（10 位大写字母数字，Apple Developer → Membership 里的 Team ID），当前值: ${JSON.stringify(teamId)}`
    );
    process.exit(2);
  }

  const json = JSON.stringify(build(teamId, bundleId), null, 2) + '\n';
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, json);
  console.log(`[build-aasa] 写入 ${out}（appIDs=${teamId}.${bundleId}）`);
}

main();
