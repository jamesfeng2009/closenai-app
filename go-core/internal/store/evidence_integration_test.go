// N4 的真库集成测：算分与重算的判定逻辑由 internal/domain 的单测证明，
// 这里只证明**只有真库能答**的四件事：
//  1. strength 落进 DECIMAL(5,4) 没被截断，metadata_json 的算分过程原样可读；
//  2. source 指针按 (user_id, source_table, source_id) 幂等复用（08 §3），
//     重跑管道不产生第二条登记；
//  3. 「同一天」在库上确实只顶一条独立观测（03 §6:161-163 的 or 关系），
//     从而把推断事实压在 candidate；
//  4. 重算读的是 evidence.role 而不是 insight_evidence.role，撤回/过期分别写进
//     insights 的哪几列、事件与版本行留没留痕；
//  5. 来源指针的归属回查打在 legacy `memories` 实表上（T2-03）：三种失败各自成一种
//     哨兵，且「读不到」确实来自「没有这一行」。
//
// 未设 TEST_DATABASE_URL 时跳过，与 N2/N3 的 store 集成测同口径。
package store

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store/canonicaldb"
)

// extractionCandidate 是「AI 从明确文本抽出一条具体观察」——03 §5 表里最正常的一档。
// Metadata 里故意带 llm_confidence：它是提案方的自信度，**不得**成为落库分数。
//
// ⚠️ sourceID 必须是 plantMemory 真种下去、且属于 userID 的那一行：T2-03 起
// AdmitEvidence 会回查来源行归属，编一个 id 会得到 ErrUnknownSourceRow。
func extractionCandidate(userID, sourceID string, occurred time.Time,
	independent []string) domain.EvidenceCandidate {
	return domain.EvidenceCandidate{
		UserID: userID, SourceKind: domain.SourceAIExtraction,
		SourceTable: domain.SourceTableMemories, SourceID: sourceID, OccurredAt: occurred,
		EvidenceType: "direct",
		Observation:  "User wrote: stood up three times during the night.",
		Excerpt:      "night log",
		Metadata:     json.RawMessage(`{"llm_confidence":0.99,"rationale":"explicit text"}`),
		Explicitness: domain.Explicit, Specificity: domain.Specific,
		GenerationRunID:        newID(),
		ProposedIndependentIDs: independent,
	}
}

// utcDayPair 给出同一 UTC 日内、都不晚于当下的两个时刻（未来时刻会被算分拒绝）。
func utcDayPair(t time.Time) (time.Time, time.Time) {
	day := time.Date(t.Year(), t.Month(), t.Day(), 0, 0, 0, 0, time.UTC)
	first, second := day.Add(time.Hour), day.Add(2*time.Hour)
	if second.After(t) {
		day = day.AddDate(0, 0, -1)
		first, second = day.Add(time.Hour), day.Add(2*time.Hour)
	}
	return first, second
}

func TestAdmitEvidenceScoresInDBAndKeepsProvenance(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n4-ev-score"
	defer func() { cleanupCanonical(t, pool, userID) }()

	now := time.Now().UTC()
	observed, _ := utcDayPair(now)
	sourceID := plantMemory(t, pool, userID)
	svc := serviceFor(pool)

	res, err := svc.AdmitEvidence(context.Background(),
		extractionCandidate(userID, sourceID, observed, nil))
	if err != nil {
		t.Fatalf("AdmitEvidence: %v", err)
	}
	if res.ID == "" || res.SourceRecordID == "" {
		t.Fatalf("结果缺 id：%+v", res)
	}

	// 分数只可能由 Go 算：03:108「LLM confidence is never the final evidence score」。
	var dbStrength float64
	var meta string
	if err := pool.QueryRow(context.Background(),
		`SELECT strength, metadata_json FROM evidence WHERE id = $1 AND user_id = $2`,
		res.ID, userID).Scan(&dbStrength, &meta); err != nil {
		t.Fatalf("read evidence: %v", err)
	}
	if dbStrength != res.Strength {
		t.Errorf("库内 strength=%v 与结论 %v 不一致（DECIMAL(5,4) 截断？）", dbStrength, res.Strength)
	}
	if dbStrength >= 0.99 || dbStrength <= 0 {
		t.Errorf("strength=%v，应在 (0, 0.99) 之间：既不是调用方自称的置信度，也没被压成 0", dbStrength)
	}
	// 02 §37「Why do we know it」：五因子取值 + 独立性计数 + 调用方原文都要留在同一行。
	for _, key := range []string{"source_reliability", "explicitness", "specificity",
		"repetition", "temporal_relevance", "independent_observations",
		"proposed_but_not_counted", "class", "mode", "caller", "llm_confidence"} {
		if !strings.Contains(meta, key) {
			t.Errorf("metadata_json 缺 %s：%s", key, meta)
		}
	}

	// 同一业务行的第二次登记必须复用指针（08 §3 幂等），而证据本身各插一条。
	if _, err := svc.AdmitEvidence(context.Background(),
		extractionCandidate(userID, sourceID, observed, nil)); err != nil {
		t.Fatalf("AdmitEvidence 2: %v", err)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM source_records WHERE user_id = $1 AND source_id = $2`,
		userID, sourceID); n != 1 {
		t.Errorf("source_records=%d，同指针应复用既有行", n)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM evidence WHERE user_id = $1`, userID); n != 2 {
		t.Errorf("evidence=%d，两次提案应各插一条", n)
	}
}

