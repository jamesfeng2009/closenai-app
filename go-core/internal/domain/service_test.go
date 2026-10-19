// N3 Service 单测：Gate 的结论如何变成权威行。
//
// 这里用 fake store（同包内手写），真库行为在 internal/store 的集成测里验。
// 断言重点是三条口径：
//  1. 拒绝也留行，且 reject_reason/gate_detail 落得住（G-2）；
//  2. 只有 accept/reactivate 动 current_version（02 §32 的乐观并发前提）；
//  3. 任何写失败都必须让事务回滚——不能出现「事件写进去了、权威行没写」的半状态。
package domain

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"
)

type fakeStore struct {
	modelID     string
	modelVer    int64
	evidence    map[string]Evidence
	factHit     string
	patternID   string
	reactCount  int64
	reactivated []string

	facts    []FactRow
	patterns []PatternRow
	insights []InsightRow
	links    []string
	versions []VersionRow
	events   []EventRow

	// N4 写读面
	sources           []SourceRecordRow
	written           []EvidenceWrite
	insightSt         *InsightState
	insightEv         []EvidenceRow
	statusSet         []InsightStatusRow
	rejectionHit      string
	lookedFingerprint string
	cooldownDays      int
	readErr           error

	// T2-03 来源行归属桩：sourceOwner 是「回查到的那一行属于谁」，
	// 置空即「行查不到」（走 ErrUnknownSourceRow），改成别人的 id 即越权。
	// sourceErr 是**读库故障**，它必须与「查不到」分开造，否则瞬态与永久两格判据会互相冒充。
	sourceOwner   string
	sourceErr     error
	sourceLookups []string

	// T2-06 传播面桩：sourceRecord 是那条登记行（Found=false 即「从没登记过」），
	// evidenceForSource 是它支撑的 evidence id，deletedMarks/restores 记两次翻转，
	// failPropagateFor 让**指定那一条** evidence 的传播失败（恢复支路的靶）。
	sourceRecord      SourceRecordState
	evidenceForSource []string
	deletedMarks      int
	restores          int
	restoreErr        error
	failPropagateFor  string
	// derivedBy 非 nil 时按 evidence id 给不同的派生行（多条 evidence 的用例需要）。
	derivedBy map[string][]DerivedSupport

	// N5 写读面
	prefs        UserPrefs
	refStates    []RefState
	history      []ProactiveSendRow
	lastActivity time.Time
	hasActivity  bool
	corrected    []string

	proactives   []ProactiveRow
	proacState   *ProactiveState
	statusWrites []ProactiveStatusRow
	sentMarks    []string
	expiredIDs   []string
	linkedProac  []string
	proacErr     error

	// N6 写读面（outcome / feedback / 生命周期 / 传播）
	outcomes        []OutcomeRow
	outcomeLinks    []string
	outcomeStatuses []string
	feedbacks       []FeedbackRow
	latestFB        *LatestFeedback
	// T4-03：显式桩「同一对象最近一条同型 outcome」（同日幂等那两格的输入）。
	// 没设桩时回落到本会话已写入的 outcomes 尾部（见 outcome_fake_test.go）。
	latestOutcome   *LatestOutcome
	patternSt       *PatternState
	lifecycleWrites []PatternLifecycleRow
	factWrites      []FactStatusRow
	goalDone        []string
	commitmentDone  []string
	// T4-02 的三格写痕 + 两张表的新行：complete 复用上面两格，这里只记
	// skip / 两种 cancel。桩按真库那样**不看当前状态**——转移判据在 domain，
	// 桩里复刻它会让「domain 漏判」在单测里假绿。
	goals             []GoalRow
	commitments       []CommitmentRow
	commitmentSkips   []string
	commitmentCancels []string
	goalCancels       []string
	patternInsights   []PatternInsightRef
	queued            []QueuedProactive
	derived           []DerivedSupport

	bumpErr    error
	insertErr  error
	linkErr    error
	rolledBack bool
	inTx       bool

	// N7 投递面（outbox 入队 / inbox 幂等 / 保留期清理）。
	// inbox 用 (message_id, consumer) 作键——18 §7 的唯一键就是这一对，
	// fake 必须按同一形状判重复，否则「同消息不同消费者」的用例会假绿。
	outbox    []OutboxRow
	inbox     map[string]InboxStatus
	processed []string
	failures  []InboxFailureRow

	outboxErr    error
	inboxErr     error
	failureErr   error
	outboxCutoff time.Time
	inboxCutoff  time.Time
	cleaned      bool
	// outboxDeleted / inboxDeleted 由用例预置，用来断言 Service 把仓储的计数
	// 原样搬进 DeliveryCleanupResult（而不是自己算一遍）。
	outboxDeleted int64
	inboxDeleted  int64
}

