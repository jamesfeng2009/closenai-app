-- T7-13：订阅接口的原子限流计数（Cloudflare D1）。
--
-- 用法见 deploy/cloudflare/README.md。这张表刻意只存三列：
--   bucket_key   = 'ip:' + sha256(cf-connecting-ip) 的前 32 位十六进制
--   window_start = 窗口起点的 Unix 秒（60 秒窗口）
--   count        = 本窗口内该 IP 的提交次数
-- 原始 IP 与邮箱都不进这张表；旧窗口由 subscribe.js 在下一个窗口的首个请求里清掉。
CREATE TABLE IF NOT EXISTS subscribe_rate_limits (
  bucket_key TEXT PRIMARY KEY,
  window_start INTEGER NOT NULL,
  count INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS subscribe_rate_limits_window_start
  ON subscribe_rate_limits (window_start);
