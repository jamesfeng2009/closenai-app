# Cloudflare D1：订阅限流（T7-13）

`web/functions/api/subscribe.js` 用 D1 绑定 `RATE_LIMIT_DB` 做原子计数。
**未完成下面两步之前不要部署这版代码**：缺绑定时接口按 `503 rate_limit_unavailable`
失败关闭（故意的，不允许静默变成无限接受）。

## 1. 建库并建表

```bash
wrangler d1 create closenai-rate-limit
# 把输出里的 database_id 记下来
wrangler d1 execute closenai-rate-limit --remote \
  --file deploy/cloudflare/subscribe_rate_limits.sql
```

## 2. 给 Pages 项目绑两个环境

在 Cloudflare 控制台（或 Pages projects API 的 `deployment_configs`）给
`closenai-web` 的 **production 与 preview** 都加同一个 D1 绑定：

- 变量名：`RATE_LIMIT_DB`
- 值：`closenai-rate-limit` 的 database_id

原有的 `WAITLIST`（KV）绑定保持不变。

## 口子与保留期

- 键：`ip:` + `sha256(cf-connecting-ip)` 前 32 位；原始 IP 不落库。
- 窗口：60 秒（`RATE_LIMIT_WINDOW_SECONDS`），上限 3 次（`RATE_LIMIT_MAX`）。
- 故障方向：D1 未绑定 / 查询报错 / 语句没回行 ⇒ `503 rate_limit_unavailable`，不写名单。
- 保留期：只在 `window_start / count` 上多留一个窗口；下一次任意 IP 进入新窗口时，
  `subscribe.js` 会删除早于两个窗口的行。完全无流量时最后一批行会留到下一次请求。

## 验证（部署后）

```bash
for i in 1 2 3 4; do
  curl -s -o /dev/null -w '%{http_code}\n' -X POST https://closenai.app/api/subscribe \
    -H 'content-type: application/json' -d "{\"email\":\"probe+$i@example.com\"}"
done
# 期望：200 200 200 429（同一个出口 IP、60 秒内）
```
