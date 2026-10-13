package domain

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

// N6 Service 单测：outcome / feedback / 源失效传播如何变成 canonical 态。
//
// 钉六条口径：
//  1. **非权威来源不能传播**：origin=llm_inference 只登记 candidate，不 ++、不改模式；
//  2. recorded 才 ++，且版本行与事件都要落（02 §31 的账本）；
//  3. 反例只削分数：evidence_count 与回链原样带回（04:313）；
//  4. 用户表态**必然留痕**（不变量 6），改不动状态也留痕；
//  5. 重复表态＝该目标最近一条就是同型 ⇒ ErrDuplicateFeedback，不写第二行；
//  6. 跨用户引用在**任何写入之前**失败（不变量 9），事务回滚不留半状态。

func outcomeStore(refs ...RefState) (*fakeStore, *Service) {
	f := newFakeStore()
	f.refStates = refs
	s, _ := newTestService(f)
	return f, s
}

func patternGreen() PatternState {
	return PatternState{ID: "pat-1", PersonalModelID: "model-1",
		Status: PatternSupported, Confidence: 0.80, HasConfidence: true,
		EvidenceCount: 4, ScoreDetail: `{"strength":0.8}`, ModelVersion: 0}
}

func TestRecordOutcome_UserStatementBumpsOnceAndRecordsVersion(t *testing.T) {
	f, s := outcomeStore(RefState{RefType: RefGoal, ID: "goal-1", Status: "active"})
	receipt, err := s.RecordOutcome(context.Background(), OutcomeProposal{
		UserID: owner, Type: OutcomeGoalCompleted, Result: OutcomeResultBetter,
		Origin: OriginUserStatement, Description: "用户说这周跑完了三次",
		Targets: []TargetRef{{Type: RefGoal, ID: "goal-1"}}})
	if err != nil {
		t.Fatalf("RecordOutcome: %v", err)
	}
	if receipt.Status != string(OutcomeStatusRecorded) {
		t.Errorf("status = %s，user_statement 应落 recorded", receipt.Status)
	}
	if len(f.outcomes) != 1 {
		t.Fatalf("outcomes 行数 = %d，期望 1", len(f.outcomes))
	}
	row := f.outcomes[0]
	if row.OutcomeType != string(OutcomeGoalCompleted) || row.Result != string(OutcomeResultBetter) ||
		row.Origin != string(OriginUserStatement) {
		t.Errorf("行内容未原样落库：%+v", row)
	}
	if row.OccurredAt.IsZero() == row.HasOccurredAt {
		t.Errorf("occurred_at 与 HasOccurredAt 不自洽：%v/%v", row.OccurredAt, row.HasOccurredAt)
	}
	// O-5：recorded ⇒ ++ 一次，且 personal_model_versions 有 reason 行。
	if f.modelVer != 1 || receipt.ModelVersion != 1 || row.ModelVersion != 1 {
		t.Errorf("版本 = %d/%d/%d，recorded 应 ++ 一次", f.modelVer, receipt.ModelVersion,
			row.ModelVersion)
	}
	if len(f.versions) != 1 || f.versions[0].Reason != "outcome_recorded" {
		t.Fatalf("personal_model_versions = %+v，期望一条 outcome_recorded（02:255-267）", f.versions)
	}
	if len(f.outcomeLinks) != 1 ||
		!strings.HasPrefix(f.outcomeLinks[0], row.ID+":"+owner+":goal:goal-1") {
		t.Errorf("outcome_links = %v", f.outcomeLinks)
	}
	if len(f.events) < 1 || f.events[0].EventType != "outcome_recorded" {
		t.Fatalf("事件 = %+v，第一条必须是 outcome_recorded", f.events)
	}
	// 目标完成落到既有表（02:824），不是 outcomes 表里的第二套状态。
	if strings.Join(f.goalDone, ",") != "goal-1" {
		t.Errorf("goal 完成 = %v，期望 [goal-1]", f.goalDone)
	}
}