// ── N7 投递面的 fake 实现（真库行为在 internal/store 的集成测里验）───────────

// inboxKey 就是 18 §7 的唯一键 (message_id, consumer)。
func inboxKey(messageID, consumer string) string { return messageID + "|" + consumer }

func (f *fakeStore) EnqueueOutbox(ctx context.Context, row OutboxRow) error {
	if f.outboxErr != nil {
		return f.outboxErr
	}
	f.outbox = append(f.outbox, row)
	return nil
}

func (f *fakeStore) InsertInbox(ctx context.Context, row InboxRow) (bool, error) {
	if f.inboxErr != nil {
		return false, f.inboxErr
	}
	if f.inbox == nil {
		f.inbox = map[string]InboxStatus{}
	}
	key := inboxKey(row.MessageID, row.Consumer)
	if _, ok := f.inbox[key]; ok {
		return false, nil
	}
	f.inbox[key] = InboxStatus{Status: "pending", Found: true}
	return true, nil
}

func (f *fakeStore) GetInboxStatus(ctx context.Context, messageID,
	consumer string) (InboxStatus, error) {
	if f.inboxErr != nil {
		return InboxStatus{}, f.inboxErr
	}
	state, ok := f.inbox[inboxKey(messageID, consumer)]
	if !ok {
		return InboxStatus{}, nil
	}
	return state, nil
}

func (f *fakeStore) MarkInboxProcessed(ctx context.Context, messageID,
	consumer string) error {
	if f.inboxErr != nil {
		return f.inboxErr
	}
	key := inboxKey(messageID, consumer)
	state, ok := f.inbox[key]
	if !ok {
		return errors.New("fake: 幂等行不在本事务的写入结果里")
	}
	state.Status = "processed"
	f.inbox[key] = state
	f.processed = append(f.processed, key)
	return nil
}

// DeletePublishedOutboxBefore / DeleteProcessedInboxBefore 只记截止时间，
// 删了哪些行由真库用例判（fake 没有状态列，硬造计数会变成自证）。
func (f *fakeStore) DeletePublishedOutboxBefore(ctx context.Context,
	before time.Time) (int64, error) {
	f.outboxCutoff, f.cleaned = before, true
	return f.outboxDeleted, nil
}

func (f *fakeStore) DeleteProcessedInboxBefore(ctx context.Context,
	before time.Time) (int64, error) {
	f.inboxCutoff, f.cleaned = before, true
	return f.inboxDeleted, nil
}

// RecordInboxFailure 是 Transactor 上的池面动作：留痕之后该幂等行进 failed 终态，
// 下一次同 message_id 的投递必须被判成「已按策略记录永久拒绝」（14 §7 第三类）。
func (f *fakeStore) RecordInboxFailure(ctx context.Context, row InboxFailureRow) error {
	if f.failureErr != nil {
		return f.failureErr
	}
	f.failures = append(f.failures, row)
	if f.inbox == nil {
		f.inbox = map[string]InboxStatus{}
	}
	key := inboxKey(row.MessageID, row.Consumer)
	state := f.inbox[key]
	state.Status, state.Found = "failed", true
	state.AttemptCount++
	f.inbox[key] = state
	return nil
}

