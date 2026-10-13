// Outbox worker 单测：投递编排的四条硬次序（18 §5/§6、14 §8、17 §9）。
//
//  1. confirm 之前不置 published（Publisher 返回 nil 才算 broker 确认，18 §6）；
//  2. 失败按 14 §8 的阶梯退避，到 MaxAttempts 转 dead_lettered（17 §9 不阻塞队列）；
//  3. 租约守卫拒绝（0 行）算「我没发布成」，既不算成功也不算异常；
//  4. 一条的失败不影响同批其余行。
//
// 认领的并发语义（FOR UPDATE SKIP LOCKED）与租约到期重取属库行为，
// 在 internal/store 的真库集成测里验，这里用 fake 只验编排。
package outbox

import (
	"context"
	"errors"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

type claimCall struct {
	batchLimit   int
	leaseSeconds int
}

// fakeRepo 记录每一次状态写回，断言全靠它——真库侧的行为不在这里证明。
type fakeRepo struct {
	claims    []domain.OutboxClaim
	callCount int
	calls     []claimCall

	publishedIDs []string
	retries      []retryWrite
	deadLetted   []deadWrite

	// 每条写回可单独置失败与「0 行」（租约被接管）。
	publishedErr   error
	publishedOK    bool
	retryErr       error
	retryOK        bool
	deadLetterErr  error
	deadLetterOK   bool
	retryNotOKFor  string
	publishedNotOK string

	// 只服务于 TestRunPollsAndStopsWithContext 的并发面：跨 goroutine 读写
	// 一律走 channel / atomic，否则 -race 会在这里报假阳性的测试竞争。
	claimed    chan claimCall
	failClaims atomic.Bool
	// repeatClaims：Run 用例每轮都返回同一批，避免测试线程改动 claims。
	// 与 failClaims 一样必须是 atomic——worker 在另一个 goroutine 上读它。
	repeatClaims atomic.Bool
}

type retryWrite struct {
	id         string
	retryAfter time.Duration
	lastError  string
}

type deadWrite struct {
	id        string
	lastError string
}

func newFakeRepo(claims ...domain.OutboxClaim) *fakeRepo {
	// 默认全绿：写回成功且放行（用例只在自己需要时改这几项）。
	return &fakeRepo{claims: claims, publishedOK: true, retryOK: true, deadLetterOK: true}
}

func (r *fakeRepo) ClaimOutboxBatch(ctx context.Context, batchLimit, leaseSeconds int) ([]domain.OutboxClaim, error) {
	call := claimCall{batchLimit: batchLimit, leaseSeconds: leaseSeconds}
	r.callCount++
	r.calls = append(r.calls, call)
	if r.claimed != nil {
		// 非阻塞上报：Run 用例会先确认「至少轮询了两轮」，之后才放行投递。
		// 报告不是测试断言的承重面，宁可丢也不能把 worker 卡死在这里。
		select {
		case r.claimed <- call:
		default:
		}
	}
	if r.failClaims.Load() {
		return nil, errors.New("connection refused")
	}
	if r.repeatClaims.Load() {
		// 每轮都返回同一批：并发用例不改 claims 切片，也就没有切片上的数据竞争。
		return []domain.OutboxClaim{claim("m1", 1)}, nil
	}
	out := r.claims
	r.claims = nil
	return out, nil
}

func (r *fakeRepo) MarkOutboxPublished(ctx context.Context, id string) (bool, error) {
	if r.publishedErr != nil {
		return false, r.publishedErr
	}
	if r.publishedNotOK == id {
		return false, nil
	}
	if !r.publishedOK {
		return false, nil
	}
	r.publishedIDs = append(r.publishedIDs, id)
	return true, nil
}

func (r *fakeRepo) MarkOutboxRetry(ctx context.Context, id string,
	retryAfter time.Duration, lastError string) (bool, error) {
	if r.retryErr != nil {
		return false, r.retryErr
	}
	if r.retryNotOKFor == id {
		return false, nil
	}
	if !r.retryOK {
		return false, nil
	}
	r.retries = append(r.retries, retryWrite{id: id, retryAfter: retryAfter,
		lastError: lastError})
	return true, nil
}

func (r *fakeRepo) MarkOutboxDeadLettered(ctx context.Context, id,
	lastError string) (bool, error) {
	if r.deadLetterErr != nil {
		return false, r.deadLetterErr
	}
	if !r.deadLetterOK {
		return false, nil
	}
	r.deadLetted = append(r.deadLetted, deadWrite{id: id, lastError: lastError})
	return true, nil
}

// fakePublisher 的 errBy 按 message_id 指定失败；nil 表全绿。
// 记到的 envelope 用来断言「由列拼出、payload 不二次封装」（N7-5）。
// signal 只服务于并发用例：非阻塞上报，投递结果本身由 got 断言。
type fakePublisher struct {
	got    []Envelope
	errBy  map[string]error
	called int
	signal chan Envelope
}

func (p *fakePublisher) Publish(ctx context.Context, msg Envelope) error {
	p.called++
	p.got = append(p.got, msg)
	if p.signal != nil {
		select {
		case p.signal <- msg:
		default:
		}
	}
	if err := p.errBy[msg.MessageID]; err != nil {
		return err
	}
	return nil
}

func claim(id string, attempt int) domain.OutboxClaim {
	return domain.OutboxClaim{ID: id + "-row", MessageID: id,
		MessageType: "pattern_admitted", SchemaVersion: 1, RoutingKey: "pattern.created",
		UserID: "user-1", CorrelationID: "corr-" + id, CausationID: "in-" + id,
		Payload: `{"status":"candidate"}`, AttemptCount: attempt,
		LeaseExpiresAt: time.Now().Add(time.Minute)}
}

func policy(batch, lease, maxAttempts int) domain.DeliveryPolicy {
	p := domain.DefaultDeliveryPolicy()
	p.BatchSize, p.LeaseSeconds, p.MaxAttempts = batch, lease, maxAttempts
	return p
}

func TestRunOncePublishesOnlyAfterConfirm(t *testing.T) {
	repo := newFakeRepo(claim("m1", 1), claim("m2", 1))
	pub := &fakePublisher{errBy: map[string]error{}}
	w := NewWorker(repo, pub, policy(50, 120, 8))
	got, err := w.RunOnce(context.Background())
	if err != nil {
		t.Fatalf("RunOnce: %v", err)
	}
	if got.Claimed != 2 || got.Published != 2 || got.Retried != 0 {
		t.Errorf("计数=%+v，两条都该 published", got)
	}
	if len(repo.publishedIDs) != 2 {
		t.Errorf("published 写回=%v", repo.publishedIDs)
	}
	// 认领参数走策略（18 §5 批认领 + N7-2 租约）。
	if repo.calls[0].batchLimit != 50 || repo.calls[0].leaseSeconds != 120 {
		t.Errorf("认领参数 batch=%d lease=%d", repo.calls[0].batchLimit,
			repo.calls[0].leaseSeconds)
	}
	// envelope 由列拼出（N7-5）：逐字段搬运，payload 不被再包一层。
	env := pub.got[0]
	if env.MessageID != "m1" || env.RoutingKey != "pattern.created" ||
		env.MessageType != "pattern_admitted" || env.SchemaVersion != 1 ||
		env.CorrelationID != "corr-m1" || env.CausationID != "in-m1" ||
		env.Payload != `{"status":"candidate"}` || env.UserID != "user-1" {
		t.Errorf("envelope 拼装丢字段：%+v", env)
	}
}

func TestRunOnceRetriesWithLadder(t *testing.T) {
	cases := []struct {
		name    string
		attempt int
		want    time.Duration
	}{
		{"第 1 次失败退 30s", 1, 30 * time.Second},
		{"第 2 次失败退 2m", 2, 2 * time.Minute},
		{"第 3 次失败退 10m", 3, 10 * time.Minute},
		{"阶梯走完沿用最后一档", 6, 10 * time.Minute},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			repo := newFakeRepo(claim("m1", tc.attempt))
			pub := &fakePublisher{errBy: map[string]error{"m1": errors.New("broker 不可达")}}
			w := NewWorker(repo, pub, policy(50, 120, 8))
			got, err := w.RunOnce(context.Background())
			if err != nil {
				t.Fatalf("RunOnce: %v", err)
			}
			if got.Retried != 1 || got.Published != 0 || got.DeadLetted != 0 {
				t.Errorf("计数=%+v", got)
			}
			if len(repo.retries) != 1 || repo.retries[0].retryAfter != tc.want {
				t.Fatalf("退避写回=%+v，期望 %v", repo.retries, tc.want)
			}
			if !strings.Contains(repo.retries[0].lastError, "broker 不可达") {
				t.Errorf("last_error=%q，失败原因要进账（17 §9 failure record）",
					repo.retries[0].lastError)
			}
			if len(repo.publishedIDs) != 0 {
				t.Error("发布没确认却置了 published（18 §6）")
			}
		})
	}
}

