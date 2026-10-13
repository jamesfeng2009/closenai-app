// N6 的真库集成测：outcome 记录、用户表态、源失效传播的**判定逻辑**由
// internal/domain 的单测证明，这里只证明只有真库能答的六件事：
//
//  1. outcomes/outcome_links/user_feedback 三张新表的列形状在 N2 DDL 下真的写得进去
//     （origin/result 无 CHECK、DECIMAL(5,4) 的 0.648 不被截成 0.64、model_version 带得上）；
//  2. 「recorded 才 ++、candidate 不传播」落到 personal_models.current_version 上；
//  3. 反例只削分数：evidence_count 与回链在真库不变，削穿 D 级下界才换状态；
//  4. 生命周期改写**不抹掉** last_supported_at（COALESCE 那条改动的直接验证），
//     而 04:231 的 recent_correction 与 N5 的 freshness 都读它；
//  5. 越权引用在写入之前失败并整笔回滚（不变量 9 的数据面，fake store 只能证「报错了」）；
//  6. 源撤回的传播只让「仅依赖它」的行进失效路径，且全程不删任何行；
//     GDPR purge 逐表计数可查、重复执行仍是 0。
//
// 未设 TEST_DATABASE_URL 时跳过，与 N2/N3/N4/N5 的 store 集成测同口径。
package store

import (
	"context"
	"errors"
	"math"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store/canonicaldb"
)

// n6Pattern 是 patterns 行里 N6 会碰或不会碰的全部格子。「不会碰」也要断言，
// 因为 O-4 的口径正是「单个反例不清除历史证据」。
type n6Pattern struct {
	status        string
	confidence    float64
	hasConfidence bool
	evidenceCount int64
	scoreDetail   string
	lastSupported string // ''＝库里是 NULL
	confirmedAt   string
	rejectedAt    string
	expiredAt     string
	rejectReason  string
	modelVersion  int64
}

func readPattern(t *testing.T, pool *pgxpool.Pool, id string) n6Pattern {
	t.Helper()
	var p n6Pattern
	var conf, version float64
	err := pool.QueryRow(context.Background(),
		`SELECT status, coalesce(confidence::float8, -1), evidence_count,
		        coalesce(score_detail, ''), coalesce(last_supported_at::text, ''),
		        coalesce(confirmed_at::text, ''), coalesce(rejected_at::text, ''),
		        coalesce(expired_at::text, ''), coalesce(reject_reason, ''),
		        coalesce(model_version, -1)
		 FROM patterns WHERE id = $1`, id).
		Scan(&p.status, &conf, &p.evidenceCount, &p.scoreDetail, &p.lastSupported,
			&p.confirmedAt, &p.rejectedAt, &p.expiredAt, &p.rejectReason, &version)
	if err != nil {
		t.Fatalf("read pattern %s: %v", id, err)
	}
	p.hasConfidence = conf >= 0
	if p.hasConfidence {
		p.confidence = conf
	}
	if version >= 0 {
		p.modelVersion = int64(version)
	}
	return p
}

func readOutcome(t *testing.T, pool *pgxpool.Pool, id string) (
	status, result, origin, outcomeType, description string, modelVersion int64) {
	t.Helper()
	err := pool.QueryRow(context.Background(),
		`SELECT status, result, origin, outcome_type, coalesce(description, ''),
		        coalesce(model_version, -1)
		 FROM outcomes WHERE id = $1`, id).
		Scan(&status, &result, &origin, &outcomeType, &description, &modelVersion)
	if err != nil {
		t.Fatalf("read outcome %s: %v", id, err)
	}
	return
}

func readModelVersion(t *testing.T, pool *pgxpool.Pool, userID string) int64 {
	t.Helper()
	return countWhere(t, pool,
		`SELECT current_version FROM personal_models WHERE user_id = $1`, userID)
}

// n6Goal 是目标行与它的完成时刻：完成时刻要能比较「第二次完成有没有把它推后」。
func n6Goal(t *testing.T, pool *pgxpool.Pool, id string) (status, completedAt string) {
	t.Helper()
	err := pool.QueryRow(context.Background(),
		`SELECT status, coalesce(completed_at::text, '') FROM personal_goals WHERE id = $1`, id).
		Scan(&status, &completedAt)
	if err != nil {
		t.Fatalf("read goal %s: %v", id, err)
	}
	return
}

func n6Proactive(t *testing.T, pool *pgxpool.Pool, id string) (status, blockReason string) {
	t.Helper()
	err := pool.QueryRow(context.Background(),
		`SELECT status, coalesce(block_reason, '') FROM proactives WHERE id = $1`, id).
		Scan(&status, &blockReason)
	if err != nil {
		t.Fatalf("read proactive %s: %v", id, err)
	}
	return
}

func n6Fact(t *testing.T, pool *pgxpool.Pool, id string) (status, rejectReason, invalidatedAt string) {
	t.Helper()
	err := pool.QueryRow(context.Background(),
		`SELECT status, coalesce(reject_reason, ''), coalesce(invalidated_at::text, '')
		 FROM personal_facts WHERE id = $1`, id).
		Scan(&status, &rejectReason, &invalidatedAt)
	if err != nil {
		t.Fatalf("read fact %s: %v", id, err)
	}
	return
}