// 独立重复的核验必须**先于**写入：越权引用不得留下任何半状态。
func TestAdmitEvidenceRejectsForeignIndependentReference(t *testing.T) {
	pool := poolForTest(t)
	const ownerUser, attackerUser = "n4-ev-owner", "n4-ev-attacker"
	defer func() {
		cleanupCanonical(t, pool, ownerUser)
		cleanupCanonical(t, pool, attackerUser)
	}()

	ownerChain := plantUser(t, pool, ownerUser)
	svc := serviceFor(pool)
	_, err := svc.AdmitEvidence(context.Background(), extractionCandidate(attackerUser,
		plantMemory(t, pool, attackerUser), time.Now().UTC().Add(-time.Hour), nil))
	if err != nil {
		t.Fatalf("首条提案: %v", err)
	}
	evidenceBefore := countWhere(t, pool, `SELECT count(*) FROM evidence WHERE user_id = $1`, attackerUser)
	sourceBefore := countWhere(t, pool, `SELECT count(*) FROM source_records WHERE user_id = $1`, attackerUser)

	if _, err := svc.AdmitEvidence(context.Background(), extractionCandidate(attackerUser,
		plantMemory(t, pool, attackerUser), time.Now().UTC().Add(-2*time.Hour),
		[]string{ownerChain.evidenceIDs[0]})); err == nil {
		t.Fatal("引用别人的证据必须报错，而不是静默少算一条")
	}
	// 事务回滚面：核验失败前写入的 source 登记也不许留下。
	if n := countWhere(t, pool, `SELECT count(*) FROM evidence WHERE user_id = $1`, attackerUser); n != evidenceBefore {
		t.Errorf("evidence=%d（原本 %d），失败提案不该留行", n, evidenceBefore)
	}
	if n := countWhere(t, pool, `SELECT count(*) FROM source_records WHERE user_id = $1`, attackerUser); n != sourceBefore {
		t.Errorf("source_records=%d（原本 %d），越权提案不该留下登记", n, sourceBefore)
	}
}

