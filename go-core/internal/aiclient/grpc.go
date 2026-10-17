package aiclient

import (
	"context"
	"fmt"
	"sync"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"

	closenaiv1 "github.com/jamesfeng2009/closenai-app/go-core/gen/closenai/v1"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// 四个默认值的出处（逐条实测，不是工程直觉）：
//
//	Embed 10s     ← 本仓出站调用的既有口径就是 10s：core/auth.py:122、services/auth_service.py:510、
//	                services/push/service.py:128/215/270/312 全部 timeout=10。
//	Safety 800ms  ← safety_service.py:212 `CHECK_TIMEOUT = 0.8`，**逐字同一个预算**。
//	                换进程不许把安全判定的延迟预算放大，否则「超时不得等于放行」会变成「慢到放弃判定」。
//	Stream 60s    ← vercel.json:5 `"maxDuration": 60`：现役运行时对单次函数请求的硬上限。
//	                Python 侧 provider 调用自己的读超时是 600s（openai._constants.DEFAULT_TIMEOUT
//	                read=600），但那条流在 Vercel 上活不过 60s，所以 60s 才是真实上限。
//	Retries 2     ← openai SDK 的 DEFAULT_MAX_RETRIES=2（openai._constants），初始退避 0.5s、
//	                上限 8s 也取自同处的 INITIAL_RETRY_DELAY / MAX_RETRY_DELAY。
//	                为什么照抄而不是自己定：今天一次 provider 失败在 Python 进程内**已经重试过 2 次**，
//	                Go 侧改成 0 会静默砍掉现网的有效重试次数；改成 5 则把上游限流放大。
const (
	defaultEmbedDeadline  = 10 * time.Second
	defaultSafetyDeadline = 800 * time.Millisecond
	defaultStreamDeadline = 60 * time.Second
	defaultRetries        = 2
	defaultBackoffInitial = 500 * time.Millisecond
	defaultBackoffMax     = 8 * time.Second
	defaultBreakerCount   = 5
	// defaultBreakerSeconds ＝ 对面 readiness 探测一个周期（deploy/k8s/40-py-api.yaml:38
	// periodSeconds: 5）：重启中的 Pod 最快也要这么久才可能被判定为 ready。
	defaultBreakerSeconds = 5
	// defaultStreamBuffer 是本地补齐：pump 最多领先慢消费者 16 块。
	// 它必须是个**小**数字，否则「背压」这件事在这条 channel 上根本不存在
	// （缓冲够大就永远不阻塞，HTTP2 流控也就永远不会把压力顶回服务端）。
	defaultStreamBuffer = 16
	// schemaVersion 恒 "1"：V3 10:151 逐字 "Every contract message has schema_version"，
	// 而 proto 侧这个字段是 string（15:44 是本仓唯一给它定类型的地方，代价与裁决见
	// contracts/proto/closenai_events.proto 文件头与 ownership.md §5）。
	schemaVersion = "1"
)

// Options 是本包的全部可调项。生产由 cmd/go-api 从 config.Config 组装，
// 于是 config 仍然是唯一读 env 的地方，本包不偷偷读环境变量。
type Options struct {
	// Target 形如 "py-api.closenai.svc.cluster.local:50051"。空串＝这条面**没接线**，
	// 所有调用按禁用降级，与 N8 灰度键全 0 时「部署了但一条都不自己答」是同一种安全态。
	Target string
	// Disabled 是显式关：运维留了地址但要停掉 AI 面时用这一格（沿 N9 的开关口径，
	// 拼错值一律拒绝启动，见 internal/config/config.go:123-130 的同一条纪律）。
	Disabled bool

	EmbedDeadline  time.Duration
	SafetyDeadline time.Duration
	StreamDeadline time.Duration

	// Retries 是**额外**尝试次数（1 次首发 + Retries 次重投）。
	// 0 ＝ 用默认（2，出处见上面的注释），-1 ＝ 显式关掉重试。
	// 为什么要负数当开关而不是 0：默认值本身就是 2，而 0 是「运维想一次都不重投」的合法诉求，
	// 让同一个数字既表示「没设」又表示「设成不重试」，就必然有一侧读错——
	// 这一版就是这么错的（0 直接落到 0 次，把 openai SDK 已经给了现网的 2 次重投悄悄砍没）。
	// 只作用于 unary（Embed / ClassifySafety）；StreamChat 恒不重试：半途的流不能重放，
	// 重放会让用户看到同一句话出现两次。
	Retries int

	BackoffInitial time.Duration
	BackoffMax     time.Duration

	BreakerThreshold int // 0 ＝ 用默认（5），-1 ＝ 关掉熔断器；判据同 Retries
	BreakerCooldown  time.Duration
	StreamBuffer     int

	// 测试注入点，生产留空。
	Now   func() time.Time
	Sleep func(ctx context.Context, d time.Duration) error
}

func (o Options) withDefaults() Options {
	if o.EmbedDeadline == 0 {
		o.EmbedDeadline = defaultEmbedDeadline
	}
	if o.SafetyDeadline == 0 {
		o.SafetyDeadline = defaultSafetyDeadline
	}
	if o.StreamDeadline == 0 {
		o.StreamDeadline = defaultStreamDeadline
	}
	if o.Retries == 0 {
		o.Retries = defaultRetries
	} else if o.Retries < 0 {
		o.Retries = 0
	}
	if o.BackoffInitial == 0 {
		o.BackoffInitial = defaultBackoffInitial
	}
	if o.BackoffMax == 0 {
		o.BackoffMax = defaultBackoffMax
	}
	if o.BreakerThreshold == 0 {
		o.BreakerThreshold = defaultBreakerCount
	}
	if o.BreakerCooldown == 0 {
		o.BreakerCooldown = time.Duration(defaultBreakerSeconds) * time.Second
	}
	if o.StreamBuffer <= 0 {
		o.StreamBuffer = defaultStreamBuffer
	}
	return o
}

// GRPC 是 Client 的 gRPC 实现。调用方只依赖接口，所以单测换成 Fake 不用动一行调用代码。
type GRPC struct {
	opts    Options
	breaker *breaker

	mu      sync.Mutex
	conn    *grpc.ClientConn
	api     closenaiv1.ClosenAIClient
	dialErr error
	now     func() time.Time
	sleep   func(ctx context.Context, d time.Duration) error
}

// New 不立刻建连（lazy）：Go 进程要在 AI 面缺席时照样起得来——
// 这是 N8 的现实约束（灰度键全 0 时 Go 只当代理），也是 V3 13 §8
// 「canonical user data remains available」在启动顺序上的含义：AI 面配错不许让整个 API 起不来。
func New(opts Options) *GRPC {
	opts = opts.withDefaults()
	now := opts.Now
	if now == nil {
		now = time.Now
	}
	sleep := opts.Sleep
	if sleep == nil {
		sleep = sleepCtx
	}
	return &GRPC{
		opts:    opts,
		breaker: newBreaker(opts.BreakerThreshold, int(opts.BreakerCooldown/time.Second), func() int64 { return now().Unix() }),
		now:     now,
		sleep:   sleep,
	}
}

// disabled 是「这一面没接线」的统一判据：显式关，或没给地址。
// 两者行为相同（不发请求、按各自的失败面降级）。
func (c *GRPC) disabled() (FailureCode, bool) {
	if c.opts.Disabled || c.opts.Target == "" {
		return FailureDisabled, true
	}
	return FailureNone, false
}

func (c *GRPC) client() (closenaiv1.ClosenAIClient, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.dialErr != nil {
		return nil, c.dialErr
	}
	if c.api != nil {
		return c.api, nil
	}
	conn, err := c.newConn()
	if err != nil {
		c.dialErr = err
		return nil, err
	}
	c.conn = conn
	c.api = closenaiv1.NewClosenAIClient(conn)
	return c.api, nil
}

// newConn 用明文。理由要说破：两个运行时在同一 k3s 集群的同一 namespace
// （deploy/k8s/00-namespace.yaml ＋ 30/40 两份 Deployment），本仓还没有 service mesh，
// 跨网络调用今天不存在。真跨出集群那天这一行必须换掉，且换的是**默认值**而不是加开关。
//
// 用 grpc.NewClient 而不是 grpc.Dial：后者自 1.6x 起标 deprecated，而 NewClient 明确不在启动期连接，
// 正好对上上面「AI 面配错不许让进程起不来」那条要求。
func (c *GRPC) newConn() (*grpc.ClientConn, error) {
	return grpc.NewClient(c.opts.Target, grpc.WithTransportCredentials(insecure.NewCredentials()))
}

func (c *GRPC) Close() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.conn == nil {
		return nil
	}
	err := c.conn.Close()
	c.conn = nil
	c.api = nil
	c.dialErr = nil
	return err
}

