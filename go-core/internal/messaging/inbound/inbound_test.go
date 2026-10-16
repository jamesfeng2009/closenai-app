// 入站消费面的闸（R5-②）。
//
// 这个文件判七件事，每一件都对应一种「管道接上了但把消息搞坏」的形态：
//  1. kill switch 关 ⇒ 不拨号、不声明、不消费、不碰 DB（灰度 0 时现网行为逐字不变）；
//  2. **重复的 message_id 第二次不许走到领域入口**（tasks.md 的 R5 验收逐字要求）——
//     所以 fake Domain 把 inbox 认领做成真语义，Admit 的调用计数才是可读的证据；
//  3. ACK 的依据是 `ConsumeResult.Ack` 而不是 err：已留痕的永久拒绝必须 ACK（14 §7 第 3 项），
//     瞬态失败必须 `Nack(requeue=false)` 交给 broker 的 30s 层（14 §8 不许无限原地 requeue）；
//  4. 契约级非法走的是 `domain.PermanentRejection`，而 `classifyFailure` 用
//     `errors.As(cause, &PermanentRejection{})`（delivery.go:344）**按值类型**认它——
//     传指针就等于把毒消息读成瞬态、让它在阶梯上无限重投。这条由
//     TestDecodeRejectionReachesTheDomainClassifier 用**真** `domain.Service` 钉住；
//  5. 解码器的字段映射不错位：归属只从 `x-user-id` 取、窗口要两个时间戳都在才算、
//     `SAFETY_STATUS_UNSPECIFIED` 落 nil（D-8 的 fail-closed 方向）；
//  6. 会话死了会重拨＋重声明（amqp091-go v1.10.0 不自动重连），在途那一条拿到的是
//     **有上限**的收尾预算，不是随父 ctx 一起被取消；
//  7. 指标面的分类不错位：把 nack 记成 ack 就是把积压记成已消费。
//
// 全程无 broker、无真库：Session 与 Transactor 两个缝都是本地 fake，
// 真 broker 那一段留给 R5-③ 的互操作闸。
package inbound

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"

	closenaiv1 "github.com/jamesfeng2009/closenai-app/go-core/gen/closenai/v1"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// TestCandidateKindsTableUsesProtoEnumNames 钉住 `CandidateKinds` 那张表：
// 键↔proto kind 的对应一旦写反（pattern 配成 KIND_INSIGHT），
// 每个用例的 body 都得跟着改，而错的那版照样能自证——所以这里直接对生成物取值。
func TestCandidateKindsTableUsesProtoEnumNames(t *testing.T) {
	want := map[string]closenaiv1.Kind{
		"candidate.fact":    closenaiv1.Kind_KIND_FACT,
		"candidate.pattern": closenaiv1.Kind_KIND_PATTERN,
		"candidate.insight": closenaiv1.Kind_KIND_INSIGHT,
	}
	if len(CandidateKinds) != len(want) {
		t.Fatalf("表里有 %d 条，期望 %d 条", len(CandidateKinds), len(want))
	}
	for key, k := range want {
		if CandidateKinds[key] != k {
			t.Errorf("%s ⇒ %s，期望 %s", key, CandidateKinds[key], k)
		}
	}
}

// ── fake：领域入口 ──────────────────────────────────────────────────────────

// fakeDomain 把 `ConsumeIdempotent` 的**认领语义**照真实现做出来
// （delivery.go:267-318：先 INSERT inbox，0 行＝重复且不再跑副作用）。
// 它不是真事务，但「第二次不调领域入口」这件事只有在这一跳被做出来才可判；
// 真事务那一面由 TestDecodeRejectionReachesTheDomainClassifier 用真 Service 补。
type fakeDomain struct {
	mu         sync.Mutex
	consumed   map[string]string // message_id → processed|failed
	consumeCtx []context.Context // 每次消费的 ctx，用来验在途那一条的收尾预算

	admits     []domain.Candidate
	evidences  []domain.EvidenceCandidate
	proactive  []domain.ProactiveCandidate
	admitErr   error
	evidenceEr error
	proactErr  error
	fnCalls    int
	// 在事务**里**取的 ctx 快照：Handle 之后会 cancel 收尾 ctx，事后再问 Err()
	// 问到的永远是 canceled，判不了「停机有没有打断在途那一条」。
	ctxErrAtHold error
	deadlineAt   time.Time
	hasDeadline  bool
	// hold 在进事务前阻塞：用来造「一条正卡在收尾中途的交付」。
	hold func()
}

func newFakeDomain() *fakeDomain {
	return &fakeDomain{consumed: map[string]string{}}
}

func (f *fakeDomain) ConsumeIdempotent(ctx context.Context, msg domain.InboundMessage,
	fn func(context.Context, domain.Store) error) (domain.ConsumeResult, error) {
	f.mu.Lock()
	f.consumeCtx = append(f.consumeCtx, ctx)
	if msg.Consumer != Consumer {
		f.mu.Unlock()
		return domain.ConsumeResult{}, fmt.Errorf("fake: consumer=%q，本队列只认 %q", msg.Consumer, Consumer)
	}
	if msg.MessageID == "" {
		f.mu.Unlock()
		return domain.ConsumeResult{}, errors.New("fake: 没有 message_id 就占不了幂等位")
	}
	if state, ok := f.consumed[msg.MessageID]; ok {
		f.mu.Unlock()
		switch state {
		case "processed":
			return domain.ConsumeResult{Status: domain.ConsumeDuplicate, Ack: true}, nil
		case "failed":
			return domain.ConsumeResult{Status: domain.ConsumeRejected, Ack: true}, nil
		default:
			return domain.ConsumeResult{Status: domain.ConsumeTransient, Ack: false}, nil
		}
	}
	f.fnCalls++
	hold := f.hold
	if hold != nil {
		f.ctxErrAtHold = ctx.Err()
		f.deadlineAt, f.hasDeadline = ctx.Deadline()
	}
	f.mu.Unlock()
	if hold != nil {
		hold()
	}

	err := fn(ctx, nil)

	f.mu.Lock()
	defer f.mu.Unlock()
	if err == nil {
		f.consumed[msg.MessageID] = "processed"
		return domain.ConsumeResult{Status: domain.ConsumeProcessed, Ack: true}, nil
	}
	var rej domain.PermanentRejection
	if errors.As(err, &rej) {
		f.consumed[msg.MessageID] = "failed"
		return domain.ConsumeResult{Status: domain.ConsumeRejected, Ack: true, Detail: rej.Code}, err
	}
	return domain.ConsumeResult{Status: domain.ConsumeTransient, Ack: false}, err
}

func (f *fakeDomain) Admit(_ context.Context, c domain.Candidate) (domain.Result, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.admits = append(f.admits, c)
	// Handle 只看 `ConsumeResult`，领域返回值在这里不参与判定。
	return domain.Result{Action: domain.ActionInsert}, f.admitErr
}

func (f *fakeDomain) AdmitEvidence(_ context.Context, c domain.EvidenceCandidate) (domain.EvidenceResult, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.evidences = append(f.evidences, c)
	return domain.EvidenceResult{}, f.evidenceEr
}

func (f *fakeDomain) AdmitProactive(_ context.Context, c domain.ProactiveCandidate) (domain.ProactiveResult, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.proactive = append(f.proactive, c)
	return domain.ProactiveResult{}, f.proactErr
}

func (f *fakeDomain) counts() (fn, admits, evidences, proactives int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.fnCalls, len(f.admits), len(f.evidences), len(f.proactive)
}

// ── fake：broker 会话 ───────────────────────────────────────────────────────

type fakeSession struct {
	mu        sync.Mutex
	declares  int
	closes    int
	alive     bool
	declareEr error
	consumes  int
	queues    []string
	prefetch  []int
	ch        chan Delivery
}

func (s *fakeSession) Declare() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.declares++
	if s.declareEr != nil {
		return s.declareEr
	}
	s.alive = true
	return nil
}

func (s *fakeSession) Consume(ctx context.Context, queue string, prefetch int) (<-chan Delivery, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.alive {
		return nil, errors.New("fake: 没声明过拓扑就消费")
	}
	s.consumes++
	s.queues = append(s.queues, queue)
	s.prefetch = append(s.prefetch, prefetch)
	s.ch = make(chan Delivery, 8)
	return s.ch, nil
}