func newFakeStore() *fakeStore {
	return &fakeStore{
		modelID:  "model-1",
		modelVer: 0,
		// N4 起派生 claim 与 fact 晋升都要看**已算分且互相独立**的证据，
		// 只有 id 的桩会让 trend 停在证据关（03 §6:161-163、§7:175-181）。
		evidence: map[string]Evidence{
			"e1": {ID: "e1", UserID: owner, Strength: 0.90, HasStrength: true,
				Role: "supporting", SourceID: "s1", ObservedAt: windowStart},
			"e2": {ID: "e2", UserID: owner, Strength: 0.88, HasStrength: true,
				Role: "supporting", SourceID: "s2", ObservedAt: windowEnd},
		},
		// N5 默认「什么都绿」：偏好行在、刚说过话、没有历史触达。
		// 各关的拒绝样本在自己的用例里逐条改这几项，而不是在默认桩里埋条件。
		prefs: UserPrefs{UserID: owner, Timezone: "UTC", PushEnabled: true,
			PushFrequency: "daily", AIConsent: true, Tier: "free",
			CreatedAt: windowStart, Found: true},
		lastActivity: windowEnd,
		hasActivity:  true,
		// T2-03 默认「来源行在、且就属于提案里这个用户」：准入面与归属回查是两格判据，
		// N3–N6 的用例不该被新加的那一格拖红，越权样本在自己的用例里显式改这几项。
		sourceOwner: owner,
	}
}

func (f *fakeStore) InTx(ctx context.Context, fn func(context.Context, Store) error) error {
	if f.inTx {
		return errors.New("fake: 事务嵌套，Service 应当在同一个 InTx 内跑完")
	}
	f.inTx = true
	defer func() { f.inTx = false }()
	saved := f.snapshotDelivery()
	if err := fn(ctx, f); err != nil {
		f.rolledBack = true
		// 投递面按真库语义回滚：18 §8 的单事务里 inbox/outbox 行与权威态同生同死，
		// 18 §9 case 1 的「瞬时失败连 inbox 行一起消失」必须能在 fake 上复现，
		// 否则 ConsumeIdempotent 的分道用例只能断言一个真库里不存在的状态。
		f.restoreDelivery(saved)
		return err
	}
	return nil
}

// deliverySnapshot 只搬投递面的四个写入面；权威行不做回滚，
// 因为 N3–N6 的既有用例按「失败后仍能看到已写了一半的行」断言回滚标记。
type deliverySnapshot struct {
	inbox      map[string]InboxStatus
	numOutbox  int
	numProcess int
	numFail    int
}

func (f *fakeStore) snapshotDelivery() deliverySnapshot {
	snap := deliverySnapshot{numOutbox: len(f.outbox), numProcess: len(f.processed),
		numFail: len(f.failures), inbox: map[string]InboxStatus{}}
	for key, state := range f.inbox {
		snap.inbox[key] = state
	}
	return snap
}

func (f *fakeStore) restoreDelivery(snap deliverySnapshot) {
	if len(f.outbox) > snap.numOutbox {
		f.outbox = f.outbox[:snap.numOutbox]
	}
	if len(f.processed) > snap.numProcess {
		f.processed = f.processed[:snap.numProcess]
	}
	if len(f.failures) > snap.numFail {
		f.failures = f.failures[:snap.numFail]
	}
	if f.inbox == nil {
		return
	}
	for key, state := range snap.inbox {
		f.inbox[key] = state
	}
	for key := range f.inbox {
		if _, ok := snap.inbox[key]; !ok {
			delete(f.inbox, key)
		}
	}
}

func (f *fakeStore) LoadEvidence(ctx context.Context, ids []string) ([]Evidence, error) {
	out := make([]Evidence, 0, len(ids))
	for _, id := range ids {
		if row, ok := f.evidence[id]; ok {
			out = append(out, row)
		}
	}
	return out, nil
}

func (f *fakeStore) FindPatternByFingerprint(ctx context.Context, userID, fingerprint string) (string, bool, error) {
	if f.patternID == "" {
		return "", false, nil
	}
	return f.patternID, true, nil
}

