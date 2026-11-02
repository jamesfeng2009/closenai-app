#!/usr/bin/env bash
# 部署 web/ 到 Cloudflare Pages（含 Functions）。
#
# 关键点（踩过的坑，别改）：
#   1. 必须 --cwd web：wrangler 的 functions 目录固定取 process.cwd()/functions，
#      不认 --functions-directory（v4 已移除该参数）。
#   2. 必须在命令里 unset 代理：沙箱代理会让 wrangler 的 fetch 报 "fetch failed"。
#   3. 退出码 124 是尾部清理卡住，部署通常已成功，看日志里的 Deployment complete。
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

ACCOUNT_ID="${CF_ACCOUNT_ID:-d1a2e66eb944d87c62b59436d02d00bc}"
PROJECT="closenai-web"
WRANGLER="${WRANGLER_BIN:-/Users/fengyu/.nvm/versions/node/v24.12.0/bin/wrangler}"

echo "→ 刷新登录态"
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
  CI=1 WRANGLER_SEND_METRICS=false "$WRANGLER" whoami >/dev/null 2>&1 || {
    echo "✘ 刷新失败，请先执行：wrangler login"; exit 1; }

echo "→ 部署 web/"
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
  CLOUDFLARE_ACCOUNT_ID="$ACCOUNT_ID" CI=1 WRANGLER_SEND_METRICS=false \
  "$WRANGLER" pages deploy . --cwd web \
  --project-name="$PROJECT" --branch=main --commit-dirty=true 2>&1 | grep -vE '^\s*$'

echo "→ 验证线上"
sleep 3
FAIL=0
check() {
  local path="$1" want="$2"
  local got
  got=$(curl -s -o /dev/null -w '%{http_code}' -m 20 "https://closenai.app${path}")
  if [ "$got" = "$want" ]; then
    printf '  %-22s %s ✓\n' "$path" "$got"
  else
    printf '  %-22s %s (期望 %s) ✗\n' "$path" "$got" "$want"
    FAIL=1
  fi
}
check "/" 200
check "/privacy" 200
check "/terms" 200
check "/r/testcode" 200
check "/api/subscribe" 405
check "/zzz-nonexistent-$$" 404

[ "$FAIL" -eq 0 ] && echo "→ 全部通过" || echo "→ 有路径不符预期，检查上面的标记"
exit "$FAIL"
