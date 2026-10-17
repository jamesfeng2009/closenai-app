// G-C3＋访问日志闸（工作单批 T-C）：入站 X-Trace-Id 消毒 + 每请求一行可 grep 的访问日志。
//
// 判据分两层：sanitizeTraceID 的形状表是「什么会被拒」的直接证据；
// 走 Wrap 的功能层证明「拒了之后重新生成、且日志行与回执里都不含 CR/LF」。
package httpapi

import (
	"bytes"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/obs"
)

func TestSanitizeTraceIDShape(t *testing.T) {
	ok := []string{"T-42", "0f6c3a9b-1111-2222-3333-444455556666", strings.Repeat("a", 128), "a.b_c-d"}
	for _, v := range ok {
		if got := sanitizeTraceID(v); got != v {
			t.Errorf("合规值必须逐字放行：%q → %q", v, got)
		}
	}
	bad := map[string]string{
		"空串":             "",
		"CR+LF 注入":       "evil\r\nINJECTED: 1",
		"裸 LF（伪造行）":      "evil\nfake line",
		"裸 CR":           "evil\rfake",
		"尾随换行（Go $ 不放过）": "abc\n",
		"控制字符":           "ab\x00cd",
		"超长 129":         strings.Repeat("a", 129),
		"空格":             "a b",
		"斜杠路径样":          "../etc/passwd",
	}
	for name, v := range bad {
		if got := sanitizeTraceID(v); got != "" {
			t.Errorf("%s：不合形的值必须归零，%q → %q", name, v, got)
		}
	}
}

// runWrapped 用 Wrap 包一个回显 ctx 的 handler，返回响应、以及期间产出的日志行。
func runWrapped(t *testing.T, inbound string, header bool) (*httptest.ResponseRecorder, []map[string]any) {
	t.Helper()
	orig := slog.Default()
	var buf bytes.Buffer
	// 走 obs 的完整装配（JSON handler＋ctx 注入）：这条闸判的是「生产日志行长什么样」，
	// 用裸 NewJSONHandler 会绿在「trace 根本没进日志」的假形态上。
	obs.Setup(&buf)
	defer slog.SetDefault(orig)

	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusTeapot)
	})
	m := NewMiddleware(nil, false)
	req := httptest.NewRequest(http.MethodGet, "/v1/x", nil)
	if header {
		req.Header.Set(TraceHeader, inbound)
	}
	rec := httptest.NewRecorder()
	m.Wrap(inner).ServeHTTP(rec, req)

	var lines []map[string]any
	for _, part := range bytes.Split(bytes.TrimSpace(buf.Bytes()), []byte("\n")) {
		if len(part) == 0 {
			continue
		}
		var m map[string]any
		if err := json.Unmarshal(part, &m); err != nil {
			t.Fatalf("访问日志必须是可 grep 的 JSON 行：%v（原文 %q）", err, part)
		}
		lines = append(lines, m)
	}
	return rec, lines
}

func TestAccessLogCarriesAdoptedTrace(t *testing.T) {
	rec, lines := runWrapped(t, "T-GC3-OK", true)
	if len(lines) != 1 {
		t.Fatalf("每请求恰好一行访问日志，得到 %d 行", len(lines))
	}
	line := lines[0]
	if line["trace_id"] != "T-GC3-OK" {
		t.Errorf("访问日志 trace_id 必须逐字：得到 %v", line["trace_id"])
	}
	if line["msg"] != "go-api: access" || line["method"] != http.MethodGet ||
		line["path"] != "/v1/x" || line["status"] != float64(http.StatusTeapot) {
		t.Errorf("访问日志缺维度或值不对：%v", line)
	}
	if _, has := line["duration_ms"]; !has {
		t.Errorf("没有 duration_ms，这条日志就没法回答「慢在哪」：%v", line)
	}
	if got := rec.Header().Get(TraceHeader); got != "T-GC3-OK" {
		t.Errorf("回执头必须回显采用值，得到 %q", got)
	}
}

func TestMalformedHeaderIsRegeneratedNotEchoed(t *testing.T) {
	// httptest.NewRequest 允许直接塞带控制字符的头值（真实 HTTP 栈会拦，
	// 但消毒判据要能独立于传输层被打破——变异 M-C3 打的正是这格）。
	evil := "abc\r\nX-Injected: 1"
	rec, lines := runWrapped(t, evil, true)
	if len(lines) != 1 {
		t.Fatalf("应恰好一行访问日志，得到 %d", len(lines))
	}
	got := lines[0]["trace_id"].(string)
	if got == evil || strings.ContainsAny(got, "\r\n") {
		t.Errorf("注入值进了日志行：trace_id=%q", got)
	}
	if !traceIDShape.MatchString(got) || got == "abc" {
		t.Errorf("不合形头应整体弃用并重新生成（新 uuid 形状），得到 %q", got)
	}
	if rh := rec.Header().Get(TraceHeader); rh != got {
		t.Errorf("回执与日志的 trace 必须同源：日志=%q 回执=%q", got, rh)
	}
}

func TestMissingHeaderStillLogsFreshTrace(t *testing.T) {
	_, lines := runWrapped(t, "", false)
	if len(lines) != 1 {
		t.Fatalf("应恰好一行，得到 %d", len(lines))
	}
	got := lines[0]["trace_id"].(string)
	if !traceIDShape.MatchString(got) || got == "" || got == "-" {
		t.Errorf("Go 侧访问日志恒有 trace（缺失即重生成），得到 %q", got)
	}
}

// ctx 载体与响应面的连通：Wrap 之后 domain.TraceIDFrom 在 handler 里可见——
// 这是 T-B「emit 写出带 trace 的 outbox 行」在 HTTP 起源侧的前提。
func TestDomainCarrierVisibleInHandler(t *testing.T) {
	seen := make(chan string, 1)
	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seen <- domain.TraceIDFrom(r.Context())
	})
	req := httptest.NewRequest(http.MethodPost, "/v1/y", nil)
	req.Header.Set(TraceHeader, "T-CARRIER-9")
	NewMiddleware(nil, false).Wrap(inner).ServeHTTP(httptest.NewRecorder(), req)
	if got := <-seen; got != "T-CARRIER-9" {
		t.Errorf("handler 里 domain 载体应可见采用值，得到 %q", got)
	}
}