func (s *fakeSession) Alive() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.alive
}

func (s *fakeSession) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.closes++
	s.alive = false
	if s.ch != nil {
		close(s.ch)
		s.ch = nil
	}
	return nil
}

// send 往当前会话的交付通道投一条（通道带缓冲，且全程持锁，所以不会与 Close 抢）。
func (s *fakeSession) send(d Delivery) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.ch == nil {
		panic("fake: 会话还没开始消费就投递")
	}
	s.ch <- d
}

// kill 模拟连接断：交付通道关掉、判活翻假（amqp091 的 NotifyClose 就是这个形状）。
// 通道置 nil 是必须的：Runner 随后会 Close 这条会话，二次 close 会 panic。
func (s *fakeSession) kill() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.alive = false
	if s.ch != nil {
		close(s.ch)
		s.ch = nil
	}
}

// ── fake：交付 ──────────────────────────────────────────────────────────────

type settleCall struct {
	ack     bool
	requeue bool
}

type recorder struct {
	mu     sync.Mutex
	calls  []settleCall
	setErr error
}

func (r *recorder) settle(ack, requeue bool) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.calls = append(r.calls, settleCall{ack, requeue})
	return r.setErr
}

func (r *recorder) seen() []settleCall {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]settleCall{}, r.calls...)
}

// delivery 造一条**契约合法**的交付；用例只覆盖自己要判的那一格，
// 免得每个用例都重抄一遍五个 header（抄错的 header 会让「非法用例」假绿）。
func delivery(rec *recorder, mutate func(*Delivery)) Delivery {
	body, err := json.Marshal(map[string]any{
		"schemaVersion": SupportedSchemaVersion,
		"kind":          "KIND_PATTERN",
		"patternType":   "evening_rumination",
		"fingerprint":   "fp-1",
		"confidence":    0.7,
		"safety":        map[string]any{"status": "SAFETY_STATUS_OK"},
	})
	if err != nil {
		panic(err)
	}
	d := Delivery{
		RoutingKey:  "candidate.pattern",
		MessageID:   "cnd-0001",
		MessageType: "candidate_pattern",
		ContentType: ContentTypeJSON,
		Headers: map[string]any{
			HeaderSchemaVersion: SupportedSchemaVersion,
			HeaderUserID:        "user-42",
			HeaderCausationID:   "cmd-0001",
		},
		Body: body,
	}
	if rec != nil {
		d.Settle = rec.settle
	}
	if mutate != nil {
		mutate(&d)
	}
	return d
}

// ── 装配 ────────────────────────────────────────────────────────────────────

func onSwitch() Switch {
	return Switch{Enabled: true, URL: "amqp://127.0.0.1:5672/",
		Prefetch: DefaultPrefetch, MaxBodyBytes: DefaultMaxBodyBytes,
		DialTimeout: time.Second, DrainTimeout: time.Second}
}

func testRunner(t *testing.T, dm Domain, sw Switch, dial DialFunc) *Runner {
	t.Helper()
	if dial == nil {
		// Handle-only 的用例不该碰连接面：给一条会失败的拨号，
		// 万一将来 Run/Handle 里多出一次隐式拨号，这里先红而不是悄悄连上。
		dial = func(context.Context, string, time.Duration) (Session, error) {
			return nil, errors.New("fake: 这条用例不该拨号")
		}
	}
	r, err := New(Deps{Dial: dial, Domain: dm, Switch: sw,
		Log: func(string, ...any) {}})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return r
}

// ── 1．kill switch 与装配期校验 ─────────────────────────────────────────────

func TestKillSwitchOffTouchesNothing(t *testing.T) {
	dm := newFakeDomain()
	dials := 0
	r := testRunner(t, dm, Switch{Prefetch: 1, MaxBodyBytes: 1024,
		DialTimeout: time.Second, DrainTimeout: time.Second},
		func(context.Context, string, time.Duration) (Session, error) {
			dials++
			return nil, errors.New("fake: 开关关着不该拨号")
		})
	if err := r.Run(context.Background()); err != nil {
		t.Fatalf("Run（关）带回错误：%v", err)
	}
	if dials != 0 {
		t.Errorf("开关关着却拨了 %d 次号 ⇒ 灰度 0 时这段接线会连 broker", dials)
	}
	fn, admits, ev, pa := dm.counts()
	if fn+admits+ev+pa != 0 {
		t.Errorf("开关关着却走了领域面：fn=%d admit=%d evidence=%d proactive=%d", fn, admits, ev, pa)
	}
	if st := r.Stats(); st.Received != 0 || st.Acks != 0 || st.Nacks != 0 {
		t.Errorf("关着的 worker 不该有计数：%+v", st)
	}
}

func TestEnabledWithoutURLRefusesToStart(t *testing.T) {
	// 空转的 worker 比没有 worker 更坏：运维会把它读成「入站面在跑」。
	_, err := New(Deps{
		Dial:   func(context.Context, string, time.Duration) (Session, error) { return &fakeSession{}, nil },
		Domain: newFakeDomain(),
		Switch: Switch{Enabled: true, Prefetch: 1, MaxBodyBytes: 1024,
			DialTimeout: time.Second, DrainTimeout: time.Second},
	})
	if err == nil || !strings.Contains(err.Error(), EnvURL) {
		t.Fatalf("%s 开了但没给 %s 必须拒绝启动，实收：%v", EnvWorker, EnvURL, err)
	}
}

func TestSwitchParametersMustBePositive(t *testing.T) {
	for name, sw := range map[string]Switch{
		"prefetch=0": {Enabled: true, URL: "amqp://x", Prefetch: 0, MaxBodyBytes: 1024,
			DialTimeout: time.Second, DrainTimeout: time.Second},
		"body=0": {Enabled: true, URL: "amqp://x", Prefetch: 1, MaxBodyBytes: 0,
			DialTimeout: time.Second, DrainTimeout: time.Second},
		"drain=0": {Enabled: true, URL: "amqp://x", Prefetch: 1, MaxBodyBytes: 1024,
			DialTimeout: time.Second},
	} {
		if _, err := New(Deps{
			Dial:   func(context.Context, string, time.Duration) (Session, error) { return &fakeSession{}, nil },
			Domain: newFakeDomain(), Switch: sw,
		}); err == nil {
			t.Errorf("%s 被放过了 ⇒ 0 在 AMQP 里＝不限 prefetch，而 0 字节上限会让每条消息都超限", name)
		}
	}
}

func TestSwitchVocabularyIsTheSharedOne(t *testing.T) {
	// 三处（Go dispatch、Go inbound、Python 的 _TRUE）认「开」的字面量必须同表，
	// 否则同一个 ConfigMap 值在两个进程里读出相反结论。
	for _, on := range []string{"1", "true", "TRUE", " on ", "Yes", "yes"} {
		if !isOn(on) {
			t.Errorf("%q 该认成开（与 dispatch.isOn／Python 的 _TRUE 同表）", on)
		}
	}
	for _, off := range []string{"", "  ", "0", "false", "off", "no", "maybe", "y"} {
		if isOn(off) {
			t.Errorf("%q 不该认成开：默认关是这条接线的安全侧", off)
		}
	}
	sw := LoadSwitch(func(k string) string {
		return map[string]string{EnvWorker: "on", EnvURL: "  amqp://broker  "}[k]
	})
	if !sw.Enabled || sw.URL != "amqp://broker" {
		t.Errorf("LoadSwitch 读错： %+v", sw)
	}
	if sw.Prefetch != DefaultPrefetch || sw.MaxBodyBytes != DefaultMaxBodyBytes {
		t.Errorf("LoadSwitch 不该顺手改包内默认：%+v", sw)
	}
	// 谁都不给键 ＝ 关，且不需要 URL（这是灰度 0 的现网形态）。
	off := LoadSwitch(func(string) string { return "" })
	if off.Enabled || off.URL != "" {
		t.Errorf("默认值不关：%+v", off)
	}
	if err := off.Validate(); err != nil {
		t.Errorf("默认 Switch 该直接可用：%v", err)
	}
}

