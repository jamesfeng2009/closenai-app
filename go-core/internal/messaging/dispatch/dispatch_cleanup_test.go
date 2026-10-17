// 批 3 D-G14-① 的闸：把投递记录的保留期清理挂进出箱 worker 这个既有周期面。
//
// 这一格要防的失效有两种，方向相反：
//   - **没接**：`CleanupDeliveryRecords`（`domain/delivery.go:393`，N7-11 拍的唯一清理入口）
//     永远零调用方 ⇒ outbox／inbox 只增不减，直到某天有人手写一条 DELETE 绕过保留期口径；
//   - **接错**：把它挂到一个不受 kill switch 管的定时器上 ⇒ 灰度 0 时进程仍在删生产数据，
//     「部署了但没开这个键＝现网行为一字不变」那条证据就作废了。
//     第 4 条用例（开关关着钩子一次都不许被调）判的就是这一种。
//
// 计数面（Cleanups／CleanupErrors／两张表各搬走多少行）也在这里钉：
// 没有它，「清理到底有没有在跑」又只能靠读代码确认——那正是本批改判的起因。
package dispatch

import (
	"context"
	"errors"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// ── fake：清理钩子与假时钟 ──────────────────────────────────────────────────

type cleanupHook struct {
	mu    sync.Mutex
	calls int
	res   domain.DeliveryCleanupResult
	err   error
}

func (c *cleanupHook) fn(ctx context.Context) (domain.DeliveryCleanupResult, error) {
	c.mu.Lock()
	c.calls++
	c.mu.Unlock()
	return c.res, c.err
}

func (c *cleanupHook) count() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.calls
}

// fakeClock 存在的理由只有一个：节拍用例不许真的等 6 小时。
// 用它的那几条判的是「推进量按不由 cleanupEvery() 走」这种纯函数性质；
// 而「循环自己在等时间走」那条（TestCleanupRepeatsOnTheCadence）故意用真时钟。
type fakeClock struct {
	mu sync.Mutex
	at time.Time
}

func newClock() *fakeClock {
	return &fakeClock{at: time.Date(2026, 10, 17, 3, 0, 0, 0, time.UTC)}
}

func (c *fakeClock) now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.at
}

func (c *fakeClock) advance(d time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.at = c.at.Add(d)
}

func withCleanup(hook *cleanupHook, every time.Duration, clock *fakeClock) func(*Deps) {
	return func(d *Deps) {
		d.Cleanup = hook.fn
		d.CleanupEvery = every
		if clock != nil {
			d.Now = clock.now
		}
	}
}

// nextCleanupAt 读的是 Run 独占的字段，所以必须走同一把锁（-race 不是装饰）。
func (r *Runner) nextCleanupAt() time.Time {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.nextCleanup
}

// ── 1．挂上之后真的在跑 ─────────────────────────────────────────────────────

// TestCleanupRunsAtStartupAndIsCounted：进程一起来就清一次（重启不该把历史行再攒 6 小时），
// 且搬走的行数要能在指标面上读出来。
func TestCleanupRunsAtStartupAndIsCounted(t *testing.T) {
	hook := &cleanupHook{res: domain.DeliveryCleanupResult{OutboxDeleted: 7, InboxDeleted: 3}}
	repo, sess := &fakeRepo{}, &fakeSession{}
	h := newHarness(t, onSwitch(), repo, sess, nil, nil, withCleanup(hook, time.Hour, nil))
	h.run()

	h.waitFor(t, func() bool { return hook.count() >= 1 }, "循环起来了但清理一次都没跑")
	if st := h.runner.Stats(); st.Cleanups != 1 || st.OutboxRowsDeleted != 7 || st.InboxRowsDeleted != 3 {
		t.Errorf("清理计数面错位： %+v", st)
	}
	h.waitFor(t, func() bool {
		return strings.Contains(h.logged(), "保留期清理")
	}, "清掉了却没有日志：运维读不出保留期清理在不在跑")

	// 节拍还没到就不许再清：这一格防的是「把 nextCleanup 写成零值 ⇒ 每轮一次 DELETE」。
	time.Sleep(80 * time.Millisecond)
	if got := hook.count(); got != 1 {
		t.Fatalf("CleanupEvery=1h、只过了几十毫秒却清了 %d 次 ⇒ 节拍没生效", got)
	}
	if n := h.runner.nextCleanupAt(); !n.After(h.runner.now().Add(30 * time.Minute)) {
		t.Errorf("下一次清理排到了 %v，应当在约 1h 之后 ⇒ 推进量没按 cleanupEvery()", n)
	}
}