// 不变量 5/7 的交集：LLM 的推断不是模型的既成事实。
func TestRecordOutcome_LLMInferenceIsCandidateOnly(t *testing.T) {
	f, s := outcomeStore(RefState{RefType: RefPattern, ID: "pat-1", Status: "supported"})
	f.patternSt = &PatternState{}
	*f.patternSt = patternGreen()
	receipt, err := s.RecordOutcome(context.Background(), OutcomeProposal{
		UserID: owner, Type: OutcomePatternDisconfirmed, Result: OutcomeResultWorse,
		Origin: OriginLLMInference, Description: "模型推断这条模式不成立",
		Targets:         []TargetRef{{Type: RefPattern, ID: "pat-1"}},
		GenerationRunID: "run-1"})
	if err != nil {
		t.Fatalf("RecordOutcome: %v", err)
	}
	if receipt.Status != string(OutcomeStatusCandidate) {
		t.Errorf("status = %s，llm_inference 只能是 candidate（04:285）", receipt.Status)
	}
	if f.modelVer != 0 || len(f.versions) != 0 {
		t.Errorf("candidate 却动了版本账本：%d/%+v", f.modelVer, f.versions)
	}
	if len(f.lifecycleWrites) != 0 {
		t.Errorf("candidate 却传播到了 pattern：%+v", f.lifecycleWrites)
	}
	if len(f.outcomes) != 1 || f.outcomes[0].GenerationRunID != "run-1" {
		t.Fatalf("candidate 也必须留行与留痕号（不变量 8）：%+v", f.outcomes)
	}
	if len(f.events) != 1 || f.events[0].EventType != "outcome_candidate" {
		t.Errorf("事件 = %+v，期望一条 outcome_candidate", f.events)
	}
	if !strings.Contains(receipt.Detail, "candidate") {
		t.Errorf("receipt.Detail = %q，应说明为什么没传播", receipt.Detail)
	}
}

// O-4：反例只削 confidence，evidence_count 与回链一律不动。
func TestRecordOutcome_DisconfirmLowersConfidenceOnly(t *testing.T) {
	f, s := outcomeStore(RefState{RefType: RefPattern, ID: "pat-1", Status: "supported"})
	f.patternSt = &PatternState{}
	*f.patternSt = patternGreen()
	if _, err := s.RecordOutcome(context.Background(), OutcomeProposal{
		UserID: owner, Type: OutcomePatternDisconfirmed, Result: OutcomeResultWorse,
		Origin: OriginUserStatement, Description: "这周完全没有发生",
		Targets: []TargetRef{{Type: RefPattern, ID: "pat-1"}}}); err != nil {
		t.Fatalf("RecordOutcome: %v", err)
	}
	if len(f.lifecycleWrites) != 1 {
		t.Fatalf("生命周期写入 = %d，期望 1", len(f.lifecycleWrites))
	}
	row := f.lifecycleWrites[0]
	if row.EvidenceCount != 4 {
		t.Errorf("evidence_count 被改写 = %d，04:313 要求单个反例不清除历史证据", row.EvidenceCount)
	}
	if row.Status != string(PatternSupported) {
		t.Errorf("status = %s，0.72 仍在 D 级之上，状态不该动（O-4）", row.Status)
	}
	if !row.HasConfidence || row.Confidence > 0.73 || row.Confidence < 0.71 {
		t.Errorf("confidence = %v，期望 0.80×0.90≈0.72", row.Confidence)
	}
	if row.RejectReason != "" {
		t.Errorf("只降分数却写了拒因 %q", row.RejectReason)
	}
	if !strings.Contains(row.ScoreDetail, "\"step\":0.1") {
		t.Errorf("score_detail 没留下削分过程：%s", row.ScoreDetail)
	}
	// 削分之后仍只 ++ 一次（outcome 那一次），生命周期写行复用同一版本。
	if len(f.versions) != 1 || f.modelVer != 1 {
		t.Errorf("版本账本被写了两次：%+v（当前 %d）", f.versions, f.modelVer)
	}
	if len(f.events) < 2 {
		t.Errorf("事件数 = %d，outcome 与 pattern 生命周期各一条", len(f.events))
	}
}

// 削穿 D 级下界才走 weakened（01:320），且**不是** rejected。
func TestRecordOutcome_DisconfirmBelowFloorWeakens(t *testing.T) {
	f, s := outcomeStore(RefState{RefType: RefPattern, ID: "pat-1", Status: "active"})
	f.patternSt = &PatternState{}
	*f.patternSt = patternGreen()
	f.patternSt.Status = PatternActive
	f.patternSt.Confidence = 0.48
	receipt, err := s.RecordOutcome(context.Background(), OutcomeProposal{
		UserID: owner, Type: OutcomePatternDisconfirmed, Origin: OriginUserStatement,
		Description: "连着两周都没有", Targets: []TargetRef{{Type: RefPattern, ID: "pat-1"}}})
	if err != nil {
		t.Fatalf("RecordOutcome: %v", err)
	}
	if len(f.lifecycleWrites) != 1 || f.lifecycleWrites[0].Status != string(PatternWeakened) {
		t.Fatalf("生命周期写行 = %+v，期望 weakened", f.lifecycleWrites)
	}
	if f.lifecycleWrites[0].RejectReason != ReasonEvidenceWithdrawn.String() {
		t.Errorf("削弱未带机器可读码：%q", f.lifecycleWrites[0].RejectReason)
	}
	if len(receipt.Patterns) != 1 || receipt.Patterns[0].To != PatternWeakened {
		t.Errorf("receipt.Patterns = %+v", receipt.Patterns)
	}
}

