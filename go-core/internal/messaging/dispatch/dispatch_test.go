// 进程接线的闸（R4-④）。
//
// 这个文件证明的六件事，每一件都对应一种「接线把现网搞坏」的形态：
//  1. kill switch 关 ⇒ 不拨号、不声明、不认领（灰度 0 时进程行为与今天逐字一致）；
//  2. 「开了但没给 broker 地址」在装配期就拒，而不是起一个空转的 goroutine；
//  3. broker 起不来时 worker **不拖死 API 进程**：重试、不退出；
//  4. 连接断了会重拨＋重声明（17 §2「The worker must tolerate restarts」，
//     而 amqp091-go v1.10.0 不自动重连）；
//  5. 停机时先停止认领、把在途那一批做完（17 §11 步 1/2），会话只关一次；
//  6. 指标面的分类不错位：broker 确认才算 accepted，失败进 rejected，
//     到上限才进 dlq——把失败记成 accepted 就是假账。
//
// 全程无 broker、无真库：Session/Dial/Repository 三个缝都是本地 fake。
package dispatch

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/messaging/outbox"
)

// ── fake ─────────────────────────────────────────────────

type fakeSession struct {
	mu        sync.Mutex
	alive     bool
	declares  int
	closes    int
	pubCalls  int
	pubErr    error // 让发布回错，验「失败不算 accepted」
	ctxErrs   []error
	deadlines []time.Time // 在途批次是否真的拿到一个**有上限**的收尾预算
	onPublish func()      // 每条发布前回调（保留把手，个别用例要用）
}

func (f *fakeSession) Declare() error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.declares++
	f.alive = true
	return nil
}

func (f *fakeSession) Publisher(time.Duration) (outbox.Publisher, error) {
	return fakePublisher{s: f}, nil
}

func (f *fakeSession) Alive() bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.alive
}

func (f *fakeSession) Close() error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.closes++
	f.alive = false
	return nil
}

func (f *fakeSession) set(boolFn func(*fakeSession)) {
	f.mu.Lock()
	defer f.mu.Unlock()
	boolFn(f)
}

type fakePublisher struct{ s *fakeSession }

func (p fakePublisher) Publish(ctx context.Context, _ outbox.Envelope) error {
	p.s.mu.Lock()
	p.s.pubCalls++
	p.s.ctxErrs = append(p.s.ctxErrs, ctx.Err())
	if d, ok := ctx.Deadline(); ok {
		p.s.deadlines = append(p.s.deadlines, d)
	}
	hook, err := p.s.onPublish, p.s.pubErr
	p.s.mu.Unlock()
	if hook != nil {
		hook()
	}
	return err
}

type fakeRepo struct {
	mu sync.Mutex
	// batches 依次给出每轮认领到的行；用尽后一直给空批。
	batches [][]domain.OutboxClaim
	claims  int
	onClaim func() // 认领**当时**回调（停机用例在这里取消父 ctx）

	published []string
	retried   []string
	dlqd      []string
}

func (f *fakeRepo) ClaimOutboxBatch(_ context.Context, _, _ int) ([]domain.OutboxClaim, error) {
	f.mu.Lock()
	f.claims++
	hook := f.onClaim
	var batch []domain.OutboxClaim
	if len(f.batches) > 0 {
		batch, f.batches = f.batches[0], f.batches[1:]
	}
	f.mu.Unlock()
	if hook != nil {
		hook()
	}
	return batch, nil
}

func (f *fakeRepo) MarkOutboxPublished(_ context.Context, id string) (bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.published = append(f.published, id)
	return true, nil
}

func (f *fakeRepo) MarkOutboxRetry(_ context.Context, id string, _ time.Duration, _ string) (bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.retried = append(f.retried, id)
	return true, nil
}

func (f *fakeRepo) MarkOutboxDeadLettered(_ context.Context, id, _ string) (bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.dlqd = append(f.dlqd, id)
	return true, nil
}

