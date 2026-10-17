// 批 3 D-G14-② 的闸：候选入库之后那条「请对面算下一段」的链。
//
// 这一段是 `RequestAITask` 在全仓第一个生产调用点（此前只有 `tools/r5interop`
// 那台取证驱动器调过，那是第三条路、不算现网）。它判的五件事各自对应一种静默故障：
//   - 命令**种类与来源**对不对（写反就是把 insight 任务派给 pattern handler，能跑通但语义串台）；
//   - 命令**拿的是不是 inbox 那一笔事务**（自开事务＝候选行与命令行两笔 COMMIT，
//     半途崩溃就是「状态变了却没人去算」，而 outbox 补不回来）；
//   - 入队失败**有没有让这一笔回滚并不 ACK**（吞掉＝候选收下了、下一段永远没请求）；
//   - 重复投递**有没有只推一次**（命令 id 由来源派生，第二次同 id 会撞 UNIQUE 而报错，
//     表现是「链有时不生效」而不是「链重复」，最难查）；
//   - 契约级非法**有没有完全不推**（畸形消息一个命令都不该产）。
//
// ⚠️ 口径（不粉饰）：链表与 taskType 的字面量由 `domain/task_chain_test.go` 对账，
// 这里只判入站这一跳的行为。另外三个领域入口各自再开事务（R5b ②），所以本文件能证到的
// 上限是「命令与 inbox 同事务」，证不到「命令与候选行同事务」——第 6 条把 `admits==1`
// 显式断言下来，就是要把这个边界写成事实而不是留给读代码的人猜。
package inbound

import (
	"context"
	"errors"
	"reflect"
	"testing"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// expectedTaskID 是**独立算出来**的字面量（`shasum -a 256` 对 "task|<stage>|<来源 id>" 取前 40 位），
// 不是调 `domain.DeriveTaskOperationID` 现算——用它自己的输出当期望值就等于没测。
const (
	expectedInsightIDForCnd0001 = "task-98ad8bcbc82f31d3993e45258331083f2708bd0b"
	expectedPatternIDForCnd0002 = "task-bbfe16c547419eb0553fa917a8b786a7c23f523e"
)

// 这一组用例全部走 `Handle`，所以每条交付都必须带一个 settle 记录器：
// `Delivery.Settle` 为 nil 时 ACK/NACK 当场报错（「无法确认」），
// 而那个错误会把「链有没有推」的判据淹掉。传 nil 只在该用例本来就要看错误时用。
func newRec() *recorder { return &recorder{} }

func factDelivery(rec *recorder, id string) Delivery {
	return delivery(rec, func(d *Delivery) {
		d.RoutingKey = "candidate.fact"
		d.MessageType = "candidate_fact"
		d.MessageID = id
		d.Body = []byte(`{"kind":"KIND_FACT","subjectKey":"sk-1","predicate":"is","objectValue":"晚上睡不着"}`)
	})
}

func insightDelivery(rec *recorder, id string) Delivery {
	return delivery(rec, func(d *Delivery) {
		d.RoutingKey = "candidate.insight"
		d.MessageType = "candidate_insight"
		d.MessageID = id
		d.Body = []byte(`{"kind":"KIND_INSIGHT","statement":"夜里反刍白天回避","claimType":"pattern_link"}`)
	})
}

func evidenceDelivery(rec *recorder, id string) Delivery {
	return delivery(rec, func(d *Delivery) {
		d.RoutingKey = "candidate.evidence"
		d.MessageType = "candidate_evidence"
		d.MessageID = id
		d.Body = []byte(`{"sourceKind":"SOURCE_KIND_USER_STATEMENT","metadataJson":"{}"}`)
	})
}

// ── fake 的读侧：链这一组用例只读计数与记录，写入面在 inbound_test.go 的 fakeDomain ──

func (f *fakeDomain) taskCalls() []domain.AITaskRequest {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]domain.AITaskRequest{}, f.tasks...)
}

func (f *fakeDomain) inboxStores() []domain.Store {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]domain.Store{}, f.taskStores...)
}

func (f *fakeDomain) admitCalls() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.admits)
}

func (f *fakeDomain) evidenceCalls() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.evidences)
}