// ── 2．幂等：重复消息第二次不许碰领域入口 ───────────────────────────────────

func TestDuplicateMessageDoesNotCallDomainTwice(t *testing.T) {
	dm := newFakeDomain()
	r := testRunner(t, dm, onSwitch(), nil)

	first := delivery(nil, nil)
	rec1 := &recorder{}
	first.Settle = rec1.settle
	res, err := r.Handle(context.Background(), first)
	if err != nil || res.Status != domain.ConsumeProcessed {
		t.Fatalf("首投应 processed：res=%+v err=%v", res, err)
	}

	// 同 message_id 的第二次投递（broker at_least_once 的常态，D-R5-6）。
	second := delivery(nil, nil)
	rec2 := &recorder{}
	second.Settle = rec2.settle
	res2, err := r.Handle(context.Background(), second)
	if err != nil {
		t.Fatalf("重复投递不该报错：%v", err)
	}
	if res2.Status != domain.ConsumeDuplicate || !res2.Ack {
		t.Errorf("重复投递应 duplicate＋ack，实收 %+v", res2)
	}
	fn, admits, ev, pa := dm.counts()
	if fn != 1 || admits != 1 || ev+pa != 0 {
		t.Errorf("重复消息把领域入口又跑了一遍：fn=%d admit=%d evidence=%d proactive=%d ⇒ "+
			"解码与 Admit 必须都在 inbox 认领之后（副作用跑两次就是双写）", fn, admits, ev, pa)
	}
	if got := rec2.seen(); len(got) != 1 || !got[0].ack {
		t.Errorf("重复消息的 broker 动作不对：%v ⇒ duplicate 是 ACK 语义（14 §7 第 2 项）", got)
	}
	st := r.Stats()
	if st.Processed != 1 || st.Duplicates != 1 || st.Acks != 2 {
		t.Errorf("指标面错位：%+v", st)
	}
}

// TestSecondDecodeNeverHappens 把上一条收紧一格：重复消息连**解码**都不该发生。
// 它是 fn 计数（而不是 admits 计数）的意义所在——一条畸形但重复的消息，
// 若在认领之前就解码，排查时会看到「同一 id 两条不同结论」。
func TestDuplicateSkipsDecodingToo(t *testing.T) {
	dm := newFakeDomain()
	r := testRunner(t, dm, onSwitch(), nil)
	bad := delivery(nil, func(d *Delivery) { d.Body = []byte("{not json") })
	if _, err := r.Handle(context.Background(), bad); err == nil {
		t.Fatal("畸形载荷该带回错误")
	}
	fnAfterFirst, _, _, _ := dm.counts()
	if fnAfterFirst != 1 {
		t.Fatalf("首投应进 fn 一次，实收 %d", fnAfterFirst)
	}
	if _, err := r.Handle(context.Background(), bad); err != nil {
		t.Logf("重投同一 id 的返回：%v", err)
	}
	fnAfterSecond, _, _, _ := dm.counts()
	if fnAfterSecond != fnAfterFirst {
		t.Errorf("重复消息又解码了一次：fn %d → %d", fnAfterFirst, fnAfterSecond)
	}
}

// ── 3．ACK 依据是 ConsumeResult.Ack，不是 err ───────────────────────────────

func TestPermanentRejectionAcksAfterLeavingTrace(t *testing.T) {
	dm := newFakeDomain()
	dm.admitErr = domain.PermanentRejection{Code: "gate_says_no", Detail: "领域侧的永久拒绝"}
	r := testRunner(t, dm, onSwitch(), nil)

	rec := &recorder{}
	res, err := r.Handle(context.Background(), delivery(rec, nil))
	if !errors.Is(err, domain.ErrPermanentMessage) {
		t.Fatalf("永久拒绝要能 errors.Is(ErrPermanentMessage)，实收 %v", err)
	}
	if !res.Ack || res.Status != domain.ConsumeRejected {
		t.Errorf("已留痕的永久拒绝必须 ACK（14 §7 第 3 项），实收 %+v ⇒ ACK 被写成看 err 了？", res)
	}
	if got := rec.seen(); len(got) != 1 || !got[0].ack {
		t.Errorf("broker 动作不对：%v", got)
	}
	st := r.Stats()
	if st.Rejected != 1 || st.Acks != 1 || st.Nacks != 0 {
		t.Errorf("把永久拒绝记成瞬态＝假账：%+v", st)
	}
}

func TestTransientFailureNacksWithoutRequeue(t *testing.T) {
	dm := newFakeDomain()
	dm.admitErr = errors.New("pg: 连接断了") // 非 PermanentRejection ⇒ 瞬态
	r := testRunner(t, dm, onSwitch(), nil)

	rec := &recorder{}
	res, err := r.Handle(context.Background(), delivery(rec, nil))
	if res.Ack {
		t.Errorf("瞬态失败却 ack：%+v", res)
	}
	if err == nil || errors.Is(err, domain.ErrPermanentMessage) {
		t.Errorf("瞬态失败原样带回、不带哨兵：%v", err)
	}
	got := rec.seen()
	// requeue=true 会热转（14 §8 明令 prefer 显式重试队列）；本拓扑里 requeue=false
	// 才落到 30s 层（topology.go:190-199 的 x-dead-letter-routing-key）。
	if len(got) != 1 || got[0].ack || got[0].requeue {
		t.Errorf("瞬态该 Nack(requeue=false) 交给阶梯，实收 %v", got)
	}
	st := r.Stats()
	if st.Transient != 1 || st.Nacks != 1 || st.Acks != 0 {
		t.Errorf("指标面错位：%+v", st)
	}
}

func TestSettleErrorIsCountedNotSwallowed(t *testing.T) {
	dm := newFakeDomain()
	r := testRunner(t, dm, onSwitch(), nil)
	rec := &recorder{setErr: errors.New("fake: channel 已关")}
	if _, err := r.Handle(context.Background(), delivery(rec, nil)); err == nil {
		t.Error("ack 失败必须报错：把它咽掉＝账上写着已消费")
	}
	st := r.Stats()
	if st.SettleErrs != 1 || st.Acks != 0 {
		t.Errorf("结算失败却记成 ack：%+v", st)
	}
}

func TestMissingMessageIDNeverReachesDomain(t *testing.T) {
	dm := newFakeDomain()
	r := testRunner(t, dm, onSwitch(), nil)
	rec := &recorder{}
	res, err := r.Handle(context.Background(), delivery(rec, func(d *Delivery) { d.MessageID = "  " }))
	if err == nil {
		t.Fatal("缺 message_id 要有结论")
	}
	if res.Status != domain.ConsumeRejected || res.Ack {
		t.Errorf("res=%+v ⇒ 这一条既占不了幂等位也留不了痕，只能 reject", res)
	}
	fn, admits, _, _ := dm.counts()
	if fn+admits != 0 {
		t.Errorf("连幂等键都没有就进领域面：fn=%d admit=%d", fn, admits)
	}
	got := rec.seen()
	if len(got) != 1 || got[0].ack || got[0].requeue {
		t.Errorf("该 Nack(requeue=false)，实收 %v", got)
	}
	if st := r.Stats(); st.RejectsByReason[ReasonMissingMessageID] != 1 {
		t.Errorf("毒消息排查第一眼看的就是 reason 计数：%+v", st.RejectsByReason)
	}
}

func TestStatsSnapshotCopiesTheReasonMap(t *testing.T) {
	dm := newFakeDomain()
	r := testRunner(t, dm, onSwitch(), nil)
	_, _ = r.Handle(context.Background(), delivery(nil, func(d *Delivery) { d.MessageID = "" }))
	snap := r.Stats()
	snap.RejectsByReason[ReasonMissingMessageID] = 999
	delete(snap.RejectsByReason, ReasonMissingMessageID)
	if got := r.Stats().RejectsByReason[ReasonMissingMessageID]; got != 1 {
		t.Errorf("Stats() 交出去的是内部 map 的引用，读侧能改写指标：%v", got)
	}
}