// errRepo 只让认领失败（库抖动属可恢复，不许终止进程）。
type errRepo struct{ fakeRepo }

func (e *errRepo) ClaimOutboxBatch(_ context.Context, _, _ int) ([]domain.OutboxClaim, error) {
	e.mu.Lock()
	e.claims++
	e.mu.Unlock()
	return nil, errors.New("fake: 库在抖")
}

func claim(id string) domain.OutboxClaim {
	return domain.OutboxClaim{ID: id, MessageID: id, MessageType: "ai_pattern_requested",
		SchemaVersion: 1, RoutingKey: domain.AITaskPattern.RoutingKey(), UserID: "u-1",
		Payload: `{"k":1}`, AttemptCount: 1}
}

// 带锁的读法：Run 在自己的 goroutine 里写这些计数，测试这边并发读——
// 不加锁的话 `-race` 先把 fake 自己报红，用例断言什么都还没判。
func (f *fakeSession) declaresCount() int { f.mu.Lock(); defer f.mu.Unlock(); return f.declares }
func (f *fakeSession) closesCount() int   { f.mu.Lock(); defer f.mu.Unlock(); return f.closes }
func (f *fakeSession) pubCallsCount() int { f.mu.Lock(); defer f.mu.Unlock(); return f.pubCalls }
func (f *fakeSession) declaresCountIs(n int) func() bool {
	return func() bool { return f.declaresCount() == n }
}

func (f *fakeRepo) claimsCount() int    { f.mu.Lock(); defer f.mu.Unlock(); return f.claims }
func (f *fakeRepo) retriedCount() int   { f.mu.Lock(); defer f.mu.Unlock(); return len(f.retried) }
func (f *fakeRepo) publishedCount() int { f.mu.Lock(); defer f.mu.Unlock(); return len(f.published) }
func (f *fakeRepo) retriedCountIs(n int) func() bool {
	return func() bool { return f.retriedCount() == n }
}

// ── harness ──────────────────────────────────────────────

type harness struct {
	sess   *fakeSession
	sw     Switch
	policy domain.DeliveryPolicy

	runner *Runner
	dials  int
	logs   []string
	mu     sync.Mutex

	ctx    context.Context
	cancel context.CancelFunc
	done   chan error
}

// newHarness 先把 fake 与 runner 装好但**不起循环**，
// 这样用例可以在 Run 起来之前就把 onClaim 这类把手接到 cancel 上。
func newHarness(t *testing.T, sw Switch, repo outbox.Repository, sess *fakeSession,
	dialErr error, logf func(string, ...any)) *harness {
	t.Helper()
	h := &harness{sess: sess, sw: sw, done: make(chan error, 1), policy: domain.DefaultDeliveryPolicy()}
	dial := func(context.Context, string, time.Duration) (Session, error) {
		h.mu.Lock()
		h.dials++
		h.mu.Unlock()
		if dialErr != nil {
			return nil, dialErr
		}
		if sess == nil {
			return nil, errors.New("fake: 没给 session")
		}
		return sess, nil
	}
	if logf == nil {
		logf = func(format string, args ...any) {
			h.mu.Lock()
			defer h.mu.Unlock()
			h.logs = append(h.logs, fmt.Sprintf(format, args...))
		}
	}
	r, err := New(Deps{Dial: dial, Repo: repo, Policy: h.policy, Switch: sw, Log: logf})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	h.runner = r
	h.ctx, h.cancel = context.WithCancel(context.Background())
	t.Cleanup(func() {
		if h.done == nil {
			return
		}
		h.cancel()
		select {
		case err := <-h.done:
			if err != nil {
				t.Errorf("Run 回了错（停机路径应当回 nil）：%v", err)
			}
		case <-time.After(5 * time.Second):
			t.Error("Run 在 ctx 取消后 5s 还没退出")
		}
	})
	return h
}

