// G-C1（工作单批 T-C）：obs 的 handler 必须把 ctx 里的 trace/user 原样打进 JSON 行。
//
// 这条闸判的是「全链路 grep 能不能落到 Go 日志」这件事本身：M-C1 的变异形态
// （摘掉 Handler 的注入）红的就是这里，而不是任何装配代码。
package obs

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"testing"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// capture 把默认 logger 换成写进 buf 的 JSON handler，返回行内容。
func capture(t *testing.T, f func()) []map[string]any {
	t.Helper()
	var buf bytes.Buffer
	orig := slog.Default()
	Setup(&buf)
	defer slog.SetDefault(orig)
	f()
	var lines []map[string]any
	for _, part := range bytes.Split(bytes.TrimSpace(buf.Bytes()), []byte("\n")) {
		if len(part) == 0 {
			continue
		}
		var m map[string]any
		if err := json.Unmarshal(part, &m); err != nil {
			t.Fatalf("obs 的输出必须是可解析的 JSON 行：%v（原文 %q）", err, part)
		}
		lines = append(lines, m)
	}
	return lines
}

func TestTraceFromCtxIsVerbatimInJSON(t *testing.T) {
	ctx := domain.WithTraceID(context.Background(), "T-GC1-42")
	lines := capture(t, func() {
		slog.InfoContext(ctx, "hello")
	})
	if len(lines) != 1 {
		t.Fatalf("一行日志应产出一条 JSON，得到 %d", len(lines))
	}
	if got := lines[0]["trace_id"]; got != "T-GC1-42" {
		t.Errorf("trace_id 字段必须逐字等于 ctx 注入值，得到 %q", got)
	}
	// 缺 user 也必须有格子：grep 规则两边统一，字段不许时隐时现。
	if got := lines[0]["user_id"]; got != "-" {
		t.Errorf("无 user 时 user_id 应为 \"-\"，得到 %q", got)
	}
}

func TestBothFieldsAbsentBecomeDashes(t *testing.T) {
	lines := capture(t, func() {
		slog.Info("no ctx at all")
	})
	if lines[0]["trace_id"] != "-" || lines[0]["user_id"] != "-" {
		t.Errorf("两侧缺值必须打 \"-\"（与 Python ContextFilter 逐字对齐），得到 trace=%q user=%q",
			lines[0]["trace_id"], lines[0]["user_id"])
	}
}

func TestUserCarrierRoundtrip(t *testing.T) {
	ctx := WithUser(context.Background(), "u-7")
	if UserIDFrom(ctx) != "u-7" {
		t.Fatal("WithUser/UserIDFrom 没成环")
	}
	if got := UserIDFrom(WithUser(context.Background(), "")); got != "" {
		t.Errorf("空 user 不该注入，得到 %q", got)
	}
	// 注入口径同 trace：带 user 的行 JSON 里逐字可见。
	lines := capture(t, func() {
		slog.InfoContext(ctx, "with user")
	})
	if lines[0]["user_id"] != "u-7" {
		t.Errorf("user_id 字段必须逐字，得到 %q", lines[0]["user_id"])
	}
}

func TestWithAttrsKeepsInjection(t *testing.T) {
	// WithAttrs/WithGroup 返回的包装若丢了 ctxHandler，
	// 「logger := slog.With(...)」派生的 logger 就会静默丢 trace——这是最隐蔽的变异形态。
	var buf bytes.Buffer
	orig := slog.Default()
	Setup(&buf)
	defer slog.SetDefault(orig)
	base := slog.Default().With("component", "gate")
	base.InfoContext(domain.WithTraceID(context.Background(), "T-WITHATTRS"), "x")
	var m map[string]any
	if err := json.Unmarshal(bytes.TrimSpace(buf.Bytes()), &m); err != nil {
		t.Fatalf("输出应是单行 JSON：%v（%q）", err, buf.String())
	}
	if m["trace_id"] != "T-WITHATTRS" || m["component"] != "gate" {
		t.Errorf("WithAttrs 派生的 logger 必须仍注入 trace_id，得到 %q（全部字段 %v）", m["trace_id"], m)
	}
}