func TestNilMapsDoNotPanicOnFirstPoison(t *testing.T) {
	// RejectsByReason 若在装配期没建好，**第一条毒消息**就会把这个 worker 打 panic
	// （正常消息永远走不到那格，所以常规用例测不出来）。
	dm := newFakeDomain()
	r := testRunner(t, dm, onSwitch(), nil)
	done := make(chan struct{})
	go func() {
		defer close(done)
		_, _ = r.Handle(context.Background(), delivery(nil, func(d *Delivery) { d.ContentType = "text/plain" }))
	}()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("Handle 卡住了")
	}
	if st := r.Stats(); st.RejectsByReason == nil || st.RejectsByReason[ReasonContentType] != 1 {
		t.Errorf("reason 面没记上：%+v", st.RejectsByReason)
	}
}

// ── 4．契约级非法必须被领域分类器认出来（errors.As 的值/指针之坑） ──────────

// TestDecodeRejectionReachesTheDomainClassifier 用**真** `domain.Service` 跑一条
// 契约级非法的消息。它判的是 delivery.go:344 那一行 `errors.As(cause, &rej)`：
// rej 是**值类型** PermanentRejection，而 Decode 给的是 `*PermanentRejection`——
// 指针不可赋值给值类型，于是 Handle 里那句解引用（inbound.go 的 fn）一旦退化成
// `return decErr`，非法载荷就被读成瞬态：不留痕、不 ACK，而是永远在阶梯上撞库。
func TestDecodeRejectionReachesTheDomainClassifier(t *testing.T) {
	tx := newFakeTransactor()
	svc := domain.NewService(tx, domain.DefaultPolicy())
	r := testRunner(t, svc, onSwitch(), nil)

	rec := &recorder{}
	// text/plain 是纯契约级非法：它在解码器第一道门就出局，永远碰不到 Store 的写面
	// （fakeTransactor 给的 Store 只实现了 inbox 那三格，别的 method 被调用会当场 panic）。
	res, err := r.Handle(context.Background(), delivery(rec, func(d *Delivery) { d.ContentType = "text/plain" }))
	if res.Status != domain.ConsumeRejected || !res.Ack {
		t.Fatalf("契约级非法应走「留痕＋ACK」，实收 res=%+v err=%v", res, err)
	}
	if len(tx.failureCodes()) != 1 || tx.failureCodes()[0] != ReasonContentType {
		t.Errorf("留痕没落在 inbox 上：%v ⇒ 这条消息被丢掉了却没人知道为什么", tx.failureCodes())
	}
	if got := rec.seen(); len(got) != 1 || !got[0].ack {
		t.Errorf("broker 动作：%v", got)
	}
	if st := r.Stats(); st.RejectsByReason[ReasonContentType] != 1 || st.Nacks != 0 {
		t.Errorf("非法载荷被记成瞬态：%+v", st)
	}

	// 同一条重投：真实现里 inbox 已有 failed 行 ⇒ duplicateVerdict 回「此前已按策略记录」＋ACK
	// （delivery.go:330-333）。第二次不该再补一条留痕——否则 error_code 计数会随重投翻倍。
	rec2 := &recorder{}
	res2, err2 := r.Handle(context.Background(), delivery(rec2, func(d *Delivery) { d.ContentType = "text/plain" }))
	if res2.Status != domain.ConsumeRejected || !res2.Ack {
		t.Fatalf("重投的永久拒绝应 ACK 且不再留痕，实收 res=%+v err=%v", res2, err2)
	}
	if n := len(tx.failureCodes()); n != 1 {
		t.Errorf("同一条消息留痕了 %d 次 ⇒ 账被重投次数放大", n)
	}
}

// ── 5／6．会话生命周期 ──────────────────────────────────────────────────────

// dialer 是带计数的拨号器，同时充当「当前该返回哪条会话」的把手。
// 两处都要锁：Run 在自己的 goroutine 里读，用例主线程写；裸 int/裸变量
// 会被 -race 判成数据竞争（而这条用例的意义就是并发路径本身）。
type dialer struct {
	mu     sync.Mutex
	n      int
	sess   Session
	err    error
	onDial func()
}

func (d *dialer) Dial(context.Context, string, time.Duration) (Session, error) {
	d.mu.Lock()
	d.n++
	sess, err, hook := d.sess, d.err, d.onDial
	d.mu.Unlock()
	if hook != nil {
		hook()
	}
	if err != nil {
		return nil, err
	}
	return sess, nil
}

func (d *dialer) set(s Session) {
	d.mu.Lock()
	d.sess = s
	d.mu.Unlock()
}

func (d *dialer) count() int {
	d.mu.Lock()
	defer d.mu.Unlock()
	return d.n
}

func TestRunConsumesQueueAndDeclaresOncePerSession(t *testing.T) {
	sess := &fakeSession{}
	dm := newFakeDomain()
	dial := &dialer{sess: sess}
	r := testRunner(t, dm, onSwitch(), dial.Dial)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- r.Run(ctx) }()

	waitFor(t, 2*time.Second, func() bool { return sess.consumeCount() == 1 }, "没有开始消费")
	sess.send(delivery(nil, nil))
	waitFor(t, 2*time.Second, func() bool {
		fn, _, _, _ := dm.counts()
		return fn == 1
	}, "交付没被处理")

	if sess.declareCount() != 1 {
		t.Errorf("声明次数=%d ⇒ D-R5-3 要 Go 独占主动声明，且每条会话只一次", sess.declareCount())
	}
	if q := sess.lastQueue(); q != Queue {
		t.Errorf("消费的是 %q，不是契约里的 %q", q, Queue)
	}
	if p := sess.lastPrefetch(); p != DefaultPrefetch {
		t.Errorf("prefetch=%d ⇒ 与包内默认（也是 Python 侧那台）不一致，在途条数会被放大", p)
	}

	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Errorf("Run 退出带回 %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("取消 ctx 后 Run 没退出")
	}
	if sess.closeCount() != 1 {
		t.Errorf("会话关了 %d 次 ⇒ 退出路径上的 closeSession 被走重复了", sess.closeCount())
	}
	if dial.count() != 1 {
		t.Errorf("拨号 %d 次 ⇒ 一条会话该只拨一次", dial.count())
	}
}

func TestRedialsAndReDeclaresWhenSessionDies(t *testing.T) {
	first := &fakeSession{}
	second := &fakeSession{}
	dm := newFakeDomain()
	dial := &dialer{sess: first}
	r := testRunner(t, dm, onSwitch(), dial.Dial)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- r.Run(ctx) }()
	waitFor(t, 2*time.Second, func() bool { return first.consumeCount() == 1 }, "首条会话没开始消费")

	// 连接断：交付通道关闭（amqp091-go 不自动重连，17 §2 要求 worker 容忍重启）。
	dial.set(second)
	first.kill()
	waitFor(t, 2*time.Second, func() bool { return second.consumeCount() == 1 }, "没有重拨重消费")

	if second.declareCount() != 1 {
		t.Errorf("重拨后没重声明拓扑：declares=%d", second.declareCount())
	}
	st := r.Stats()
	if st.Redials != 1 || st.Dials != 2 {
		t.Errorf("重拨计数错位：%+v", st)
	}
	// 断线期间未 ack 的消息由 broker 重投，这里不额外做什么——但幂等键那一跳在前面，
	// 所以第二条真投过来时领域入口不会再跑（TestDuplicateMessageDoesNotCallDomainTwice）。
	second.send(delivery(nil, nil))
	waitFor(t, 2*time.Second, func() bool {
		fn, _, _, _ := dm.counts()
		return fn == 1
	}, "新会话上的交付没被处理")
	if first.closeCount() == 0 {
		t.Error("死掉的会话没被 Close ⇒ 重拨会泄漏连接")
	}
}

