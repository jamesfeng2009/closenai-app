package aiclient

import "sync"

// breaker 是一个「连续失败计数 + 冷却窗口」的开关，不是通用熔断器框架。
//
// 为什么这一包要有它：V3 13 §8 在「AI is unavailable」那一节逐条要求
// 「user-facing product must degrade gracefully」，而 Go 是用户请求路径上的那一侧——
// Python 滚动重启期间，每条用户消息都去烧满一个 deadline 才降级，
// 产品体感就是「整个对话功能卡住 0.8s×N」，而不是「暂时用模板回一句」。
// 所以这里要做的不是「保护下游」（gRPC 自己会返回失败），而是**给调用方省掉这段等待**。
//
// 两个数都进 config 可调：
//
//	· threshold 默认 5 次连续失败：本仓没有先例数字，属**本地补齐**，
//	  判据是「一次 readiness 周期（40-py-api.yaml:38 periodSeconds: 5）内失败的次数」；
//	· cooldown 默认 5s ＝ 对面 readiness 探测的一个周期，之后放一次试探（half-open）。
//
// 刻意只统计 transient 类失败：malformed/rejected 是 permanent，对面没坏，
// 让一条字段误解把整条 AI 面打开熔断是更严重的故障（见 grpc.go 里 record 的调用点）。
type breaker struct {
	mu        sync.Mutex
	threshold int
	cooldown  func() int64 // 秒；写成函数是为了测试里能换时钟
	failures  int
	openUntil int64
	// probing 为 true 表示当前是 half-open：只放一次试探，其余请求仍旧直接降级。
	probing bool
	// now 注入时钟（Unix 秒）。生产用真实时间。
	now func() int64
}

func newBreaker(threshold int, cooldownSeconds int, now func() int64) *breaker {
	if now == nil {
		now = unixNow
	}
	return &breaker{threshold: threshold, cooldown: func() int64 { return int64(cooldownSeconds) }, now: now}
}

// allow 返回这次是否允许发出请求。打开期间 false ⇒ 调用方按各自失败面降级
// （Embed→degraded，ClassifySafety→unavailable，StreamChat→模板）。
func (b *breaker) allow() bool {
	if b == nil || b.threshold <= 0 {
		return true // threshold<=0 ＝ 关掉熔断器（运维 kill switch 的一种，不是未初始化）
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	now := b.now()
	if b.failures < b.threshold {
		return true
	}
	if now >= b.openUntil {
		if b.probing {
			return false // 上一次试探还没回来，不放第二次
		}
		b.probing = true
		return true
	}
	return false
}

func (b *breaker) recordTransient() {
	if b == nil || b.threshold <= 0 {
		return
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	wasProbing := b.probing
	b.probing = false
	if wasProbing {
		// 试探又失败：重新打开，计数不必再涨（否则每次冷却都多算一次，窗口越拖越长）。
		b.openUntil = b.now() + b.cooldown()
		return
	}
	b.failures++
	if b.failures >= b.threshold {
		b.openUntil = b.now() + b.cooldown()
	}
}

func (b *breaker) recordSuccess() {
	if b == nil {
		return
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	b.failures = 0
	b.probing = false
	b.openUntil = 0
}

// state 是给测试与观测用的快照：打开时返回 (true, 还剩几秒)。
func (b *breaker) state() (bool, int64) {
	if b == nil || b.threshold <= 0 {
		return false, 0
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.failures < b.threshold {
		return false, 0
	}
	left := b.openUntil - b.now()
	if left < 0 {
		left = 0
	}
	return true, left
}
