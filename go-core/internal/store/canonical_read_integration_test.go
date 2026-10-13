// N9 权威读路径的真库集成测（裁决七-7）。domain 的单测证明的是判据，
// 这里证明的只有真库能答的四件事：
//  1. `s.status <> 'deleted'` 在库上真的把已失效源滤掉，而 withdrawn 计数仍然对；
//  2. 每条查询都带 user_id 作用域，别人的洞察读不到（05 §20）；
//  3. 二跳 Insight→Pattern→Evidence 是**一条**语句返回全部 pattern 的依据（N9-7 的 N+1 禁令）；
//  4. 可空列（strength / excerpt / occurred_at）搬成 domain 的 Has* 而不是猜零值。
//
// 未设 TEST_DATABASE_URL 时跳过，与 N2–N7 的 store 集成测同口径。
package store

import (
	"context"
	"errors"
	"testing"

	"github.com/jackc/pgx/v5/pgtype"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store/canonicaldb"
)

// plantEvidenceForInsight 再挂一条依据（自己的源指针），同时链到 insight 与 pattern。
// 返回 source_records.id，供「标失效」用。
func plantEvidenceForInsight(t *testing.T, q *canonicaldb.Queries, c chain,
	patternID, memoryID string, strength float64, excerpt string) string {
	t.Helper()
	ctx := context.Background()
	sourceID, err := q.InsertSourceRecord(ctx, canonicaldb.InsertSourceRecordParams{
		ID: newID(), UserID: c.userID, SourceType: "journal_entry",
		SourceTable: "memories", SourceID: memoryID, OccurredAt: ts(c.now),
		RevisionOf: nullText(),
	})
	if err != nil {
		t.Fatalf("InsertSourceRecord: %v", err)
	}
	evidenceID := newID()
	if err := q.InsertEvidence(ctx, canonicaldb.InsertEvidenceParams{
		ID: evidenceID, UserID: c.userID, SourceRecordID: sourceID,
		EvidenceType: "direct", Role: "supporting", Strength: num(t, strength),
		Observation: txt("user wrote: " + excerpt), Excerpt: txt(excerpt),
		MetadataJson: "{}", GenerationRunID: txt(newID()),
	}); err != nil {
		t.Fatalf("InsertEvidence: %v", err)
	}
	if err := q.LinkInsightEvidence(ctx, canonicaldb.LinkInsightEvidenceParams{
		InsightID: c.insightID, EvidenceID: evidenceID, UserID: c.userID, Role: "supporting",
	}); err != nil {
		t.Fatalf("LinkInsightEvidence: %v", err)
	}
	if patternID != "" {
		if err := q.LinkPatternEvidence(ctx, canonicaldb.LinkPatternEvidenceParams{
			PatternID: patternID, EvidenceID: evidenceID, UserID: c.userID,
		}); err != nil {
			t.Fatalf("LinkPatternEvidence: %v", err)
		}
	}
	return sourceID
}

