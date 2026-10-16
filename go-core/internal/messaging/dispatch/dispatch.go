// Package dispatch 是 outbox worker 的**进程面**（V3 17 §2 的职责清单、§11 的优雅停机）。
//
// 它只管三件事，一件都不多：
//  1. 读开关（kill switch）——键不给就**什么都不做**，这是 R4-④ 的关键验收；
//  2. 管一段连接的生命周期：拨号 → 声明拓扑 → 每轮认领前判断连接是否还活着（17 §2
//     「The worker must tolerate restarts」，而 amqp091-go v1.10.0 不会自动重连）；
//  3. 把 `outbox.Worker` 的计数攒成指标面。
//
// 投递本身、退避阶梯、租约一律不在这里做——那是 `internal/messaging/outbox` 与
// `domain.DeliveryPolicy` 已有的面，在这里重做就是第二套口径。
//
// 与 `outbox.Worker.Run` 的差别是刻意的：那个循环在 ctx 取消的那一刻就中断在途批次
// （outbox.go:160-177 实测），而 17 §11 要的是「先停止接受新工作、再把在途的做完」。
// 所以这里只调 `RunOnce`，循环自己写。
package dispatch

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/outbox"
)

// 开关面（全 env-only，默认关；凭据不进代码也不进日志，见 amqp.redactURL）。
const (
	EnvWorker        = "CLOSEN_OUTBOX_WORKER"
	EnvURL           = "CLOSEN_AMQP_URL"
	EnvPollSeconds   = "CLOSEN_OUTBOX_POLL_SECONDS"
	EnvConfirmMillis = "CLOSEN_AMQP_CONFIRM_TIMEOUT_MS"
	EnvDialSeconds   = "CLOSEN_AMQP_DIAL_TIMEOUT_SECONDS"
	EnvDrainSeconds  = "CLOSEN_OUTBOX_DRAIN_SECONDS"
)

// 默认值。ConfirmTimeout 与 Poll 的量级依据：
//   - 单条确认必须远小于租约（`domain/delivery_policy.go:14-16` 逐字「必须显著大于一次发布超时」），
//     否则一次慢发布会在租约到期后被第二个 worker 双投；
//   - 空轮 1s：投递延迟的直接下限，太短就是白烧 CPU，太长就是让 Python 干等。
const (
	defaultPoll = time.Second
	// 50 条 × 5s ＝ 250s 已经大于默认租约 120s，所以 5s 是**上限**不是预期耗时；
	// 真到那个量级时启动会打一条警告（见 Run 里的双投预算检查），而不是静默上线。
	defaultConfirmTimeout = 5 * time.Second
	defaultDialTimeout    = 10 * time.Second
	defaultDrainTimeout   = 30 * time.Second
)

// Switch 是「这个进程要不要跑 worker」与它的三个时间参数。
type Switch struct {
	Enabled        bool
	URL            string
	Poll           time.Duration
	ConfirmTimeout time.Duration
	DialTimeout    time.Duration
	DrainTimeout   time.Duration
}

// LoadSwitch 只读 env，不猜默认以外的配置来源。
func LoadSwitch(getenv func(string) string) Switch {
	return Switch{
		Enabled:        isOn(getenv(EnvWorker)),
		URL:            strings.TrimSpace(getenv(EnvURL)),
		Poll:           durationOr(getenv(EnvPollSeconds), defaultPoll, time.Second),
		ConfirmTimeout: durationOr(getenv(EnvConfirmMillis), defaultConfirmTimeout, time.Millisecond),
		DialTimeout:    durationOr(getenv(EnvDialSeconds), defaultDialTimeout, time.Second),
		DrainTimeout:   durationOr(getenv(EnvDrainSeconds), defaultDrainTimeout, time.Second),
	}
}

// Validate 拦住「开了关但没给地址」这类只会让 worker 空转的形态。
func (s Switch) Validate() error {
	if s.Poll <= 0 || s.ConfirmTimeout <= 0 || s.DialTimeout <= 0 || s.DrainTimeout <= 0 {
		return errors.New("dispatch: Poll/ConfirmTimeout/DialTimeout/DrainTimeout 必须全为正")
	}
	if s.Enabled && s.URL == "" {
		return fmt.Errorf("dispatch: %s 开了但 %s 为空 ⇒ worker 只会空转，拒绝启动", EnvWorker, EnvURL)
	}
	return nil
}

func isOn(v string) bool {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "1", "true", "on", "yes":
		return true
	default:
		return false
	}
}