// 模式没有置信度（NULL）时不得伪造一个分数。
func TestRecordOutcome_NoConfidenceToDispose(t *testing.T) {
	f, s := outcomeStore(RefState{RefType: RefPattern, ID: "pat-1", Status: "supported"})
	f.patternSt = &PatternState{}
	*f.patternSt = patternGreen()
	f.patternSt.Confidence, f.patternSt.HasConfidence = 0, false
	if _, err := s.RecordOutcome(context.Background(), OutcomeProposal{
		UserID: owner, Type: OutcomePatternDisconfirmed, Origin: OriginUserStatement,
		Description: "反例", Targets: []TargetRef{{Type: RefPattern, ID: "pat-1"}}}); err != nil {
		t.Fatalf("RecordOutcome: %v", err)
	}
	if len(f.lifecycleWrites) != 0 {
		t.Errorf("NULL 置信度被当成 0 削了：%+v", f.lifecycleWrites)
	}
}

// outcome 升档默认关闭（01:697「This policy must be conservative」）。
func TestRecordOutcome_AutoConfirmDefaultOff(t *testing.T) {
	f, s := outcomeStore(RefState{RefType: RefPattern, ID: "pat-1", Status: "observed"})
	f.patternSt = &PatternState{}
	*f.patternSt = patternGreen()
	f.patternSt.Status = PatternObserved
	if s.gate.Policy.Outcome.AutoConfirmFromOutcome {
		t.Fatal("AutoConfirmFromOutcome 默认必须关")
	}
	if _, err := s.RecordOutcome(context.Background(), OutcomeProposal{
		UserID: owner, Type: OutcomePatternConfirmed, Origin: OriginProductEvent,
		Description: "系统观测到完成", Targets: []TargetRef{{Type: RefPattern, ID: "pat-1"}}}); err != nil {
		t.Fatalf("RecordOutcome: %v", err)
	}
	if len(f.lifecycleWrites) != 0 {
		t.Errorf("outcome 自动把模式升成 confirmed：%+v（01:697 要求保守）", f.lifecycleWrites)
	}
}

// 传播链第二跳：pattern 变了要请回它支撑的 insight 重算（04 §17）。
func TestRecordOutcome_RevalidatesInsightsForPattern(t *testing.T) {
	f, s := outcomeStore(RefState{RefType: RefPattern, ID: "pat-1", Status: "supported"})
	f.patternSt = &PatternState{}
	*f.patternSt = patternGreen()
	f.patternInsights = []PatternInsightRef{{ID: "ins-1", Status: "published"}}
	f.insightSt = &InsightState{ID: "ins-1", UserID: owner, PersonalModelID: "model-1",
		ClaimType: "trend", Status: "published", ModelVersion: 1}
	f.insightEv = []EvidenceRow{{ID: "e1", UserID: owner, Strength: 0.20, HasStrength: true,
		Role: "supporting", SourceID: "s1"}}
	receipt, err := s.RecordOutcome(context.Background(), OutcomeProposal{
		UserID: owner, Type: OutcomeBehaviorChange, Origin: OriginUserStatement,
		Description: "行为变了", Targets: []TargetRef{{Type: RefPattern, ID: "pat-1"}}})
	if err != nil {
		t.Fatalf("RecordOutcome: %v", err)
	}
	if strings.Join(receipt.Revalidated, ",") != "ins-1" {
		t.Fatalf("Revalidated = %v，期望 [ins-1]", receipt.Revalidated)
	}
	// 触发的是**重校验请求**，不在本事务里改写 insight（同事务改会撞 N4 的乐观锁，
	// 见 requestInsightRevalidation 的根因说明）。
	requested := false
	for _, e := range f.events {
		if e.EventType == "insight_revalidation_requested" && e.AggregateID == "ins-1" {
			requested = true
		}
	}
	if !requested {
		t.Errorf("缺少 insight 重校验请求事件：%+v", f.events)
	}
	if len(f.statusSet) != 0 {
		t.Errorf("本事务直接改写了 insight 状态：%+v", f.statusSet)
	}
}