func TestN9LiveEvidenceReadKeepsOnlyLiveSupport(t *testing.T) {
	pool := poolForTest(t)
	q := canonicaldb.New(pool)
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })
	c := plantChain(t, q, userID)
	repo := NewCanonical(pool)
	ctx := context.Background()

	// plantChain 已经挂了 1 条活依据；再加第二条，两条都活。
	secondMemory := newID()
	plantEvidenceForInsight(t, q, c, "", secondMemory, 0.6, "wrote at 03:14 again")

	items, err := repo.ListLiveEvidenceForInsight(ctx, userID, c.insightID)
	if err != nil {
		t.Fatalf("ListLiveEvidenceForInsight: %v", err)
	}
	if len(items) != 2 {
		t.Fatalf("活依据 = %d 条，期望 2：%+v", len(items), items)
	}
	counts, err := repo.CountInsightEvidenceSupport(ctx, userID, c.insightID)
	if err != nil {
		t.Fatalf("CountInsightEvidenceSupport: %v", err)
	}
	if counts.Total != 2 || counts.Live != 2 {
		t.Fatalf("支撑计数 = %+v，期望 total 2 / live 2", counts)
	}

	// 失效标记只落在 source 层（O-6/O-7：evidence 行不删）。
	if err := q.MarkSourceRecordDeleted(ctx, canonicaldb.MarkSourceRecordDeletedParams{
		ID: c.sourceID, UserID: userID,
	}); err != nil {
		t.Fatalf("MarkSourceRecordDeleted: %v", err)
	}
	items, err = repo.ListLiveEvidenceForInsight(ctx, userID, c.insightID)
	if err != nil {
		t.Fatalf("ListLiveEvidenceForInsight(失效后): %v", err)
	}
	if len(items) != 1 || items[0].Source.SourceID != secondMemory {
		t.Fatalf("活依据 = %+v，期望只剩第二条（N9-6 的活判据没生效）", items)
	}
	counts, err = repo.CountInsightEvidenceSupport(ctx, userID, c.insightID)
	if err != nil {
		t.Fatalf("CountInsightEvidenceSupport(失效后): %v", err)
	}
	if counts.Total != 2 || counts.Live != 1 {
		t.Errorf("支撑计数 = %+v，期望 total 2 / live 1（撤回必须可数）", counts)
	}

	// 端到端：解释器把两个数变成用户能看懂的 withdrawn_count。
	explainer := domain.NewInsightExplainer(repo,
		domain.Policy{Read: domain.ReadPolicy{EvidenceMaxItems: 20, PatternMaxItems: 6}})
	view, err := explainer.Explain(ctx, userID, c.insightID)
	if err != nil {
		t.Fatalf("Explain: %v", err)
	}
	if view.Summary.Count != 1 || view.Summary.WithdrawnCount != 1 {
		t.Errorf("摘要 = %+v，期望 count 1 / withdrawn 1", view.Summary)
	}
	if len(view.Evidence) != 1 || view.Evidence[0].MemoryRef == nil ||
		view.Evidence[0].MemoryRef.MemoryID != secondMemory {
		t.Fatalf("memory_ref 回链不对：%+v", view.Evidence)
	}

	// 可空列的搬运（第 4 件事）：strength/excerpt/observed_at 都必须带 Has*。
	item := view.Evidence[0]
	if !item.HasStrength || item.Strength != 0.6 {
		t.Errorf("strength = %v/%v，期望 0.6", item.Strength, item.HasStrength)
	}
	if !item.HasExcerpt || item.Excerpt != "wrote at 03:14 again" {
		t.Errorf("excerpt = %q/%v", item.Excerpt, item.HasExcerpt)
	}
	if !item.HasObservedAt || item.ObservedAt.IsZero() {
		t.Errorf("observed_at = %v/%v，期望非零", item.ObservedAt, item.HasObservedAt)
	}
	if item.Source.SourceTable != "memories" || item.Source.RecordID == "" {
		t.Errorf("source 指针不全：%+v", item.Source)
	}
}

