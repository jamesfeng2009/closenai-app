// Package aiclient 是 Go 侧调用 Python AI 面的唯一出口（R2）。
//
// ───────────────────────── 这一面包住什么
//
//	· 三方法接口（Embed / ClassifySafety / StreamChat）——多一个都不加，
//	  判据是 V3 06:173 逐字 "Do not introduce gRPC merely because both runtimes use Protobuf."
//	  与 contracts/rpc/ownership.md §0 判据 2；接口方法数有一条反射闸守着。
//	· deadline / 重试 / 熔断——四条默认值全部指回本仓实测数字（见 Options 的逐行注释），
//	  不在这包里凭空发明超时。
//	· 降级模板——只有「Python 整个进程联系不上」这条路径需要在 Go 侧出文案，
//	  因为那时 message_generator.py:136 的就地兜底根本不会执行；模板副本由
//	  scripts/gen_go_ai_side.py 从 backend/data/copy/*.json 逐字节录过来。
//	· ChatChunk → SSE 行的映射——现网字节口径是 routers/chat.py:39-42 的 _sse_event()，
//	  夹具 sse_event_golden.json 就是它的输出。
//
// ───────────────────────── 为什么这里可以 import gen/ 与 grpc，而 domain 不行
//
// domain 的 arch 闸（internal/domain/arch_test.go 的 transportImports）把 grpc / protobuf
// 列进黑名单，V3 07:8 逐字 "It must not depend on PostgreSQL, RabbitMQ, Protobuf or an LLM."。
// 所以解码这件事必须发生在 domain 之外、HTTP 层之内，这一包就是那个位置：
// 边界内用 protobuf，边界外只交已解码的 Go 值对象。ownership.md §5 的「不改这个闸」
// 读作「只加不减」，本包不动那个黑名单。
package aiclient

