package aiclient

import (
	"context"
	"sync"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// Fake 是给调用方与单测用的 Client 实现。它存在的全部理由：
// R2 的验收项要求「Go 单测用 fake 覆盖超时/429/5xx/畸形输出四类」，
// 而这四类在真 server 上各造一次需要 Python 侧配合（那是 R3 的 grpc test harness）。
//
// 用法是**逐次脚本**而不是「永远返回错误」：一次调用给一个结果，
// 因为这一包最容易写错的地方正是「第 1 次超时、第 2 次成功」这种重试中途的语义
// （runUnary 的共享预算、attempts 计数、降级与拦截的分叉都只在中途成功时才看得见）。
type Fake struct {
	mu sync.Mutex

	// EmbedResults / SafetyResults / StreamResults 按调用次序给出结果；
	// 用尽之后重复最后一条，所以「三条脚本 + 第十次调用」不会 panic，
	// 也不会让测试写一长串 padding。
	EmbedResults  []EmbedStep
	SafetyResults []SafetyStep
	StreamResults []StreamStep

	// Counts 记录每个方法被调了几次（观测「重试有没有真的发生」的最后一环：
	// 只有 attempts 数字而没有调用计数，就分不清「一次调用重试三轮」和「调用方自己重试三轮」）。
	Counts map[string]int
}

// EmbedStep 一次 Embed 的结果。
type EmbedStep struct {
	Result EmbedResult
	Err    error
}

// SafetyStep 一次 ClassifySafety 的结果。
type SafetyStep struct {
	Decision SafetyDecision
	Err      error
}

// StreamStep 一次 StreamChat 的结果：Chunks 是要下发的块（Fake 同步全发再关闭），
// FailAfter 表示发完前 FailAfter 块之后让流「断掉」（>0 才有意义，配 Err 用）。
type StreamStep struct {
	Chunks    []ChatChunk
	Err       error
	FailAfter int
}

func NewFake() *Fake {
	return &Fake{Counts: map[string]int{}}
}

// 编译期确认三个实现都满足接口——加方法时这里第一个红。
var (
	_ Client = (*GRPC)(nil)
	_ Client = (*Fake)(nil)
)

func (f *Fake) count(op string) int {
	if f.Counts == nil {
		f.Counts = map[string]int{}
	}
	f.Counts[op]++
	return f.Counts[op]
}

func (f *Fake) Embed(_ context.Context, req EmbedRequest) (EmbedResult, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	n := f.count("Embed")
	step, ok := pick(f.EmbedResults, n)
	if !ok {
		// 没脚本＝返回一个「没向量但成功」的结果而不是 panic：
		// 调用方在自己的测试里忘配脚本时，看到空向量比看到 nil 指针更好查。
		return EmbedResult{Degraded: true, Failure: FailureNone, Attempts: 1}, nil
	}
	out := step.Result
	out.Attempts = max1(out.Attempts)
	if out.Degraded && out.Failure == FailureNone {
		out.Failure = FailureTransport
	}
	if out.Failure != FailureNone && out.Degraded && len(out.Vectors) != 0 {
		// 不变量：降级与向量不能同现（grpc.go 的 Embed 自己守，Fake 也必须守同一条，
		// 否则调用方「看 !Degraded 才用 Vectors」的写法在换实现时会静默变样）。
		out.Vectors = nil
	}
	return out, step.Err
}

func (f *Fake) ClassifySafety(_ context.Context, _ TextInput) (SafetyDecision, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	n := f.count("ClassifySafety")
	step, ok := pick(f.SafetyResults, n)
	if !ok {
		return SafetyDecision{Safety: unavailableSafety(), Failure: FailureNone}, nil
	}
	out := step.Decision
	out.Attempts = max1(out.Attempts)
	if step.Err != nil && out.Safety.Status == "" {
		// 与真实现同形：**任何失败面都必须写 unavailable**（D-8）。
		// Fake 若在这里让零值溜过去，调用方的测试就会以为「忘了填也算拦下」。
		out.Safety = unavailableSafety()
	}
	if out.Failure == FailureNone && step.Err != nil {
		out.Failure = FailureTransport
	}
	return out, step.Err
}

func (f *Fake) StreamChat(_ context.Context, _ ChatRequest) (<-chan ChatChunk, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	n := f.count("StreamChat")
	step, ok := pick(f.StreamResults, n)
	out := make(chan ChatChunk, len(step.Chunks)+1)
	if !ok {
		close(out)
		return out, nil
	}
	if step.Err != nil && step.FailAfter <= 0 {
		close(out)
		return out, step.Err
	}
	// 同步发完（不起 goroutine）：Fake 的价值是**确定性**，
	// 一旦这里也异步，调用方的测就会多一个与真实现无关的竞态。
	sent := 0
	for _, c := range step.Chunks {
		out <- c
		sent++
		if step.FailAfter > 0 && sent >= step.FailAfter {
			break
		}
	}
	if step.FailAfter > 0 && sent >= step.FailAfter {
		out <- errorChunk(FailureTransport)
	} else if len(step.Chunks) == 0 || !isTerminal(step.Chunks[len(step.Chunks)-1]) {
		// 保证「最后一个块一定带收尾」这条接口约定在 Fake 上也成立——
		// 调用方的 `for range` 循环靠这个判据决定要不要写 done 事件。
		out <- ChatChunk{Finish: FinishDone}
	}
	close(out)
	return out, nil
}

func (f *Fake) Close() error { return nil }

func pick[T any](steps []T, n int) (T, bool) {
	var zero T
	if len(steps) == 0 {
		return zero, false
	}
	if n <= len(steps) {
		return steps[n-1], true
	}
	return steps[len(steps)-1], true
}

func max1(v int) int {
	if v < 1 {
		return 1
	}
	return v
}

// unavailableSafety 与 grpc.go 的 safetyFailure 同值：Fake 也必须用同一个常量造失败面，
// 否则「Fake 说拦下了、真实现放过去了」这种分叉测不出来。
func unavailableSafety() domain.Safety {
	return domain.Safety{Status: domain.SafetyUnavailable}
}