func TestDialFailureDoesNotKillTheLoop(t *testing.T) {
	// broker 起不来时 worker **不拖死 API 进程**：报错、等一会、再试。
	dial := &dialer{err: errors.New("fake: broker refused")}
	r := testRunner(t, newFakeDomain(), onSwitch(), dial.Dial)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- r.Run(ctx) }()
	waitFor(t, 3*time.Second, func() bool { return dial.count() >= 2 }, "拨号失败后没有重试")
	cancel()
	if err := <-done; err != nil {
		t.Errorf("拨号失败不该让 Run 报错退出（那是进程级故障）：%v", err)
	}
	if st := r.Stats(); st.Dials != 0 {
		t.Errorf("拨号全失败却记了 %d 次成功 ⇒ Dials 是「建成的会话」不是「试了几次」", st.Dials)
	}
}

func TestInFlightDeliveryGetsABoundedDeadline(t *testing.T) {
	// 停机时父 ctx 已取消，但在途那一条必须还能把事务做完——并且**有上限**：
	// pod 到点是 SIGKILL，一个卡在退出路径上的 worker 会被读成「优雅停机成功」。
	//
	// 这条用例把交付**卡在事务里**再取消：select 的两个 case 同时就绪时 Go 随机挑，
	// 「先投再取消」会让另一条路径（直接退出）也通过，等于测了个概率。
	dm := newFakeDomain()
	release := make(chan struct{})
	dm.hold = func() { <-release }
	sess := &fakeSession{}
	r := testRunner(t, dm, onSwitch(), func(context.Context, string, time.Duration) (Session, error) {
		return sess, nil
	})
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- r.Run(ctx) }()
	waitFor(t, 2*time.Second, func() bool { return sess.consumeCount() == 1 }, "没开始消费")

	rec := &recorder{}
	sess.send(delivery(rec, nil))
	waitFor(t, 2*time.Second, func() bool {
		dm.mu.Lock()
		defer dm.mu.Unlock()
		return len(dm.consumeCtx) == 1
	}, "在途那一条没进事务")
	cancel() // 父 ctx 在收尾中途取消
	time.Sleep(20 * time.Millisecond)
	close(release)
	waitFor(t, 2*time.Second, func() bool { return len(rec.seen()) == 1 }, "取消父 ctx 把在途那一条一起打断了")

	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("Run 没退出")
	}

	dm.mu.Lock()
	errAtHold, hasDeadline := dm.ctxErrAtHold, dm.hasDeadline
	deadline := dm.deadlineAt
	dm.mu.Unlock()
	if errAtHold != nil {
		t.Errorf("在途交付拿到的是已取消的 ctx：%v ⇒ 收尾会直接被停机打断", errAtHold)
	}
	if !hasDeadline {
		t.Fatal("收尾 ctx 没有 deadline ⇒ 一条卡住的消息能让 pod 一直等到 SIGKILL")
	}
	if left := time.Until(deadline); left <= 0 {
		t.Errorf("收尾预算在交付还在途时就用完了（剩 %v）", left)
	}
}

// ── 7．解码：契约判据 ───────────────────────────────────────────────────────

func TestDecodeRejectsContractViolations(t *testing.T) {
	for _, tc := range []struct {
		name   string
		mut    func(*Delivery)
		reason string
	}{
		{"键不在表里", func(d *Delivery) {
			d.RoutingKey = "candidate.observed"
			d.MessageType = "candidate_observed"
		}, ReasonUnknownRoutingKey},
		{"通配键本身不能进门", func(d *Delivery) {
			d.RoutingKey = "candidate.*"
			d.MessageType = "candidate__"
		}, ReasonUnknownRoutingKey},
		{"命令面的键不归属这里", func(d *Delivery) {
			d.RoutingKey = "ai.pattern.requested"
			d.MessageType = "ai_pattern_requested"
		}, ReasonUnknownRoutingKey},
		{"content_type 空着也不许猜", func(d *Delivery) { d.ContentType = "" }, ReasonContentType},
		{"content_type 是 protobuf（那是 O1 的事）", func(d *Delivery) {
			d.ContentType = "application/protobuf"
		}, ReasonContentType},
		{"body 超过上限", func(d *Delivery) {
			// 夹具按**生产上限之上**造，而不是把上限调小去迁就一条 4KB 的 body：
			// 这条要判的是对面真投来一条 64KB+ 的载荷时，这里一条字节都不解（14 §9）。
			d.Body = []byte(strings.Repeat("x", DefaultMaxBodyBytes+1))
		}, ReasonBodyTooLarge},
		{"message_type 与键不同源", func(d *Delivery) { d.MessageType = "candidate_insight" }, ReasonMessageTypeMismatch},
		{"message_type 空着", func(d *Delivery) { d.MessageType = "" }, ReasonMessageTypeMismatch},
		{"schema 版本不认识", func(d *Delivery) {
			d.Headers[HeaderSchemaVersion] = "2"
		}, ReasonSchemaVersion},
		{"两侧一起升到 2（header 那道门的**唯一**独立证人）", func(d *Delivery) {
			// 2026-10-16 变异自证查出的假绿：上面两条只改 header，体内仍是夹具的 "1"，
			// 于是 `checkBodyVersion`（candidate_decode.go:169）用**同一个 reason** 把它们兜住——
			// 把 :134 那道门整个摘掉，这一包仍全绿。版本门真正的用户后果发生在两侧同升
			// （对面 proto 升级、header 跟着 body 一起写 2），那时只剩这道门认得它，
			// 所以这一格才是这道门的证人。
			d.Headers[HeaderSchemaVersion] = "2"
			d.Body = []byte(`{"schemaVersion":"2","kind":"KIND_PATTERN","patternType":"evening_rumination",` +
				`"fingerprint":"fp-1","confidence":0.7,"safety":{"status":"SAFETY_STATUS_OK"}}`)
		}, ReasonSchemaVersion},
		{"schema 版本是空格", func(d *Delivery) {
			d.Headers[HeaderSchemaVersion] = "  "
		}, ReasonSchemaVersion},
		{"schema 版本读不出十进制串", func(d *Delivery) {
			// 2026-10-16 更正：这一格原来写的是 `d.Headers[HeaderSchemaVersion] = 1` 并期望拒收，
			// 而 R5-③ 的真进程互操作闸实测到 int 恰恰是**我们自己的发布侧**写上线的形态
			// （`amqp/publisher.go:130` 放 `msg.SchemaVersion`）。把数字当非法，等于让
			// 「Go 发、Go 收」那一跳（重放路径）永远进不了领域入口，而且是静默上账。
			// 数字形态的归一由 TestVersionHeaderAcceptsIntWireForm 钉；这里留的是
			// 「这一格根本不是版本」那一类。
			d.Headers[HeaderSchemaVersion] = map[string]any{"v": 1}
		}, ReasonSchemaVersion},
		{"header 缺 user", func(d *Delivery) { delete(d.Headers, HeaderUserID) }, ReasonMissingUserID},
		{"header 的 user 是空白", func(d *Delivery) { d.Headers[HeaderUserID] = "   " }, ReasonMissingUserID},
		{"header 的 user 不是字符串", func(d *Delivery) {
			d.Headers[HeaderUserID] = 42
		}, ReasonMissingUserID},
		{"body 不是 JSON", func(d *Delivery) { d.Body = []byte("plain text") }, ReasonNotJSON},
		{"JSON 合法但字段不认识", func(d *Delivery) {
			d.Body = []byte(`{"kind":"KIND_PATTERN","unknownField":1}`)
		}, ReasonPayloadInvalid},
		{"提案自报 reserved 的 user_id", func(d *Delivery) {
			d.Body = []byte(`{"kind":"KIND_PATTERN","userId":"attacker"}`)
		}, ReasonPayloadInvalid},
		{"体内 kind 与键不一致", func(d *Delivery) {
			d.Body = []byte(`{"kind":"KIND_INSIGHT","statement":"x","safety":{"status":"SAFETY_STATUS_OK"}}`)
		}, ReasonKindMismatch},
		{"体内没写 kind（＝UNSPECIFIED）", func(d *Delivery) {
			d.Body = []byte(`{"statement":"x"}`)
		}, ReasonKindMismatch},
		{"evidence 的 metadata 不是 JSON", func(d *Delivery) {
			d.RoutingKey = "candidate.evidence"
			d.MessageType = "candidate_evidence"
			d.Body = []byte(`{"sourceKind":"SOURCE_KIND_USER_STATEMENT","metadataJson":"{oops"}`)
		}, ReasonMetadataNotJSON},
		{"体内版本与 header 版本不一致", func(d *Delivery) {
			d.Headers[HeaderSchemaVersion] = SupportedSchemaVersion
			d.Body = []byte(`{"schemaVersion":"2","kind":"KIND_PATTERN"}`)
		}, ReasonSchemaVersion},
	} {
		t.Run(tc.name, func(t *testing.T) {
			d := delivery(nil, tc.mut)
			call, rej := Decode(d, DefaultMaxBodyBytes)
			if call != nil {
				t.Fatalf("这条本该出局，却拿到了领域调用：%v", rej)
			}
			if rej == nil || rej.Code != tc.reason {
				t.Fatalf("reason=%v 期望 %q", rej, tc.reason)
			}
			if strings.TrimSpace(rej.Detail) == "" {
				t.Error("永久拒绝不带可诊断的 detail ⇒ inbox 账上只有一个码，排查的人看不出为什么")
			}
			if !errors.Is(*rej, domain.ErrPermanentMessage) {
				t.Errorf("拒绝结论必须满足 errors.Is(ErrPermanentMessage)，否则 classifyFailure 认不出：%v", rej)
			}
		})
	}
}