func n6InsightStatus(t *testing.T, pool *pgxpool.Pool, id string) string {
	t.Helper()
	var status string
	if err := pool.QueryRow(context.Background(),
		`SELECT status FROM insights WHERE id = $1`, id).Scan(&status); err != nil {
		t.Fatalf("read insight %s: %v", id, err)
	}
	return status
}

// plantQueuedProactive 落一条仍在队列里的触达，并把 packet 的引用清单写成 refs。
// N6 的取消判据只认 packet.ref_ids（N5 落的可复现输入），所以 refs 就是归属证明。
func plantQueuedProactive(t *testing.T, pool *pgxpool.Pool, c chain, refs []string) string {
	t.Helper()
	ctx := context.Background()
	detail := "{}"
	if len(refs) > 0 {
		quoted := make([]string, 0, len(refs))
		for _, r := range refs {
			quoted = append(quoted, `"`+r+`"`)
		}
		detail = `{"ref_ids":[` + strings.Join(quoted, ",") + `]}`
	}
	id := newID()
	if err := canonicaldb.New(pool).InsertProactive(ctx, canonicaldb.InsertProactiveParams{
		ID: id, UserID: c.userID, PersonalModelID: c.modelID,
		Status: "eligible", TriggerType: "goal_followup",
		Message:     txt("这周的深睡时段还是留给你自己吗"),
		ScheduledAt: ts(c.now), ExpiresAt: ts(c.now.AddDate(0, 0, 1)),
		EligibilityScore:  num(t, 0.8),
		EligibilityDetail: txt(detail),
		GenerationModel:   txt("test-model"), GenerationVersion: txt("v1"),
		GenerationRunID: txt(newID()), ModelVersion: i8(1),
	}); err != nil {
		t.Fatalf("InsertProactive: %v", err)
	}
	return id
}

// n6Outcome 是一条最小可写的 outcome 提案。GenerationRunID 每次新生成：
// 不变量 8 要求 AI 产出可追溯，而 plantChain 的那条预置行是手工种的，不该复用。
func n6Outcome(c chain, kind domain.OutcomeType, origin domain.OutcomeOrigin,
	targets ...domain.TargetRef) domain.OutcomeProposal {
	return domain.OutcomeProposal{
		UserID: c.userID, PersonalModelID: c.modelID, Type: kind,
		Result: domain.OutcomeResultBetter, Origin: origin,
		Description:     "user reported two deep-work blocks this week",
		OccurredAt:      time.Now().UTC(),
		Targets:         targets,
		GenerationRunID: newID(),
	}
}