// T2-04 §怎么做.2 的真库面：终局分越出 [0,1] 时**整笔事务**回滚。
//
// 只有真库能答这一格：domain 单测里的 fake 刻意不回滚权威行
// （service_test.go:241-242），所以「strength 越界 ⇒ 连先写入的 source 登记也不留」
// 在单测里恒等于看不见。而这一格偏偏排在 UpsertSourceRecord **之后**——
// 终局分要算出来才判得动——所以它是准入校验里唯一一笔「已经动过库」的拒绝。
func TestAdmitEvidenceRejectsOutOfRangeStrengthInDB(t *testing.T) {
	pool := poolForTest(t)
	const userID = "t204-ev-strength-range"
	defer func() { cleanupCanonical(t, pool, userID) }()
	ctx := context.Background()

	// 对面那格证人：默认策略下同一条必须正常入账（否则下面的「被拒」可以是
	// 「谁都进不了」那种坏接线，闸就成了一只扫空集的眼睛）。
	if _, err := serviceFor(pool).AdmitEvidence(ctx,
		extractionCandidate(userID, plantMemory(t, pool, userID),
			time.Now().UTC().Add(-time.Hour), nil)); err != nil {
		t.Fatalf("默认策略下该入账: %v", err)
	}
	sourceBefore := countWhere(t, pool,
		`SELECT count(*) FROM source_records WHERE user_id = $1`, userID)

	// 把可靠度改成 >1：几何平均后 strength ≈ 1.12，越出定义域。
	// 这不是「构造一个不可能的输入」——因子表正是 G-5 那个 env 覆盖面对象，
	// 一个手抖的 3.0 就能写出来，而它要进的是用户看得见的置信度百分比。
	poisoned := domain.DefaultPolicy()
	poisoned.Evidence.ReliabilityExtraction = 3
	svc := domain.NewService(NewCanonical(pool), poisoned)
	c := extractionCandidate(userID, plantMemory(t, pool, userID),
		time.Now().UTC().Add(-2*time.Hour), nil)
	if _, err := svc.AdmitEvidence(ctx, c); !errors.Is(err, domain.ErrCandidateShape) {
		t.Fatalf("err=%v，越界分必须挂 ErrCandidateShape 转永久（瞬态重投不会让因子表回到定义域）", err)
	}
	if n := countWhere(t, pool, `SELECT count(*) FROM evidence WHERE user_id = $1`, userID); n != 1 {
		t.Errorf("evidence=%d，期望仍然只有证人那一条", n)
	}
	if n := countWhere(t, pool, `SELECT count(*) FROM source_records WHERE user_id = $1`, userID); n != sourceBefore {
		t.Errorf("source_records=%d（原本 %d），越界提案不得留下登记", n, sourceBefore)
	}
}

// T2-03 的真库面：来源指针的三种失败各回各的哨兵，且一律不留半状态。
//
// domain 的单测证明的是判据，这里只证明**只有真库能答**的那件事：
// `SELECT user_id FROM memories WHERE id = $1` 对着 legacy 实表读得动，
// 而「读不到」确实来自 pgx.ErrNoRows 而不是列名写错那种错——后者会被
// 伪装成「这一行不存在」，把每条正常候选都永久拒绝。
func TestAdmitEvidenceChecksSourceRowOwnership(t *testing.T) {
	pool := poolForTest(t)
	const ownerUser, attackerUser = "t203-ev-owner", "t203-ev-attacker"
	defer func() {
		cleanupCanonical(t, pool, ownerUser)
		cleanupCanonical(t, pool, attackerUser)
	}()
	ctx := context.Background()
	svc := serviceFor(pool)
	at := time.Now().UTC().Add(-time.Hour)

	own := plantMemory(t, pool, ownerUser)

	// 证人：自己的行必须**进得了库**。少了这一条，下面三条「被拒」可以来自
	// 「谁都进不了」这种坏接线，闸就成了一只扫空集的眼睛。
	res, err := svc.AdmitEvidence(ctx, extractionCandidate(ownerUser, own, at, nil))
	if err != nil {
		t.Fatalf("自己的来源行必须入账: %v", err)
	}
	if res.ID == "" || res.SourceRecordID == "" {
		t.Fatalf("入账结果缺 id：%+v", res)
	}

	t.Run("别人的行 → ErrForeignSource", func(t *testing.T) {
		if _, err := svc.AdmitEvidence(ctx, extractionCandidate(attackerUser, own, at, nil)); !errors.Is(err,
			domain.ErrForeignSource) {
			t.Fatalf("err=%v，期望 ErrForeignSource", err)
		}
		if n := countWhere(t, pool, `SELECT count(*) FROM evidence WHERE user_id = $1`, attackerUser); n != 0 {
			t.Errorf("evidence=%d，越权提案不得留行", n)
		}
		if n := countWhere(t, pool, `SELECT count(*) FROM source_records WHERE user_id = $1`, attackerUser); n != 0 {
			t.Errorf("source_records=%d，越权提案不得留登记", n)
		}
	})

	t.Run("没有这一行 → ErrUnknownSourceRow", func(t *testing.T) {
		if _, err := svc.AdmitEvidence(ctx, extractionCandidate(attackerUser, newID(), at, nil)); !errors.Is(err,
			domain.ErrUnknownSourceRow) {
			t.Fatalf("err=%v，期望 ErrUnknownSourceRow（它与越权分开是运维需要：一个指时序，一个指引用面出 bug）", err)
		}
	})

	t.Run("没开的表 → 连回查都不发起", func(t *testing.T) {
		c := extractionCandidate(ownerUser, own, at, nil)
		c.SourceTable = "journal_entries"
		if _, err := svc.AdmitEvidence(ctx, c); !errors.Is(err, domain.ErrUnsupportedSourceTable) {
			t.Fatalf("err=%v，期望 ErrUnsupportedSourceTable", err)
		}
		if n := countWhere(t, pool, `SELECT count(*) FROM evidence WHERE user_id = $1`, ownerUser); n != 1 {
			t.Errorf("evidence=%d，期望仍然只有证人那一条", n)
		}
	})
}

