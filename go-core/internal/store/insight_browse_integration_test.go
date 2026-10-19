// T3-01／T3-02 的真库集成测（闸 G-05「无证据不上线」、闸 G-06「纠错真落库」）。
//
// 这两条判据**只有真库能答**，所以不放在 domain／httpapi 的单测里：
//   - G-05 的实现是一台 SQL 里的三表内连（insight_evidence → evidence → source_records）
//     ＋ `s.status <> 'deleted'`。用替身测它等于测「我写的 mock 返回了我塞进去的东西」。
//     真库才证得了「标了 deleted 的那条依据确实让洞察从列表里消失」这件事；
//   - G-06 的实现跨四张表（user_feedback / insights / personal_model_versions / personal_model_events），
//     工单 §验收 逐字要求「反馈后 user_feedback 有行且 insights.status 按矩阵变化」。
//
// 分页那条（keyset 的 limit=1 边界）也在这里：它的正确性依赖 ORDER BY 与
// `(published_at, id)` 的**实际比较语义**，同一秒两行这件事只有库能造出来。
//
// 未设 TEST_DATABASE_URL 时跳过，与 N2–N9 的 store 集成测同口径。
package store

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store/canonicaldb"
)

// insightOpts 是造一颗洞察时要控制的三格：状态、上线时刻、有没有活依据。
//
// `publishedAt` 给零值＝**不落 published_at**（列可空）。这一格不是可有可无的讲究：
// 列表的 ORDER BY 与游标都以它作键，`status='published' 而 published_at IS NULL`
// 这种行在库上真实存在（改写状态而不带时刻的路径不止一条），
// 于是「上了状态却没上线」的行必须在列表里缺席——否则游标编不出、翻页会漂。
type insightOpts struct {
	status      string
	publishedAt time.Time
	evidence    int
}

// plantInsight 造一颗挂在 model 上的洞察，并按 opts 挂 evidence（每条都是**独立**的源指针）。
func plantInsight(t *testing.T, pool *pgxpool.Pool, q *canonicaldb.Queries,
	userID, modelID, statement string, opts insightOpts) string {
	t.Helper()
	ctx := context.Background()
	id := newID()
	if err := q.InsertInsight(ctx, canonicaldb.InsertInsightParams{
		ID: id, UserID: userID, PersonalModelID: modelID,
		Statement: statement, ClaimType: "trend",
		Status: orDefault(opts.status, "published"), Confidence: num(t, 0.75),
		TimeFrom: ts(time.Now().UTC().AddDate(0, 0, -7)), TimeTo: ts(time.Now().UTC()),
		GenerationModel: txt("test-model"), GenerationVersion: txt("v1"),
		GenerationRunID: txt(newID()), RejectReason: nullText(),
		GateDetail: txt(`{"gates":["schema","evidence"]}`), ModelVersion: i8(1),
	}); err != nil {
		t.Fatalf("InsertInsight: %v", err)
	}
	if !opts.publishedAt.IsZero() {
		if _, err := q.UpdateInsightStatus(ctx, canonicaldb.UpdateInsightStatusParams{
			ID: id, UserID: userID, Status: orDefault(opts.status, "published"),
			PublishedAt: ts(opts.publishedAt), RejectReason: nullText(),
			GateDetail: txt(`{"gates":["schema","evidence","temporal"]}`), ModelVersion: i8(2),
		}); err != nil {
			t.Fatalf("UpdateInsightStatus: %v", err)
		}
	}
	for i := 0; i < opts.evidence; i++ {
		evID := plantEvidenceOn(t, pool, userID, 0.8,
			time.Now().UTC().Add(-time.Duration(i+1)*24*time.Hour))
		if err := q.LinkInsightEvidence(ctx, canonicaldb.LinkInsightEvidenceParams{
			InsightID: id, EvidenceID: evID, UserID: userID, Role: "supporting",
		}); err != nil {
			t.Fatalf("LinkInsightEvidence: %v", err)
		}
	}
	return id
}

func orDefault(v, fallback string) string {
	if v == "" {
		return fallback
	}
	return v
}

func listIDs(t *testing.T, repo *Canonical, userID string, after domain.InsightKey,
	hasAfter bool, limit int) []domain.InsightListItem {
	t.Helper()
	rows, err := repo.ListInsightsForBrowsing(context.Background(), userID,
		domain.InsightReturnableStatuses, after, hasAfter, limit)
	if err != nil {
		t.Fatalf("ListInsightsForBrowsing: %v", err)
	}
	return rows
}