func TestErrUnknownSignalBecomesPermanent(t *testing.T) {
	// 枚举拼写类非法走的是**已有哨兵** domain.ErrUnknownSignal（evidence_score.go:233）：
	// 它重投一万次也不会变成合法，所以必须转永久去留痕，而不是在阶梯上无限撞库。
	d := delivery(nil, func(d *Delivery) {
		d.RoutingKey = "candidate.evidence"
		d.MessageType = "candidate_evidence"
		d.Body = []byte(`{"sourceKind":"SOURCE_KIND_USER_STATEMENT"}`)
	})
	call, rej := Decode(d, DefaultMaxBodyBytes)
	if rej != nil {
		t.Fatalf("这条契约面合法：%v", rej)
	}
	dm := newFakeDomain()
	dm.evidenceEr = fmt.Errorf("store: %w", domain.ErrUnknownSignal)
	err := call(context.Background(), dm)
	var pr domain.PermanentRejection
	if !errors.As(err, &pr) || pr.Code != ReasonUnknownDomainValue {
		t.Fatalf("ErrUnknownSignal 没被转成永久拒绝：%v", err)
	}

	// 反向：真·瞬态不许被猜成永久（猜错＝把一次 DB 抖动永久记账）。
	dm2 := newFakeDomain()
	dm2.admitErr = errors.New("pgx: conn closed")
	ok := delivery(nil, nil)
	call2, rej2 := Decode(ok, DefaultMaxBodyBytes)
	if rej2 != nil {
		t.Fatalf("合法载荷：%v", rej2)
	}
	if err := call2(context.Background(), dm2); err == nil || errors.Is(err, domain.ErrPermanentMessage) {
		t.Errorf("DB 抖动被读成永久拒绝：%v ⇒ 阶梯上的重投才是它的正确归宿", err)
	}
}

// ── 8．解码：字段映射 ───────────────────────────────────────────────────────

func TestDecodeMapsPatternCandidate(t *testing.T) {
	d := delivery(nil, func(d *Delivery) {
		d.Body = []byte(`{
		  "schemaVersion":"1","kind":"KIND_PATTERN","patternType":"evening_rumination",
		  "scope":"daily","description":"晚上反刍","fingerprint":"fp-9","confidence":0.71,
		  "detectorVersion":"det-3","evidenceIds":["e1","e2"],"patternIds":["p1"],
		  "windowFrom":"2026-10-01T00:00:00Z","windowTo":"2026-10-08T00:00:00Z",
		  "generationRunId":"run-1","generationModel":"glm","generationVer":"v2",
		  "safety":{"status":"SAFETY_STATUS_CRISIS","categories":["self_harm"]}
		}`)
		d.Headers[HeaderCausationID] = "cmd-9"
	})
	call, rej := Decode(d, DefaultMaxBodyBytes)
	if rej != nil {
		t.Fatalf("合法载荷被拒：%+v", rej)
	}
	dm := newFakeDomain()
	if err := call(context.Background(), dm); err != nil {
		t.Fatalf("Admit: %v", err)
	}
	if len(dm.admits) != 1 {
		t.Fatalf("Admit 跑了 %d 次", len(dm.admits))
	}
	c := dm.admits[0]
	if c.UserID != "user-42" {
		t.Errorf("归属 user_id=%q ⇒ 只能从 x-user-id 取（体内那列是 reserved）", c.UserID)
	}
	if c.Kind != domain.KindPattern || c.PatternType != "evening_rumination" ||
		c.Scope != "daily" || c.Fingerprint != "fp-9" || c.Description != "晚上反刍" {
		t.Errorf("pattern 的列面错位：%+v", c)
	}
	if c.Confidence != 0.71 || c.DetectorVersion != "det-3" ||
		strings.Join(c.EvidenceIDs, ",") != "e1,e2" || strings.Join(c.PatternIDs, ",") != "p1" {
		t.Errorf("重复/引用面错位：%+v", c)
	}
	if !c.HasWindow || !c.WindowFrom.Equal(time.Date(2026, 10, 1, 0, 0, 0, 0, time.UTC)) ||
		!c.WindowTo.Equal(time.Date(2026, 10, 8, 0, 0, 0, 0, time.UTC)) {
		t.Errorf("窗口没搬对：has=%v from=%v to=%v", c.HasWindow, c.WindowFrom, c.WindowTo)
	}
	if c.GenerationRunID != "run-1" || c.GenerationModel != "glm" || c.GenerationVer != "v2" {
		t.Errorf("留痕三列错位：%+v", c)
	}
	if c.Safety == nil || c.Safety.Status != domain.SafetyCrisis ||
		strings.Join(c.Safety.Categories, ",") != "self_harm" {
		t.Errorf("Safety 没搬对：%+v", c.Safety)
	}
	if !c.IsSensitive() {
		t.Error("crisis＋self_harm 该判敏感域（candidate.go 的 IsSensitive）")
	}
	if c.PersonalModelID != "" {
		t.Errorf("PersonalModelID 由领域侧定位，提案不许带：%q", c.PersonalModelID)
	}
}

func TestDecodeWindowNeedsBothEnds(t *testing.T) {
	// proto3 的 0 值陷阱（closenai_ai.proto:221-224）：只有 from 没有 to ＝没有窗口，
	// 而不是「窗口右端是 epoch」。
	for name, body := range map[string]string{
		"只有 from": `{"kind":"KIND_PATTERN","fingerprint":"f","windowFrom":"2026-10-01T00:00:00Z"}`,
		"只有 to":   `{"kind":"KIND_PATTERN","fingerprint":"f","windowTo":"2026-10-08T00:00:00Z"}`,
		"都没有":     `{"kind":"KIND_PATTERN","fingerprint":"f"}`,
	} {
		d := delivery(nil, func(d *Delivery) { d.Body = []byte(body) })
		call, rej := Decode(d, DefaultMaxBodyBytes)
		if rej != nil {
			t.Fatalf("%s：%+v", name, rej)
		}
		dm := newFakeDomain()
		if err := call(context.Background(), dm); err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		if dm.admits[0].HasWindow {
			t.Errorf("%s ⇒ HasWindow 被置真了，领域侧会拿一个 epoch 端点去做窗口判定", name)
		}
	}
}

func TestSafetyUnspecifiedFailsClosed(t *testing.T) {
	// D-8：任何一侧都不许把 UNSPECIFIED 读成 ok。落 nil ⇒ Gate 第 6 关按「判定缺失」fail-closed。
	for name, body := range map[string]string{
		"完全没有 safety 字段": `{"kind":"KIND_FACT","subjectKey":"k","predicate":"p","objectValue":"o"}`,
		"写了 UNSPECIFIED": `{"kind":"KIND_FACT","subjectKey":"k","safety":{"status":"SAFETY_STATUS_UNSPECIFIED"}}`,
		"写了空 safety":     `{"kind":"KIND_FACT","subjectKey":"k","safety":{}}`,
	} {
		d := delivery(nil, func(d *Delivery) {
			d.RoutingKey = "candidate.fact"
			d.MessageType = "candidate_fact"
			d.Body = []byte(body)
		})
		call, rej := Decode(d, DefaultMaxBodyBytes)
		if rej != nil {
			t.Fatalf("%s：%+v", name, rej)
		}
		dm := newFakeDomain()
		if err := call(context.Background(), dm); err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		if dm.admits[0].Safety != nil {
			t.Errorf("%s ⇒ Safety=%+v，等于让 UNSPECIFIED 当放行", name, dm.admits[0].Safety)
		}
	}
}