func TestN6RecordOutcomeWritesCanonicalRowAndBumpsOnce(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n6-outcome-recorded"
	defer func() { cleanupCanonical(t, pool, userID) }()

	ctx := context.Background()
	c := plantChain(t, canonicaldb.New(pool), userID)
	before := readModelVersion(t, pool, userID)
	svc := serviceFor(pool)

	receipt, err := svc.RecordOutcome(ctx, n6Outcome(c, domain.OutcomeGoalProgress,
		domain.OriginUserStatement, domain.TargetRef{Type: domain.RefGoal, ID: c.goalID}))
	if err != nil {
		t.Fatalf("RecordOutcome: %v", err)
	}
	if receipt.Status != string(domain.OutcomeStatusRecorded) {
		t.Fatalf("status=%q，user_statement 是权威来源，必须落 recorded（04:279-285）", receipt.Status)
	}
	if got := readModelVersion(t, pool, userID); got != before+1 {
		t.Errorf("current_version=%d，期望 %d：权威结论＝模型内容变了（02:1069）", got, before+1)
	}
	if receipt.ModelVersion != before+1 {
		t.Errorf("receipt.ModelVersion=%d，行上带的必须是新版本", receipt.ModelVersion)
	}

	status, result, origin, outcomeType, description, modelVersion := readOutcome(t, pool, receipt.ID)
	if status != "recorded" || result != "better" || origin != "user_statement" ||
		outcomeType != "goal_progress" || modelVersion != before+1 {
		t.Errorf("库内 outcome=(%s,%s,%s,%s,v=%d)，与提案逐字对不上",
			status, result, origin, outcomeType, modelVersion)
	}
	if !strings.Contains(description, "deep-work") {
		t.Errorf("description=%q，陈述要原样存着（02 §18 的可核实对象）", description)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM outcome_links WHERE outcome_id = $1 AND target_type = 'goal' AND target_id = $2`,
		receipt.ID, c.goalID); n != 1 {
		t.Errorf("outcome_links %d 条，回链是 04 §22 三个度量的连接点", n)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_versions WHERE user_id = $1 AND reason = 'outcome_recorded'`,
		userID); n != 1 {
		t.Errorf("版本留痕 %d 条，每次模型变更都要记一行改了什么（02 §24）", n)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_events WHERE aggregate_id = $1 AND event_type = 'outcome_recorded'`,
		receipt.ID); n != 1 {
		t.Errorf("事件 %d 条，outcome 本身也要进事件流（01 §22 不变量 6 的同族口径）", n)
	}
	// goal_progress 不是「完成」：它只登记进度，不得碰 goals.status（O-4 的最小干预）。
	if goalStatus, _ := n6Goal(t, pool, c.goalID); goalStatus != "active" {
		t.Errorf("goal status=%q，progress 不该完成目标", goalStatus)
	}
}

func TestN6LLMInferenceOutcomeStaysCandidateAndPropagatesNothing(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n6-outcome-candidate"
	defer func() { cleanupCanonical(t, pool, userID) }()

	ctx := context.Background()
	c := plantChain(t, canonicaldb.New(pool), userID)
	before := readModelVersion(t, pool, userID)
	beforePattern := readPattern(t, pool, c.patternID)
	svc := serviceFor(pool)

	receipt, err := svc.RecordOutcome(ctx, n6Outcome(c, domain.OutcomePatternDisconfirmed,
		domain.OriginLLMInference, domain.TargetRef{Type: domain.RefPattern, ID: c.patternID}))
	if err != nil {
		t.Fatalf("RecordOutcome: %v", err)
	}
	if receipt.Status != string(domain.OutcomeStatusCandidate) {
		t.Fatalf("status=%q，LLM 推断只能是 candidate（04:285）", receipt.Status)
	}
	if got := readModelVersion(t, pool, userID); got != before {
		t.Errorf("current_version=%d，candidate 不该推进模型版本", got)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_versions WHERE user_id = $1`, userID); n != 0 {
		t.Errorf("版本留痕 %d 条，candidate 没有模型变更可记", n)
	}
	after := readPattern(t, pool, c.patternID)
	if after.confidence != beforePattern.confidence || after.status != beforePattern.status {
		t.Errorf("pattern=(%s,%.4f)，反例来自非权威来源时不得改写模式（不变量 7 的方向）",
			after.status, after.confidence)
	}
	if !strings.Contains(receipt.Detail, "candidate") {
		t.Errorf("receipt.Detail=%q，不传播的理由要随结果返回", receipt.Detail)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_events WHERE aggregate_id = $1 AND event_type = 'outcome_candidate'`,
		receipt.ID); n != 1 {
		t.Errorf("outcome_candidate 事件 %d 条：登记了但没采信，也必须查得到是谁推断的", n)
	}
}

func TestN6DisconfirmOutcomeLowersConfidenceWithoutTouchingEvidence(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n6-outcome-disconfirm"
	defer func() { cleanupCanonical(t, pool, userID) }()

	ctx := context.Background()
	c := plantChain(t, canonicaldb.New(pool), userID)
	before := readPattern(t, pool, c.patternID)
	if before.confidence != 0.72 {
		t.Fatalf("种下的置信度=%.4f，期望 0.72", before.confidence)
	}
	svc := serviceFor(pool)

	// 0.72 ×(1−0.10)＝0.648，仍在 D 级下界（0.45）之上 ⇒ 只降分、不换状态。
	if _, err := svc.RecordOutcome(ctx, n6Outcome(c, domain.OutcomePatternDisconfirmed,
		domain.OriginUserStatement, domain.TargetRef{Type: domain.RefPattern, ID: c.patternID})); err != nil {
		t.Fatalf("RecordOutcome: %v", err)
	}
	after := readPattern(t, pool, c.patternID)
	if math.Abs(after.confidence-0.648) > 1e-9 {
		t.Errorf("confidence=%.6f，期望 0.6480（DECIMAL(5,4) 不截尾）", after.confidence)
	}
	if after.status != before.status {
		t.Errorf("status=%q，还在 D 级之上就不该换状态（O-4）", after.status)
	}
	if after.evidenceCount != before.evidenceCount {
		t.Errorf("evidence_count=%d，单个反例不清除历史证据（04:313）", after.evidenceCount)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM pattern_evidence WHERE pattern_id = $1`, c.patternID); n != 1 {
		t.Errorf("回链 %d 条，传播不得拆掉证据连接", n)
	}
	if !strings.Contains(after.scoreDetail, `"step":0.1`) {
		t.Errorf("score_detail=%q，分数怎么来的要落进行里（02 §37）", after.scoreDetail)
	}
	if after.rejectReason != "" {
		t.Errorf("reject_reason=%q，只降分不失效，不该写拒绝码", after.rejectReason)
	}
	// 第二跳：04 §17 要的是「触发受影响 Insight 重校验」，不是在本事务里替 N4 改写。
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_events WHERE aggregate_id = $1 AND event_type = 'insight_revalidation_requested'`,
		c.insightID); n != 1 {
		t.Errorf("重校验请求事件 %d 条", n)
	}
	if got := n6InsightStatus(t, pool, c.insightID); got != "published" {
		t.Errorf("insight status=%q，本批不替 N4 的重算入口改写状态", got)
	}
}

func TestN6DisconfirmBelowDFloorWeakensPattern(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n6-outcome-below-floor"
	defer func() { cleanupCanonical(t, pool, userID) }()

	ctx := context.Background()
	c := plantChain(t, canonicaldb.New(pool), userID)
	if _, err := pool.Exec(ctx, `UPDATE patterns SET confidence = $2 WHERE id = $1`,
		c.patternID, num(t, 0.48)); err != nil {
		t.Fatalf("压低置信度： %v", err)
	}
	svc := serviceFor(pool)

	// 0.48 ×0.9＝0.432 < 0.45 ⇒ 削穿下界才走 weakened，并带 evidence_withdrawn 码。
	if _, err := svc.RecordOutcome(ctx, n6Outcome(c, domain.OutcomeBehaviorChange,
		domain.OriginActionCompletion, domain.TargetRef{Type: domain.RefPattern, ID: c.patternID})); err != nil {
		t.Fatalf("RecordOutcome: %v", err)
	}
	after := readPattern(t, pool, c.patternID)
	if after.status != string(domain.PatternWeakened) {
		t.Fatalf("status=%q，跌出 D 级必须 weakened", after.status)
	}
	if math.Abs(after.confidence-0.432) > 1e-9 {
		t.Errorf("confidence=%.6f，期望 0.4320", after.confidence)
	}
	if after.rejectReason != domain.ReasonEvidenceWithdrawn.String() {
		t.Errorf("reject_reason=%q", after.rejectReason)
	}
	if after.evidenceCount != 1 {
		t.Errorf("evidence_count=%d，降档不清证据", after.evidenceCount)
	}
}

func TestN6GoalCompletionCancelsDependentQueuedProactive(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n6-outcome-complete"
	defer func() { cleanupCanonical(t, pool, userID) }()

	ctx := context.Background()
	c := plantChain(t, canonicaldb.New(pool), userID)
	// 一封指着这条目标，一封不指（plantChain 那封的 packet 里根本没有 ref_ids）：
	// 「目标已完成」只该撤掉显式依赖它的触达（04 §12 的取消条件要可核实）。
	dependent := plantQueuedProactive(t, pool, c, []string{c.goalID})
	unrelated := c.proactiveID
	svc := serviceFor(pool)

	receipt, err := svc.RecordOutcome(ctx, n6Outcome(c, domain.OutcomeGoalCompleted,
		domain.OriginUserStatement, domain.TargetRef{Type: domain.RefGoal, ID: c.goalID}))
	if err != nil {
		t.Fatalf("RecordOutcome: %v", err)
	}
	if receipt.CompletedTarget != c.goalID {
		t.Errorf("CompletedTarget=%q，期望 %s", receipt.CompletedTarget, c.goalID)
	}
	if len(receipt.CancelledProactives) != 1 || receipt.CancelledProactives[0] != dependent {
		t.Errorf("CancelledProactives=%v，期望只取消 %s", receipt.CancelledProactives, dependent)
	}
	if status, _ := n6Goal(t, pool, c.goalID); status != "completed" {
		t.Errorf("goal status=%q，02:824 要求落到真正使用的那张表", status)
	}
	if status, block := n6Proactive(t, pool, dependent); status != "cancelled" ||
		block != domain.ReasonStaleTrigger.String() {
		t.Errorf("被指着的触达=(%s,%s)，期望 cancelled/%s",
			status, block, domain.ReasonStaleTrigger)
	}
	if status, _ := n6Proactive(t, pool, unrelated); status != "eligible" {
		t.Errorf("无关触达 status=%q，不该被顺手取消（漏撤 < 错撤）", status)
	}

	// 幂等：已完成的目标不二次改写完成时刻，否则历史里的「什么时候完成」是假的。
	_, beforeCompletedAt := n6Goal(t, pool, c.goalID)
	if beforeCompletedAt == "" {
		t.Fatalf("completed_at 为空，改写没落库")
	}
	if _, err := svc.RecordOutcome(ctx, n6Outcome(c, domain.OutcomeGoalCompleted,
		domain.OriginUserStatement, domain.TargetRef{Type: domain.RefGoal, ID: c.goalID})); err != nil {
		t.Fatalf("第二次 RecordOutcome: %v", err)
	}
	if _, afterCompletedAt := n6Goal(t, pool, c.goalID); afterCompletedAt != beforeCompletedAt {
		t.Errorf("completed_at 从 %s 变成 %s，二次完成把时刻推后了",
			beforeCompletedAt, afterCompletedAt)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_events WHERE event_type = 'target_completed' AND user_id = $1`,
		userID); n != 1 {
		t.Errorf("target_completed 事件 %d 条，第二次完成不应再记一次", n)
	}
}