func TestRecordOutcome_CancelQueuedProactivesOnCompletion(t *testing.T) {
	f, s := outcomeStore(RefState{RefType: RefGoal, ID: "goal-1", Status: "active"})
	f.queued = []QueuedProactive{
		{ID: "pa-1", Status: "scheduled", Trigger: "goal_follow_up",
			Detail: `{"ref_ids":["goal-1"]}`},
		{ID: "pa-2", Status: "candidate", Trigger: "memory_recall",
			Detail: `{"ref_ids":["goal-9"]}`},
		// 读不出 packet 的行**不取消**：撤掉一封可能无关的触达比漏撤更贵。
		{ID: "pa-3", Status: "eligible", Trigger: "goal_follow_up", Detail: "{not json"},
	}
	receipt, err := s.RecordOutcome(context.Background(), OutcomeProposal{
		UserID: owner, Type: OutcomeGoalCompleted, Origin: OriginActionCompletion,
		Description: "完成了", Targets: []TargetRef{{Type: RefGoal, ID: "goal-1"}}})
	if err != nil {
		t.Fatalf("RecordOutcome: %v", err)
	}
	if strings.Join(receipt.CancelledProactives, ",") != "pa-1" {
		t.Errorf("取消 = %v，期望只取消引用 goal-1 的那封", receipt.CancelledProactives)
	}
	if len(f.statusWrites) != 1 {
		t.Fatalf("proactives 写行 = %+v", f.statusWrites)
	}
	write := f.statusWrites[0]
	if write.Status != string(ProactiveStatusCancelled) ||
		write.BlockReason != ReasonStaleTrigger.String() {
		t.Errorf("取消落列 = %s/%s", write.Status, write.BlockReason)
	}
	var detail map[string]any
	if err := json.Unmarshal([]byte(write.Detail), &detail); err != nil {
		t.Fatalf("取消原因不是 JSON：%v", err)
	}
	if detail["cancelled_because"] != "target_completed" || detail["target_id"] != "goal-1" {
		t.Errorf("取消 detail = %v", detail)
	}
}

// 已完成的目标不二次改写：CompleteGoal 会无条件刷 completed_at。
func TestRecordOutcome_CompletionIsIdempotentOnRow(t *testing.T) {
	f, s := outcomeStore(RefState{RefType: RefCommitment, ID: "c-1", Status: "completed"})
	if _, err := s.RecordOutcome(context.Background(), OutcomeProposal{
		UserID: owner, Type: OutcomeCommitmentCompleted, Origin: OriginActionCompletion,
		Description: "又报了一次完成",
		Targets:     []TargetRef{{Type: RefCommitment, ID: "c-1"}}}); err != nil {
		t.Fatalf("RecordOutcome: %v", err)
	}
	if len(f.commitmentDone) != 0 {
		t.Errorf("已完成承诺被二次改写：%v（completed_at 会被推到这次 outcome 上）",
			f.commitmentDone)
	}
	if len(f.outcomes) != 1 {
		t.Errorf("outcome 行仍应记录（历史要留），得到 %d 行", len(f.outcomes))
	}
}

func TestRecordOutcome_RejectsForeignTargetBeforeAnyWrite(t *testing.T) {
	// goal-1 属于别人：ListRefStates 的 UNION 按 user_id 过滤，那条不会回来。
	f, s := outcomeStore(RefState{RefType: RefGoal, ID: "goal-other", Status: "active"})
	_, err := s.RecordOutcome(context.Background(), OutcomeProposal{
		UserID: owner, Type: OutcomeGoalCompleted, Origin: OriginUserStatement,
		Description: "越权引用", Targets: []TargetRef{{Type: RefGoal, ID: "goal-1"}}})
	if !errors.Is(err, ErrInvalidOwnership) {
		t.Fatalf("err = %v，期望 ErrInvalidOwnership", err)
	}
	if len(f.outcomes) != 0 || len(f.outcomeLinks) != 0 || f.modelVer != 0 ||
		len(f.events) != 0 {
		t.Errorf("越权前已写入：%d/%d/%d/%d（不变量 9：核验必须在写之前）",
			len(f.outcomes), len(f.outcomeLinks), f.modelVer, len(f.events))
	}
	if !f.rolledBack {
		t.Error("越权失败没有回滚事务")
	}
}

func TestRecordOutcome_GoalProgressDoesNotComplete(t *testing.T) {
	f, s := outcomeStore(RefState{RefType: RefGoal, ID: "goal-1", Status: "active"})
	if _, err := s.RecordOutcome(context.Background(), OutcomeProposal{
		UserID: owner, Type: OutcomeGoalProgress, Origin: OriginUserStatement,
		Description: "有进展", Targets: []TargetRef{{Type: RefGoal, ID: "goal-1"}}}); err != nil {
		t.Fatalf("RecordOutcome: %v", err)
	}
	if len(f.goalDone) != 0 {
		t.Errorf("goal_progress 却完成了目标：%v", f.goalDone)
	}
}