func (f *fakeDomain) proactiveCalls() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.proactive)
}

// TestPatternCandidateEnqueuesInsightTask 是链的第二跳（pattern → 请对面起草洞察）。
func TestPatternCandidateEnqueuesInsightTask(t *testing.T) {
	dm := newFakeDomain()
	r := testRunner(t, dm, onSwitch(), nil)
	rec := &recorder{}

	res, err := r.Handle(context.Background(), delivery(rec, nil)) // 默认这条就是 candidate.pattern
	if err != nil || res.Status != domain.ConsumeProcessed {
		t.Fatalf("首投应 processed：res=%+v err=%v", res, err)
	}
	tasks := dm.taskCalls()
	if len(tasks) != 1 {
		t.Fatalf("pattern 入库后应入队 1 条命令，实收 %d ⇒ 链在这一跳断了", len(tasks))
	}
	got := tasks[0]
	if got.Kind != domain.AITaskInsight {
		t.Errorf("下一段应是 %s，实收 %s", domain.AITaskInsight, got.Kind)
	}
	if got.UserID != "user-42" {
		t.Errorf("命令的归属=%q ⇒ 只能从 x-user-id 取，体内没有第二处", got.UserID)
	}
	if got.OperationID != expectedInsightIDForCnd0001 {
		t.Errorf("命令 id=%q，期望 %q ⇒ 派生规则变了（重试会换新 id＝幂等键失效）",
			got.OperationID, expectedInsightIDForCnd0001)
	}
	wantBody := `{"sourceKind":"pattern","sourceMessageId":"cnd-0001","taskType":"TASK_TYPE_INSIGHT_DRAFT"}`
	if got.Payload != wantBody {
		t.Errorf("命令体=%q，期望 %q", got.Payload, wantBody)
	}
	if st := r.Stats(); st.FollowUpsEnqueued != 1 || st.FollowUpSkipped != 0 || st.FollowUpErrors != 0 {
		t.Errorf("链的计数面错位：%+v", st)
	}
}

// TestFactCandidateEnqueuesPatternTask 是链的第一跳（fact → 请对面找模式）。
// 单独一条而不是塞进上一条的参数表：两跳的 stage 与来源 id 都不同，
// 参数表写错一侧的期望值会让另一侧跟着假绿（这条链最容易犯的错就是两跳互换）。
func TestFactCandidateEnqueuesPatternTask(t *testing.T) {
	dm := newFakeDomain()
	r := testRunner(t, dm, onSwitch(), nil)

	if _, err := r.Handle(context.Background(), factDelivery(newRec(), "cnd-0002")); err != nil {
		t.Fatalf("fact 首投应成功：%v", err)
	}
	tasks := dm.taskCalls()
	if len(tasks) != 1 {
		t.Fatalf("应入队 1 条命令，实收 %d", len(tasks))
	}
	if tasks[0].Kind != domain.AITaskPattern {
		t.Errorf("fact 的下一段应是 %s，实收 %s", domain.AITaskPattern, tasks[0].Kind)
	}
	if tasks[0].OperationID != expectedPatternIDForCnd0002 {
		t.Errorf("命令 id=%q，期望 %q", tasks[0].OperationID, expectedPatternIDForCnd0002)
	}
	if want := `{"sourceKind":"fact","sourceMessageId":"cnd-0002","taskType":"TASK_TYPE_PATTERN_DETECT"}`; tasks[0].Payload != want {
		t.Errorf("命令体=%q，期望 %q", tasks[0].Payload, want)
	}
	if admits := dm.admitCalls(); admits != 1 {
		t.Errorf("Admit 跑了 %d 次 ⇒ 命令不能替代领域入口", admits)
	}
}