// countTransient 记一次对面故障。**调用方自己取消的不算**——
// 用户关掉页面会让每条流以 Canceled 结束，把它们计进连续失败就会
// 「晚高峰把 AI 面熔断掉」，那是把一个正常现象读成了一次事故。
func (c *GRPC) countTransient(ctx context.Context) {
	if ctx.Err() != nil {
		return
	}
	c.breaker.recordTransient()
}

// ---------------------------------------------------------------- Embed

func (c *GRPC) Embed(ctx context.Context, req EmbedRequest) (EmbedResult, error) {
	// 空 texts 是调用方的编程错误而不是「这次没向量」：返回 err 让它当场显形。
	// 今天两个调用点都不可能传空（memory_service.py:465 传 [text]、cron_service.py:1024 传非空 texts），
	// 所以这条分支是护栏，不是行为面。
	if len(req.Texts) == 0 {
		return EmbedResult{}, &Error{Op: "Embed", Code: codes.InvalidArgument,
			Kind: FailurePermanent, Cause: FailureRejected, Attempt: 1, Err: fmt.Errorf("texts 为空")}
	}
	if code, off := c.disabled(); off {
		// fail-soft（ownership.md §1 第一行「不得变硬失败」）：没接线也回 degraded 而不是错误，
		// 于是「记一条想法」这条路径在没有 AI 面的环境里照样成功，只少一个向量。
		return EmbedResult{Degraded: true, Failure: code}, nil
	}
	if !c.breaker.allow() {
		return EmbedResult{Degraded: true, Failure: FailureBreakerOpen}, nil
	}

	var out EmbedResult
	attempts, err := c.runUnary(ctx, "Embed", c.opts.EmbedDeadline, func(ctx context.Context) error {
		api, e := c.client()
		if e != nil {
			return e
		}
		resp, callErr := api.Embed(ctx, toProtoEmbedRequest(req))
		if callErr != nil {
			return callErr
		}
		decoded, why := decodeEmbedResponse(resp, len(req.Texts))
		if why != "" {
			return malformed("Embed", why)
		}
		out = decoded
		return nil
	})
	out.Attempts = attempts
	if err != nil {
		e := toCallError("Embed", err, attempts)
		if e.Kind == FailureTransient {
			c.countTransient(ctx)
		} else {
			c.breaker.recordSuccess()
		}
		// 这里是 Embed 与 ClassifySafety 的分叉点，也是这一包最容易写错的一格：
		// 同一次传输失败，对 Embed 是**降级**（nil 向量、调用方继续），
		// 对 ClassifySafety 是**拦下**（unavailable）。把任一侧写成另一侧的行为，
		// TestEmbedKeepsFailSoft / TestSafetyTimeoutIsNeverPass 各打红一次。
		return EmbedResult{Degraded: true, Failure: e.Cause, Attempts: attempts}, nil
	}
	c.breaker.recordSuccess()
	return out, nil
}