func (f *fakeStore) FindLiveFactKey(ctx context.Context, userID, subjectKey, predicate, objectValue string) (string, bool, error) {
	if f.factHit == "" {
		return "", false, nil
	}
	return f.factHit, true, nil
}

func (f *fakeStore) RecentUserRejection(ctx context.Context, userID,
	fingerprint string, withinDays int) (string, bool, error) {
	f.lookedFingerprint = fingerprint
	f.cooldownDays = withinDays
	if f.rejectionHit == "" {
		return "", false, nil
	}
	return f.rejectionHit, true, nil
}

func (f *fakeStore) EnsurePersonalModel(ctx context.Context, userID string) (string, int64, error) {
	return f.modelID, f.modelVer, nil
}

func (f *fakeStore) BumpVersion(ctx context.Context, modelID string, expected int64) (int64, error) {
	if f.bumpErr != nil {
		return 0, f.bumpErr
	}
	if expected != f.modelVer {
		return 0, ErrVersionConflict
	}
	f.modelVer++
	return f.modelVer, nil
}

func (f *fakeStore) InsertVersion(ctx context.Context, row VersionRow) error {
	if f.insertErr != nil {
		return f.insertErr
	}
	f.versions = append(f.versions, row)
	return nil
}

func (f *fakeStore) AppendEvent(ctx context.Context, row EventRow) error {
	if f.insertErr != nil {
		return f.insertErr
	}
	f.events = append(f.events, row)
	return nil
}

func (f *fakeStore) InsertFact(ctx context.Context, row FactRow) error {
	if f.insertErr != nil {
		return f.insertErr
	}
	f.facts = append(f.facts, row)
	return nil
}

func (f *fakeStore) InsertPattern(ctx context.Context, row PatternRow) error {
	if f.insertErr != nil {
		return f.insertErr
	}
	f.patterns = append(f.patterns, row)
	return nil
}

func (f *fakeStore) InsertInsight(ctx context.Context, row InsightRow) error {
	if f.insertErr != nil {
		return f.insertErr
	}
	f.insights = append(f.insights, row)
	return nil
}

func (f *fakeStore) LinkEvidence(ctx context.Context, kind Kind, aggregateID, userID string, ids []string) error {
	if f.linkErr != nil {
		return f.linkErr
	}
	for _, id := range ids {
		f.links = append(f.links, string(kind)+":"+aggregateID+":"+id)
	}
	return nil
}

func (f *fakeStore) LinkPatterns(ctx context.Context, insightID, userID string, ids []string) error {
	if f.linkErr != nil {
		return f.linkErr
	}
	for _, id := range ids {
		f.links = append(f.links, "insight_pattern:"+insightID+":"+id)
	}
	return nil
}

func (f *fakeStore) ReactivatePattern(ctx context.Context, userID, patternID string, ids []string) (int64, error) {
	if f.linkErr != nil {
		return 0, f.linkErr
	}
	f.reactivated = append(f.reactivated, patternID)
	for _, id := range ids {
		f.links = append(f.links, "pattern:"+patternID+":"+id)
	}
	f.reactCount += int64(len(ids))
	return f.reactCount, nil
}

func (f *fakeStore) UpsertSourceRecord(ctx context.Context, row SourceRecordRow) (string, error) {
	if f.insertErr != nil {
		return "", f.insertErr
	}
	f.sources = append(f.sources, row)
	return "src-" + row.SourceTable + "/" + row.SourceID, nil
}

// SourceRowOwner 与真库实现同形（internal/store/source_owner.go）：
// 表不在准入面 ⇒ error（两张表漂了，属接线错误）；sourceOwner 为空 ⇒ found=false
// （行不存在）；否则回一个固定的主人。三种状态分开造，判据才不会互相冒充。
func (f *fakeStore) SourceRowOwner(ctx context.Context,
	sourceTable, sourceID string) (string, bool, error) {
	if f.sourceErr != nil {
		return "", false, f.sourceErr
	}
	f.sourceLookups = append(f.sourceLookups, sourceTable+"|"+sourceID)
	if !contains(AdmissibleSourceTables, sourceTable) {
		return "", false, fmt.Errorf("fake: source_table=%q 没有回查语句（准入面=%v）",
			sourceTable, AdmissibleSourceTables)
	}
	return f.sourceOwner, f.sourceOwner != "", nil
}