func TestDecodeMapsEvidenceCandidate(t *testing.T) {
	d := delivery(nil, func(d *Delivery) {
		d.RoutingKey = "candidate.evidence"
		d.MessageType = "candidate_evidence"
		d.Body = []byte(`{
		  "sourceKind":"SOURCE_KIND_USER_STATEMENT",
		  "source":{"sourceTable":"chat_messages","sourceId":"msg-7","occurredAt":"2026-10-05T09:00:00Z"},
		  "evidenceType":"direct","role":"supporting","observation":"原话","excerpt":"我最近睡不好",
		  "metadataJson":"{\"channel\":\"app\"}","explicitness":"EXPLICITNESS_EXPLICIT",
		  "specificity":"SPECIFICITY_CONTEXTUAL","generationRunId":"run-9",
		  "proposedIndependentIds":["e3","e4"]
		}`)
	})
	call, rej := Decode(d, DefaultMaxBodyBytes)
	if rej != nil {
		t.Fatalf("合法载荷被拒：%+v", rej)
	}
	dm := newFakeDomain()
	if err := call(context.Background(), dm); err != nil {
		t.Fatalf("AdmitEvidence: %v", err)
	}
	c := dm.evidences[0]
	if c.UserID != "user-42" {
		t.Errorf("归属错位：%q", c.UserID)
	}
	if string(c.SourceKind) != "user_statement" || c.SourceTable != "chat_messages" ||
		c.SourceID != "msg-7" || !c.OccurredAt.Equal(time.Date(2026, 10, 5, 9, 0, 0, 0, time.UTC)) {
		t.Errorf("source 指针面错位（枚举要去掉 SOURCE_KIND_ 前缀并小写）：%+v", c)
	}
	if string(c.Explicitness) != "explicit" || string(c.Specificity) != "contextual" {
		t.Errorf("算分输入错位：explicitness=%q specificity=%q", c.Explicitness, c.Specificity)
	}
	if string(c.Metadata) != `{"channel":"app"}` {
		t.Errorf("metadata 要**原样**进 evidence.metadata_json，实收 %q", c.Metadata)
	}
	if strings.Join(c.ProposedIndependentIDs, ",") != "e3,e4" {
		t.Errorf("提案侧的独立重复引用要搬过去给 Go 核验（一个都不采信≠不看）：%v", c.ProposedIndependentIDs)
	}
	if c.GenerationRunID != "run-9" || c.EvidenceType != "direct" || c.Role != "supporting" {
		t.Errorf("其余列面错位：%+v", c)
	}
}

func TestUnspecifiedEnumsBecomeEmptyNotAGuessedValue(t *testing.T) {
	// 去前缀后如果是 UNSPECIFIED ⇒ 空串。空串是**领域侧已经认得的失败输入**
	// （checkEvidenceCandidate 带 ErrUnknownSignal 回来），不是「未知的一种新写法」。
	d := delivery(nil, func(d *Delivery) {
		d.RoutingKey = "candidate.evidence"
		d.MessageType = "candidate_evidence"
		d.Body = []byte(`{"sourceKind":"SOURCE_KIND_UNSPECIFIED","explicitness":"EXPLICITNESS_UNSPECIFIED"}`)
	})
	call, rej := Decode(d, DefaultMaxBodyBytes)
	if rej != nil {
		t.Fatalf("契约面不管枚举取值合不合法，那是领域侧的判定：%+v", rej)
	}
	dm := newFakeDomain()
	if err := call(context.Background(), dm); err != nil {
		t.Fatalf("call: %v", err)
	}
	c := dm.evidences[0]
	if c.SourceKind != "" || c.Explicitness != "" || c.Specificity != "" {
		t.Errorf("UNSPECIFIED 被映射成了值：%+v ⇒ 领域侧那关会被绕过去", c)
	}
}

func TestDecodeMapsProactiveDraft(t *testing.T) {
	d := delivery(nil, func(d *Delivery) {
		d.RoutingKey = "candidate.proactive"
		d.MessageType = "candidate_proactive"
		d.Body = []byte(`{
		  "candidateId":"pc-1","triggerType":"TRIGGER_TYPE_MILESTONE",
		  "triggerSource":"TRIGGER_SOURCE_NEW_PATTERN","message":"这周做到了","confidence":0.62,
		  "evidenceIds":["e1"],"refIds":["i1","i2"],"anchorRefType":"REF_TYPE_GOAL",
		  "anchorRefId":"goal-3","scheduledAt":"2026-10-06T20:00:00Z",
		  "expiresAt":"2026-10-07T20:00:00Z","journeyEventId":"je-5",
		  "generationRunId":"run-2","generationModel":"glm","generationVer":"v1",
		  "safety":{"status":"SAFETY_STATUS_OK"}
		}`)
	})
	call, rej := Decode(d, DefaultMaxBodyBytes)
	if rej != nil {
		t.Fatalf("合法载荷被拒：%+v", rej)
	}
	dm := newFakeDomain()
	if err := call(context.Background(), dm); err != nil {
		t.Fatalf("AdmitProactive: %v", err)
	}
	c := dm.proactive[0]
	if c.ID != "pc-1" {
		t.Errorf("candidateId 是四条提案里唯一的提案侧 id（幂等重跑位），实收 %q", c.ID)
	}
	if string(c.TriggerType) != "milestone" || string(c.TriggerSource) != "new_pattern" ||
		string(c.AnchorRefType) != "goal" {
		t.Errorf("枚举映射错位：%+v", c)
	}
	if c.AnchorRefID != "goal-3" || !c.HasScheduledAt || !c.HasExpiresAt {
		t.Errorf("锚点与显式在场性错位：%+v", c)
	}
	// 锚点**时刻**不在提案里：proto 没有 anchor_at，Go 从库里取那一行。
	if c.Message != "这周做到了" || c.Confidence != 0.62 ||
		strings.Join(c.EvidenceIDs, ",") != "e1" || strings.Join(c.RefIDs, ",") != "i1,i2" {
		t.Errorf("正文与引用面错位：%+v", c)
	}
	if c.Safety == nil || c.Safety.Status != domain.SafetyOK {
		t.Errorf("显式 SAFETY_STATUS_OK 要落成非 nil：%+v", c.Safety)
	}
}

func TestProactiveTimestampsArePresenceFlagged(t *testing.T) {
	d := delivery(nil, func(d *Delivery) {
		d.RoutingKey = "candidate.proactive"
		d.MessageType = "candidate_proactive"
		d.Body = []byte(`{"triggerType":"TRIGGER_TYPE_MILESTONE","message":"m"}`)
	})
	call, rej := Decode(d, DefaultMaxBodyBytes)
	if rej != nil {
		t.Fatalf("%+v", rej)
	}
	dm := newFakeDomain()
	if err := call(context.Background(), dm); err != nil {
		t.Fatalf("call: %v", err)
	}
	c := dm.proactive[0]
	if c.HasScheduledAt || c.HasExpiresAt {
		t.Errorf("提案没给时间戳却带上了在场性 ⇒ proactive.go:210-213 会把 epoch 当有效期")
	}
}