// run 起循环，用的就是 harness 自己那条可取消 ctx——
// 停机用例要在批次中途取消时，调的就是同一个 h.cancel()。
func (h *harness) run() {
	h.done = make(chan error, 1)
	go func() { h.done <- h.runner.Run(h.ctx) }()
}

func (h *harness) waitFor(t *testing.T, cond func() bool, msg string) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatal(msg)
		}
		time.Sleep(5 * time.Millisecond)
	}
}

func (h *harness) logged() string {
	h.mu.Lock()
	defer h.mu.Unlock()
	return strings.Join(h.logs, "\n")
}

// dialsCount：Run 的 goroutine 在拨号闭包里写 h.dials，测试这边并发读，
// 不加锁就是 -race 报的第一条（假账没判成，先把 fake 自己判红）。
func (h *harness) dialsCount() int {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.dials
}

func onSwitch() Switch {
	return Switch{Enabled: true, URL: "amqp://127.0.0.1:5672/",
		Poll: 10 * time.Millisecond, ConfirmTimeout: 5 * time.Second,
		DialTimeout: time.Second, DrainTimeout: 5 * time.Second}
}

// swDrainCeiling＝DrainTimeout(5s) 加轮询与调度抖动的余量：
// 用它判「收尾预算确实设了上限」，而不是拿一个恰好相等的值去赌时钟精度。
const swDrainCeiling = 7 * time.Second

// ── 1/2：开关面 ───────────────────────────────────────────

// TestKillSwitchOffTouchesNothing 是 M4 的靶子：把开关摘掉（Run 不再判 Enabled）就红。
func TestKillSwitchOffTouchesNothing(t *testing.T) {
	repo, sess := &fakeRepo{}, &fakeSession{}
	sw := onSwitch()
	sw.Enabled = false
	h := newHarness(t, sw, repo, sess, nil, nil)
	h.run()
	// 给它几个轮询周期：任何一次越界都会在计数上留下痕迹。
	time.Sleep(80 * time.Millisecond)

	if h.dialsCount() != 0 {
		t.Errorf("开关关着却拨了 %d 次号", h.dialsCount())
	}
	if sess.declaresCount() != 0 {
		t.Errorf("开关关着却声明了 %d 次拓扑（声明会动 broker 上的队列参数）", sess.declaresCount())
	}
	if repo.claimsCount() != 0 {
		t.Errorf("开关关着却认领了 %d 批（这会和别的投递面抢行）", repo.claimsCount())
	}
	h.cancel()
	if err := <-h.done; err != nil {
		t.Errorf("Run=%v，期望 nil", err)
	}
	h.done = nil // 退出已确认，Cleanup 不必再等
	if !strings.Contains(h.logged(), "未启动") {
		t.Errorf("关着的时候不说一句，运维只能靠猜：%q", h.logged())
	}
}

// 默认 env（谁都不给）⇒ 关。这条是「灰度 0 时进程行为与今天一致」的配置侧证据。
func TestLoadSwitchDefaultsToOff(t *testing.T) {
	sw := LoadSwitch(func(string) string { return "" })
	if sw.Enabled {
		t.Error("默认必须是关：给一个没人要的 worker 等于给现网加一条未测的写路径")
	}
	if sw.URL != "" {
		t.Errorf("默认 URL=%q，应为空", sw.URL)
	}
	if err := sw.Validate(); err != nil {
		t.Errorf("默认开关组合不该被 Validate 拒：%v", err)
	}
}