// 03 §6:161-163：同源或同日**各自**都只算一次独立观测（组合键 source|day 是错的）。
// 「独立重复」还要求被引用的观测更早——晚于本条的是后续佐证，不是一次重复。
func TestAdmitEvidenceSameDayCountsOnce(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n4-ev-sameday"
	defer func() { cleanupCanonical(t, pool, userID) }()

	first, laterSameDay := utcDayPair(time.Now().UTC())
	previousDay := first.AddDate(0, 0, -1)
	svc := serviceFor(pool)

	base, err := svc.AdmitEvidence(context.Background(),
		extractionCandidate(userID, plantMemory(t, pool, userID), previousDay, nil))
	if err != nil {
		t.Fatalf("AdmitEvidence 1: %v", err)
	}
	if base.Independent != 1 {
		t.Fatalf("Independent=%d，首条只顶自己一次观测", base.Independent)
	}

	across, err := svc.AdmitEvidence(context.Background(),
		extractionCandidate(userID, plantMemory(t, pool, userID), first, []string{base.ID}))
	if err != nil {
		t.Fatalf("AdmitEvidence 2: %v", err)
	}
	if across.Independent != 2 {
		t.Errorf("Independent=%d，跨日且更早的引用应算两次独立观测（detail=%q）",
			across.Independent, across.Detail)
	}

	sameDay, err := svc.AdmitEvidence(context.Background(),
		extractionCandidate(userID, plantMemory(t, pool, userID), laterSameDay, []string{across.ID}))
	if err != nil {
		t.Fatalf("AdmitEvidence 3: %v", err)
	}
	if sameDay.Independent != 1 {
		t.Errorf("Independent=%d，与上一条**同一 UTC 日**（%s vs %s）不得算两次（detail=%q）",
			sameDay.Independent, first, laterSameDay, sameDay.Detail)
	}
	if !strings.Contains(sameDay.Detail, "proposed_independent=1 counted_independent=1") {
		t.Errorf("detail 没同时记提案条数与核实后的计数：%q", sameDay.Detail)
	}
	if !(across.Strength > sameDay.Strength) {
		t.Errorf("跨日 %.4f 应高于同日 %.4f（Repetition 因子生效）",
			across.Strength, sameDay.Strength)
	}
}

// 同日两条证据把推断事实压在 candidate（03 §7:175-181 要求 ≥2 条独立观测）。
func TestAdmitFactStaysCandidateWhenEvidenceSharesADay(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n4-fact-sameday"
	defer func() { cleanupCanonical(t, pool, userID) }()

	ctx := context.Background()
	p := planted{userID: userID, modelID: newID()}
	q := canonicaldb.New(pool)
	if err := q.InsertPersonalModel(ctx, canonicaldb.InsertPersonalModelParams{
		ID: p.modelID, UserID: userID,
	}); err != nil {
		t.Fatalf("InsertPersonalModel: %v", err)
	}
	first, second := utcDayPair(time.Now().UTC())
	p.evidenceIDs = []string{
		plantEvidenceOn(t, pool, userID, 0.9, first),
		plantEvidenceOn(t, pool, userID, 0.9, second),
	}

	res, err := serviceFor(pool).Admit(ctx, baseCandidate(p, domain.KindFact))
	if err != nil {
		t.Fatalf("Admit: %v", err)
	}
	if res.Action != domain.ActionInsert {
		t.Fatalf("Action=%q Reason=%q", res.Action, res.Reason)
	}
	if res.Status != "candidate" {
		t.Errorf("Status=%q detail=%q，同日两观测不构成 §7 的晋升证据", res.Status, res.Detail)
	}
}