func TestRecordUserFeedback_AlwaysLeavesAuditRow(t *testing.T) {
	f, s := outcomeStore(RefState{RefType: RefInsight, ID: "ins-1", Status: "published"})
	receipt, err := s.RecordUserFeedback(context.Background(), FeedbackProposal{
		UserID: owner, Type: FeedbackReject, TargetType: RefInsight, TargetID: "ins-1",
		CorrectionText: "不是这个原因", ResponseState: "rejected"})
	if err != nil {
		t.Fatalf("RecordUserFeedback: %v", err)
	}
	if len(f.feedbacks) != 1 {
		t.Fatalf("user_feedback 行数 = %d，不变量 6 要求必然留痕", len(f.feedbacks))
	}
	if len(f.events) != 1 || f.events[0].EventType != "user_feedback_recorded" {
		t.Fatalf("可审计事件缺失：%+v", f.events)
	}
	if receipt.TargetStatus != "retracted" || !receipt.Changed {
		t.Errorf("receipt = %+v，insight 被 reject 应 retract", receipt)
	}
	if len(f.statusSet) != 1 || f.statusSet[0].Status != "retracted" {
		t.Errorf("insight 状态写行 = %+v", f.statusSet)
	}
	if f.modelVer != 1 {
		t.Errorf("版本 = %d，用户表态改写状态应 ++ 一次", f.modelVer)
	}
	var meta map[string]any
	if err := json.Unmarshal([]byte(f.feedbacks[0].Metadata), &meta); err != nil {
		t.Fatalf("metadata_json 不是 JSON：%v", err)
	}
	// 04 §14 的响应态原词必须留得住（O-2 的第三分），状态列用 02 的六值。
	if meta["response_state"] != "rejected" {
		t.Errorf("metadata = %v，缺 response_state 原词", meta)
	}
	if meta["target_status_at_feedback"] != "published" {
		t.Errorf("metadata 没留下表态时刻的库态：%v", meta)
	}
}

// 改不动状态也要留痕：goal 的完成是动作的结果，不是表态的结果（不变量 5）。
func TestRecordUserFeedback_LeavesTraceWithoutRewrite(t *testing.T) {
	f, s := outcomeStore(RefState{RefType: RefGoal, ID: "goal-1", Status: "active"})
	receipt, err := s.RecordUserFeedback(context.Background(), FeedbackProposal{
		UserID: owner, Type: FeedbackReject, TargetType: RefGoal, TargetID: "goal-1"})
	if err != nil {
		t.Fatalf("RecordUserFeedback: %v", err)
	}
	if receipt.Changed {
		t.Error("用户的一句话不该完成/作废目标")
	}
	if len(f.feedbacks) != 1 || len(f.events) != 1 {
		t.Errorf("留痕缺失：feedback=%d event=%d", len(f.feedbacks), len(f.events))
	}
	if f.modelVer != 0 {
		t.Errorf("没有改写却动了版本号：%d（02 §32 无变化不 ++）", f.modelVer)
	}
	if receipt.Detail == "" {
		t.Error("没改写必须有说明")
	}
}

func TestRecordUserFeedback_PatternGoesThroughTransition(t *testing.T) {
	f, s := outcomeStore(RefState{RefType: RefPattern, ID: "pat-1", Status: "expired"})
	f.patternSt = &PatternState{}
	*f.patternSt = patternGreen()
	f.patternSt.Status = PatternExpired
	receipt, err := s.RecordUserFeedback(context.Background(), FeedbackProposal{
		UserID: owner, Type: FeedbackConfirm, TargetType: RefPattern, TargetID: "pat-1"})
	if err != nil {
		t.Fatalf("RecordUserFeedback: %v", err)
	}
	// 终态不接受复活（NextPatternStatus 的闸），但表态照留痕。
	if receipt.Changed || len(f.lifecycleWrites) != 0 {
		t.Errorf("已过期的 pattern 被用户一句话复活：%v %+v", receipt.Changed, f.lifecycleWrites)
	}
	if len(f.feedbacks) != 1 {
		t.Errorf("留痕缺失：%d", len(f.feedbacks))
	}
}