func TestN6ForeignTargetReferenceWritesNothing(t *testing.T) {
	pool := poolForTest(t)
	const victimID, attackerID = "n6-outcome-victim", "n6-outcome-attacker"
	defer func() {
		cleanupCanonical(t, pool, victimID)
		cleanupCanonical(t, pool, attackerID)
	}()

	ctx := context.Background()
	q := canonicaldb.New(pool)
	victim := plantChain(t, q, victimID)
	attacker := plantChain(t, q, attackerID)
	before := readModelVersion(t, pool, attackerID)
	beforeOutcomes := countWhere(t, pool,
		`SELECT count(*) FROM outcomes WHERE user_id = $1`, attackerID)
	beforeLinks := countWhere(t, pool,
		`SELECT count(*) FROM outcome_links WHERE user_id = $1`, attackerID)
	svc := serviceFor(pool)

	// 提案里引用别人的 goal：核验必须发生在任何写入之前（不变量 9）。
	_, err := svc.RecordOutcome(ctx, n6Outcome(attacker, domain.OutcomeGoalCompleted,
		domain.OriginUserStatement, domain.TargetRef{Type: domain.RefGoal, ID: victim.goalID}))
	if !errors.Is(err, domain.ErrInvalidOwnership) {
		t.Fatalf("err=%v，期望 ErrInvalidOwnership", err)
	}
	if got := countWhere(t, pool,
		`SELECT count(*) FROM outcomes WHERE user_id = $1`, attackerID); got != beforeOutcomes {
		t.Errorf("outcome 行数从 %d 变成 %d，越权提案必须整笔回滚", beforeOutcomes, got)
	}
	if got := countWhere(t, pool,
		`SELECT count(*) FROM outcome_links WHERE user_id = $1`, attackerID); got != beforeLinks {
		t.Errorf("回链从 %d 变成 %d：越权引用不得留下指向别人业务行的连接", beforeLinks, got)
	}
	if got := readModelVersion(t, pool, attackerID); got != before {
		t.Errorf("current_version=%d，失败的提案不该推进版本", got)
	}
	if status, _ := n6Goal(t, pool, victim.goalID); status != "active" {
		t.Errorf("victim goal=%q，别人的提案不该动我的目标", status)
	}
}