func (f *fakeStore) InsertEvidence(ctx context.Context, row EvidenceWrite) error {
	if f.insertErr != nil {
		return f.insertErr
	}
	f.written = append(f.written, row)
	return nil
}

func (f *fakeStore) GetInsightState(ctx context.Context,
	userID, insightID string) (InsightState, error) {
	if f.readErr != nil {
		return InsightState{}, f.readErr
	}
	if f.insightSt == nil {
		return InsightState{}, ErrInsightNotFound
	}
	return *f.insightSt, nil
}

func (f *fakeStore) LoadInsightEvidence(ctx context.Context,
	userID, insightID string) ([]EvidenceRow, error) {
	if f.readErr != nil {
		return nil, f.readErr
	}
	return f.insightEv, nil
}

func (f *fakeStore) SetInsightStatus(ctx context.Context, row InsightStatusRow) error {
	if f.insertErr != nil {
		return f.insertErr
	}
	f.statusSet = append(f.statusSet, row)
	return nil
}

func newTestService(store Transactor) (*Service, *fakeStore) {
	f := store.(*fakeStore)
	s := NewService(f, DefaultPolicy())
	n := 0
	s.newID = func() string {
		n++
		return "id-" + strings.Repeat("x", 1) + itoa(n)
	}
	s.now = func() time.Time { return windowEnd }
	return s, f
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var digits []byte
	for n > 0 {
		digits = append([]byte{byte('0' + n%10)}, digits...)
		n /= 10
	}
	return string(digits)
}

func admit(t *testing.T, s *Service, c Candidate) (Result, error) {
	t.Helper()
	return s.Admit(context.Background(), c)
}

func TestService_FactExplicitOriginGoesActive(t *testing.T) {
	for _, tc := range []struct{ origin, want string }{
		{"direct", "active"}, {"user_confirmed", "active"},
		{"repeated", "candidate"}, {"temporal", "candidate"},
		{"behavioral", "candidate"}, {"user_corrected", "candidate"},
	} {
		t.Run(tc.origin, func(t *testing.T) {
			store := newFakeStore()
			s, f := newTestService(store)
			c := okFact()
			c.Origin = tc.origin
			res, err := admit(t, s, c)
			if err != nil {
				t.Fatalf("Admit: %v", err)
			}
			if res.Status != tc.want {
				t.Errorf("Status=%q，期望 %q", res.Status, tc.want)
			}
			if len(f.facts) != 1 {
				t.Fatalf("facts 行数=%d", len(f.facts))
			}
			if f.facts[0].Status != tc.want {
				t.Errorf("落库 status=%q", f.facts[0].Status)
			}
			if len(f.versions) != 1 || f.versions[0].Version != 1 {
				t.Errorf("versions=%+v，accept 应写一条版本行", f.versions)
			}
			if res.ModelVersion != 1 || f.facts[0].ModelVersion != 1 {
				t.Errorf("ModelVersion=%d/%d，accept 应 ++ 到 1", res.ModelVersion, f.facts[0].ModelVersion)
			}
			if len(f.links) != 1 || f.links[0] != "fact:"+res.ID+":e1" {
				t.Errorf("links=%v，证据回链缺失", f.links)
			}
		})
	}
}