// TestN9ReadPathIsScopedToOwner 是 05 §20 在库上的证明：
// 换一个人、同一条 insight id，读面一律「不存在」，而且依据查询也不外泄。
func TestN9ReadPathIsScopedToOwner(t *testing.T) {
	pool := poolForTest(t)
	q := canonicaldb.New(pool)
	ownerID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, ownerID) })
	c := plantChain(t, q, ownerID)
	repo := NewCanonical(pool)
	ctx := context.Background()

	const attackerID = "n9-attacker-no-rows"
	if _, err := repo.GetInsightForReading(ctx, attackerID, c.insightID); !errors.Is(err,
		domain.ErrInsightNotFound) {
		t.Errorf("越权读洞察 err = %v，期望 %v", err, domain.ErrInsightNotFound)
	}
	items, err := repo.ListLiveEvidenceForInsight(ctx, attackerID, c.insightID)
	if err != nil {
		t.Fatalf("越权读依据: %v", err)
	}
	if len(items) != 0 {
		t.Errorf("越权读到 %d 条依据：%+v", len(items), items)
	}
	counts, err := repo.CountInsightEvidenceSupport(ctx, attackerID, c.insightID)
	if err != nil {
		t.Fatalf("越权计数: %v", err)
	}
	if counts.Total != 0 || counts.Live != 0 {
		t.Errorf("越权计数 = %+v，期望全 0", counts)
	}
	patterns, err := repo.ListPatternsForInsightReading(ctx, attackerID, c.insightID)
	if err != nil || len(patterns) != 0 {
		t.Errorf("越权 pattern 读 = %+v/%v，期望空", patterns, err)
	}
	links, err := repo.ListLivePatternEvidenceForInsight(ctx, attackerID, c.insightID)
	if err != nil || len(links) != 0 {
		t.Errorf("越权二跳读 = %+v/%v，期望空", links, err)
	}

	// 主人自己读必须通（否则上面的空结果是「谁都读不到」而不是作用域）。
	if _, err := repo.GetInsightForReading(ctx, ownerID, c.insightID); err != nil {
		t.Errorf("本人读洞察失败: %v", err)
	}
}

// TestN9SecondHopIsOneQueryForAllPatterns 钉 N9-7：二跳一条语句取回所有 pattern 的依据。
func TestN9SecondHopIsOneQueryForAllPatterns(t *testing.T) {
	pool := poolForTest(t)
	q := canonicaldb.New(pool)
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })
	c := plantChain(t, q, userID)
	ctx := context.Background()

	// 第二个 pattern（candidate 也链上去，读路径不按 pattern 状态筛——
	// 状态由 domain 与前端如实展示，不藏）。
	secondPattern := newID()
	if err := q.InsertPattern(ctx, canonicaldb.InsertPatternParams{
		ID: secondPattern, UserID: userID, PersonalModelID: c.modelID,
		PatternType: "sleep_timing", SubjectKey: txt("user"), Scope: txt("evening"),
		Description: "records cluster after midnight", Status: "active",
		Confidence: num(t, 0.66), EvidenceCount: 1,
		ObservedFrom: ts(c.now.AddDate(0, 0, -14)), ObservedTo: ts(c.now),
		DetectorVersion: txt("v1"), Fingerprint: txt("fp-" + userID + "-2"),
		ScoreDetail: txt(`{}`), ModelVersion: i8(4),
		GenerationRunID: txt(newID()), RejectReason: nullText(),
	}); err != nil {
		t.Fatalf("InsertPattern: %v", err)
	}
	if err := q.LinkInsightPattern(ctx, canonicaldb.LinkInsightPatternParams{
		InsightID: c.insightID, PatternID: secondPattern, UserID: userID,
	}); err != nil {
		t.Fatalf("LinkInsightPattern: %v", err)
	}
	plantEvidenceForInsight(t, q, c, secondPattern, newID(), 0.7, "another night")

	repo := NewCanonical(pool)
	patterns, err := repo.ListPatternsForInsightReading(ctx, userID, c.insightID)
	if err != nil {
		t.Fatalf("ListPatternsForInsightReading: %v", err)
	}
	if len(patterns) != 2 {
		t.Fatalf("pattern 行 = %d，期望 2", len(patterns))
	}
	links, err := repo.ListLivePatternEvidenceForInsight(ctx, userID, c.insightID)
	if err != nil {
		t.Fatalf("ListLivePatternEvidenceForInsight: %v", err)
	}
	// plantChain 的 evidence 同时挂在第一个 pattern 上，所以这里是 2 行、1 次往返。
	if len(links) != 2 {
		t.Fatalf("二跳行数 = %d，期望 2：%+v", len(links), links)
	}
	seen := map[string]int{}
	for _, link := range links {
		seen[link.PatternID]++
		if link.Item.Source.SourceTable != "memories" {
			t.Errorf("二跳源指针不对：%+v", link.Item.Source)
		}
	}
	if seen[c.patternID] != 1 || seen[secondPattern] != 1 {
		t.Errorf("二跳归组前的分布 = %v，期望两个 pattern 各 1 行", seen)
	}

	// 源失效要同时把二跳滤干净（活判据在两条语句里同字面，不是只在一条里）。
	if err := q.MarkSourceRecordDeleted(ctx, canonicaldb.MarkSourceRecordDeletedParams{
		ID: c.sourceID, UserID: userID,
	}); err != nil {
		t.Fatalf("MarkSourceRecordDeleted: %v", err)
	}
	links, err = repo.ListLivePatternEvidenceForInsight(ctx, userID, c.insightID)
	if err != nil {
		t.Fatalf("ListLivePatternEvidenceForInsight(失效后): %v", err)
	}
	if len(links) != 1 || links[0].PatternID != secondPattern {
		t.Fatalf("失效后二跳 = %+v，期望只剩第二个 pattern 的行", links)
	}
}