// runUnary 在**共享预算**内重试：deadline 是这一次逻辑调用的总预算，不是每次尝试重新开始。
// 这条口径照抄今天的实现——safety_service.py:386 把整个（含 SDK 内部最多 2 次重投的）llm.complete
// 包在 `asyncio.wait_for(..., timeout=0.8)` 里面。若每次尝试重新给 0.8s，
// Go 侧就比 Python 侧慢 Retries+1 倍才放弃，而那笔延迟是用户付的。
func (c *GRPC) runUnary(ctx context.Context, op string, deadline time.Duration, attempt func(context.Context) error) (int, error) {
	deadlineCtx, cancel := context.WithTimeout(ctx, deadline)
	defer cancel()

	backoff := c.opts.BackoffInitial
	for n := 1; ; n++ {
		err := attempt(deadlineCtx)
		if err == nil {
			return n, nil
		}
		e := toCallError(op, err, n)
		if e.Kind == FailurePermanent {
			return n, e // 畸形/业务拒绝：重试不会变好（V3 17 §8 的 Permanent ＋ 16:160 逐字 do not blindly retry）
		}
		if n > c.opts.Retries {
			return n, e
		}
		if err := c.sleep(deadlineCtx, backoff); err != nil {
			// 预算用完或调用方取消：如实报成超时，而不是报最后一次传输错误——
			// 否则日志里看不出到底是预算不够还是对面在挂，这两种的处置完全不同。
			return n, &Error{Op: op, Code: codes.DeadlineExceeded, Kind: FailureTransient,
				Cause: FailureDeadline, Attempt: n, Err: e}
		}
		backoff *= 2
		if backoff > c.opts.BackoffMax {
			backoff = c.opts.BackoffMax
		}
	}
}