func TestN6FeedbackConfirmWritesLifecycleAndKeepsLastSupportedAt(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n6-feedback-confirm"
	defer func() { cleanupCanonical(t, pool, userID) }()

	ctx := context.Background()
	c := plantChain(t, canonicaldb.New(pool), userID)
	// plantChain 预置了一条 confirm 表态，留着它这条测试测的就是「重复」而不是
	// 「首次」。这里先清掉，让「确认」成为该目标的第一次表态。
	if _, err := pool.Exec(ctx, `DELETE FROM user_feedback WHERE user_id = $1`, userID); err != nil {
		t.Fatalf("清预置表态: %v", err)
	}
	before := readPattern(t, pool, c.patternID)
	if before.lastSupported == "" {
		t.Fatalf("种下的 last_supported_at 为空，COALESCE 的验证没有对象")
	}
	beforeVersion := readModelVersion(t, pool, userID)
	svc := serviceFor(pool)

	receipt, err := svc.RecordUserFeedback(ctx, domain.FeedbackProposal{
		UserID: userID, Type: domain.FeedbackConfirm,
		TargetType: domain.RefPattern, TargetID: c.patternID,
		ResponseState: "confirmed",
	})
	if err != nil {
		t.Fatalf("RecordUserFeedback: %v", err)
	}
	if !receipt.Changed || receipt.TargetStatus != string(domain.PatternConfirmed) {
		t.Fatalf("receipt=(changed=%t,status=%q)", receipt.Changed, receipt.TargetStatus)
	}
	after := readPattern(t, pool, c.patternID)
	if after.status != "confirmed" {
		t.Errorf("status=%q", after.status)
	}
	if after.confirmedAt == "" {
		t.Errorf("confirmed_at 为空，用户确认的时刻是 01 §15 Path A 的凭证")
	}
	// 本轮 UpdatePatternLifecycle 的 COALESCE 改动就靠这一条钉住：
	// 生命周期转移不是「新的支撑观测」，把 NULL 直写进去会抹掉 N3 建立的那一列。
	if after.lastSupported != before.lastSupported {
		t.Errorf("last_supported_at 从 %s 变成 %s，表态改写不得抹掉它（04:231/N5 freshness 都读它）",
			before.lastSupported, after.lastSupported)
	}
	if after.confidence != before.confidence {
		t.Errorf("confidence 从 %.4f 变成 %.4f，分数由证据决定，不由一句同意决定（01:677）",
			before.confidence, after.confidence)
	}
	if after.evidenceCount != before.evidenceCount {
		t.Errorf("evidence_count 从 %d 变成 %d", before.evidenceCount, after.evidenceCount)
	}
	if got := readModelVersion(t, pool, userID); got != beforeVersion+1 {
		t.Errorf("current_version=%d，期望 %d", got, beforeVersion+1)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_versions WHERE user_id = $1 AND reason = 'pattern_confirmed'`,
		userID); n != 1 {
		t.Errorf("版本留痕 %d 条，reason 用 02:255-267 的原词", n)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM user_feedback WHERE user_id = $1 AND target_id = $2
		   AND metadata_json LIKE '%"response_state":"confirmed"%'`,
		userID, c.patternID); n != 1 {
		t.Errorf("带响应态原词的表态 %d 条，04 §14 的措辞只存 metadata 不当第二列", n)
	}
}