// TestCleanupRepeatsOnTheCadence：到点要真的再来一次，否则「挂上」只等于「开机清一次」。
// 这条刻意用**真时钟**（Now 不给）＋20ms 节拍：假时钟不会自己走，
// 用它就只能测「推进一下」，而这里要证的恰恰是循环自己在等时间往前走。
func TestCleanupRepeatsOnTheCadence(t *testing.T) {
	hook := &cleanupHook{}
	repo, sess := &fakeRepo{}, &fakeSession{}
	sw := onSwitch()
	sw.Poll = 2 * time.Millisecond
	h := newHarness(t, sw, repo, sess, nil, nil, withCleanup(hook, 20*time.Millisecond, nil))
	h.run()

	h.waitFor(t, func() bool { return hook.count() >= 3 }, "节拍到了却没再清：清理退化成开机一次")
	if st := h.runner.Stats(); st.Cleanups < 3 {
		t.Errorf("钩子跑了 %d 次而账上只记 %d ⇒ 计数与真实调用脱钩", hook.count(), st.Cleanups)
	}
}

// ── 2．清理失败不许影响投递 ─────────────────────────────────────────────────

func TestCleanupFailureKeepsTheDeliveryLoop(t *testing.T) {
	hook := &cleanupHook{err: errors.New("fake: 保留期清理撞库失败")}
	repo := &fakeRepo{batches: [][]domain.OutboxClaim{{claim("row-1")}}}
	sess := &fakeSession{}
	h := newHarness(t, onSwitch(), repo, sess, nil, nil, withCleanup(hook, time.Millisecond, nil))
	h.run()

	h.waitFor(t, func() bool { return repo.claimsCount() >= 2 }, "清理报错之后投递循环停了")
	if st := h.runner.Stats(); st.CleanupErrors < 1 || st.Batches < 2 {
		t.Errorf("清理失败(%d)与投递批次(%d)的账不对：%+v", st.CleanupErrors, st.Batches, st)
	} else if st.ClaimErrors != 0 {
		t.Errorf("把清理失败算进了认领失败： %+v ⇒ 两类故障在排查时是两个问题", st)
	}
	h.waitFor(t, func() bool {
		return strings.Contains(h.logged(), "保留期清理没做成")
	}, "清理失败却没打日志：只涨计数不吭声，运维永远不会去看一个计数器")
}

// ── 3．kill switch 与「没接钩子」这两格必须真的什么都不做 ────────────────────

// TestKillSwitchOffNeverCallsCleanup 是本条改判的核心证据：清理挂在 worker 的循环里，
// 所以开关关着＝既不产行也不删行。把 maybeCleanup 挪到循环外（或另起定时器）就打红它。
func TestKillSwitchOffNeverCallsCleanup(t *testing.T) {
	hook := &cleanupHook{res: domain.DeliveryCleanupResult{OutboxDeleted: 99}}
	repo, sess := &fakeRepo{}, &fakeSession{}
	sw := onSwitch()
	sw.Enabled = false
	h := newHarness(t, sw, repo, sess, nil, nil, withCleanup(hook, time.Millisecond, nil))
	h.run()

	time.Sleep(80 * time.Millisecond)
	if got := hook.count(); got != 0 {
		t.Fatalf("kill switch 关着却清了 %d 次 ⇒ 灰度 0 时进程仍在删生产数据", got)
	}
	if st := h.runner.Stats(); st.Cleanups != 0 || st.OutboxRowsDeleted != 0 {
		t.Errorf("开关关着账上却有清理： %+v", st)
	}
	if n := h.runner.nextCleanupAt(); !n.IsZero() {
		t.Errorf("开关关着却推进了下次清理时间：%v", n)
	}
}

// TestNilHookLeavesNoPhantomSchedule：装配处忘了接钩子时，不许留下一个
// 「看起来排好了但永远不跑」的时间戳——那会让 Stats.Cleanups==0 被读成「还没到点」。
func TestNilHookLeavesNoPhantomSchedule(t *testing.T) {
	repo, sess := &fakeRepo{}, &fakeSession{}
	h := newHarness(t, onSwitch(), repo, sess, nil, nil)
	h.run()

	h.waitFor(t, func() bool { return repo.claimsCount() >= 2 }, "循环没跑起来")
	if st := h.runner.Stats(); st.Cleanups != 0 {
		t.Errorf("没接钩子却记了 %d 次清理", st.Cleanups)
	}
	if n := h.runner.nextCleanupAt(); !n.IsZero() {
		t.Errorf("没接钩子却把 nextCleanup 推到了 %v ⇒ 制造了「在清」的假象", n)
	}
}

// ── 4．零值节拍的回落（直接调 maybeCleanup，不经循环，免得赌时钟精度）────────