// ---------------------------------------------------------------- ClassifySafety

func (c *GRPC) ClassifySafety(ctx context.Context, in TextInput) (SafetyDecision, error) {
	// 空文本的判定**留在服务端**（safety_service.py:362-363），Go 侧不短路，
	// 否则「空文本」在两处结论可能不同（一侧 True、一侧 unavailable）。
	if code, off := c.disabled(); off {
		return c.safetyFailure(code, 0), unavailableErr("ClassifySafety", code, 0)
	}
	if !c.breaker.allow() {
		return c.safetyFailure(FailureBreakerOpen, 0), unavailableErr("ClassifySafety", FailureBreakerOpen, 0)
	}

	var out SafetyDecision
	attempts, err := c.runUnary(ctx, "ClassifySafety", c.opts.SafetyDeadline, func(ctx context.Context) error {
		api, e := c.client()
		if e != nil {
			return e
		}
		resp, callErr := api.ClassifySafety(ctx, &closenaiv1.TextInput{
			SchemaVersion: schemaVersion,
			Text:          in.Text,
			Language:      in.Language,
			Scope:         in.Scope,
		})
		if callErr != nil {
			return callErr
		}
		decoded, why := decodeSafetyResult(resp)
		if why != "" {
			return malformed("ClassifySafety", why)
		}
		out = decoded
		return nil
	})
	out.Attempts = attempts
	if err != nil {
		e := toCallError("ClassifySafety", err, attempts)
		if e.Kind == FailureTransient {
			c.countTransient(ctx)
		} else {
			c.breaker.recordSuccess()
		}
		// D-8：这一行**只**可能产出 SafetyUnavailable。
		// 把它改成 SafetyOK 就是「超时被当放行」那条缺陷复活——两条闸会红：
		// 本包的 TestSafetyTimeoutIsNeverPass ＋ domain/gate.go:396-404 的判定闸。
		return c.safetyFailure(e.Cause, attempts), e
	}
	c.breaker.recordSuccess()
	return out, nil
}

// safetyFailure 造一个「判定拿不到」的结果。集中在这一处写，是为了让
// 「任何失败面都必须是 unavailable」这件事只有一个可能写错的地方。
func (c *GRPC) safetyFailure(code FailureCode, attempts int) SafetyDecision {
	return SafetyDecision{
		Safety:   domain.Safety{Status: domain.SafetyUnavailable},
		Reason:   "model",
		Failure:  code,
		Attempts: attempts,
	}
}

func unavailableErr(op string, code FailureCode, attempts int) *Error {
	return &Error{Op: op, Code: codes.Unavailable, Kind: FailureTransient, Cause: code,
		Attempt: attempts, Err: fmt.Errorf("判定不可用，按 fail-closed 拦下（D-8）")}
}

// ---------------------------------------------------------------- StreamChat