func TestRunOnceDeadLettersAtMaxAttempts(t *testing.T) {
	repo := newFakeRepo(claim("m1", 8))
	pub := &fakePublisher{errBy: map[string]error{"m1": errors.New("永远发不出去")}}
	w := NewWorker(repo, pub, policy(50, 120, 8))
	got, err := w.RunOnce(context.Background())
	if err != nil {
		t.Fatalf("RunOnce: %v", err)
	}
	if got.DeadLetted != 1 || got.Retried != 0 {
		t.Errorf("计数=%+v，第 8 次应转 dead_lettered 而不是继续退避", got)
	}
	if len(repo.deadLetted) != 1 || repo.deadLetted[0].id != "m1-row" {
		t.Fatalf("死信写回=%+v", repo.deadLetted)
	}
	if len(repo.retries) != 0 {
		t.Errorf("死信不该再排退避：%+v", repo.retries)
	}
}

func TestRunOnceLastErrorFitsColumn(t *testing.T) {
	repo := newFakeRepo(claim("m1", 1))
	pub := &fakePublisher{errBy: map[string]error{"m1": errors.New(strings.Repeat("x", 4000))}}
	w := NewWorker(repo, pub, policy(50, 120, 8))
	if _, err := w.RunOnce(context.Background()); err != nil {
		t.Fatalf("RunOnce: %v", err)
	}
	if got := len(repo.retries[0].lastError); got != lastErrorMax {
		t.Errorf("last_error 长度=%d，应截断到 VARCHAR(%d) 而不是撞库", got, lastErrorMax)
	}
}