import (
	"context"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// Client 是 AI 出口的接口面。方法面**刻意只有三个业务方法**（加 Close 共 4 个）——
// 多一个就违反 ownership.md §0 判据 2，所以有一条反射闸（TestClientSurfaceIsExactlyThreeMethods）
// 按名字逐个比对，改名、加方法都会红。
type Client interface {
	// Embed 向量化。失败面＝**保持 fail-soft**（ownership.md §1 第一行：今天两个调用点都
	// except → return None）：一次网络抖动不该让「记一条想法」失败。
	// 所以这个方法的 err 只用于**编程错误**（空 texts、未配置又没禁用），
	// 传输与 provider 失败一律走 EmbedResult.Degraded —— 把「别硬失败」写进返回形状里，
	// 而不是指望每个调用方都记得吞异常。
	Embed(ctx context.Context, req EmbedRequest) (EmbedResult, error)

	// ClassifySafety 内容交到用户手上之前必须判完。
	//
	// ⚠️ D-8 落点：今天 Python 两侧都写明超时 fail-open（safety_service.py:10 与 :48 的
	// 注释、:393-395 的 except → return True），跨进程后**超时不得等于放行**。
	// 本包的实现是：任何传输失败（deadline / 连接失败 / 畸形判定）都返回
	// Safety.Status = domain.SafetyUnavailable ＋ 一个非 nil err。
	// 之所以两份都给：domain/gate.go:396-404 对 nil、unavailable、未知态三种情况一律
	// ReasonSafetyUnavailable 拦下，所以「调用方忽略 err」也走不到放行分支——fail-closed 是
	// 结构性保证，不靠纪律。把 Status 写成 SafetyOK 会被那条闸当场打红。
	ClassifySafety(ctx context.Context, in TextInput) (SafetyDecision, error)

	// StreamChat 对话 token 流。err 只表示「这条流根本没开起来」（鉴权、参数、熔断打开）；
	// 流一旦开起来，终止信息一律用 ChatChunk.Finish 表达（DONE / ERROR / BLOCKED / DEGRADED），
	// 因为 SSE 侧没有别的通道能表达「流到一半断了」。
	//
	// 返回的 channel 保证最终关闭（所以 range 一定结束），且**最后一个 chunk 的 Finish 非空**。
	// 缓冲大小见 Options.StreamBuffer：背压就在这条 channel 上——消费方（HTTP handler）写得慢，
	// pump goroutine 会阻塞在发送上，再由 gRPC/HTTP2 的流控把压力顶回服务端。
	StreamChat(ctx context.Context, req ChatRequest) (<-chan ChatChunk, error)

	// Close 释放底层连接。幂等。
	Close() error
}

// EmbedRequest 的字段面逐一对应 closenai_ai_rpc.proto 的 EmbedRequest（那里逐字标了来源：
// backend/core/llm.py:259-263 的入参面 ＋ 三个必须由调用方显式带的归属字段）。
type EmbedRequest struct {
	Texts []string
	// Dimensions 是**指针**而不是 uint32：None 与 0 语义不同——None 走 provider 默认维度，
	// 而 dashscope 分支会自己塞 settings.embedding_dim（llm.py:281-284，config.py:86 默认 512，
	// 落库列 HALFVEC(512)，models/memory.py:36）。零值 0 会被服务端读成「要 0 维」。
	Dimensions *uint32
	Model      string
	UserID     string
	// Purpose 必须显式带：Python 侧缺省值由调用栈帧推导（llm.py:74
	// `purpose or _purpose_from_frame(depth=3)`），跨进程后栈帧是 RPC handler，
	// 推出来的 purpose 会变成 handler 名 ⇒ 留痕当场失真。
	Purpose string
	TraceID string
}

type EmbedResult struct {
	// Vectors[i] 对应 EmbedRequest.Texts[i]：顺序即契约（llm.py:291-293 按 index 排序后返回，
	// 注释逐字「按输入顺序返回向量」）。
	Vectors [][]float32
	// Dimensions 是服务端实报维度。⚠️ 语义是「**这批**的维度」，不是「某条向量的维度」：
	// proto 把它放在 response 层（EmbedResponse.dimensions = 3）而本包按条校验，
	// 所以一条不合格整批置 nil。R4 若给每条带维度，要改这里并同步改契约。
	Dimensions uint32
	Model      string

	// Degraded 为 true 表示这次没拿到向量但**调用方应当继续**（fail-soft 的载体）。
	// 此时 Vectors 必为 nil——这个不变量由本包自己守住，见 TestEmbedDegradedNeverCarriesVectors。
	Degraded bool
	Failure  FailureCode
	Attempts int
}

// TextInput 对应 proto 的 TextInput。空串/纯空白由**服务端**直接判安全
// （safety_service.py:362-363），Go 侧不许另判一套，否则「空文本」在两处结论不同。
type TextInput struct {
	Text     string
	Language string
	// Scope：chat|assertion，留空＝chat（与 safety_service.py:356 的形参默认值一致）。
	// 本包只透传、不解释——越界句式判定只在 scope=="assertion" 时才跑（:368）。
	Scope string
}

// SafetyDecision 是已解码的判定结果，可直接塞进 domain.Candidate.Safety。
type SafetyDecision struct {
	Safety domain.Safety
	// Reason：命中面 banned|overreach|crisis|model|""。这四个值今天只 log 不回传
	// （safety_service.py:366/:371/:391），是 R1 补进契约的字段，为的是跨进程后还能按面降级。
	Reason  string
	Matched []string
	// AdviceBlocked 是 §二 铁律开关：关系暴力类恒为 True（safety_service.py:317）。
	// 显式带出而不让消费方从 Categories 反推——反推等于把一条合规铁律写进两个语言各自的代码里。
	AdviceBlocked bool

	// Failure 非空表示这个判定**不是**服务端给的，而是本包给的（超时/连接失败/畸形）。
	// 此时 Safety.Status 恒为 domain.SafetyUnavailable。
	Failure  FailureCode
	Attempts int
}

// ChatRequest 对应 proto 的 StreamChatRequest。字段面实测只有这四个：
// backend/models/schemas.py:20-23 是 content + message_type，user_id / language 来自
// 鉴权与语言依赖（routers/chat.py:58-59）⇒ 跨进程后必须由 Go 显式带（handler 侧没有 HTTP 请求）。
// 刻意没有 conversation_id / history / personality 之类：会话历史与记忆上下文都在服务端就地取
// （chat.py:102 get_memory_context），配额与用户消息落库也在进流之前做完（:73/:78-80）。
type ChatRequest struct {
	UserID      string
	Content     string
	MessageType string
	Language    string
}

// Finish 是终止标记；空字符串表示「中间块」。值面与本包映射到 SSE 的事件名同源。
type Finish string

const (
	FinishChunk    Finish = ""
	FinishDone     Finish = "done"
	FinishError    Finish = "error"
	FinishBlocked  Finish = "blocked"
	FinishDegraded Finish = "degraded"
)

// ChatChunk 是已解码的一块流数据。
type ChatChunk struct {
	Content string
	Finish  Finish
	// ErrorCode：现网数字沿用 backend/core/errors.py:52 QUOTA_EXCEEDED=40301、:72 INTERNAL_ERROR=500。
	// HasErrorCode 而不是 *int：0 在这个面里没有合法含义，但「没带码」和「码是 0」要分得开。
	ErrorCode    int
	HasErrorCode bool
	ErrorMessage string
	// Memories 只在「记忆上下文」那一块上非空（chat.py:104 的 memory_context 事件，
	// 形状取 message_generator.py:398-404 的 {id, content, category}）。
	Memories []MemoryContext
	// Failure 非空 ⇒ 这个块是本包造的（降级模板块或流断裂块），不是服务端字节。
	Failure FailureCode
}

// MemoryContext 是**展示面**的三键。它不是 common.proto 的 MemoryRef
// （{memory_id, excerpt, created_at}，用于 /chat/history 的「记忆依据 ▾」），
// 也不是已删除的 MemoryRef（号位已进 reserved）。两套键面都固化在现网字节里，不合并。
type MemoryContext struct {
	ID       string
	Content  string
	Category string
}

// FailureCode 是稳定的原因码：给日志与指标用，调用方不许靠字符串匹配判行为
// （行为判据在 Kind() 上）。
type FailureCode string

const (
	FailureNone        FailureCode = ""
	FailureDisabled    FailureCode = "ai_disabled"       // 没配 URL 且没配禁用开关——安全态，不是错误
	FailureBreakerOpen FailureCode = "breaker_open"      // 熔断打开，本次没发出请求
	FailureDeadline    FailureCode = "deadline_exceeded" // 超时；D-8 要求它**不得**读成放行
	FailureTransport   FailureCode = "transport"         // 连接失败、服务端 5xx 一类
	FailureRateLimited FailureCode = "rate_limited"      // 429 类（配额）
	FailureMalformed   FailureCode = "malformed"         // 契约违规：形状或取值不对，重试不会变好
	FailureRejected    FailureCode = "rejected"          // 服务端业务拒绝（InvalidArgument 等）
	FailureUnavailable FailureCode = "service_unavailable"
)

// Kind 把原因码归成 V3 17 §8「Retry Classification」的两类
// （transient：AI provider timeout / dependency unavailable；permanent：malformed protobuf、
// invalid candidate、rejected claim type）。畸形归 permanent 就是 tasks.md §R2 验收项里
// 「畸形输出走 N3 reject 而非重试」那句的落点。
func (c FailureCode) Kind() FailureKind {
	switch c {
	case FailureDeadline, FailureTransport, FailureRateLimited, FailureBreakerOpen, FailureUnavailable, FailureDisabled:
		return FailureTransient
	default:
		return FailurePermanent
	}
}

type FailureKind int

const (
	// FailureTransient 是**可恢复**的：换一次调用就可能成功（超时、连接失败、429、熔断打开）。
	// 命名故意不说「重试」：StreamChat 从不重试，Embed 的重试是另一件事。
	FailureTransient FailureKind = iota
	// FailurePermanent 是**重试不会变好**的：契约违规与服务端业务拒绝（V3 17 §8 的 Permanent）。
	FailurePermanent
)