func TestReevaluateInsightRetractsOnContradictingEvidence(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n4-reeval-conflict"
	defer func() { cleanupCanonical(t, pool, userID) }()

	ctx := context.Background()
	p := plantUser(t, pool, userID)
	svc := serviceFor(pool)
	ins, err := svc.Admit(ctx, baseCandidate(p, domain.KindInsight))
	if err != nil {
		t.Fatalf("Admit insight: %v", err)
	}
	if ins.Action != domain.ActionInsert {
		t.Fatalf("insight Action=%q Reason=%q Detail=%q", ins.Action, ins.Reason, ins.Detail)
	}
	if _, err := pool.Exec(ctx,
		`UPDATE insights SET status = 'published' WHERE id = $1 AND user_id = $2`,
		ins.ID, userID); err != nil {
		t.Fatalf("publish: %v", err)
	}

	// 先跑一次「支撑没跌破」的重算：不得写状态、不得 ++ 版本。
	keep, err := svc.ReevaluateInsight(ctx, userID, ins.ID)
	if err != nil {
		t.Fatalf("ReevaluateInsight（基线）: %v", err)
	}
	if keep.Status != "published" {
		t.Fatalf("status=%q，两条 A/B 级支撑不该改状态", keep.Status)
	}
	if keep.ModelVersion != ins.ModelVersion {
		t.Errorf("版本=%d，无变化不得 ++（02 §32）", keep.ModelVersion)
	}

	// 插一条**更强的反面证据**。回链上故意写 role='supporting'：
	// 重算读的是 evidence.role（03 §7 的冲突是证据属性，不是某次引用的属性）。
	sourceID, err := canonicaldb.New(pool).InsertSourceRecord(ctx,
		canonicaldb.InsertSourceRecordParams{
			ID: newID(), UserID: userID, SourceType: "journal_entry",
			SourceTable: "memories", SourceID: newID(),
			OccurredAt: ts(time.Now().UTC().AddDate(0, 0, -7)), RevisionOf: nullText(),
		})
	if err != nil {
		t.Fatalf("InsertSourceRecord: %v", err)
	}
	against := newID()
	if err := canonicaldb.New(pool).InsertEvidence(ctx, canonicaldb.InsertEvidenceParams{
		ID: against, UserID: userID, SourceRecordID: sourceID,
		EvidenceType: "direct", Role: "contradicting", Strength: num(t, 0.95),
		Observation: txt("User wrote: the entries are all before 21:00."),
		Excerpt:     txt("excerpt"), MetadataJson: "{}", GenerationRunID: txt(newID()),
	}); err != nil {
		t.Fatalf("InsertEvidence: %v", err)
	}
	if _, err := pool.Exec(ctx,
		`INSERT INTO insight_evidence (insight_id, evidence_id, user_id, role)
		 VALUES ($1, $2, $3, 'supporting')`, ins.ID, against, userID); err != nil {
		t.Fatalf("link: %v", err)
	}

	res, err := svc.ReevaluateInsight(ctx, userID, ins.ID)
	if err != nil {
		t.Fatalf("ReevaluateInsight: %v", err)
	}
	if res.Status != "retracted" {
		t.Fatalf("status=%q，冲突证据不弱于最强支撑必须撤回（03 §7:186-187）", res.Status)
	}

	var status, reason, detail string
	var version int64
	if err := pool.QueryRow(ctx,
		`SELECT status, coalesce(reject_reason, ''), coalesce(gate_detail, ''), coalesce(model_version, 0)
		 FROM insights WHERE id = $1`, ins.ID).Scan(&status, &reason, &detail, &version); err != nil {
		t.Fatalf("read insight: %v", err)
	}
	if status != "retracted" || version != ins.ModelVersion+1 {
		t.Errorf("库内 status=%q version=%d，期望 retracted/%d", status, version, ins.ModelVersion+1)
	}
	if reason != domain.ReasonEvidenceConflict.String() {
		t.Errorf("reject_reason=%q，期望 %s（不复用 evidence_below_floor：两个码在 G-1 里是两回事）",
			reason, domain.ReasonEvidenceConflict)
	}
	if !strings.Contains(detail, `"conflict":true`) {
		t.Errorf("gate_detail=%q，冲突事实必须落进行里", detail)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_events WHERE aggregate_id = $1 AND event_type = 'insight_retracted'`,
		ins.ID); n != 1 {
		t.Errorf("insight_retracted 事件 %d 条，期望 1", n)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_versions WHERE user_id = $1 AND reason = 'insight_retracted'`,
		userID); n != 1 {
		t.Errorf("版本留痕 %d 条，每次模型变更都要记一行改了什么（02 §24）", n)
	}
	if v := countWhere(t, pool,
		`SELECT current_version FROM personal_models WHERE user_id = $1`, userID); v != ins.ModelVersion+1 {
		t.Errorf("current_version=%d，重算的 ++ 没落到模型行上", v)
	}
}