// durationOr：非数字一律回默认值而不是 0——把拼错的放量键读成 0，
// 表现是「worker 起来了但每 0 秒轮询一次」，那是自伤式 DoS。
func durationOr(raw string, def, unit time.Duration) time.Duration {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return def
	}
	n, err := strconv.Atoi(raw)
	if err != nil || n <= 0 {
		return def
	}
	return time.Duration(n) * unit
}

// Session 是 worker 需要的一段 broker 会话（生产实现＝`amqp.Session`）。
//
// 只有这四个方法，是为了让进程接线的六条闸不需要真 broker 也能跑：
// 关→开、拨号失败、连接断后重拨、停机时把在途做完——四条都在 fake 上可判。
type Session interface {
	Declare() error
	Publisher(confirmTimeout time.Duration) (outbox.Publisher, error)
	Alive() bool
	Close() error
}

// DialFunc 建立会话。拨号本身带超时，所以签名里必须有 ctx。
type DialFunc func(ctx context.Context, url string, dialTimeout time.Duration) (Session, error)

// Stats 是指标面：一次进程生命周期内的累计计数。
//
// 命名对着 17 §2/§11 与 outbox.Result 来，**不自己发明第二套分类**：
// Accepted＝broker 确认后置 published，Rejected＝投递失败退回重试阶梯，
// DeadLettered＝到 MaxAttempts 转终态。
//
// ⚠️ `oldest_pending`（积压年龄）**不在这里**：它需要一条 outbox 的最小时间查询，
// 而池面（db/queries/canonical.sql）今天没有这个动作。按 R4-④ 的口径
// 「池面缺就登记为指标面欠账，而不是当场加 SQL 面」——欠账记在 tasks.md。
type Stats struct {
	Batches      int
	Claimed      int
	Accepted     int
	Rejected     int
	DeadLettered int
	Lost         int
	Unrecorded   int
	Dials        int
	Redials      int
	ClaimErrors  int
}

// Deps 是装配面。Log 是缝隙：本包不 import log，一个投递编排不该绑死日志实现
// （与 outbox.Worker.OnError 同一方向）。
type Deps struct {
	Dial   DialFunc
	Repo   outbox.Repository
	Policy domain.DeliveryPolicy
	Switch Switch
	Log    func(format string, args ...any)
}

// Runner 串起 Session 与 outbox.Worker。可并发读 Stats（HTTP 侧或日志协程），写只在 Run 里。
type Runner struct {
	deps Deps
	mu   sync.Mutex
	stat Stats

	// 当前会话与其 worker：只有 Run 这一个 goroutine 会碰，但 Close 路径跨方法，
	// 所以一并放锁内，省掉「谁 owns session」的口头约定。
	session Session
	worker  *outbox.Worker
}

func New(deps Deps) (*Runner, error) {
	if deps.Dial == nil {
		return nil, errors.New("dispatch: Dial 为 nil")
	}
	if deps.Repo == nil {
		return nil, errors.New("dispatch: Repo 为 nil")
	}
	if err := deps.Switch.Validate(); err != nil {
		return nil, err
	}
	if deps.Log == nil {
		deps.Log = func(string, ...any) {}
	}
	return &Runner{deps: deps}, nil
}

// Stats 取累计计数快照。
func (r *Runner) Stats() Stats {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.stat
}

// Run 常驻直到 ctx 取消。
//
// kill switch 关时**直接返回 nil**：不拨号、不声明、不认领，一行 DB 都不碰。
// 这就是「接线不许改变现网任何响应」的实现层证据——见 dispatch_test.go 头两条。
// （「开了但没给 URL」到不了这里：`New` 在 Validate 就拒了，见 EnvURL 那条错误。）
func (r *Runner) Run(ctx context.Context) error {
	sw := r.deps.Switch
	if !sw.Enabled {
		r.logf("outbox worker 未启动：%s 没给 1（kill switch 默认关）", EnvWorker)
		return nil
	}
	// 双投预算：一批的最坏确认时间不许超过租约，否则第二个 worker 会在第一批还没发完时
	// 把同一批重新认领（18 §5 的 SKIP LOCKED 只防「同时」，不防「租约过期」）。
	if worst := time.Duration(r.deps.Policy.BatchSize) * sw.ConfirmTimeout; worst > r.deps.Policy.OutboxLease() {
		r.logf("outbox: 警告 一批最坏确认时间 %v > 租约 %v ⇒ 慢 broker 时会被双投（幂等消费面兜重复）",
			worst, r.deps.Policy.OutboxLease())
	}
	defer r.closeSession()

	for {
		// 17 §11 步 1：先看闸，再认领。ctx 已取消就一批都不再开。
		if ctx.Err() != nil {
			return nil
		}
		worker, err := r.currentWorker(ctx)
		if err != nil {
			r.count(func(s *Stats) { s.ClaimErrors++ })
			r.logf("outbox: broker 会话没建起来：%v ⇒ %v 后重试", err, sw.Poll)
			if !sleep(ctx, sw.Poll) {
				return nil
			}
			continue
		}
		// 17 §11 步 2：在途这一批用**不受停机信号影响**的 ctx 做完，但要设上限——
		// 没有上限的话 pod 会在 terminationGracePeriod 到点后被 SIGKILL，
		// 那和完全不 drain 一样，还多出一段「看起来在收尾」的假象。
		drain, cancel := context.WithTimeout(context.WithoutCancel(ctx), sw.DrainTimeout)
		res, err := worker.RunOnce(drain)
		cancel()
		r.record(res, err)
		if res.Claimed == 0 && !sleep(ctx, sw.Poll) {
			return nil
		}
	}
}