func TestService_RejectStillWritesRow(t *testing.T) {
	cases := []struct {
		name       string
		candidate  func() Candidate
		mutate     func(*Candidate)
		wantReason RejectReason
		wantStatus string
	}{
		{"fact 无证据", okFact, func(c *Candidate) { c.EvidenceIDs = nil },
			ReasonEvidenceMissing, "rejected"},
		{"pattern 禁用类型", okPattern, func(c *Candidate) { c.PatternType = "diagnosis" },
			ReasonClaimTypeRestricted, "rejected"},
		{"insight 低置信", okInsight, func(c *Candidate) { c.Confidence = 0.10 },
			ReasonConfidenceBelowThresh, "draft"},
		{"insight 危机", okInsight, func(c *Candidate) {
			c.Confidence = 0.95
			c.Safety = &Safety{Status: SafetyCrisis, Categories: []string{"self_harm"}}
		}, ReasonSafetyBlocked, "draft"},
		{"fact 重复", okFact, func(c *Candidate) {}, ReasonDuplicate, "rejected"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			store := newFakeStore()
			if tc.wantReason == ReasonDuplicate {
				store.factHit = "live-fact"
			}
			s, f := newTestService(store)
			c := tc.candidate()
			if tc.mutate != nil {
				tc.mutate(&c)
			}
			res, err := admit(t, s, c)
			if err != nil {
				t.Fatalf("Admit: %v", err)
			}
			if res.Action != ActionReject || res.Reason != tc.wantReason {
				t.Fatalf("Action=%q Reason=%q，期望 reject/%s", res.Action, res.Reason, tc.wantReason)
			}
			if res.Status != tc.wantStatus {
				t.Errorf("Status=%q，期望 %q", res.Status, tc.wantStatus)
			}
			if res.ModelVersion != 0 {
				t.Errorf("ModelVersion=%d，拒绝不得 ++ 权威版本", res.ModelVersion)
			}
			// 拒绝留痕：行 + 事件，且不动模型版本。
			switch c.Kind {
			case KindFact:
				if len(f.facts) != 1 || f.facts[0].RejectReason != tc.wantReason.String() {
					t.Errorf("拒绝的 fact 行=%+v", f.facts[0])
				}
			case KindPattern:
				if len(f.patterns) != 1 || f.patterns[0].RejectReason != tc.wantReason.String() {
					t.Errorf("拒绝的 pattern 行=%+v", f.patterns[0])
				}
			case KindInsight:
				if len(f.insights) != 1 {
					t.Fatalf("拒绝的 insight 没落行")
				}
				row := f.insights[0]
				if row.Status != "draft" || row.RejectReason != tc.wantReason.String() {
					t.Errorf("insight 行 status=%q reason=%q", row.Status, row.RejectReason)
				}
				// insight_status 九态没有 rejected（02:144-154），gate_detail 必须承载七关结论。
				if !strings.Contains(row.GateDetail, tc.wantReason.String()) {
					t.Errorf("gate_detail=%q，应含拒绝码", row.GateDetail)
				}
			}
			if len(f.events) != 1 || f.events[0].EventType != "candidate_rejected" {
				t.Errorf("事件=%+v", f.events)
			}
			if f.events[0].GenerationRunID != c.GenerationRunID {
				t.Errorf("事件未带 generation_run_id：%+v", f.events[0])
			}
			if len(f.links) != 0 {
				t.Errorf("拒绝不应回链证据，got %v", f.links)
			}
		})
	}
}

func TestService_PatternFingerprintHitReactivates(t *testing.T) {
	store := newFakeStore()
	store.patternID = "existing-pattern"
	store.reactCount = 5
	s, f := newTestService(store)
	res, err := admit(t, s, okPattern())
	if err != nil {
		t.Fatalf("Admit: %v", err)
	}
	if res.Action != ActionReactivate || res.ID != "existing-pattern" {
		t.Fatalf("Action=%q ID=%q", res.Action, res.ID)
	}
	if len(f.patterns) != 0 {
		t.Errorf("命中指纹却新插了 pattern 行：%+v", f.patterns)
	}
	if len(f.versions) != 1 || f.versions[0].Reason != "pattern_reactivated" {
		t.Errorf("versions=%+v，reactivate 应写一条版本行", f.versions)
	}
	if len(f.events) != 1 || f.events[0].EventType != "pattern_reactivated" {
		t.Errorf("事件=%+v", f.events)
	}
	if res.EvidenceCount != 7 {
		t.Errorf("EvidenceCount=%d，期望沿用仓储重算值 5+2", res.EvidenceCount)
	}
	for _, link := range f.links {
		if !strings.HasPrefix(link, "pattern:existing-pattern:") {
			t.Errorf("回链指向了错行：%v", link)
		}
	}
}