// N7-14（七-5.1 第 2 条）钉住的一闸：重算的乐观锁期望值取**模型行当前版本**，
// 不是 insight 行自己的 model_version 快照。
//
// 只有真库测得出来：fake store 的 putInsight 把两边刻意同步（service_evidence_test.go:303
// 的注释就是为这件事写的），所以快照落后的那条路径在单测里永远走不到。这里先在
// insight 落库之后再 Admit 一条 fact 把 current_version 顶高，此时
// insights.model_version（快照）必然小于 personal_models.current_version（现值）——
// 拿快照当期望值就是 ErrVersionConflict ⇒ 整笔回滚 ⇒ 撤回永远做不成。
func TestN7ReevaluateTakesCurrentModelVersion(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n7-reeval-lock"
	defer func() { cleanupCanonical(t, pool, userID) }()
	ctx := context.Background()
	p := plantUser(t, pool, userID)
	svc := serviceFor(pool)

	ins, err := svc.Admit(ctx, baseCandidate(p, domain.KindInsight))
	if err != nil {
		t.Fatalf("Admit insight: %v", err)
	}
	if _, err := pool.Exec(ctx,
		`UPDATE insights SET status = 'published' WHERE id = $1 AND user_id = $2`,
		ins.ID, userID); err != nil {
		t.Fatalf("publish: %v", err)
	}

	// 让模型在洞察之后再变一次：事实落库一定 ++（statusFor 的 KindFact 分支恒 bump）。
	fact, err := svc.Admit(ctx, baseCandidate(p, domain.KindFact))
	if err != nil {
		t.Fatalf("Admit fact: %v", err)
	}
	var snapshot, current int64
	if err := pool.QueryRow(ctx,
		`SELECT i.model_version, m.current_version FROM insights i
		 JOIN personal_models m ON m.id = i.personal_model_id
		 WHERE i.id = $1`, ins.ID).Scan(&snapshot, &current); err != nil {
		t.Fatalf("read versions: %v", err)
	}
	if current <= snapshot {
		t.Fatalf("前置状态没造出来：insight 快照=%d 现值=%d，需要现值已领先快照", snapshot, current)
	}
	if fact.ModelVersion != current {
		t.Fatalf("Admit 回传的模型版本=%d，库内现值=%d，两边对不上就没法解释 ++ 的归属",
			fact.ModelVersion, current)
	}

	sourceID, err := canonicaldb.New(pool).InsertSourceRecord(ctx,
		canonicaldb.InsertSourceRecordParams{
			ID: newID(), UserID: userID, SourceType: "journal_entry",
			SourceTable: "memories", SourceID: newID(),
			OccurredAt: ts(time.Now().UTC().AddDate(0, 0, -7)), RevisionOf: nullText(),
		})
	if err != nil {
		t.Fatalf("InsertSourceRecord: %v", err)
	}
	against := newID()
	if err := canonicaldb.New(pool).InsertEvidence(ctx, canonicaldb.InsertEvidenceParams{
		ID: against, UserID: userID, SourceRecordID: sourceID,
		EvidenceType: "direct", Role: "contradicting", Strength: num(t, 0.95),
		Observation: txt("User wrote: the entries are all before 21:00."),
		Excerpt:     txt("excerpt"), MetadataJson: "{}", GenerationRunID: txt(newID()),
	}); err != nil {
		t.Fatalf("InsertEvidence: %v", err)
	}
	if _, err := pool.Exec(ctx,
		`INSERT INTO insight_evidence (insight_id, evidence_id, user_id, role)
		 VALUES ($1, $2, $3, 'supporting')`, ins.ID, against, userID); err != nil {
		t.Fatalf("link: %v", err)
	}

	res, err := svc.ReevaluateInsight(ctx, userID, ins.ID)
	if err != nil {
		t.Fatalf("ReevaluateInsight: %v（期望值取到快照就会假报版本冲突、整笔回滚）", err)
	}
	if res.Status != "retracted" {
		t.Fatalf("status=%q，冲突证据不弱于最强支撑必须撤回", res.Status)
	}
	var after int64
	if err := pool.QueryRow(ctx,
		`SELECT i.model_version FROM insights i WHERE i.id = $1`, ins.ID).Scan(&after); err != nil {
		t.Fatalf("read insight version: %v", err)
	}
	if after != current+1 {
		t.Errorf("insights.model_version=%d，期望在**当时的**现值 %d 之上 ++（快照 %d 只用于证明它落后）",
			after, current, snapshot)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_versions WHERE user_id = $1 AND version = $2`,
		userID, current+1); n != 1 {
		t.Errorf("版本留痕 %d 条，++ 后的新版本必须有一行说明改了什么（02 §24）", n)
	}
}

// 03 §11:309-310：撑得住的证据也会随窗口过期。过期是生命周期退化，不是拒绝，
// reject_reason 必须留 NULL，否则 O 侧会把过期算成误判。
func TestReevaluateInsightExpiresWithoutRejectReason(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n4-reeval-expired"
	defer func() { cleanupCanonical(t, pool, userID) }()

	ctx := context.Background()
	p := plantUser(t, pool, userID)
	svc := serviceFor(pool)
	ins, err := svc.Admit(ctx, baseCandidate(p, domain.KindInsight))
	if err != nil {
		t.Fatalf("Admit: %v", err)
	}
	stale := time.Now().UTC().AddDate(0, 0, -domain.DefaultPolicy().Evidence.InsightStaleAfterDays-10)
	if _, err := pool.Exec(ctx,
		`UPDATE insights SET status = 'published', time_to = $2 WHERE id = $1 AND user_id = $3`,
		ins.ID, ts(stale), userID); err != nil {
		t.Fatalf("age the window: %v", err)
	}

	res, err := svc.ReevaluateInsight(ctx, userID, ins.ID)
	if err != nil {
		t.Fatalf("ReevaluateInsight: %v", err)
	}
	if res.Status != "expired" {
		t.Fatalf("status=%q，期望 expired", res.Status)
	}
	var reasonSet bool
	var stored string
	if err := pool.QueryRow(ctx,
		`SELECT reject_reason FROM insights WHERE id = $1`, ins.ID).Scan(&stored); err == nil {
		reasonSet = true
	}
	if reasonSet && stored != "" {
		t.Errorf("reject_reason=%q，过期不得写拒绝码", stored)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_events WHERE aggregate_id = $1 AND event_type = 'insight_expired'`,
		ins.ID); n != 1 {
		t.Errorf("insight_expired 事件 %d 条，期望 1", n)
	}
}

