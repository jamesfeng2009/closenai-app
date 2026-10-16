package aiclient

import (
	"errors"
	"fmt"

	"google.golang.org/grpc/codes"
)

// Error 是本包对外唯一的错误形状。它存在的理由只有一个：让调用方能问一句
// 「这次是该重试，还是该按 N3 的 reject 走」，而不必去匹配 gRPC 的字符串。
//
// V3 17 §8 把失败分成 transient / permanent 两类，§9 接着要求 permanent 走
// retry limit → DLQ → failure record → alert 而不是无限重投；
// 16:160 逐字 "Permanent business rejection; record it and do not blindly retry."。
// 这两句合起来就是 Kind 这个字段的全部理由。
type Error struct {
	// Op 是方法名（Embed / ClassifySafety / StreamChat），只用于日志与错误串。
	Op    string
	Code  codes.Code
	Kind  FailureKind
	Cause FailureCode
	// Attempt 是失败时已经试到第几次（从 1 起）。观测「重试到底有没有发生」全靠它，
	// 没有这个数字就只能相信代码里那句「应该会重试」。
	Attempt int
	Err     error
}

func (e *Error) Error() string {
	if e == nil {
		return "<nil>"
	}
	if e.Err == nil {
		return fmt.Sprintf("aiclient: %s 失败（%s，第 %d 次尝试）", e.Op, e.Cause, e.Attempt)
	}
	return fmt.Sprintf("aiclient: %s 失败（%s，第 %d 次尝试）：%v", e.Op, e.Cause, e.Attempt, e.Err)
}

func (e *Error) Unwrap() error { return e.Err }

// AsError 把错误链上的 *Error 取出来（errors.As 语义，所以调用方 wrap 过也能问）。
func AsError(err error, target **Error) bool {
	return errors.As(err, target)
}

// IsRetryable 只回答「这一类错误重试有没有可能变好」。它**不**代表本包已经重试过——
// 重试预算在 Options 里，StreamChat 恒不重试（半途的流不能重放）。
func IsRetryable(err error) bool {
	var e *Error
	return AsError(err, &e) && e.Kind == FailureTransient
}

// classify 把 gRPC 状态码归成原因码。
//
// 这份映射是**本仓补齐**的，不是 V3 条文：V3 全篇没有定义过任何 rpc（见
// closenai_ai_rpc.proto 文件头的逐字扫描结果）。归类的依据是两头各自今天真实发生的故障：
//
//	· Unavailable ← Python 进程没起/在重启（k8s 滚动）——transient，13 §8「AI is unavailable」那一节。
//	· DeadlineExceeded ← 17 §8 逐字把 "AI provider timeout" 归 Transient。
//	· ResourceExhausted ← provider 429（R3 的 handler 按这一码回报配额类失败）——transient，
//	  但本包**不**因它退避重投太久：它意味着对面在限流，重投只会加剧，见 Options.Retries 的注释。
//	· InvalidArgument / FailedPrecondition / OutOfRange → permanent：17 §8 的 Permanent 面
//	  （unsupported schema、invalid candidate、rejected claim type）。
//	· Internal → transient：对面 handler 抛了未捕获异常，换一次可能就好（也是 5xx 类）。
//	· Unimplemented / DataLoss / 未知 → permanent：跨语言里只有版本错配会给出 Unimplemented，
//	  那不是「再试一次」的问题。
func classify(op string, err error, attempt int) *Error {
	code := statusOf(err)
	e := &Error{Op: op, Code: code, Attempt: attempt, Err: err}
	switch code {
	case codes.DeadlineExceeded:
		e.Kind, e.Cause = FailureTransient, FailureDeadline
	case codes.Unavailable:
		e.Kind, e.Cause = FailureTransient, FailureTransport
	case codes.ResourceExhausted:
		e.Kind, e.Cause = FailureTransient, FailureRateLimited
	case codes.Internal, codes.Aborted, codes.Canceled:
		e.Kind, e.Cause = FailureTransient, FailureUnavailable
	case codes.Unimplemented, codes.DataLoss, codes.Unknown:
		e.Kind, e.Cause = FailurePermanent, FailureMalformed
	default:
		e.Kind, e.Cause = FailurePermanent, FailureRejected
	}
	return e
}

// malformed 是「对面的字节不合契约」这一类：V3 17 §8 把它列在 Permanent 的第一行
// （malformed protobuf），所以它既进 reject 面，也**不该**计入熔断的连续失败计数——
// 对面好好的，是我们的校验没过，把它当故障会让一次字段误解把整条 AI 面关进小黑屋。
func malformed(op, why string) *Error {
	return &Error{
		Op: op, Code: codes.InvalidArgument, Kind: FailurePermanent,
		Cause: FailureMalformed, Attempt: 1, Err: errors.New(why),
	}
}