// TestFollowUpWritesIntoTheInboxTransaction 钉「命令与 inbox 同一笔事务」：
// 传进来的 Store 必须**正是** fakeInboxStore{} 这个身份哨兵——它内嵌一个永远为 nil 的
// domain.Store，被当真 Store 调用会当场 panic，所以「传了但没真用」也判得出来。
func TestFollowUpWritesIntoTheInboxTransaction(t *testing.T) {
	dm := newFakeDomain()
	r := testRunner(t, dm, onSwitch(), nil)
	if _, err := r.Handle(context.Background(), delivery(newRec(), nil)); err != nil {
		t.Fatalf("Handle: %v", err)
	}
	stores := dm.inboxStores()
	if len(stores) != 1 {
		t.Fatalf("命令跑了 %d 次，期望 1 次", len(stores))
	}
	if _, ok := stores[0].(fakeInboxStore); !ok {
		t.Fatalf("RequestAITask 拿到的 Store 是 %s，不是 inbox 那一笔事务 ⇒ "+
			"「自开事务」会把候选行与命令行拆成两笔 COMMIT，半途崩溃就补不回来",
			reflect.TypeOf(stores[0]))
	}
}

// TestInsightCandidateEndsTheChain：洞察是这条链的产物，再往下没有下一段。
// 这一格判的是「别把链接成环」在入站这一侧的表现。
func TestInsightCandidateEndsTheChain(t *testing.T) {
	dm := newFakeDomain()
	r := testRunner(t, dm, onSwitch(), nil)
	rec := &recorder{}
	res, err := r.Handle(context.Background(), insightDelivery(rec, "cnd-0003"))
	if err != nil || res.Status != domain.ConsumeProcessed {
		t.Fatalf("insight 首投应 processed：res=%+v err=%v", res, err)
	}
	if got := len(dm.taskCalls()); got != 0 {
		t.Fatalf("insight 不该再请下一段，实收 %d 条命令 ⇒ 链上多了一条没登记的边", got)
	}
	if st := r.Stats(); st.FollowUpSkipped != 1 || st.FollowUpsEnqueued != 0 {
		t.Errorf("没有下一段要走 skipped 计数（这是正常终态，不是错误）：%+v", st)
	}
	if got := rec.seen(); len(got) != 1 || !got[0].ack {
		t.Errorf("终态照样要 ACK：%v", got)
	}
}

// TestEvidenceCandidateNeverTouchesTheChain：evidence／proactive 不在 `CandidateKinds` 里，
// 于是 `admittedKind` 天然回 false。这条是「别给没登记的候选形态发明触发面」的判据。
func TestEvidenceCandidateNeverTouchesTheChain(t *testing.T) {
	dm := newFakeDomain()
	r := testRunner(t, dm, onSwitch(), nil)
	if _, err := r.Handle(context.Background(), evidenceDelivery(newRec(), "cnd-0004")); err != nil {
		t.Fatalf("evidence 首投应成功：%v", err)
	}
	if got := len(dm.taskCalls()); got != 0 {
		t.Fatalf("evidence 入站却产了 %d 条命令 ⇒ 它的触发面在 service_evidence.go，不在这条链上", got)
	}
	if ev := dm.evidenceCalls(); ev != 1 {
		t.Errorf("AdmitEvidence 跑了 %d 次，期望 1 次", ev)
	}
	if st := r.Stats(); st.FollowUpSkipped != 1 || st.FollowUpErrors != 0 {
		t.Errorf("计数面错位：%+v", st)
	}
}

// TestFollowUpFailureRollsBackWithoutAck 钉「入队失败必须外传」：
// 命令没写进去＝这一笔没做完，要回滚＋不 ACK＋交给阶梯重投（14 §8 的 requeue=false 层）。
// 把错误咽掉的话账面上是「候选收下了」，而下一段永远没人请求——那是本仓最贵的一类静默丢失。
func TestFollowUpFailureRollsBackWithoutAck(t *testing.T) {
	dm := newFakeDomain()
	dm.taskErr = errors.New("fake: outbox 插入失败")
	r := testRunner(t, dm, onSwitch(), nil)
	rec := &recorder{}

	res, err := r.Handle(context.Background(), delivery(rec, nil))
	if err == nil {
		t.Fatal("入队失败却整个 Handle 无错 ⇒ 错误被吞了")
	}
	if res.Ack {
		t.Errorf("入队失败却 ack：%+v ⇒ 命令丢了且没人知道", res)
	}
	got := rec.seen()
	if len(got) != 1 || got[0].ack || got[0].requeue {
		t.Errorf("该 Nack(requeue=false) 交给阶梯，实收 %v", got)
	}
	st := r.Stats()
	if st.FollowUpErrors != 1 || st.FollowUpsEnqueued != 0 || st.Processed != 0 {
		t.Errorf("计数面把失败的批算成了成功：%+v", st)
	}
	// 显式把 R5b ② 的边界写成事实：领域入口在命令失败之前**确实**跑过了（它自开事务）。
	if fn, admits, _, _ := dm.counts(); fn != 1 || admits != 1 {
		t.Errorf("fn=%d admit=%d ⇒ 本文件的判据上限是「命令与 inbox 同事务」，不是「与候选行同事务」", fn, admits)
	}
}