func TestRunOnceLeaseGuardIsNotFailure(t *testing.T) {
	// 发布成功但确认写入被拒（别的 worker 已接管）：消息可能已在 broker 上，
	// 由幂等消费面兜重复（18 §12），这里既不能算 Published 也不能算 Unrecorded。
	repo := newFakeRepo(claim("m1", 1))
	repo.publishedNotOK = "m1-row"
	w := NewWorker(repo, &fakePublisher{errBy: map[string]error{}}, policy(50, 120, 8))
	got, err := w.RunOnce(context.Background())
	if err != nil {
		t.Fatalf("RunOnce: %v", err)
	}
	if got.Lost != 1 || got.Published != 0 || got.Unrecorded != 0 {
		t.Errorf("计数=%+v，期望 Lost=1/Published=0/Unrecorded=0", got)
	}
	// 退避路径上的同一守卫也要计入 Lost。
	repo2 := newFakeRepo(claim("m2", 1))
	repo2.retryNotOKFor = "m2-row"
	w2 := NewWorker(repo2, &fakePublisher{errBy: map[string]error{"m2": errors.New("抖动")}},
		policy(50, 120, 8))
	got2, err := w2.RunOnce(context.Background())
	if err != nil {
		t.Fatalf("RunOnce(退避): %v", err)
	}
	if got2.Lost != 1 || got2.Retried != 0 {
		t.Errorf("退避守卫计数=%+v", got2)
	}
}