func TestRecordUserFeedback_PatternConfirmWritesLifecycle(t *testing.T) {
	f, s := outcomeStore(RefState{RefType: RefPattern, ID: "pat-1", Status: "supported"})
	f.patternSt = &PatternState{}
	*f.patternSt = patternGreen()
	receipt, err := s.RecordUserFeedback(context.Background(), FeedbackProposal{
		UserID: owner, Type: FeedbackConfirm, TargetType: RefPattern, TargetID: "pat-1"})
	if err != nil {
		t.Fatalf("RecordUserFeedback: %v", err)
	}
	if len(f.lifecycleWrites) != 1 {
		t.Fatalf("生命周期写行 = %d", len(f.lifecycleWrites))
	}
	row := f.lifecycleWrites[0]
	if row.Status != string(PatternConfirmed) || !row.ConfirmAt {
		t.Errorf("confirm 未落 confirmed/confirmed_at：%+v", row)
	}
	// 用户表态不碰分数：confidence 原样带回。
	if row.Confidence != 0.80 {
		t.Errorf("用户表态改写了分数 = %v（分数归 N4 的证据算分）", row.Confidence)
	}
	if row.EvidenceCount != 4 {
		t.Errorf("evidence_count 被改写 = %v", row.EvidenceCount)
	}
	if len(f.versions) != 1 || f.versions[0].Reason != "pattern_confirmed" {
		t.Errorf("版本行 = %+v", f.versions)
	}
	if !receipt.Changed || receipt.TargetStatus != string(PatternConfirmed) {
		t.Errorf("receipt = %+v", receipt)
	}
}

func TestRecordUserFeedback_DuplicateRejected(t *testing.T) {
	f, s := outcomeStore(RefState{RefType: RefInsight, ID: "ins-1", Status: "published"})
	f.latestFB = &LatestFeedback{ID: "fb-old", FeedbackType: string(FeedbackReject),
		Found: true}
	_, err := s.RecordUserFeedback(context.Background(), FeedbackProposal{
		UserID: owner, Type: FeedbackReject, TargetType: RefInsight, TargetID: "ins-1"})
	if !errors.Is(err, ErrDuplicateFeedback) {
		t.Fatalf("err = %v，期望 ErrDuplicateFeedback", err)
	}
	if len(f.feedbacks) != 0 {
		t.Errorf("重复表态写出了第二行：%+v", f.feedbacks)
	}
	// 0 行是刻意的：表上没有唯一约束，历史要留，但同型连点不产新的权威记录。
	if f.modelVer != 0 || len(f.events) != 0 {
		t.Errorf("重复表态还改了模型状态：%d/%+v", f.modelVer, f.events)
	}
}

// 先 reject、后 confirm、再 reject 不是重复：判据是「最近一条是否同型」。
func TestRecordUserFeedback_NotDuplicateWhenInterleaved(t *testing.T) {
	f, s := outcomeStore(RefState{RefType: RefInsight, ID: "ins-1", Status: "published"})
	for _, ty := range []FeedbackType{FeedbackReject, FeedbackConfirm, FeedbackReject} {
		if _, err := s.RecordUserFeedback(context.Background(), FeedbackProposal{
			UserID: owner, Type: ty, TargetType: RefInsight, TargetID: "ins-1"}); err != nil {
			t.Fatalf("第 3 次表态被误判为重复：%v", err)
		}
	}
	if len(f.feedbacks) != 3 {
		t.Errorf("feedback 行数 = %d，期望 3（历史要留）", len(f.feedbacks))
	}
}

func TestRecordUserFeedback_OutcomeSecondCorrectionIsTraceOnly(t *testing.T) {
	f, s := outcomeStore(RefState{RefType: RefOutcome, ID: "o-1", Status: "corrected"})
	f.outcomes = []OutcomeRow{{ID: "o-1", UserID: owner, Status: "corrected"}}
	receipt, err := s.RecordUserFeedback(context.Background(), FeedbackProposal{
		UserID: owner, Type: FeedbackCorrect, TargetType: RefOutcome, TargetID: "o-1"})
	if err != nil {
		t.Fatalf("RecordUserFeedback: %v", err)
	}
	if receipt.Changed {
		t.Error("已 corrected 的 outcome 被二次改写（01 §13 不覆盖已落定的历史）")
	}
	if len(f.feedbacks) != 1 || len(f.events) != 1 {
		t.Errorf("留痕缺失：%d/%d", len(f.feedbacks), len(f.events))
	}
	if f.modelVer != 0 {
		t.Errorf("没改写却 ++ 版本：%d", f.modelVer)
	}
}

func TestRecordUserFeedback_RejectsForeignTarget(t *testing.T) {
	f, s := outcomeStore(RefState{RefType: RefFact, ID: "fact-other", Status: "active"})
	_, err := s.RecordUserFeedback(context.Background(), FeedbackProposal{
		UserID: owner, Type: FeedbackCorrect, TargetType: RefFact, TargetID: "fact-1"})
	if !errors.Is(err, ErrInvalidOwnership) {
		t.Fatalf("err = %v", err)
	}
	if len(f.feedbacks) != 0 || !f.rolledBack {
		t.Errorf("越权表态仍留痕：%d（应整体回滚）", len(f.feedbacks))
	}
}