func TestN6DuplicateFeedbackWritesNoRowButInterleavedDoes(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n6-feedback-dup"
	defer func() { cleanupCanonical(t, pool, userID) }()

	ctx := context.Background()
	c := plantChain(t, canonicaldb.New(pool), userID)
	svc := serviceFor(pool)

	// plantChain 已有该目标上的一条 confirm ⇒ 同型再提一次就是重复提交（07 §6）。
	before := countWhere(t, pool, `SELECT count(*) FROM user_feedback WHERE user_id = $1`, userID)
	_, err := svc.RecordUserFeedback(ctx, domain.FeedbackProposal{
		UserID: userID, Type: domain.FeedbackConfirm,
		TargetType: domain.RefPattern, TargetID: c.patternID,
	})
	if !errors.Is(err, domain.ErrDuplicateFeedback) {
		t.Fatalf("err=%v，期望 ErrDuplicateFeedback", err)
	}
	if got := countWhere(t, pool, `SELECT count(*) FROM user_feedback WHERE user_id = $1`, userID); got != before {
		t.Errorf("重复表态后行数从 %d 变成 %d，同一个按钮连点两次不产生第二条权威记录", before, got)
	}

	// 中间夹过别的表态就不是重复：correct 是新的信息，且按 01:320 走 weakened。
	if _, err := svc.RecordUserFeedback(ctx, domain.FeedbackProposal{
		UserID: userID, Type: domain.FeedbackCorrect,
		TargetType: domain.RefPattern, TargetID: c.patternID,
		CorrectionText: "quiet 只在写作任务上成立",
	}); err != nil {
		t.Fatalf("interleaved RecordUserFeedback: %v", err)
	}
	if got := countWhere(t, pool, `SELECT count(*) FROM user_feedback WHERE user_id = $1`, userID); got != before+1 {
		t.Errorf("行数=%d，期望 %d（不变量 6：用户说过什么必须留痕）", got, before+1)
	}
	after := readPattern(t, pool, c.patternID)
	if after.status != string(domain.PatternWeakened) {
		t.Errorf("status=%q，纠正＝反面证据，不直接 rejected（01:677 的分离原则）", after.status)
	}
	if after.confidence != 0.72 {
		t.Errorf("confidence=%.4f，表态不碰分数", after.confidence)
	}
	// 同一条 correct 再提一次仍然是重复：判据是「最近一条」，不是「历史上出现过」。
	if _, err := svc.RecordUserFeedback(ctx, domain.FeedbackProposal{
		UserID: userID, Type: domain.FeedbackCorrect,
		TargetType: domain.RefPattern, TargetID: c.patternID,
	}); !errors.Is(err, domain.ErrDuplicateFeedback) {
		t.Errorf("第二次 correct err=%v，期望 ErrDuplicateFeedback", err)
	}
}

func TestN6FeedbackOnOutcomeRewritesStatusOnlyWhenWritable(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n6-feedback-outcome"
	defer func() { cleanupCanonical(t, pool, userID) }()

	ctx := context.Background()
	c := plantChain(t, canonicaldb.New(pool), userID)
	svc := serviceFor(pool)

	// recorded 起算可写 ⇒ corrected（01 §13「纠正不是覆盖」的数据面：状态换、行不删）。
	if _, err := svc.RecordUserFeedback(ctx, domain.FeedbackProposal{
		UserID: userID, Type: domain.FeedbackCorrect,
		TargetType: domain.RefOutcome, TargetID: c.outcomeID,
		CorrectionText: "那周其实只有一次",
	}); err != nil {
		t.Fatalf("RecordUserFeedback: %v", err)
	}
	var status string
	if err := pool.QueryRow(ctx, `SELECT status FROM outcomes WHERE id = $1`, c.outcomeID).
		Scan(&status); err != nil {
		t.Fatalf("read outcome: %v", err)
	}
	if status != string(domain.OutcomeStatusCorrected) {
		t.Fatalf("outcomes.status=%q，期望 corrected", status)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_versions WHERE user_id = $1 AND reason = 'outcome_corrected'`,
		userID); n != 1 {
		t.Errorf("版本留痕 %d 条", n)
	}

	// 已是终局（corrected）的行不再被改写：MarkOutcomeStatus 的 SQL 守卫只放行
	// recorded/candidate，领域层预检把它变成 Changed=false，留痕照写。
	receipt, err := svc.RecordUserFeedback(ctx, domain.FeedbackProposal{
		UserID: userID, Type: domain.FeedbackReject,
		TargetType: domain.RefOutcome, TargetID: c.outcomeID,
	})
	if err != nil {
		t.Fatalf("第二次 RecordUserFeedback: %v", err)
	}
	if receipt.Changed {
		t.Errorf("Changed=true，终局 outcome 不该被再次改写")
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM user_feedback WHERE user_id = $1 AND target_type = 'outcome'`,
		userID); n != 2 {
		t.Errorf("留痕 %d 条，改不动也要把用户说过什么记下来（不变量 6）", n)
	}
}

