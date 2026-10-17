// trace 载体（批 T-B，工作单 T-3）。
//
// 载体函数放 domain 而不是新包：arch 闸规定 domain 不 import 项目内他包
// （arch_test.go:5），而 emit 恰恰是需要读 trace 的写点——反向（httpapi/messaging
// import domain）合法，正向只能把叶子放这里。形状逐字对齐同文件的 Envelope：
// 走 context 不走参数，漏传一次就断链的代价由类型系统之外的闸兜（G-B2）。
package domain

import "context"

type traceKey struct{}

// WithTraceID 注入链路 id；空串视作不注入——「没有 trace」与「trace 是空串」
// 在日志面上必须同为「缺席」，否则 grep 会多出一列假值。
func WithTraceID(ctx context.Context, traceID string) context.Context {
	if traceID == "" {
		return ctx
	}
	return context.WithValue(ctx, traceKey{}, traceID)
}

// TraceIDFrom 取回链路 id；没有注入时返回空串（后台 cron 就是合法的空）。
func TraceIDFrom(ctx context.Context) string {
	v, _ := ctx.Value(traceKey{}).(string)
	return v
}