// TestDuplicateCandidateEnqueuesFollowUpOnce：at_least_once 之下同一 id 会投两次（D-R5-6 明说
// 同一命令会跑两次 LLM），而链只能往前推一次。
func TestDuplicateCandidateEnqueuesFollowUpOnce(t *testing.T) {
	dm := newFakeDomain()
	r := testRunner(t, dm, onSwitch(), nil)
	if _, err := r.Handle(context.Background(), delivery(newRec(), nil)); err != nil {
		t.Fatalf("首投: %v", err)
	}
	if _, err := r.Handle(context.Background(), delivery(newRec(), nil)); err != nil {
		t.Fatalf("重投同一 id 不该报错：%v", err)
	}
	if got := len(dm.taskCalls()); got != 1 {
		t.Fatalf("重复投递把命令又入队了一次（%d 条）⇒ 第二次同 id 会撞 outbox 的 UNIQUE，"+
			"现网表现是「链有时不生效」", got)
	}
	if st := r.Stats(); st.FollowUpsEnqueued != 1 || st.Duplicates != 1 {
		t.Errorf("计数面错位：%+v", st)
	}
}

// TestRejectedCandidateEnqueuesNoCommand：契约级非法连领域入口都不该跑到，更不能往下推。
// 这一格防的是「把链挂在 ConsumeIdempotent 之后而不是事务里」那种写法——
// 那种写法下永久拒绝也会产出一条命令，而拒绝的那条候选根本没入库。
func TestRejectedCandidateEnqueuesNoCommand(t *testing.T) {
	dm := newFakeDomain()
	r := testRunner(t, dm, onSwitch(), nil)
	bad := delivery(newRec(), func(d *Delivery) { d.Body = []byte("{not json") })
	if _, err := r.Handle(context.Background(), bad); err == nil {
		t.Fatal("畸形载荷该报错")
	}
	if got := len(dm.taskCalls()); got != 0 {
		t.Fatalf("畸形候选产了 %d 条命令 ⇒ 没入库的东西被推下了链", got)
	}
	if st := r.Stats(); st.FollowUpsEnqueued != 0 || st.FollowUpSkipped != 0 {
		t.Errorf("解码失败时链的三格计数都该是 0：%+v", st)
	}
}

// TestProactiveCandidateAlsoStaysOffTheChain：与 evidence 同一判据的另一侧——
// `CandidateKinds` 只有三键，proactive 进来也不能推。
func TestProactiveCandidateAlsoStaysOffTheChain(t *testing.T) {
	dm := newFakeDomain()
	r := testRunner(t, dm, onSwitch(), nil)
	d := delivery(newRec(), func(d *Delivery) {
		d.RoutingKey = "candidate.proactive"
		d.MessageType = "candidate_proactive"
		d.MessageID = "cnd-0005"
		d.Body = []byte(`{
		  "candidateId":"pc-1","triggerType":"TRIGGER_TYPE_MILESTONE",
		  "triggerSource":"TRIGGER_SOURCE_NEW_PATTERN","message":"这周做到了","confidence":0.62,
		  "safety":{"status":"SAFETY_STATUS_OK"}
		}`)
	})
	if _, err := r.Handle(context.Background(), d); err != nil {
		t.Fatalf("proactive 首投应成功：%v", err)
	}
	if got := len(dm.taskCalls()); got != 0 {
		t.Fatalf("proactive 入站却产了 %d 条命令（它的触发面在 proactive gate）", got)
	}
	if pa := dm.proactiveCalls(); pa != 1 {
		t.Errorf("AdmitProactive 跑了 %d 次，期望 1 次", pa)
	}
}