func (c *GRPC) StreamChat(ctx context.Context, req ChatRequest) (<-chan ChatChunk, error) {
	if code, off := c.disabled(); off {
		return c.degradedStream(ctx, req, code), nil
	}
	if !c.breaker.allow() {
		return c.degradedStream(ctx, req, FailureBreakerOpen), nil
	}
	// 流不重试（见 Options.Retries 的注释）。
	api, err := c.client()
	if err != nil {
		e := classify("StreamChat", err, 1)
		c.countTransient(ctx)
		return c.degradedStream(ctx, req, e.Cause), nil
	}
	streamCtx, cancel := context.WithTimeout(ctx, c.opts.StreamDeadline)
	client, err := api.StreamChat(streamCtx, &closenaiv1.StreamChatRequest{
		SchemaVersion: schemaVersion,
		UserId:        req.UserID,
		Content:       req.Content,
		MessageType:   req.MessageType,
		Language:      req.Language,
	})
	if err != nil {
		cancel()
		e := classify("StreamChat", err, 1)
		if e.Kind == FailureTransient {
			c.countTransient(ctx)
		}
		return c.degradedStream(ctx, req, e.Cause), nil
	}
	out := make(chan ChatChunk, c.opts.StreamBuffer)
	go c.pump(streamCtx, cancel, client, req, out)
	return out, nil
}

// pump 把 gRPC 服务端流搬到 channel 上，并保证四件事：
//  1. channel 一定关闭（调用方 range 不会挂死）；
//  2. 最后一个块的 Finish 一定非空；
//  3. 流在**没吐出任何内容**时断掉 ⇒ 补一块降级模板 + FinishDegraded
//     （对齐 message_generator.py:135-136 的 `except Exception: yield self._fallback_reply(...)`）；
//  4. 流在**已经吐过内容**之后断掉 ⇒ 只报 FinishError，不追加模板句。
//     用户已经看到半句话，接一句「今天过得怎么样？」比接一个错误更让人困惑，
//     但把模板句拼在半句话末尾会产出一条谁都没审过的文案——所以选择如实报错，
//     这一支与 chat.py:137-140 的 error 事件同形。
//
// 3 与 4 的分界就是「有没有收到过非空 content」，用 delivered 记。
func (c *GRPC) pump(streamCtx context.Context, cancel context.CancelFunc,
	client closenaiv1.ClosenAI_StreamChatClient, req ChatRequest, out chan<- ChatChunk) {
	defer cancel()
	defer close(out)

	delivered := false
	for {
		msg, err := client.Recv()
		if err != nil {
			e := classify("StreamChat", err, 1)
			c.reportStreamFailure(streamCtx, e)
			if !delivered {
				emitDegraded(req, out, e.Cause)
				return
			}
			out <- errorChunk(e.Cause)
			return
		}
		chunk, why := decodeChatChunk(msg)
		if why != "" {
			c.breaker.recordSuccess() // 对面没坏，是我们的校验没过（见 breaker.go）
			if !delivered {
				emitDegraded(req, out, FailureMalformed)
				return
			}
			out <- errorChunk(FailureMalformed)
			return
		}
		c.breaker.recordSuccess()
		if chunk.Content != "" {
			delivered = true
		}
		out <- chunk
		if chunk.Finish != FinishChunk {
			return // DONE / ERROR / BLOCKED / DEGRADED：流到此收尾，本包不加戏
		}
	}
}

func (c *GRPC) reportStreamFailure(ctx context.Context, e *Error) {
	if e.Kind == FailurePermanent {
		c.breaker.recordSuccess()
		return
	}
	c.countTransient(ctx)
}

func errorChunk(code FailureCode) ChatChunk {
	return ChatChunk{
		Finish: FinishError, ErrorCode: internalErrorCode, HasErrorCode: true,
		ErrorMessage: internalErrorMessage, Failure: code,
	}
}

// emitDegraded 出一块降级模板 + 一个 DEGRADED 终止块。
// SSE 侧看到的是 token + done（映射见 sse.go），所以已发布客户端不需要认识任何新事件类型。
func emitDegraded(req ChatRequest, out chan<- ChatChunk, code FailureCode) {
	text, ok := FallbackReply(req.Content, req.Language)
	if !ok {
		// 15 语言模板全空＝副本坏了。宁可如实报错，也不发空字符串给用户
		//（Python 同形：copy.t_list 兜底返回 []，`len(user_input) % 0` 当场 ZeroDivisionError
		// → chat.py:137 的 error 事件）。这条分支被 backend 侧的闸守成不可达，留着是护栏。
		out <- errorChunk(code)
		return
	}
	out <- ChatChunk{Content: text, Failure: code}
	out <- ChatChunk{Finish: FinishDegraded, Failure: code}
}