func TestPropagateEvidenceInvalidation_OnlyDependentPath(t *testing.T) {
	f, s := outcomeStore()
	f.derived = []DerivedSupport{
		// 仅依赖这条证据 ⇒ 进失效路径。
		{Kind: KindFact, ID: "fact-1", Status: "active", TotalSupport: 1, LiveSupport: 0},
		{Kind: KindPattern, ID: "pat-1", Status: "supported", TotalSupport: 2, LiveSupport: 0},
		// 还有别的活支撑 ⇒ 只留痕。
		{Kind: KindInsight, ID: "ins-2", Status: "published", TotalSupport: 3, LiveSupport: 2},
	}
	f.patternSt = &PatternState{}
	*f.patternSt = patternGreen()
	result, err := s.PropagateEvidenceInvalidation(context.Background(), owner, "e1")
	if err != nil {
		t.Fatalf("Propagate: %v", err)
	}
	if len(result.Invalidated) != 2 || len(result.Monitored) != 1 {
		t.Fatalf("Invalidated/Monitored = %d/%d，期望 2/1（%+v）",
			len(result.Invalidated), len(result.Monitored), result.Invalidated)
	}
	if len(f.factWrites) != 1 || f.factWrites[0].Status != "invalidated" ||
		f.factWrites[0].RejectReason != ReasonEvidenceWithdrawn.String() ||
		!f.factWrites[0].InvalidateAt {
		t.Errorf("fact 失效写行 = %+v", f.factWrites)
	}
	if len(f.lifecycleWrites) != 1 || f.lifecycleWrites[0].Status != string(PatternWeakened) {
		t.Errorf("pattern 失效写行 = %+v", f.lifecycleWrites)
	}
	// 有其他支撑的行：不改状态、不改分数。
	for _, w := range f.statusSet {
		if w.ID == "ins-2" {
			t.Errorf("有其他支撑的 insight 被直接改写：%+v（O-6：只重算，不硬判）", w)
		}
	}
	if f.modelVer != 1 {
		t.Errorf("版本 = %d，一次传播应只 ++ 一次", f.modelVer)
	}
	if len(f.versions) != 1 || f.versions[0].Reason != ReasonEvidenceWithdrawn.String() {
		t.Errorf("版本行 = %+v", f.versions)
	}
	if len(f.events) != 1 || f.events[0].EventType != "evidence_withdrawn" {
		t.Errorf("事件 = %+v", f.events)
	}
	// 不硬删：本批一次 DELETE 都没有（store 侧也只提供状态改写语句）。
	if strings.Contains(fmtEvents(f), "delete") {
		t.Error("传播链里出现了删除")
	}
}

func TestPropagateEvidenceInvalidation_NothingOnlyDependent(t *testing.T) {
	f, s := outcomeStore()
	f.derived = []DerivedSupport{
		{Kind: KindInsight, ID: "ins-1", Status: "published", TotalSupport: 2, LiveSupport: 1},
	}
	result, err := s.PropagateEvidenceInvalidation(context.Background(), owner, "e1")
	if err != nil {
		t.Fatalf("Propagate: %v", err)
	}
	if len(result.Invalidated) != 0 || len(result.Monitored) != 1 {
		t.Errorf("结论 = %+v", result)
	}
	if f.modelVer != 0 || len(f.versions) != 0 || len(f.events) != 0 {
		t.Errorf("没有失效对象却动了账本：%d/%+v/%+v", f.modelVer, f.versions, f.events)
	}
	if result.Detail == "" {
		t.Error("必须说明为什么什么都没动")
	}
}

// insight 的零支撑不由本批硬置状态：留重校验请求，结论交给 N4 的重算入口。
func TestPropagateEvidenceInvalidation_InsightRequestsReevaluation(t *testing.T) {
	f, s := outcomeStore()
	f.derived = []DerivedSupport{
		{Kind: KindInsight, ID: "ins-1", Status: "published", TotalSupport: 1, LiveSupport: 0},
	}
	f.insightSt = &InsightState{ID: "ins-1", UserID: owner, PersonalModelID: "model-1",
		ClaimType: "trend", Status: "published", ModelVersion: 0}
	f.insightEv = []EvidenceRow{} // 零支撑
	result, err := s.PropagateEvidenceInvalidation(context.Background(), owner, "e1")
	if err != nil {
		t.Fatalf("Propagate: %v", err)
	}
	if len(result.Invalidated) != 0 ||
		strings.Join(result.RevalidationRequested, ",") != "ins-1" {
		t.Fatalf("结论 = %+v，期望只发重校验请求", result)
	}
	if len(f.statusSet) != 0 {
		t.Errorf("本事务改写了 insight：%+v", f.statusSet)
	}
	if f.modelVer != 0 || len(f.versions) != 0 {
		t.Errorf("只请求重算却 ++ 了版本：%d/%+v", f.modelVer, f.versions)
	}
	requested := 0
	for _, e := range f.events {
		if e.EventType == "insight_revalidation_requested" {
			requested++
		}
	}
	if requested != 1 {
		t.Errorf("重校验请求事件 = %d，期望 1：%+v", requested, f.events)
	}
	if !strings.Contains(result.Detail, "ReevaluateInsight") {
		t.Errorf("Detail = %q，必须说明改写交给哪个入口", result.Detail)
	}
}