// 用户已表态（confirmed/corrected/dismissed）的行系统不得撤回（03 §12 的用户动作结果）。
func TestReevaluateInsightLeavesUserStatedRowsAlone(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n4-reeval-user-stated"
	defer func() { cleanupCanonical(t, pool, userID) }()

	ctx := context.Background()
	p := plantUser(t, pool, userID)
	svc := serviceFor(pool)
	ins, err := svc.Admit(ctx, baseCandidate(p, domain.KindInsight))
	if err != nil {
		t.Fatalf("Admit: %v", err)
	}
	// 证据弱到不行 + 窗口早已过期，两条撤回条件同时成立。
	if _, err := pool.Exec(ctx,
		`UPDATE insights SET status = 'confirmed', time_to = $2 WHERE id = $1 AND user_id = $3`,
		ins.ID, ts(time.Now().UTC().AddDate(-1, 0, 0)), userID); err != nil {
		t.Fatalf("preset: %v", err)
	}
	if _, err := pool.Exec(ctx,
		`UPDATE evidence SET strength = 0.05 WHERE user_id = $1`, userID); err != nil {
		t.Fatalf("weaken evidence: %v", err)
	}

	res, err := svc.ReevaluateInsight(ctx, userID, ins.ID)
	if err != nil {
		t.Fatalf("ReevaluateInsight: %v", err)
	}
	if res.Status != "confirmed" {
		t.Errorf("status=%q，用户表态优先于系统重算", res.Status)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_events WHERE user_id = $1
           AND event_type IN ('insight_retracted', 'insight_expired')`, userID); n != 0 {
		t.Errorf("越权改写留了 %d 条事件", n)
	}
	if v := countWhere(t, pool,
		`SELECT current_version FROM personal_models WHERE user_id = $1`, userID); v != ins.ModelVersion {
		t.Errorf("current_version=%d，不该 ++", v)
	}
}

// 第 9 关抑制签名：**不插行**。库里已有同指纹 pattern，再插一条直接撞
// ix_patterns_user_fingerprint_unique（23505），所以只补审计事件（03 §12:335-338）。
func TestAdmitSuppressedByUserHistoryWritesNoSecondRow(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n4-suppress"
	defer func() { cleanupCanonical(t, pool, userID) }()

	ctx := context.Background()
	p := plantUser(t, pool, userID)
	svc := serviceFor(pool)
	first, err := svc.Admit(ctx, baseCandidate(p, domain.KindPattern))
	if err != nil {
		t.Fatalf("Admit pattern: %v", err)
	}
	if _, err := pool.Exec(ctx,
		`INSERT INTO user_feedback (id, user_id, feedback_type, target_type, target_id, metadata_json)
		 VALUES ($1, $2, 'reject', 'pattern', $3, '{}')`, newID(), userID, first.ID); err != nil {
		t.Fatalf("user_feedback: %v", err)
	}

	res, err := svc.Admit(ctx, baseCandidate(p, domain.KindPattern))
	if err != nil {
		t.Fatalf("Admit 2: %v", err)
	}
	if res.Action != domain.ActionReject || res.Reason != domain.ReasonSuppressedByUserHistory {
		t.Fatalf("Action=%q Reason=%q，期望 reject/%s",
			res.Action, res.Reason, domain.ReasonSuppressedByUserHistory)
	}
	if res.Status != "suppressed" || res.ID != first.ID {
		t.Errorf("Status=%q ID=%q，期望 suppressed 并指向既有行 %s", res.Status, res.ID, first.ID)
	}
	if n := countWhere(t, pool, `SELECT count(*) FROM patterns WHERE user_id = $1`, userID); n != 1 {
		t.Errorf("patterns=%d，抑制路径不得插第二条行", n)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM patterns WHERE user_id = $1 AND status = 'rejected'`, userID); n != 0 {
		t.Errorf("有 %d 条 rejected 的 pattern 行，抑制不靠插留痕行", n)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_events WHERE event_type = 'candidate_suppressed' AND aggregate_id = $1`,
		first.ID); n != 1 {
		t.Errorf("candidate_suppressed 事件 %d 条，期望 1（G-2 的留痕在这里落成事件）", n)
	}
	// 冷却窗之外（把否决时间推到窗口之前）就必须恢复正常刷新。
	cooldown := domain.DefaultPolicy().RejectionCooldownDays
	if _, err := pool.Exec(ctx,
		`UPDATE user_feedback SET created_at = now() - ($2::int * interval '2 day')
		 WHERE user_id = $1`, userID, cooldown); err != nil {
		t.Fatalf("age feedback: %v", err)
	}
	after, err := svc.Admit(ctx, baseCandidate(p, domain.KindPattern))
	if err != nil {
		t.Fatalf("Admit 3: %v", err)
	}
	if after.Action != domain.ActionReactivate {
		t.Errorf("Action=%q Reason=%q，冷却窗外应回到 reactivate 而不是继续抑制",
			after.Action, after.Reason)
	}
}