// degradedStream 给一条「已经决定降级」的流：不发 RPC，直接在已关闭的 channel 上给出两块。
// 用「关闭的 channel」而不是 goroutine，是因为这条路没有任何可等待的东西，
// 多起一个 goroutine 只会让调用方多一个竞态。
func (c *GRPC) degradedStream(ctx context.Context, req ChatRequest, code FailureCode) <-chan ChatChunk {
	out := make(chan ChatChunk, 2)
	emitDegraded(req, out, code)
	close(out)
	return out
}

// ---------------------------------------------------------------- 解码（proto → Go 值对象）

func toProtoEmbedRequest(req EmbedRequest) *closenaiv1.EmbedRequest {
	out := &closenaiv1.EmbedRequest{
		SchemaVersion: schemaVersion,
		Texts:         req.Texts,
		Model:         req.Model,
		UserId:        req.UserID,
		Purpose:       req.Purpose,
		TraceId:       req.TraceID,
	}
	if req.Dimensions != nil {
		d := *req.Dimensions
		out.Dimensions = &d
	}
	return out
}

// decodeEmbedResponse 校验的是「顺序即契约」这句注释能不能真的成立。
// 每条向量的维度都必须等于 response 声明的 dimensions：半途混进一条不同维度，
// _mean_embedding（cron_service.py:1027 按 len(vectors[0]) 取列均值）就会算出一个错均值，
// 而这种错法事后没有任何一行日志会提示。
func decodeEmbedResponse(resp *closenaiv1.EmbedResponse, want int) (EmbedResult, string) {
	if resp == nil {
		return EmbedResult{}, "响应为空"
	}
	if len(resp.Vectors) != want {
		return EmbedResult{}, fmt.Sprintf("向量条数 %d ≠ 请求条数 %d（顺序即契约，缺一条就没法对回原文）",
			len(resp.Vectors), want)
	}
	dim := resp.Dimensions
	out := make([][]float32, 0, len(resp.Vectors))
	for i, v := range resp.Vectors {
		if len(v.Values) == 0 {
			return EmbedResult{}, fmt.Sprintf("第 %d 条向量是空的", i)
		}
		if dim == 0 {
			dim = uint32(len(v.Values)) // 服务端没报维度时按首条推，下面仍逐条校验一致
		} else if uint32(len(v.Values)) != dim {
			return EmbedResult{}, fmt.Sprintf("第 %d 条向量 %d 维 ≠ 声明的 %d 维", i, len(v.Values), dim)
		}
		out = append(out, v.Values)
	}
	return EmbedResult{Vectors: out, Dimensions: dim, Model: resp.Model}, ""
}

func decodeSafetyResult(resp *closenaiv1.SafetyResult) (SafetyDecision, string) {
	if resp == nil || resp.Safety == nil {
		return SafetyDecision{}, "缺少 safety 字段：判定缺失不能被读成安全"
	}
	status, ok := toDomainSafetyStatus(resp.Safety.Status)
	if !ok {
		// 未知枚举值**不**走 proto3「容忍未知值」那条：V3 15 §8 的容忍指的是消费方不许崩，
		// 而这里崩不崩是小事，把没见过的状态读成 OK 才是事故。宁可 fail-closed。
		return SafetyDecision{}, fmt.Sprintf("未知的 SafetyStatus %v", resp.Safety.Status)
	}
	return SafetyDecision{
		Safety:        domain.Safety{Status: status, Categories: resp.Safety.Categories},
		Reason:        resp.Reason,
		Matched:       resp.Matched,
		AdviceBlocked: resp.AdviceBlocked,
	}, ""
}

func toDomainSafetyStatus(s closenaiv1.SafetyStatus) (domain.SafetyStatus, bool) {
	switch s {
	case closenaiv1.SafetyStatus_SAFETY_STATUS_OK:
		return domain.SafetyOK, true
	case closenaiv1.SafetyStatus_SAFETY_STATUS_CRISIS:
		return domain.SafetyCrisis, true
	case closenaiv1.SafetyStatus_SAFETY_STATUS_UNSAFE:
		return domain.SafetyUnsafe, true
	case closenaiv1.SafetyStatus_SAFETY_STATUS_UNAVAILABLE:
		return domain.SafetyUnavailable, true
	default:
		return "", false
	}
}