func TestService_InsightLinksPatterns(t *testing.T) {
	store := newFakeStore()
	s, f := newTestService(store)
	res, err := admit(t, s, okInsight())
	if err != nil {
		t.Fatalf("Admit: %v", err)
	}
	if res.Status != "draft" {
		t.Errorf("Status=%q，接纳的 insight 停在 draft（发布归 N5）", res.Status)
	}
	want := []string{"insight:" + res.ID + ":e1", "insight:" + res.ID + ":e2",
		"insight_pattern:" + res.ID + ":p1"}
	if strings.Join(f.links, ",") != strings.Join(want, ",") {
		t.Errorf("links=%v，期望 %v", f.links, want)
	}
	if len(f.events) != 1 || f.events[0].EventType != "insight_admitted" {
		t.Errorf("事件=%+v", f.events)
	}
	if f.events[0].AggregateType != "insights" || f.events[0].AggregateID != res.ID {
		t.Errorf("事件指向错表：%+v", f.events[0])
	}
}

func TestService_VersionConflictPropagates(t *testing.T) {
	store := newFakeStore()
	store.bumpErr = ErrVersionConflict
	s, f := newTestService(store)
	if _, err := admit(t, s, okInsight()); !errors.Is(err, ErrVersionConflict) {
		t.Fatalf("err=%v，期望 ErrVersionConflict 原样上抛供 consumer 重试", err)
	}
	if !f.rolledBack {
		t.Errorf("冲突必须回滚事务")
	}
	if len(f.insights) != 0 || len(f.events) != 0 {
		t.Errorf("冲突后仍有写入痕迹：%v %v", f.insights, f.events)
	}
}

func TestService_WriteFailureRollsBack(t *testing.T) {
	for _, tc := range []struct {
		name  string
		fail  func(*fakeStore)
		cand  func() Candidate
		probe func(*fakeStore) bool
	}{
		{"事件写失败", func(f *fakeStore) { f.insertErr = errors.New("boom") }, okFact,
			func(f *fakeStore) bool { return len(f.events) == 0 }},
		{"回链写失败", func(f *fakeStore) { f.linkErr = errors.New("boom") }, okInsight,
			func(f *fakeStore) bool { return len(f.links) == 0 }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			store := newFakeStore()
			tc.fail(store)
			s, f := newTestService(store)
			if _, err := admit(t, s, tc.cand()); err == nil {
				t.Fatal("期望写失败上抛")
			}
			if !f.rolledBack {
				t.Errorf("写失败必须回滚")
			}
			if !tc.probe(f) {
				t.Errorf("半状态：权威行写了、%s 没写", tc.name)
			}
		})
	}
}

func TestService_RejectsBogusInput(t *testing.T) {
	store := newFakeStore()
	s, _ := newTestService(store)

	t.Run("kind 为空是调用错误", func(t *testing.T) {
		c := okFact()
		c.Kind = ""
		if _, err := admit(t, s, c); err == nil {
			t.Error("期望 error，而不是静默 reject")
		}
	})
	t.Run("模型 id 与库不符是调用错误", func(t *testing.T) {
		c := okFact()
		c.PersonalModelID = "someone-elses-model"
		if _, err := admit(t, s, c); err == nil {
			t.Error("期望 error，而不是按调用方给的 id 写")
		}
	})
}

func TestService_ModelIDFromStoreNotCaller(t *testing.T) {
	store := newFakeStore()
	store.modelID = "model-from-db"
	s, f := newTestService(store)
	c := okFact()
	c.PersonalModelID = "model-from-db" // 一致时放行
	res, err := admit(t, s, c)
	if err != nil {
		t.Fatalf("Admit: %v", err)
	}
	if f.facts[0].PersonalModelID != "model-from-db" {
		t.Errorf("落库模型 id=%q", f.facts[0].PersonalModelID)
	}
	if res.ID == "" || res.ModelVersion != 1 {
		t.Errorf("res=%+v", res)
	}
}