func TestLoadSwitchReadsEnv(t *testing.T) {
	for _, raw := range []string{"1", "on", "TRUE", "yes"} {
		t.Setenv(EnvWorker, raw)
		if !LoadSwitch(os.Getenv).Enabled {
			t.Errorf("%s=%q 应认成开", EnvWorker, raw)
		}
	}
	t.Setenv(EnvWorker, "0")
	if LoadSwitch(os.Getenv).Enabled {
		t.Error("0 应当是关")
	}
	// 拼错的值读成 0 就是「每 0 秒轮询一次」，所以非数字一律回默认。
	t.Setenv(EnvWorker, "1")
	t.Setenv(EnvURL, "amqp://127.0.0.1:5672")
	t.Setenv(EnvPollSeconds, "abc")
	t.Setenv(EnvDialSeconds, "0")
	t.Setenv(EnvConfirmMillis, "-1")
	sw := LoadSwitch(os.Getenv)
	if sw.Poll != defaultPoll || sw.DialTimeout != defaultDialTimeout ||
		sw.ConfirmTimeout != defaultConfirmTimeout {
		t.Errorf("非法值没回默认：poll=%v dial=%v confirm=%v",
			sw.Poll, sw.DialTimeout, sw.ConfirmTimeout)
	}
	t.Setenv(EnvPollSeconds, "3")
	if got := LoadSwitch(os.Getenv).Poll; got != 3*time.Second {
		t.Errorf("poll=%v，期望 3s", got)
	}
}

// TestEnabledWithoutURLFailsAtAssembly：开了关没给地址 ⇒ 装配期就拒。
func TestEnabledWithoutURLFailsAtAssembly(t *testing.T) {
	sw := onSwitch()
	sw.URL = ""
	_, err := New(Deps{Dial: func(context.Context, string, time.Duration) (Session, error) {
		return &fakeSession{}, nil
	}, Repo: &fakeRepo{}, Policy: domain.DefaultDeliveryPolicy(), Switch: sw})
	if err == nil || !strings.Contains(err.Error(), EnvURL) {
		t.Fatalf("错误=%v，期望点名 %s", err, EnvURL)
	}
}

func TestNewRequiresCollaborators(t *testing.T) {
	sw := onSwitch()
	if _, err := New(Deps{Repo: &fakeRepo{}, Switch: sw}); err == nil {
		t.Error("Dial 为 nil 应拒")
	}
	if _, err := New(Deps{Dial: func(context.Context, string, time.Duration) (Session, error) {
		return &fakeSession{}, nil
	}, Switch: sw}); err == nil {
		t.Error("Repo 为 nil 应拒")
	}
	bad := onSwitch()
	bad.Poll = 0
	if _, err := New(Deps{Dial: func(context.Context, string, time.Duration) (Session, error) {
		return &fakeSession{}, nil
	}, Repo: &fakeRepo{}, Switch: bad}); err == nil {
		t.Error("Poll=0 应被 Validate 拒（0 秒轮询是自我 DoS）")
	}
	noDrain := onSwitch()
	noDrain.DrainTimeout = 0
	if err := noDrain.Validate(); err == nil {
		t.Error("DrainTimeout=0 应被拒：那等于「无限等收尾」")
	}
}

// ── 3/4：拨号与重拨 ───────────────────────────────────────

// TestBrokerDownDoesNotStopTheLoop：拨号一直失败 ⇒ 不退出、不认领、按 poll 重试。
// 反过来（起不来就返回 error）会让 go-api 因 broker 抖动而整个下线，
// 那是把「投递面没通」放大成「读面也没了」。
func TestBrokerDownDoesNotStopTheLoop(t *testing.T) {
	repo := &fakeRepo{}
	h := newHarness(t, onSwitch(), repo, &fakeSession{}, errors.New("connection refused"), nil)
	h.run()
	h.waitFor(t, func() bool { return h.dialsCount() >= 2 }, "拨号失败后没重试")
	if repo.claimsCount() != 0 {
		t.Errorf("会话没建起来却认领了 %d 批", repo.claimsCount())
	}
	if !strings.Contains(h.logged(), "会话没建起来") {
		t.Errorf("拨号失败没留日志，进程看起来在跑其实什么都没做：%q", h.logged())
	}
	h.cancel()
	if err := <-h.done; err != nil {
		t.Errorf("Run=%v，期望 nil（broker 不可用不是进程错误）", err)
	}
	h.done = nil
}