// G-05：无证据（或证据全被撤回）的洞察**不得**出现在列表里。
func TestG05InsightsWithoutLiveEvidenceAreNotListed(t *testing.T) {
	pool := poolForTest(t)
	q := canonicaldb.New(pool)
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })
	p := plantUser(t, pool, userID)
	repo := NewCanonical(pool)
	ctx := context.Background()

	live := plantInsight(t, pool, q, userID, p.modelID, "有活依据", insightOpts{
		publishedAt: time.Now().UTC(), evidence: 2})
	noEvidence := plantInsight(t, pool, q, userID, p.modelID, "一条依据都没有",
		insightOpts{publishedAt: time.Now().UTC(), evidence: 0})
	withdrawn := plantInsight(t, pool, q, userID, p.modelID, "依据全被撤回",
		insightOpts{publishedAt: time.Now().UTC(), evidence: 1})
	draft := plantInsight(t, pool, q, userID, p.modelID, "AI 候选内部态",
		insightOpts{status: "draft", publishedAt: time.Now().UTC(), evidence: 1})
	notPublished := plantInsight(t, pool, q, userID, p.modelID, "状态已 published 但没上线时刻",
		insightOpts{status: "published", evidence: 1})

	// 把 withdrawn 那颗的唯一依据标失效（只翻 source 状态、不删 evidence 行，O-6/O-7）。
	var sourceID string
	if err := pool.QueryRow(ctx, `SELECT e.source_record_id FROM evidence e
		JOIN insight_evidence ie ON ie.evidence_id = e.id
		WHERE ie.insight_id = $1 AND e.user_id = $2`, withdrawn, userID).Scan(&sourceID); err != nil {
		t.Fatalf("取 withdrawn 的源指针：%v", err)
	}
	if err := q.MarkSourceRecordDeleted(ctx, canonicaldb.MarkSourceRecordDeletedParams{
		ID: sourceID, UserID: userID}); err != nil {
		t.Fatalf("MarkSourceRecordDeleted: %v", err)
	}

	// 别人的一颗：同状态、有依据，但**不许**出现在这个用户的列表里（05 §20 的 user_id 作用域）。
	other := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, other) })
	op := plantUser(t, pool, other)
	foreign := plantInsight(t, pool, q, other, op.modelID, "别人的洞察",
		insightOpts{publishedAt: time.Now().UTC(), evidence: 1})

	rows := listIDs(t, repo, userID, domain.InsightKey{}, false, 50)
	got := map[string]int64{}
	for _, r := range rows {
		got[r.ID] = r.EvidenceCount
	}
	if _, ok := got[noEvidence]; ok {
		t.Error("G-05 破了：一条依据都没有的 published 洞察出现在列表里")
	}
	if _, ok := got[withdrawn]; ok {
		t.Error("G-05 破了：唯一依据已被撤回（source_records.status='deleted'）的洞察仍在列表里")
	}
	if _, ok := got[draft]; ok {
		t.Error("draft（AI 候选内部态）出现在内部面列表里")
	}
	if _, ok := got[notPublished]; ok {
		t.Error("published_at 为空的行出现在列表里 ⇒ 它是 keyset 游标的排序键，编不出游标")
	}
	if _, ok := got[foreign]; ok {
		t.Error("跨用户泄漏：别人的洞察出现在这个用户的列表里")
	}
	if n, ok := got[live]; !ok || n != 2 {
		t.Errorf("活洞察 = %+v（命中 %v），期望 evidence_count=2", n, ok)
	}
	// 正向证人：`withdrawn` 的 evidence 行**还在**（失效只翻源状态）。
	// 少了这一格，上面那条断言就可能是「行被删了」造成的假绿——那是两种完全不同的东西。
	if n := countWhere(t, pool, `SELECT count(*) FROM insight_evidence WHERE insight_id = $1`,
		withdrawn); n != 1 {
		t.Errorf("insight_evidence=%d，期望 1 ⇒ 撤回应是「留痕＋源失效」，不是删行", n)
	}
	if n := countWhere(t, pool, `SELECT count(*) FROM source_records WHERE id = $1 AND status = 'deleted'`,
		sourceID); n != 1 {
		t.Errorf("source_records 未按 deleted 标记（%d）⇒ 上面那条缺席是别的原因，判据没走到", n)
	}
}