// currentWorker 返回可投递的 worker；会话死了就关掉旧的、重拨、重声明。
func (r *Runner) currentWorker(ctx context.Context) (*outbox.Worker, error) {
	r.mu.Lock()
	alive := r.session != nil && r.session.Alive() && r.worker != nil
	r.mu.Unlock()
	if alive {
		return r.worker, nil
	}
	if r.session != nil {
		// 走到这儿＝上一条会话已判定不可用（或从来没有）。关掉再开一条，
		// 别让一个死连接占着「有 session 所以不重拨」的位置。
		r.closeSession()
		r.count(func(s *Stats) { s.Redials++ })
	}
	dialCtx, cancel := context.WithTimeout(ctx, r.deps.Switch.DialTimeout)
	defer cancel()
	sess, err := r.deps.Dial(dialCtx, r.deps.Switch.URL, r.deps.Switch.DialTimeout)
	if err != nil {
		return nil, err
	}
	r.count(func(s *Stats) { s.Dials++ })
	if err := sess.Declare(); err != nil {
		_ = sess.Close()
		return nil, fmt.Errorf("dispatch: declare topology: %w", err)
	}
	pub, err := sess.Publisher(r.deps.Switch.ConfirmTimeout)
	if err != nil {
		_ = sess.Close()
		return nil, err
	}
	w := outbox.NewWorker(r.deps.Repo, pub, r.deps.Policy)
	w.OnError = func(claimID, stage string, err error) {
		r.logf("outbox: 写回 %s 失败（%s）：%v ⇒ 这一行会在租约到期后重投", claimID, stage, err)
	}
	r.mu.Lock()
	r.session, r.worker = sess, w
	r.mu.Unlock()
	return w, nil
}

// record 把一批的计数并进指标面，并在**真有活儿时**打一条日志。
// 空轮不打：一个 1s 轮询的 worker 一天能写 8 万行没人看的日志。
func (r *Runner) record(res outbox.Result, err error) {
	if err != nil {
		r.count(func(s *Stats) { s.ClaimErrors++ })
		r.logf("outbox: 认领失败：%v ⇒ 下一轮再来（库抖动是可恢复的，outbox 仍是权威）", err)
	}
	r.count(func(s *Stats) {
		s.Batches++
		s.Claimed += res.Claimed
		s.Accepted += res.Published
		s.Rejected += res.Retried
		s.DeadLettered += res.DeadLetted
		s.Lost += res.Lost
		s.Unrecorded += res.Unrecorded
	})
	if res.Claimed > 0 {
		snap := r.Stats()
		r.logf("outbox: 认领 %d 条 ⇒ accepted=%d rejected=%d dlq=%d lost=%d unrecorded=%d（累计 accepted=%d dlq=%d）",
			res.Claimed, res.Published, res.Retried, res.DeadLetted, res.Lost, res.Unrecorded,
			snap.Accepted, snap.DeadLettered)
	}
}

func (r *Runner) count(f func(*Stats)) {
	r.mu.Lock()
	f(&r.stat)
	r.mu.Unlock()
}

func (r *Runner) closeSession() {
	r.mu.Lock()
	sess := r.session
	r.session, r.worker = nil, nil
	r.mu.Unlock()
	if sess != nil {
		if err := sess.Close(); err != nil {
			r.logf("outbox: 关会话：%v", err)
		}
	}
}

func (r *Runner) logf(format string, args ...any) { r.deps.Log(format, args...) }

// sleep 回 false＝ctx 在这期间结束了（调用方据此退出循环）。
func sleep(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}
