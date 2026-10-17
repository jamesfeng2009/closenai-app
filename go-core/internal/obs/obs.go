// Package obs 是 go-api 的结构化日志装配点（批 T-C，工作单 T-4）。
//
// 形状：stdlib slog + JSON handler + 一个从 ctx 注入 trace_id/user_id 的包装。
// 不引第三方（D-7 的依赖足迹口径）。trace 的 ctx 载体刻意复用 domain（T-3），
// user 的载体放这里——domain 的 arch 闸不允许它认识「会话」这种 HTTP 概念。
// 缺值统一打 "-"：与 Python 侧 ContextFilter 的字面量逐字对齐，grep 只写一条规则。
package obs

import (
	"context"
	"io"
	"log/slog"
	"os"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

const dash = "-"

type userKey struct{}

// WithUser 注入会话主体；空串＝不注入（与 domain.WithTraceID 同一取舍）。
func WithUser(ctx context.Context, userID string) context.Context {
	if userID == "" {
		return ctx
	}
	return context.WithValue(ctx, userKey{}, userID)
}

// UserIDFrom 取回主体 id；没有时为空串。
func UserIDFrom(ctx context.Context) string {
	v, _ := ctx.Value(userKey{}).(string)
	return v
}

type ctxHandler struct{ base slog.Handler }

func (h ctxHandler) Enabled(ctx context.Context, l slog.Level) bool {
	return h.base.Enabled(ctx, l)
}

func (h ctxHandler) Handle(ctx context.Context, r slog.Record) error {
	t := domain.TraceIDFrom(ctx)
	if t == "" {
		t = dash
	}
	u := UserIDFrom(ctx)
	if u == "" {
		u = dash
	}
	r.AddAttrs(slog.String("trace_id", t), slog.String("user_id", u))
	return h.base.Handle(ctx, r)
}

func (h ctxHandler) WithAttrs(a []slog.Attr) slog.Handler {
	return ctxHandler{base: h.base.WithAttrs(a)}
}

func (h ctxHandler) WithGroup(g string) slog.Handler {
	return ctxHandler{base: h.base.WithGroup(g)}
}

// Setup 把默认 logger 装成「stdout JSON + ctx 关联字段」。重复调用安全（幂等）。
func Setup(w io.Writer) {
	slog.SetDefault(slog.New(ctxHandler{base: slog.NewJSONHandler(w, nil)}))
}

// SetupStdout 是进程入口的缺省形状。
func SetupStdout() { Setup(os.Stdout) }