// T3-01 §验收：keyset 分页在真库上的边界（limit=1、同刻两行的 tie-break）。
func TestT301KeysetPagingCoversEveryRowExactlyOnce(t *testing.T) {
	pool := poolForTest(t)
	q := canonicaldb.New(pool)
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })
	p := plantUser(t, pool, userID)
	repo := NewCanonical(pool)

	// 三颗同一时刻上线的洞察：排序键 (published_at, id) 的第二半在这里才是**唯一**的确定性来源。
	// 用 OFFSET 的实现在这种数据上会重复或漏行（并发插入一行就整体错位），
	// 所以工单要点名「分页边界」，而这一格只有真库答得了。
	at := time.Now().UTC().Truncate(time.Microsecond).Add(-time.Hour)
	ids := map[string]bool{}
	for i := 0; i < 3; i++ {
		id := plantInsight(t, pool, q, userID, p.modelID,
			"同刻洞察 "+string(rune('a'+i)), insightOpts{publishedAt: at, evidence: 1})
		ids[id] = true
	}
	// 再加一颗早一小时的，用来证明「翻到头」而不是「翻不完」。
	older := plantInsight(t, pool, q, userID, p.modelID, "早一小时",
		insightOpts{publishedAt: at.Add(-time.Hour), evidence: 1})
	ids[older] = true

	seen := map[string]bool{}
	cursor := domain.InsightKey{}
	hasCursor := false
	for page := 1; page <= 10; page++ {
		rows := listIDs(t, repo, userID, cursor, hasCursor, 1)
		if len(rows) > 1 {
			t.Fatalf("第 %d 页回了 %d 行，期望 LIMIT 1 生效", page, len(rows))
		}
		if len(rows) == 0 {
			break
		}
		row := rows[0]
		if seen[row.ID] {
			t.Fatalf("第 %d 页重复了 %s ⇒ 游标用的是 (published_at) 而不是 (published_at, id)", page, row.ID)
		}
		seen[row.ID] = true
		cursor = domain.InsightKey{PublishedAt: row.PublishedAt, ID: row.ID}
		hasCursor = true
	}
	if len(seen) != len(ids) {
		t.Errorf("翻页共见 %d 行，期望 %d ⇒ 同刻行被跳过或 LIMIT 语义变了", len(seen), len(ids))
	}
	for id := range ids {
		if !seen[id] {
			t.Errorf("%s 在翻页里从未出现", id)
		}
	}
}