// TestN9GetInsightForReadingMapsNullableColumns 钉第 4 件事：
// NULL 强度与 NULL 时间窗不能伪装成 0 / 公元 1 年。
func TestN9GetInsightForReadingMapsNullableColumns(t *testing.T) {
	pool := poolForTest(t)
	q := canonicaldb.New(pool)
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })
	ctx := context.Background()

	modelID := newID()
	if err := q.InsertPersonalModel(ctx, canonicaldb.InsertPersonalModelParams{
		ID: modelID, UserID: userID,
	}); err != nil {
		t.Fatalf("InsertPersonalModel: %v", err)
	}
	insightID := newID()
	if err := q.InsertInsight(ctx, canonicaldb.InsertInsightParams{
		ID: insightID, UserID: userID, PersonalModelID: modelID,
		Statement: "草稿态洞察", ClaimType: "trend", Status: "draft",
		Confidence: pgtype.Numeric{},
		TimeFrom:   pgtype.Timestamptz{}, TimeTo: pgtype.Timestamptz{},
		GenerationModel: txt("test-model"), GenerationVersion: txt("v1"),
		GenerationRunID: txt(newID()), RejectReason: nullText(),
		GateDetail: txt(`{}`), ModelVersion: i8(1),
	}); err != nil {
		t.Fatalf("InsertInsight: %v", err)
	}

	repo := NewCanonical(pool)
	insight, err := repo.GetInsightForReading(ctx, userID, insightID)
	if err != nil {
		t.Fatalf("GetInsightForReading: %v", err)
	}
	if insight.HasConfidence {
		t.Errorf("NULL confidence 被搬成了有值：%+v", insight)
	}
	if insight.HasTimeFrom || insight.HasTimeTo {
		t.Errorf("NULL 时间窗被搬成了有值：%+v", insight)
	}
	if insight.Status != "draft" {
		t.Errorf("status = %q，期望原样带出（可见性由 domain 判，store 不筛）", insight.Status)
	}

	// 草稿态在解释器那一层就被挡住，不下发任何依据查询（N9-5）。
	explainer := domain.NewInsightExplainer(repo,
		domain.Policy{Read: domain.ReadPolicy{EvidenceMaxItems: 20, PatternMaxItems: 6}})
	if _, err := explainer.Explain(ctx, userID, insightID); !errors.Is(err,
		domain.ErrInsightNotVisible) {
		t.Errorf("draft 洞察 Explain err = %v，期望 %v", err, domain.ErrInsightNotVisible)
	}
	if _, err := repo.GetInsightForReading(ctx, userID, "n9-no-such-insight"); !errors.Is(
		err, domain.ErrInsightNotFound) {
		t.Errorf("不存在的 id err = %v，期望 %v", err, domain.ErrInsightNotFound)
	}
}