// TestDeadSessionIsRedialledAndRedeclared：连接被判定死掉后必须重拨＋重声明。
func TestDeadSessionIsRedialledAndRedeclared(t *testing.T) {
	sess := &fakeSession{}
	h := newHarness(t, onSwitch(), &fakeRepo{}, sess, nil, nil)
	h.run()
	h.waitFor(t, sess.declaresCountIs(1), "第一轮没声明拓扑")

	sess.set(func(f *fakeSession) { f.alive = false }) // 模拟 broker 侧断连
	h.waitFor(t, sess.declaresCountIs(2), "连接死后没重声明")
	st := h.runner.Stats()
	if st.Redials < 1 {
		t.Errorf("Redials=%d，期望 ≥1", st.Redials)
	}
	if st.Dials < 2 {
		t.Errorf("Dials=%d，期望 ≥2（重拨要真重拨，不是复用死会话）", st.Dials)
	}
	h.cancel()
	if err := <-h.done; err != nil {
		t.Errorf("Run=%v", err)
	}
	h.done = nil
}

// ── 5：优雅停机 ───────────────────────────────────────────

// TestShutdownFinishesInFlightBatch 钉 17 §11 步 1/2/4/5：
// 在途那一批要发完（发布手上的 ctx 不许已被取消）、之后一批都不再认领、会话只关一次。
//
// 取消点故意放在**认领当场**：批次已经领到手、还没发布，此刻停机信号到来，
// 于是「先停止接受新工作、再把在途做完」这先后两条能被同一批数据同时判出来。
func TestShutdownFinishesInFlightBatch(t *testing.T) {
	sess := &fakeSession{}
	repo := &fakeRepo{batches: [][]domain.OutboxClaim{
		{claim("m-1"), claim("m-2")},
		{claim("m-3")}, // 停机之后**不该**被认领
	}}
	sw := onSwitch()
	h := newHarness(t, sw, repo, sess, nil, nil)
	repo.onClaim = func() {
		// 只取消一次：第一批领到手的同一瞬间收到停机信号。
		if repo.claimsCount() == 1 {
			h.cancel()
		}
	}
	h.run()

	// pubCallsCount 自己带锁，这里不许再套一层：fakeSession 的 mu 不可重入，
	// 外层先 Lock 会让这条 waitFor 自己死锁（-race 之前先撞出来的就是它）。
	h.waitFor(t, func() bool { return sess.pubCallsCount() == 2 }, "停机时在途的 2 条没发完")
	<-h.done
	sess.mu.Lock()
	ctxErrs := append([]error{}, sess.ctxErrs...)
	sess.mu.Unlock()
	for i, err := range ctxErrs {
		if err != nil {
			t.Errorf("第 %d 条发布手上的 ctx 已取消（%v）⇒ 在途批次被停机打断", i+1, err)
		}
	}
	if repo.claimsCount() != 1 {
		t.Errorf("认领了 %d 批，期望 1 ⇒ 停机后还在接受新工作", repo.claimsCount())
	}
	if sess.closesCount() != 1 {
		t.Errorf("会话关了 %d 次，期望恰好 1 次（17 §11 步 4/5）", sess.closesCount())
	}
	st := h.runner.Stats()
	if st.Accepted != 2 || st.Rejected != 0 {
		t.Errorf("计数错位：accepted=%d rejected=%d，期望 2/0", st.Accepted, st.Rejected)
	}
	// 收尾预算必须有上限：没有上限时 pod 会在 terminationGracePeriod 到点被 SIGKILL，
	// 那和完全不 drain 一样，只是多出一段「看起来在收尾」的假象。
	sess.mu.Lock()
	deadlines := append([]time.Time{}, sess.deadlines...)
	sess.mu.Unlock()
	if len(deadlines) != 2 {
		t.Fatalf("在途批次里有 %d 条拿到收尾预算，期望 2", len(deadlines))
	}
	for i, d := range deadlines {
		if !d.After(time.Now()) {
			t.Errorf("第 %d 条的收尾预算已过期：%v", i+1, d)
		}
		if d.After(time.Now().Add(swDrainCeiling)) {
			t.Errorf("第 %d 条的收尾预算 %v 超出 DrainTimeout(%v) 的量级 ⇒ drain 没设上限",
				i+1, d, sw.DrainTimeout)
		}
	}
	h.done = nil
}