// decodeChatChunk 把 proto 块换成值对象，同时把「这个块能不能映射成一条现网 SSE 事件」
// 判掉：判不动的一律当契约违规（V3 17 §8 的 Permanent：malformed），不走重试。
//
// 载荷规则只有一条来自现网字节：done 事件不带载荷（chat.py:132 `_sse_event("done", {})`），
// 所以 DONE 块再带 content 就是两个真相源，必须红。
// memories 与 DONE **可以**同现——那是 R3 把 memory_context 与 done 合进一块的合法形态，
// 消费侧 SSELines 会拆成两条事件。
func decodeChatChunk(msg *closenaiv1.ChatChunk) (ChatChunk, string) {
	if msg == nil {
		return ChatChunk{}, "流里收到空块"
	}
	out := ChatChunk{
		ErrorMessage: msg.ErrorMessage,
	}
	if msg.ErrorCode != nil {
		out.ErrorCode = int(*msg.ErrorCode)
		out.HasErrorCode = true
	}
	for _, m := range msg.Memories {
		out.Memories = append(out.Memories, MemoryContext{ID: m.Id, Content: m.Content, Category: m.Category})
	}
	switch msg.Finish {
	case closenaiv1.ChatFinish_CHAT_FINISH_UNSPECIFIED:
		// 中间块：token 事件（content）或 memory_context 事件（memories），二者只占一个。
		// 同现是契约违规而不是「挑一个发」：SSE 侧这是两条独立事件，
		// proto 里没有字段能定它们的先后，猜一个顺序就是造第二个真相源。
		if msg.Content != "" && len(out.Memories) > 0 {
			return ChatChunk{}, "一个块里同时有 content 与 memories：SSE 侧这是两个事件，无法定序"
		}
		out.Content = msg.Content
		// 两者都空的中间块原样透传：Python 对空 chunk 也是照发 token 事件（chat.py:114-116
		// 对每个 chunk 无条件 yield），本包不自作主张丢掉。
	case closenaiv1.ChatFinish_CHAT_FINISH_DONE:
		out.Finish = FinishDone
		out.Content = msg.Content
		if out.Content != "" {
			return ChatChunk{}, "DONE 块带了 content"
		}
	case closenaiv1.ChatFinish_CHAT_FINISH_ERROR:
		out.Finish = FinishError
		out.Content = msg.Content
		if !out.HasErrorCode {
			// 现网两条 error 路径都带码（chat.py:86 配额 40301、:138 内部 500），
			// 没码的 error 块让客户端只能猜，按契约违规处理。
			return ChatChunk{}, "ERROR 块没带 error_code"
		}
	case closenaiv1.ChatFinish_CHAT_FINISH_BLOCKED:
		out.Finish = FinishBlocked
		out.Content = msg.Content
	case closenaiv1.ChatFinish_CHAT_FINISH_DEGRADED:
		out.Finish = FinishDegraded
		out.Content = msg.Content
	default:
		return ChatChunk{}, fmt.Sprintf("未知的 ChatFinish %v", msg.Finish)
	}
	// BLOCKED 带 content 是合法例外：它的语义就是「安全话术已下发，流被截断」
	// （safety_service.py:47），话术正文就在这个块上。
	return out, ""
}

// isTerminal 判「这块是不是流的收尾」。ChatFinish 的四个非空值都是收尾，
// 空值（FinishChunk）不是——所以这个判据与 proto 的枚举一一对应，不是一张名单。
func isTerminal(chunk ChatChunk) bool {
	return chunk.Finish != FinishChunk
}

// ---------------------------------------------------------------- 小工具

func statusOf(err error) codes.Code { return status.Code(err) }

// toCallError 取出 attempt() 已经包好的 *Error（malformed 走这条），否则按 gRPC 状态码分类。
func toCallError(op string, err error, attempt int) *Error {
	var e *Error
	if AsError(err, &e) {
		e.Attempt = attempt
		return e
	}
	return classify(op, err, attempt)
}

func sleepCtx(ctx context.Context, d time.Duration) error {
	if d <= 0 {
		return nil
	}
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}

func unixNow() int64 { return time.Now().Unix() }