func TestZeroCadenceFallsBackToThePackageDefault(t *testing.T) {
	hook := &cleanupHook{}
	clock := newClock()
	repo := &fakeRepo{}
	r, err := New(Deps{
		Dial: func(context.Context, string, time.Duration) (Session, error) {
			return nil, errors.New("fake: 这条用例不该拨号")
		},
		Repo:         repo,
		Policy:       domain.DefaultDeliveryPolicy(),
		Switch:       onSwitch(),
		Log:          func(string, ...any) {},
		Cleanup:      hook.fn,
		CleanupEvery: 0, // 装配处忘了填
		Now:          clock.now,
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if got := r.cleanupEvery(); got != defaultCleanupEvery {
		t.Fatalf("CleanupEvery=0 被读成了 %v ⇒ 「忘了填」变成每轮一次 DELETE，那是自伤式撞库", got)
	}
	if err := r.maybeCleanup(context.Background()); err != nil {
		t.Fatalf("首次应当立即清：%v", err)
	}
	if hook.count() != 1 {
		t.Fatalf("首清跑了 %d 次", hook.count())
	}
	// 默认 6h：过 1 小时不该再来。
	clock.advance(time.Hour)
	if err := r.maybeCleanup(context.Background()); err != nil {
		t.Fatalf("maybeCleanup: %v", err)
	}
	if hook.count() != 1 {
		t.Fatalf("只过了 1h（默认节拍 6h）就清了第 %d 次 ⇒ 回落的默认值没被用上", hook.count())
	}
	clock.advance(6 * time.Hour)
	if err := r.maybeCleanup(context.Background()); err != nil {
		t.Fatalf("maybeCleanup: %v", err)
	}
	if hook.count() != 2 {
		t.Fatalf("过了默认节拍却没有第二次清理（仍为 %d）", hook.count())
	}
}

// TestFailedCleanupStillWaitsTheCadence：成败都推进时间戳。
// 清理失败几乎都是库抖动，原地每轮重试会把一条 DELETE 变成对撞。
func TestFailedCleanupStillWaitsTheCadence(t *testing.T) {
	hook := &cleanupHook{err: errors.New("fake: 库断了")}
	clock := newClock()
	r, err := New(Deps{
		Dial: func(context.Context, string, time.Duration) (Session, error) {
			return nil, errors.New("fake: 这条用例不该拨号")
		},
		Repo: &fakeRepo{}, Policy: domain.DefaultDeliveryPolicy(),
		Switch: onSwitch(), Log: func(string, ...any) {},
		Cleanup: hook.fn, CleanupEvery: time.Hour, Now: clock.now,
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if err := r.maybeCleanup(context.Background()); err == nil {
		t.Fatal("钩子报错却没传出来 ⇒ 上层日志永远不会说「没做成」")
	}
	if err := r.maybeCleanup(context.Background()); err != nil {
		t.Fatalf("第二次没到点应当静默返回，实收 %v ⇒ 失败后每轮重试就是一条 DELETE 撞库", err)
	}
	if hook.count() != 1 {
		t.Fatalf("到点前又跑了钩子 %d 次", hook.count())
	}
	if st := r.Stats(); st.Cleanups != 1 || st.CleanupErrors != 1 {
		t.Errorf("失败也要计入 Cleanups（它确实跑了一次）：%+v", st)
	}
}

// TestCleanupErrorReachesTheLog：maybeCleanup 的错要由 Run 打成日志，且文案点名重试口径。
func TestCleanupErrorReachesTheLog(t *testing.T) {
	hook := &cleanupHook{err: errors.New("fake: 保留期清理失败")}
	repo, sess := &fakeRepo{}, &fakeSession{}
	h := newHarness(t, onSwitch(), repo, sess, nil, nil, withCleanup(hook, time.Hour, nil))
	h.run()
	h.waitFor(t, func() bool {
		return strings.Contains(h.logged(), "保留期清理没做成") &&
			strings.Contains(h.logged(), "不影响投递循环")
	}, "错误没打成带口径的日志")
}

// TestCleanupCountsSurviveStatsSnapshot：聚合的是累计值，且两次快照叠加不许互相吃掉。
func TestCleanupCountsSurviveStatsSnapshot(t *testing.T) {
	hook := &cleanupHook{res: domain.DeliveryCleanupResult{OutboxDeleted: 2, InboxDeleted: 5}}
	clock := newClock()
	r, err := New(Deps{
		Dial: func(context.Context, string, time.Duration) (Session, error) {
			return nil, errors.New("fake: 这条用例不该拨号")
		},
		Repo: &fakeRepo{}, Policy: domain.DefaultDeliveryPolicy(),
		Switch: onSwitch(), Log: func(string, ...any) {},
		Cleanup: hook.fn, CleanupEvery: time.Hour, Now: clock.now,
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	for i := 0; i < 3; i++ {
		if err := r.maybeCleanup(context.Background()); err != nil {
			t.Fatalf("maybeCleanup: %v", err)
		}
		clock.advance(time.Hour)
	}
	st := r.Stats()
	if st.Cleanups != 3 || st.OutboxRowsDeleted != 6 || st.InboxRowsDeleted != 15 {
		t.Fatalf("三次清理的累计账错位：%+v", st)
	}
	if hook.count() != 3 {
		t.Errorf("钩子实跑 %d 次，账上 %d 次", hook.count(), st.Cleanups)
	}
}