// G-06：用户纠错**真落库**——四张表一起核，不看回执看了什么。
func TestG06CorrectFeedbackWritesAuthoritativeRows(t *testing.T) {
	pool := poolForTest(t)
	q := canonicaldb.New(pool)
	userID := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, userID) })
	p := plantUser(t, pool, userID)
	insightID := plantInsight(t, pool, q, userID, p.modelID, "待纠正的洞察",
		insightOpts{publishedAt: time.Now().UTC(), evidence: 1})

	svc := serviceFor(pool)
	ctx := context.Background()
	receipt, err := svc.RecordUserFeedback(ctx, domain.FeedbackProposal{
		UserID: userID, Type: domain.FeedbackCorrect, TargetType: domain.RefInsight,
		TargetID: insightID, CorrectionText: "其实是周五，不是周一",
		Metadata: map[string]string{"operation_id": "op-g6"}})
	if err != nil {
		t.Fatalf("RecordUserFeedback: %v", err)
	}
	if !receipt.Changed || receipt.TargetStatus != "corrected" {
		t.Errorf("回执 = %+v，期望 changed=true / status=corrected", receipt)
	}

	// ① 留痕：user_feedback 有行，正文与 metadata 都在（G-06 的字面对象）。
	var fbType, fbTarget, text, meta string
	if err := pool.QueryRow(ctx, `SELECT feedback_type, target_id, correction_text, metadata_json::text
		FROM user_feedback WHERE id = $1 AND user_id = $2`, receipt.ID, userID).
		Scan(&fbType, &fbTarget, &text, &meta); err != nil {
		t.Fatalf("读回 user_feedback：%v ⇒ 回执给了 id，库里却没有这行", err)
	}
	if fbType != "correct" || fbTarget != insightID || text != "其实是周五，不是周一" {
		t.Errorf("user_feedback = (%s,%s,%q)，期望 (correct, %s, 原话)", fbType, fbTarget, text, insightID)
	}
	if meta == "" || meta == "{}" {
		t.Errorf("metadata_json = %q，期望带 operation_id ⇒ 幂等键丢了就只能靠「最近一条同型」判重", meta)
	}

	// ② 改写：insights.status 按 O-9 矩阵变成 corrected（工单 §验收 的另一半）。
	var dbStatus string
	if err := pool.QueryRow(ctx, `SELECT status FROM insights WHERE id = $1 AND user_id = $2`,
		insightID, userID).Scan(&dbStatus); err != nil {
		t.Fatalf("读回 insights：%v", err)
	}
	if dbStatus != "corrected" {
		t.Errorf("库内 insights.status = %q，期望 corrected", dbStatus)
	}

	// ③ 账本：personal_model_versions 多一行，且它说的是「因为用户纠错」。
	// 少了这一格，「改了状态」与「改了状态并留下谁改的」在读日志的人是两件事。
	if n := countWhere(t, pool, `SELECT count(*) FROM personal_model_versions
		WHERE user_id = $1 AND version = $2 AND reason LIKE '%correct%'`,
		userID, receipt.ModelVersion); n != 1 {
		t.Errorf("版本账本行数=%d，期望 1（version=%d 且 reason 带 correct）", n, receipt.ModelVersion)
	}

	// ④ 事件：personal_model_events 里有一条 user_feedback_recorded（不变量 6 的下游半边）。
	// 表选它而不是 outbox_messages：`emit()` 先**无条件** AppendEvent，只有当事件类型
	// 有 routing key 时才再入队出箱（delivery.go:146-163）。
	// 拿出箱当「留痕」的判据，会把一次正常的「只记本地事件、不外投」读成丢了留痕。
	if n := countWhere(t, pool, `SELECT count(*) FROM personal_model_events
		WHERE user_id = $1 AND event_type = 'user_feedback_recorded'`, userID); n != 1 {
		t.Errorf("personal_model_events 反馈事件=%d，期望 1", n)
	}

	// ⑤ 这一颗洞察现在应当**仍然**出现在内部面列表里（corrected 属可回看集合，
	//    工单 T3-01 §怎么做.5「用户要能回看自己纠错过的」）。
	rows := listIDs(t, NewCanonical(pool), userID, domain.InsightKey{}, false, 50)
	var hit bool
	for _, r := range rows {
		if r.ID == insightID {
			hit = true
			if r.Status != "corrected" {
				t.Errorf("列表里状态 = %q，期望 corrected ⇒ 读路径没把改写后的状态搬出来", r.Status)
			}
		}
	}
	if !hit {
		t.Error("用户刚纠正过的洞察从列表里消失了 ⇒ 回看不了自己纠正过什么")
	}

	// ⑥ 重复同型：第二次 correct 必须被拒，且**不产生第二行**（07 §6）。
	_, err = svc.RecordUserFeedback(ctx, domain.FeedbackProposal{
		UserID: userID, Type: domain.FeedbackCorrect, TargetType: domain.RefInsight,
		TargetID: insightID, CorrectionText: "同一句话再说一遍"})
	if !errors.Is(err, domain.ErrDuplicateFeedback) {
		t.Errorf("第二次同型表态 err = %v，期望 ErrDuplicateFeedback", err)
	}
	if n := countWhere(t, pool, `SELECT count(*) FROM user_feedback WHERE user_id = $1`,
		userID); n != 1 {
		t.Errorf("user_feedback=%d，期望仍然 1 行 ⇒ 连点两次按钮往权威表写了历史", n)
	}

	// ⑦ 别人的洞察：必须被拒，且那边**一行都不许多**。
	other := newID()
	t.Cleanup(func() { cleanupCanonical(t, pool, other) })
	op := plantUser(t, pool, other)
	foreign := plantInsight(t, pool, q, other, op.modelID, "别人的洞察",
		insightOpts{publishedAt: time.Now().UTC(), evidence: 1})
	_, err = svc.RecordUserFeedback(ctx, domain.FeedbackProposal{
		UserID: userID, Type: domain.FeedbackReject, TargetType: domain.RefInsight,
		TargetID: foreign})
	if !errors.Is(err, domain.ErrInvalidOwnership) {
		t.Errorf("跨用户表态 err = %v，期望 ErrInvalidOwnership", err)
	}
	if n := countWhere(t, pool, `SELECT count(*) FROM user_feedback WHERE user_id = $1`,
		other); n != 0 {
		t.Errorf("别人的用户名下多出 %d 行反馈", n)
	}
	var foreignStatus string
	if err := pool.QueryRow(ctx, `SELECT status FROM insights WHERE id = $1 AND user_id = $2`,
		foreign, other).Scan(&foreignStatus); err != nil {
		t.Fatalf("读回别人的洞察：%v", err)
	}
	if foreignStatus != "published" {
		t.Errorf("别人的洞察状态被改成了 %q ⇒ 越权写穿过归属校验落到了改写", foreignStatus)
	}
}