// ── 6：指标面的分类 ───────────────────────────────────────

func TestNackCountsAsRejectedNotAccepted(t *testing.T) {
	sess := &fakeSession{pubErr: errors.New("broker nack")}
	repo := &fakeRepo{batches: [][]domain.OutboxClaim{{claim("m-9")}}}
	h := newHarness(t, onSwitch(), repo, sess, nil, nil)
	h.run()
	h.waitFor(t, repo.retriedCountIs(1), "发布失败没走重试阶梯")
	st := h.runner.Stats()
	if st.Accepted != 0 {
		t.Errorf("accepted=%d ⇒ 失败被记成成功（14 §11 明令禁止的那种假账）", st.Accepted)
	}
	if st.Rejected != 1 {
		t.Errorf("rejected=%d，期望 1", st.Rejected)
	}
	if repo.publishedCount() != 0 {
		t.Errorf("published 写了 %d 行 ⇒ nack 之后还置 published", repo.publishedCount())
	}
	h.cancel()
	<-h.done
	h.done = nil
}

func TestClaimErrorIsCountedAndKeepsRunning(t *testing.T) {
	sess := &fakeSession{}
	h := newHarness(t, onSwitch(), &errRepo{}, sess, nil, nil)
	h.run()
	h.waitFor(t, func() bool { return h.runner.Stats().ClaimErrors >= 1 }, "认领失败没进计数")
	if !strings.Contains(h.logged(), "库在抖") {
		t.Errorf("认领失败没打日志：%q", h.logged())
	}
	h.waitFor(t, func() bool { return h.runner.Stats().ClaimErrors >= 2 }, "库抖一次就不再来第二遍")
	h.cancel()
	<-h.done
	h.done = nil
}

// TestPublishBudgetWarningAtStartup：一批的最坏确认时间超过租约时，启动必须说话。
// 这条不拦停，因为幂等消费面兜得住重复，而拦停会把一次参数写错放大成现网不可用。
func TestPublishBudgetWarningAtStartup(t *testing.T) {
	h := newHarness(t, onSwitch(), &fakeRepo{}, &fakeSession{}, nil, nil)
	h.run()
	h.waitFor(t, func() bool { return strings.Contains(h.logged(), "双投") },
		"50×5s=250s > 默认租约 120s，却没打双投警告")
	h.cancel()
	<-h.done
	h.done = nil

	quiet := onSwitch()
	quiet.ConfirmTimeout = time.Millisecond // 50×1ms 远小于租约 ⇒ 不该说话
	h2 := newHarness(t, quiet, &fakeRepo{}, &fakeSession{}, nil, nil)
	h2.run()
	time.Sleep(60 * time.Millisecond)
	if strings.Contains(h2.logged(), "双投") {
		t.Errorf("预算内还在喊双投，警告就成了噪音：%q", h2.logged())
	}
	h2.cancel()
	<-h2.done
	h2.done = nil
}

// TestStatsAreReadableConcurrently：指标面会被另一个 goroutine 读（日志/健康检查）。
func TestStatsAreReadableConcurrently(t *testing.T) {
	repo := &fakeRepo{batches: [][]domain.OutboxClaim{{claim("m-1")}}}
	h := newHarness(t, onSwitch(), repo, &fakeSession{}, nil, nil)
	h.run()
	done := make(chan struct{})
	go func() {
		for i := 0; i < 2000; i++ {
			_ = h.runner.Stats()
		}
		close(done)
	}()
	<-done
	h.cancel()
	<-h.done
	h.done = nil
}