func TestRunOnceKeepsGoingAfterPerRowErrors(t *testing.T) {
	repo := newFakeRepo(claim("m1", 1), claim("m2", 1), claim("m3", 1))
	pub := &fakePublisher{errBy: map[string]error{"m1": errors.New("坏消息")}}
	// m1 的写回也失败（计 Unrecorded 并报 OnError），m2/m3 必须照常投递。
	repo.retryErr = errors.New("库写不进去")
	var reports []string
	w := NewWorker(repo, pub, policy(50, 120, 8))
	w.OnError = func(id, stage string, err error) {
		reports = append(reports, id+"|"+stage)
	}
	got, err := w.RunOnce(context.Background())
	if err != nil {
		t.Fatalf("RunOnce: %v", err)
	}
	if pub.called != 3 {
		t.Errorf("只投递了 %d 条，一批里的失败不该让其余白等租约", pub.called)
	}
	if got.Unrecorded != 1 {
		t.Errorf("计数=%+v，写回失败要计数暴露", got)
	}
	if len(reports) != 1 || !strings.HasPrefix(reports[0], "m1-row|mark retry") {
		t.Errorf("OnError 报告=%v", reports)
	}
	if got.Published != 2 {
		t.Errorf("其余两条应发布成：%+v", got)
	}
}

func TestRunOnceClaimFailureReturnsError(t *testing.T) {
	repo := newFakeRepo(claim("m1", 1))
	repo.failClaims.Store(true)
	pub := &fakePublisher{errBy: map[string]error{}}
	w := NewWorker(repo, pub, policy(50, 120, 8))
	if _, err := w.RunOnce(context.Background()); err == nil {
		t.Fatal("认领失败必须返回错误：没有这一批可言")
	}
	if pub.called != 0 {
		t.Error("认领失败却投递了消息")
	}
}

func TestRunPollsAndStopsWithContext(t *testing.T) {
	claimed := make(chan claimCall, 16)
	published := make(chan Envelope, 16)
	repo := newFakeRepo()
	repo.claimed = claimed
	repo.failClaims.Store(true)
	pub := &fakePublisher{errBy: map[string]error{}, signal: published}
	w := NewWorker(repo, pub, policy(50, 120, 8))

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- w.Run(ctx, 5*time.Millisecond) }()

	wait := func(msg string, ch <-chan claimCall, ch2 <-chan Envelope) {
		t.Helper()
		deadline := time.After(2 * time.Second)
		select {
		case <-ch:
		case <-ch2:
		case err := <-done:
			t.Fatalf("Run 提前退出：%v（%s）", err, msg)
		case <-deadline:
			t.Fatalf("超时：%s", msg)
		}
	}
	// 认领失败不终止进程，且持续轮询（库抖动是可恢复的）。
	wait("第一轮认领", claimed, nil)
	wait("第二轮认领（失败后仍在轮询）", claimed, nil)
	// 放行后应立即再来一批并投递（有消息不睡 poll）。
	repo.repeatClaims.Store(true)
	repo.failClaims.Store(false)
	seen := make(chan Envelope, 1)
	go func() {
		for env := range published {
			select {
			case seen <- env:
			default:
			}
		}
	}()
	wait("投递发生", nil, seen)
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Errorf("Run 返回=%v，期望 context.Canceled", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("ctx 取消后 Run 没退出")
	}
}

// TestWorkerUsesPolicyNumbersNotHardcodedDefaults 防止把批大小/租约写死在 worker 里：
// 标定（N4/O 侧）之后只换环境变量，代码零改动是 D-4/G-5 的口径。
func TestWorkerUsesPolicyNumbersNotHardcodedDefaults(t *testing.T) {
	repo := newFakeRepo()
	w := NewWorker(repo, &fakePublisher{errBy: map[string]error{}}, policy(3, 45, 2))
	if _, err := w.RunOnce(context.Background()); err != nil {
		t.Fatalf("RunOnce: %v", err)
	}
	if repo.calls[0].batchLimit != 3 || repo.calls[0].leaseSeconds != 45 {
		t.Errorf("认领参数没跟策略走：batch=%d lease=%d",
			repo.calls[0].batchLimit, repo.calls[0].leaseSeconds)
	}
	// 死信上限同样来自策略：第 2 次就转死信。
	repo2 := newFakeRepo(claim("m1", 2))
	w2 := NewWorker(repo2, &fakePublisher{errBy: map[string]error{"m1": errors.New("坏")}},
		policy(3, 45, 2))
	got, err := w2.RunOnce(context.Background())
	if err != nil {
		t.Fatalf("RunOnce: %v", err)
	}
	if got.DeadLetted != 1 {
		t.Errorf("计数=%+v，MaxAttempts=2 时第 2 次失败就该进死信", got)
	}
}
