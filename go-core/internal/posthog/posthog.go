// Package posthog 是 Go 侧的分析事件中继，只服务 N8 批 1 的 /analytics/batch。
//
// 与 Python 侧的对齐口径（backend/services/analytics_service.py::_relay_to_posthog）：
//   - 未配置 API key ⇒ 整条链路 no-op，绝不因为分析平台缺席而让业务请求失败；
//   - 发送失败只记日志，不向上抛——事件计数照常累加（Python 那侧 capture 入队即算成功）。
//
// 差异登记（tasks.md N8 批 1 的 ⚠️ 行）：posthog-python 是「入队 + 后台线程发送」，
// 这里是 httpapi handler 用一组 goroutine 并发发送、整体 3s 上限。
// 两边都不阻塞到外部平台恢复为止，但 Go 侧在 PostHog 长时间不可用时最多让这批慢 3 秒。
package posthog

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// TimeoutSeconds 是单批发送的总时限；调用方负责并发，本包只保证每个请求自己会到点。
const Timeout = 3 * time.Second

type Client struct {
	apiKey     string
	captureURL string
	http       *http.Client
}

// New 按 Python 侧同一批变量名取值（POSTHOG_API_KEY / POSTHOG_HOST）。
// host 缺省与 posthog-python 一致（app.posthog.com）；尾斜杠剥掉，避免拼出 //capture/。
func New(apiKey, host string) *Client {
	if host == "" {
		host = "https://app.posthog.com"
	}
	return &Client{
		apiKey:     apiKey,
		captureURL: strings.TrimRight(host, "/") + "/capture/",
		http:       &http.Client{Timeout: Timeout},
	}
}

// Configured 报告有没有 key——handler 与测试都用它决定要不要真的发。
func (c *Client) Configured() bool { return c != nil && c.apiKey != "" }

// Event 是 capture 端点的一载荷。Properties 存**已编码的 JSON 字节**而不是 any：
// 上游值是从请求原文来的，用 map[string]any 过一遍 Go 的 float64 会把 10**30 这类
// 超出 float64 的整数变成 1e+30（Python 侧保留任意精度整数）。
type Event struct {
	Event      string                     `json:"event"`
	DistinctID string                     `json:"distinct_id"`
	Properties map[string]json.RawMessage `json:"properties"`
	Timestamp  string                     `json:"timestamp"`
	APIKey     string                     `json:"api_key"`
}

// Capture 发送一条已匿名化的事件。ctx 由调用方带超时，失败返回 error 供上层记日志。
func (c *Client) Capture(ctx context.Context, ev Event) error {
	if !c.Configured() {
		return nil
	}
	// api_key 由客户端持有、调用方不填（与 posthog-python 的 project_api_key 同构）。
	ev.APIKey = c.apiKey
	body, err := json.Marshal(ev)
	if err != nil {
		return fmt.Errorf("posthog: marshal: %w", err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.captureURL, bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("posthog: request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	res, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("posthog: send: %w", err)
	}
	defer func() {
		_, _ = io.Copy(io.Discard, res.Body)
		_ = res.Body.Close()
	}()
	if res.StatusCode < 200 || res.StatusCode >= 300 {
		return fmt.Errorf("posthog: capture returned HTTP %d", res.StatusCode)
	}
	return nil
}