func TestPropagateEvidenceInvalidation_TerminalPatternStays(t *testing.T) {
	f, s := outcomeStore()
	f.derived = []DerivedSupport{
		{Kind: KindPattern, ID: "pat-1", Status: "rejected", TotalSupport: 1, LiveSupport: 0},
	}
	f.patternSt = &PatternState{}
	*f.patternSt = patternGreen()
	f.patternSt.Status = PatternRejected
	result, err := s.PropagateEvidenceInvalidation(context.Background(), owner, "e1")
	if err != nil {
		t.Fatalf("Propagate: %v", err)
	}
	if result.Invalidated[0].To != string(PatternRejected) {
		t.Errorf("终态 pattern 被改写：%+v", result.Invalidated[0])
	}
	if len(f.lifecycleWrites) != 0 {
		t.Errorf("非法转移仍写了库：%+v", f.lifecycleWrites)
	}
}

func TestPropagateEvidenceInvalidation_RequiresIDs(t *testing.T) {
	f, s := outcomeStore()
	for _, args := range [][2]string{{"", "e1"}, {owner, ""}} {
		if _, err := s.PropagateEvidenceInvalidation(context.Background(),
			args[0], args[1]); err == nil {
			t.Errorf("空参数 (%q,%q) 未被拒绝", args[0], args[1])
		}
	}
	if f.modelVer != 0 {
		t.Error("校验前就动了版本")
	}
}

func TestServiceErrorsAreWrapped(t *testing.T) {
	// 写库故障必须冒泡，而不是被当成「没有影响」。
	f, s := outcomeStore(RefState{RefType: RefGoal, ID: "goal-1", Status: "active"})
	f.insertErr = errors.New("fake: 库故障")
	if _, err := s.RecordOutcome(context.Background(), OutcomeProposal{
		UserID: owner, Type: OutcomeGoalCompleted, Origin: OriginUserStatement,
		Description: "完成", Targets: []TargetRef{{Type: RefGoal, ID: "goal-1"}}}); err == nil {
		t.Fatal("写失败被吞掉了")
	} else if !f.rolledBack {
		t.Error("写失败没有回滚")
	}

	f2, s2 := outcomeStore(RefState{RefType: RefPattern, ID: "pat-1", Status: "supported"})
	f2.patternSt = &PatternState{}
	*f2.patternSt = patternGreen()
	f2.readErr = errors.New("fake: 读故障")
	if _, err := s2.RecordOutcome(context.Background(), OutcomeProposal{
		UserID: owner, Type: OutcomePatternDisconfirmed, Origin: OriginUserStatement,
		Description: "反例",
		Targets:     []TargetRef{{Type: RefPattern, ID: "pat-1"}}}); err == nil {
		t.Fatal("读失败被吞掉了")
	}

	// 目标行在核验之后被并发删除：GetPatternState 必须报 ErrPatternNotFound，
	// 而不是静默造出一个 candidate 状态的模式。
	_, s3 := outcomeStore(RefState{RefType: RefPattern, ID: "pat-1", Status: "supported"})
	if _, err := s3.RecordOutcome(context.Background(), OutcomeProposal{
		UserID: owner, Type: OutcomePatternDisconfirmed, Origin: OriginUserStatement,
		Description: "反例",
		Targets:     []TargetRef{{Type: RefPattern, ID: "pat-1"}}}); !errors.Is(err,
		ErrPatternNotFound) {
		t.Errorf("err = %v，期望 ErrPatternNotFound", err)
	}
}

func fmtEvents(f *fakeStore) string {
	parts := []string{}
	for _, e := range f.events {
		parts = append(parts, e.EventType)
	}
	for _, w := range f.lifecycleWrites {
		parts = append(parts, w.Status)
	}
	return strings.Join(parts, ",")
}