func TestHeaderBytesAreAcceptedButNumbersAreNot(t *testing.T) {
	// aio-pika 那边 header 值会以 bytes 上线，Go 的 amqp091 解出来就是 `[]byte`；
	// 而数字**不是**字符串的另一种写法，兜底 `fmt.Sprint` 会让「x-user-id: 42」
	// 变成一个库里不存在的人（表现是归属校验通过了，校验的却是个不存在的人）。
	ok := delivery(nil, func(d *Delivery) {
		d.Headers[HeaderUserID] = []byte("user-77")
		d.Headers[HeaderSchemaVersion] = []byte(SupportedSchemaVersion)
	})
	if _, rej := Decode(ok, DefaultMaxBodyBytes); rej != nil {
		t.Errorf("[]byte 的 header 该认：%+v", rej)
	}
	if got := ok.HeaderString(HeaderUserID); got != "user-77" {
		t.Errorf("HeaderString 没解出 []byte：%q", got)
	}
	for name, hdr := range map[string]any{
		"数字":   int64(42),
		"布尔":   true,
		"嵌套":   map[string]any{"id": "user-77"},
		"字节别名": json.RawMessage("user-77"),
	} {
		bad := delivery(nil, func(d *Delivery) { d.Headers[HeaderUserID] = hdr })
		if _, rej := Decode(bad, DefaultMaxBodyBytes); rej == nil || rej.Code != ReasonMissingUserID {
			t.Errorf("%s 类型的 header 该按缺失处理，实收 %+v", name, rej)
		}
	}
}

// TestVersionHeaderAcceptsIntWireForm 钉住 2026-10-16 真 broker × 真进程实测出来的那一格：
// `x-schema-version` 在两侧写法不同值（对面 `contract.py:446` 写 str，我们自己的
// `amqp/publisher.go:130` 写 int），对面读侧两种都吃。这边只吃 str 的话，
// 「Go 发、Go 收」那一跳（重放路径）会**静默**变成 unsupported_schema_version——
// 不报错、只上账，所以必须有一条能被打红的测。
func TestVersionHeaderAcceptsIntWireForm(t *testing.T) {
	for name, hdr := range map[string]any{
		"int":    1,
		"int32":  int32(1),
		"int64":  int64(1),
		"uint32": uint32(1),
		"uint64": uint64(1),
		"字符串":    "1",
		"字节":     []byte("1"),
		"两侧异型":   int64(1),
		"字符串别名":  json.Number("1"),
		"布尔":     true,
		"浮点":     float64(1),
		"嵌套":     map[string]any{"v": 1},
	} {
		d := delivery(nil, func(dd *Delivery) { dd.Headers[HeaderSchemaVersion] = hdr })
		got := d.HeaderVersion(HeaderSchemaVersion)
		wantInt := strings.HasPrefix(name, "int") || strings.HasPrefix(name, "uint")
		switch {
		case wantInt && got != SupportedSchemaVersion:
			t.Errorf("%s 类型的版本该归成 %q，实得 %q", name, SupportedSchemaVersion, got)
		case name == "字符串" && got != "1":
			t.Errorf("字符串版本没读出：%q", got)
		case name == "字节" && got != "1":
			t.Errorf("字节版本没读出：%q", got)
		case name == "两侧异型" && got != "1":
			t.Errorf("int64 版本没读出：%q", got)
		case (name == "字符串别名" || name == "布尔" || name == "浮点" || name == "嵌套") && got != "":
			t.Errorf("%s 不该被读成版本，实得 %q（吃了就等于把拼写错误认成另一种合法写法）", name, got)
		}
	}

	// 端到端：只有 header 是数字、体内不写版本的一条候选必须能进门（不是被拒）。
	d := delivery(nil, func(dd *Delivery) {
		dd.Headers[HeaderSchemaVersion] = int64(1)
		body := map[string]any{"kind": "KIND_PATTERN", "patternType": "temporal_rhythm",
			"description": "d", "fingerprint": "f", "evidenceIds": []string{"e-1"},
			"generationRunId": "g-1", "safety": map[string]any{"status": "SAFETY_STATUS_OK"}}
		raw, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		dd.Body = raw
	})
	if _, rej := Decode(d, DefaultMaxBodyBytes); rej != nil {
		t.Errorf("数字形态的 x-schema-version 被判成契约级非法：%+v", rej)
	}

	// 体内版本与数字 header 不一致时仍然要拒（归一之后才比较，不是放宽到不比）。
	d2 := delivery(nil, func(dd *Delivery) { dd.Headers[HeaderSchemaVersion] = int64(2) })
	if _, rej := Decode(d2, DefaultMaxBodyBytes); rej == nil || rej.Code != ReasonSchemaVersion {
		t.Errorf("header 是 int64(2) 时该按不支持的版本拒，实收 %+v", rej)
	}
}

// ── fake：真 Service 下面的那层事务 ─────────────────────────────────────────

// fakeTransactor 只做 `store/canonical_repo.go` 的 InTx 形状：事务内的 inbox 写入
// 在 fn 报错时**整体丢弃**（18 §9 case 1），成功才提交；RecordInboxFailure 独立写在账上。
// 它带出来的 Store 只实现 inbox 那三格，别的方法被调用直接 panic——
// 「契约级非法不许碰领域写面」这条就因此是可判的。
type fakeTransactor struct {
	mu        sync.Mutex
	committed map[string]string
	failures  []domain.InboxFailureRow
}

func newFakeTransactor() *fakeTransactor {
	return &fakeTransactor{committed: map[string]string{}}
}

type inboxStore struct {
	domain.Store // 其余方法一旦被调用即 panic：那正是本用例要挡的形态
	d            *fakeTransactor
	owned        map[string]string
}

func inboxKey(messageID, consumer string) string { return consumer + "|" + messageID }

func (s *inboxStore) InsertInbox(_ context.Context, row domain.InboxRow) (bool, error) {
	k := inboxKey(row.MessageID, row.Consumer)
	if _, ok := s.d.committed[k]; ok {
		return false, nil
	}
	if _, ok := s.owned[k]; ok {
		return false, nil
	}
	s.owned[k] = "pending"
	return true, nil
}

func (s *inboxStore) GetInboxStatus(_ context.Context, messageID, consumer string) (domain.InboxStatus, error) {
	k := inboxKey(messageID, consumer)
	if v, ok := s.d.committed[k]; ok {
		return domain.InboxStatus{Status: v, AttemptCount: 1, Found: true}, nil
	}
	if v, ok := s.owned[k]; ok {
		return domain.InboxStatus{Status: v, AttemptCount: 1, Found: true}, nil
	}
	return domain.InboxStatus{Status: "", Found: false}, nil
}

func (s *inboxStore) MarkInboxProcessed(_ context.Context, messageID, consumer string) error {
	s.owned[inboxKey(messageID, consumer)] = "processed"
	return nil
}

func (f *fakeTransactor) InTx(ctx context.Context, fn func(context.Context, domain.Store) error) error {
	tx := &inboxStore{d: f, owned: map[string]string{}}
	err := fn(ctx, tx)
	f.mu.Lock()
	defer f.mu.Unlock()
	if err != nil {
		return err // 回滚：tx 里的 inbox 行不落账（18 §9）
	}
	for k, v := range tx.owned {
		f.committed[k] = v
	}
	return nil
}

func (f *fakeTransactor) RecordInboxFailure(_ context.Context, row domain.InboxFailureRow) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.failures = append(f.failures, row)
	// 真实现里这条留痕同时占掉幂等位，否则同一条消息重投还能再撞一次。
	f.committed[inboxKey(row.MessageID, row.Consumer)] = "failed"
	return nil
}

func (f *fakeTransactor) failureCodes() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := make([]string, 0, len(f.failures))
	for _, r := range f.failures {
		out = append(out, r.ErrorCode)
	}
	return out
}

// ── 小工具 ──────────────────────────────────────────────────────────────────

func waitFor(t *testing.T, budget time.Duration, cond func() bool, what string) {
	t.Helper()
	deadline := time.Now().Add(budget)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("等 %v 之后仍然不成立：%s", budget, what)
}

func (s *fakeSession) consumeCount() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.consumes
}

func (s *fakeSession) declareCount() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.declares
}

func (s *fakeSession) closeCount() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.closes
}

func (s *fakeSession) lastQueue() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.queues) == 0 {
		return ""
	}
	return s.queues[len(s.queues)-1]
}

func (s *fakeSession) lastPrefetch() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.prefetch) == 0 {
		return 0
	}
	return s.prefetch[len(s.prefetch)-1]
}
