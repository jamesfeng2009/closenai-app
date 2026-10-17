// 批 T-E：gRPC 出站面的 trace_id 透传。
//
// 键名 "x-trace-id" 与 HTTP 头（httpapi/middleware.go:24 "X-Trace-Id"，HTTP 头名大小写不敏感）、
// AMQP 头（messaging/amqp/publisher.go:44、inbound/inbound.go:59）是同一个逻辑头的三种承载形状。
// gRPC metadata 按规范一律小写，所以这里落地就是小写字面量。
//
// 与 EmbedRequest.trace_id（closenai_ai_rpc.proto:113，消息字段）是**两条**载体：
// metadata 覆盖全部三个方法、畸形请求也带得到，服务端 _apply_trace 以 metadata 优先。
package aiclient

import (
	"context"

	"google.golang.org/grpc"
	"google.golang.org/grpc/metadata"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

const metadataTraceKey = "x-trace-id"

// injectTrace 把 ctx 里的 trace 附到出站 metadata。
//
// 空值不注入（与 AMQP 发布面 publisher.go:140-142 同一取舍）：与其发一个空串让对面
// 在「没带」和「带了个空的」之间猜，不如让 metadata 缺席就是缺席。
//
// ⚠️ 后台调用方登记（工作单 §二·批 T-E）：本拦截器只搬运 ctx 里已有的值。
// cron／generation 这类没有 HTTP 请求上下文的调用，**由调用方**在发起前用
// domain.WithTraceID(ctx, <由 generation_run_id 派生的值>) 先行注入——
// 不这么做的后果不是报错，而是这一跳的日志在 grep 里凭空消失，比红更难发现。
// 今天 go-core 里 aiclient 还没有生产调用点（R 系列口径「管道通了、流量仍是零」），
// 所以这条约束现在只欠一句注释，不欠一次修复。
func injectTrace(ctx context.Context) context.Context {
	traceID := domain.TraceIDFrom(ctx)
	if traceID == "" {
		return ctx
	}
	return metadata.AppendToOutgoingContext(ctx, metadataTraceKey, traceID)
}

func withTraceUnary(ctx context.Context, method string, req, reply any, cc *grpc.ClientConn, invoker grpc.UnaryInvoker, opts ...grpc.CallOption) error {
	return invoker(injectTrace(ctx), method, req, reply, cc, opts...)
}

func withTraceStream(ctx context.Context, desc *grpc.StreamDesc, cc *grpc.ClientConn, method string, streamer grpc.Streamer, opts ...grpc.CallOption) (grpc.ClientStream, error) {
	return streamer(injectTrace(ctx), desc, cc, method, opts...)
}