func TestN6SourceWithdrawalPropagatesOnlyToDependentRows(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n6-propagate-source"
	defer func() { cleanupCanonical(t, pool, userID) }()

	ctx := context.Background()
	c := plantChain(t, canonicaldb.New(pool), userID)
	// plantChain 把 fact/pattern/insight 全挂在同一条 evidence 上 ⇒ 三条都是
	// 「仅依赖它」。源一撤，三者都必须进失效路径（01 §22 不变量 9）。
	if _, err := pool.Exec(ctx, `UPDATE source_records SET status = 'deleted' WHERE id = $1`,
		c.sourceID); err != nil {
		t.Fatalf("撤回源: %v", err)
	}
	before := readModelVersion(t, pool, userID)
	svc := serviceFor(pool)

	res, err := svc.PropagateEvidenceInvalidation(ctx, userID, c.evidenceID)
	if err != nil {
		t.Fatalf("PropagateEvidenceInvalidation: %v", err)
	}
	if len(res.Invalidated) != 2 || len(res.RevalidationRequested) != 1 || len(res.Monitored) != 0 {
		t.Fatalf("传播结果=(失效 %d, 重算 %d, 留观 %d)，期望 (2,1,0)：%v",
			len(res.Invalidated), len(res.RevalidationRequested), len(res.Monitored), res.Invalidated)
	}

	factStatus, factReason, invalidatedAt := n6Fact(t, pool, c.factID)
	if factStatus != "invalidated" || factReason != domain.ReasonEvidenceWithdrawn.String() ||
		invalidatedAt == "" {
		t.Errorf("fact=(%s,%s,%s)，期望 invalidated/%s/非空时刻",
			factStatus, factReason, invalidatedAt, domain.ReasonEvidenceWithdrawn)
	}
	pattern := readPattern(t, pool, c.patternID)
	if pattern.status != string(domain.PatternWeakened) ||
		pattern.rejectReason != domain.ReasonEvidenceWithdrawn.String() {
		t.Errorf("pattern=(%s,%s)", pattern.status, pattern.rejectReason)
	}
	if pattern.confidence != 0.72 || pattern.evidenceCount != 1 {
		t.Errorf("pattern 分数/证据被顺手改了：(%.4f,%d)", pattern.confidence, pattern.evidenceCount)
	}
	// insight 只收重校验请求：真正的改写交给 N4 的独立事务入口（缺陷已登记，本批不顺手改）。
	if got := n6InsightStatus(t, pool, c.insightID); got != "published" {
		t.Errorf("insight status=%q，本事务不替 N4 判结论", got)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_events WHERE aggregate_id = $1 AND event_type = 'insight_revalidation_requested'`,
		c.insightID); n != 1 {
		t.Errorf("重校验请求 %d 条", n)
	}
	if got := readModelVersion(t, pool, userID); got != before+1 {
		t.Errorf("current_version=%d，期望 %d：一次传播只 ++ 一次", got, before+1)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_versions WHERE user_id = $1 AND reason = 'evidence_withdrawn'`,
		userID); n != 1 {
		t.Errorf("版本留痕 %d 条", n)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_events WHERE event_type = 'evidence_withdrawn' AND user_id = $1`,
		userID); n != 1 {
		t.Errorf("evidence_withdrawn 事件 %d 条", n)
	}
	// 不硬删（O-7）：撤的是源的标记，行都还在。
	for _, q := range []struct {
		sql  string
		args []any
		want int64
	}{
		{`SELECT count(*) FROM evidence WHERE user_id = $1`, []any{userID}, 1},
		{`SELECT count(*) FROM source_records WHERE user_id = $1`, []any{userID}, 1},
		{`SELECT count(*) FROM personal_facts WHERE id = $1`, []any{c.factID}, 1},
		{`SELECT count(*) FROM insights WHERE id = $1`, []any{c.insightID}, 1},
	} {
		if got := countWhere(t, pool, q.sql, q.args...); got != q.want {
			t.Errorf("%s 计到 %d 条，期望 %d：失效不是删除", q.sql, got, q.want)
		}
	}
}

func TestN6SourceWithdrawalLeavesSharedSupportRowsAlone(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n6-propagate-shared"
	defer func() { cleanupCanonical(t, pool, userID) }()

	ctx := context.Background()
	c := plantChain(t, canonicaldb.New(pool), userID)
	// 给 pattern 再加一条**挂在另一个源**上的证据 ⇒ pattern 不再「仅依赖」第一条；
	// fact/insight 仍是仅依赖。撤第一条源时只有后两者进失效路径。
	other := plantEvidence(t, pool, userID, 0.8, 9)
	if err := canonicaldb.New(pool).LinkPatternEvidence(ctx,
		canonicaldb.LinkPatternEvidenceParams{
			PatternID: c.patternID, EvidenceID: other, UserID: userID,
		}); err != nil {
		t.Fatalf("LinkPatternEvidence: %v", err)
	}
	if _, err := pool.Exec(ctx, `UPDATE source_records SET status = 'deleted' WHERE id = $1`,
		c.sourceID); err != nil {
		t.Fatalf("撤回源: %v", err)
	}
	svc := serviceFor(pool)

	res, err := svc.PropagateEvidenceInvalidation(ctx, userID, c.evidenceID)
	if err != nil {
		t.Fatalf("PropagateEvidenceInvalidation: %v", err)
	}
	if len(res.Monitored) != 1 || res.Monitored[0] != c.patternID {
		t.Fatalf("Monitored=%v，期望只剩还有活支撑的 %s", res.Monitored, c.patternID)
	}
	if len(res.Invalidated) != 1 || res.Invalidated[0].Kind != domain.KindFact {
		t.Errorf("Invalidated=%v，期望只有 fact", res.Invalidated)
	}
	if got := readPattern(t, pool, c.patternID); got.status != "supported" {
		t.Errorf("pattern status=%q，有其他活支撑就交给 N4 的分数说话，不在这里降档", got.status)
	}
	if got := countWhere(t, pool,
		`SELECT count(*) FROM pattern_evidence WHERE pattern_id = $1`, c.patternID); got != 2 {
		t.Errorf("回链 %d 条：留观不得拆连接", got)
	}
}

func TestN6PurgeCoversNewTablesAndIsIdempotent(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n6-purge"
	defer func() { cleanupCanonical(t, pool, userID) }()

	ctx := context.Background()
	c := plantChain(t, canonicaldb.New(pool), userID)
	svc := serviceFor(pool)
	receipt, err := svc.RecordOutcome(ctx, n6Outcome(c, domain.OutcomeCommitmentCompleted,
		domain.OriginUserStatement,
		domain.TargetRef{Type: domain.RefCommitment, ID: c.commitmentID}))
	if err != nil {
		t.Fatalf("RecordOutcome: %v", err)
	}
	if _, err := svc.RecordUserFeedback(ctx, domain.FeedbackProposal{
		UserID: userID, Type: domain.FeedbackOutdated,
		TargetType: domain.RefInsight, TargetID: c.insightID,
	}); err != nil {
		t.Fatalf("RecordUserFeedback: %v", err)
	}

	// 注销链路跑在 Python 侧（D-5：逐表 rowcount，不靠级联删），这里先把「计数可查」
	// 钉在 Go 侧：三张 N6 表都必须是按 user_id 数得出来、删得净的。
	for _, table := range []string{"outcomes", "outcome_links", "user_feedback"} {
		if n := countWhere(t, pool, `SELECT count(*) FROM `+table+` WHERE user_id = $1`, userID); n == 0 {
			t.Fatalf("%s 计数为 0，本用例没在这张表上留下行", table)
		}
	}
	// 三处落点各一张表：outcome 记 recorded、commitment 完成、insight 因表态过期。
	for _, ref := range []struct {
		sql, id, want string
	}{
		{`SELECT status FROM outcomes WHERE id = $1`, receipt.ID,
			string(domain.OutcomeStatusRecorded)},
		{`SELECT status FROM personal_commitments WHERE id = $1`, c.commitmentID, "completed"},
		{`SELECT status FROM insights WHERE id = $1`, c.insightID, "expired"},
	} {
		var got string
		if err := pool.QueryRow(ctx, ref.sql, ref.id).Scan(&got); err != nil {
			t.Fatalf("read (%s): %v", ref.sql, err)
		}
		if got != ref.want {
			t.Errorf("%s → %q，期望 %q", ref.sql, got, ref.want)
		}
	}

	// 逐表删：全表计数归零，再删一次仍是 0（幂等，重复注销不报错也不留半成品）。
	for round := 1; round <= 2; round++ {
		deleted := int64(0)
		for _, table := range canonicalTables {
			tag, err := pool.Exec(ctx, `DELETE FROM `+table+` WHERE user_id = $1`, userID)
			if err != nil {
				t.Fatalf("第 %d 轮 purge %s: %v", round, table, err)
			}
			deleted += tag.RowsAffected()
		}
		if round == 1 && deleted == 0 {
			t.Fatalf("首轮 purge 删了 0 行，种下的图没进这些表")
		}
		if round == 2 && deleted != 0 {
			t.Errorf("第二轮又删了 %d 行，purge 不幂等", deleted)
		}
	}
	for _, table := range []string{"outcomes", "outcome_links", "user_feedback", "personal_models"} {
		if n := countWhere(t, pool, `SELECT count(*) FROM `+table+` WHERE user_id = $1`, userID); n != 0 {
			t.Errorf("%s 残留 %d 行，D-5 的口径是进不了 PURGE_TARGETS 就永远删不净", table, n)
		}
	}
}
