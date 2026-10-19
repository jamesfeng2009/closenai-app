// N3 Domain Gate 的真库集成测：把 internal/domain 的决策落到 `alembic upgrade head`
// 之后的实表上跑一遍。单测能证明判定逻辑，证明不了三件只有真库能答的事：
//
//  1. 落库列与 N2 DDL 对得上（NOT NULL、DECIMAL(5,4) 精度、VARCHAR 宽度——
//     在 fake store 里永远不会撞）；
//  2. 指纹幂等真的靠 ix_patterns_user_fingerprint_unique 兜底，而不是只靠代码记性；
//  3. 02 §32 的乐观并发在 READ COMMITTED 下确实是「0 行 + 整事务回滚」，不是死锁或脏写。
//
// 未设 TEST_DATABASE_URL 时跳过，与 N2 的 store 集成测同口径。
package store

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store/canonicaldb"
)

// planted 是一个用户的最小可用图：模型行 + 两条证据。
type planted struct {
	userID      string
	modelID     string
	evidenceIDs []string
}

func plantUser(t *testing.T, pool *pgxpool.Pool, userID string) planted {
	t.Helper()
	ctx := context.Background()
	q := canonicaldb.New(pool)
	p := planted{userID: userID, modelID: newID()}
	if err := q.InsertPersonalModel(ctx, canonicaldb.InsertPersonalModelParams{
		ID: p.modelID, UserID: userID,
	}); err != nil {
		t.Fatalf("InsertPersonalModel: %v", err)
	}
	for i := 0; i < 2; i++ {
		p.evidenceIDs = append(p.evidenceIDs, plantEvidence(t, pool, userID, 0.9, 3*i+1))
	}
	return p
}

// plantEvidence 落一条已算分的支撑证据，occurred_at 由 daysAgo 决定。
// 分日很重要：03 §6:161-163 把「同一天」也算重复观测，两条同日证据只顶 1 条独立支撑，
// 派生 claim 会被证据关拦下（N4 起）。
func plantEvidence(t *testing.T, pool *pgxpool.Pool, userID string, strength float64, daysAgo int) string {
	t.Helper()
	// 正午落点：避免 AddDate 后的时刻跨到相邻 UTC 日，日桶（dayKey）才稳定。
	occurred := time.Now().UTC().AddDate(0, 0, -daysAgo)
	occurred = time.Date(occurred.Year(), occurred.Month(), occurred.Day(),
		12, 0, 0, 0, time.UTC)
	return plantEvidenceOn(t, pool, userID, strength, occurred)
}

// plantEvidenceOn 同上，但时刻精确到分钟——用来构造「同一天的两条观测」。
func plantEvidenceOn(t *testing.T, pool *pgxpool.Pool, userID string, strength float64, occurred time.Time) string {
	t.Helper()
	ctx := context.Background()
	q := canonicaldb.New(pool)
	sourceID, err := q.InsertSourceRecord(ctx, canonicaldb.InsertSourceRecordParams{
		ID: newID(), UserID: userID, SourceType: "journal_entry",
		SourceTable: "memories", SourceID: newID(), OccurredAt: ts(occurred),
		RevisionOf: nullText(),
	})
	if err != nil {
		t.Fatalf("InsertSourceRecord: %v", err)
	}
	id := newID()
	if err := q.InsertEvidence(ctx, canonicaldb.InsertEvidenceParams{
		ID: id, UserID: userID, SourceRecordID: sourceID,
		EvidenceType: "direct", Role: "supporting", Strength: num(t, strength),
		Observation: txt("planted for the N3 gate integration"),
		Excerpt:     txt("excerpt"), MetadataJson: "{}", GenerationRunID: txt(newID()),
	}); err != nil {
		t.Fatalf("InsertEvidence: %v", err)
	}
	return id
}

func serviceFor(pool *pgxpool.Pool) *domain.Service {
	return domain.NewService(NewCanonical(pool), domain.DefaultPolicy())
}

// plantMemory 落一行 legacy `memories` 素材并返回它的主键。
//
// T2-03 起它是真库用例的**必需**夹具：`AdmitEvidence` 会按提案里的
// (source_table, source_id) 回查归属（internal/store/source_owner.go），
// 拿 `newID()` 那种随手编的 id 去提案，今天会变成「行不存在」而整条被拒。
// 列取 alembic 41cb43e22901:48-67 的 NOT NULL 面（`deleted` 有库级默认，
// 见 9e1b3d5f7a2c:27；memories 没有任何外键，所以不必先种 users 行）。
// 取值面照着 models/memory.py:20-38 的注释选：category=factual、source=self。
func plantMemory(t *testing.T, pool *pgxpool.Pool, userID string) string {
	t.Helper()
	ctx := context.Background()
	id := newID()
	if _, err := pool.Exec(ctx, `
		INSERT INTO memories (id, user_id, category, content, status,
		                      conviction_score, source, drift_score)
		VALUES ($1, $2, 'factual', $3, 'current', 0.8, 'self', 0.0)`,
		id, userID, "user wrote: stood up three times during the night."); err != nil {
		t.Fatalf("plant memories row: %v", err)
	}
	// memories 不在 canonicalTables 里（那是 N2 建的表清单），按主键单删。
	t.Cleanup(func() {
		if _, err := pool.Exec(ctx, `DELETE FROM memories WHERE id = $1`, id); err != nil {
			t.Errorf("cleanup memories %s: %v", id, err)
		}
	})
	return id
}

func baseCandidate(p planted, kind domain.Kind) domain.Candidate {
	now := time.Now().UTC().Truncate(time.Microsecond)
	c := domain.Candidate{
		UserID: p.userID, PersonalModelID: p.modelID, Kind: kind,
		EvidenceIDs: p.evidenceIDs, GenerationRunID: newID(),
		WindowFrom: now.AddDate(0, 0, -21), WindowTo: now, HasWindow: true,
		Safety:     &domain.Safety{Status: domain.SafetyOK},
		Confidence: 0.8,
	}
	switch kind {
	case domain.KindFact:
		c.Origin = "repeated"
		c.SubjectKey, c.Predicate = "user", "prefers_work_environment"
		c.ObjectValue = `{"value":"quiet"}`
	case domain.KindPattern:
		c.PatternType, c.SubjectKey, c.Scope = "temporal_rhythm", "user", "work"
		c.Description = "workday entries cluster after 23:00"
		c.Fingerprint = "fp-n3-" + p.userID
		c.DetectorVersion = "v1"
	case domain.KindInsight:
		c.ClaimType = "trend"
		c.Statement = "Over the last three weeks workday entries moved later."
		c.Confidence = 0.82
	}
	return c
}

func countWhere(t *testing.T, pool *pgxpool.Pool, query string, args ...any) int64 {
	t.Helper()
	var n int64
	if err := pool.QueryRow(context.Background(), query, args...).Scan(&n); err != nil {
		t.Fatalf("count (%s): %v", query, err)
	}
	return n
}

func scalar(t *testing.T, pool *pgxpool.Pool, query string, args ...any) (string, int64) {
	t.Helper()
	var s string
	var n int64
	if err := pool.QueryRow(context.Background(), query, args...).Scan(&s, &n); err != nil {
		t.Fatalf("scalar (%s): %v", query, err)
	}
	return s, n
}

func TestAdmitAcceptsFactAgainstRealSchema(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n3-fact"
	defer func() { cleanupCanonical(t, pool, userID) }()

	p := plantUser(t, pool, userID)
	c := baseCandidate(p, domain.KindFact)
	c.Origin = "direct" // 03:169-173：用户明说的路径直接进 active

	res, err := serviceFor(pool).Admit(context.Background(), c)
	if err != nil {
		t.Fatalf("Admit: %v", err)
	}
	if res.Action != domain.ActionInsert || res.Status != "active" {
		t.Fatalf("Action=%q Status=%q", res.Action, res.Status)
	}
	if res.ModelVersion != 1 {
		t.Errorf("ModelVersion=%d，accept 应 ++ 到 1", res.ModelVersion)
	}

	status, version := scalar(t, pool,
		`SELECT status, coalesce(model_version, 0) FROM personal_facts WHERE id = $1 AND user_id = $2`,
		res.ID, userID)
	if status != "active" || version != 1 {
		t.Errorf("库内行 status=%q version=%d", status, version)
	}
	// 置信落进 DECIMAL(5,4) 没被截成整数，说明 numOf 的标度口径对上了。
	var conf float64
	if err := pool.QueryRow(context.Background(),
		`SELECT confidence FROM personal_facts WHERE id = $1`, res.ID).Scan(&conf); err != nil {
		t.Fatalf("read confidence: %v", err)
	}
	if conf < 0.7999 || conf > 0.8001 {
		t.Errorf("confidence=%v，期望 0.8000", conf)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM fact_evidence WHERE fact_id = $1 AND user_id = $2`,
		res.ID, userID); n != int64(len(p.evidenceIDs)) {
		t.Errorf("证据回链 %d 条，期望 %d —— N9「为什么看到这条」就没源了", n, len(p.evidenceIDs))
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_events WHERE aggregate_id = $1 AND event_type = 'fact_admitted'`,
		res.ID); n != 1 {
		t.Errorf("accept 事件 %d 条，期望 1", n)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_versions WHERE user_id = $1 AND version = 1`,
		userID); n != 1 {
		t.Errorf("personal_model_versions %d 条，每次模型变更都要留一行「改了什么」（02 §24）", n)
	}
}

func TestAdmitRejectsThreeKindsAndKeepsAuditRow(t *testing.T) {
	pool := poolForTest(t)
	cases := []struct {
		name       string
		userID     string
		kind       domain.Kind
		mutate     func(*domain.Candidate)
		table      string
		wantReason domain.RejectReason
		wantStatus string
	}{
		{"无证据的 fact", "n3-rej-fact", domain.KindFact,
			func(c *domain.Candidate) { c.EvidenceIDs = nil },
			"personal_facts", domain.ReasonEvidenceMissing, "rejected"},
		{"因果句 pattern", "n3-rej-pattern", domain.KindPattern,
			func(c *domain.Candidate) {
				c.Description = "Overtime caused later check-ins, so work drives sleep."
			},
			"patterns", domain.ReasonClaimTypeRestricted, "rejected"},
		{"低置信 insight", "n3-rej-insight", domain.KindInsight,
			func(c *domain.Candidate) { c.Confidence = 0.33 },
			"insights", domain.ReasonConfidenceBelowThresh, "draft"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			defer func() { cleanupCanonical(t, pool, tc.userID) }()
			p := plantUser(t, pool, tc.userID)
			c := baseCandidate(p, tc.kind)
			tc.mutate(&c)

			res, err := serviceFor(pool).Admit(context.Background(), c)
			if err != nil {
				t.Fatalf("Admit: %v", err)
			}
			if res.Action != domain.ActionReject || res.Reason != tc.wantReason {
				t.Fatalf("Action=%q Reason=%q，期望 reject/%s", res.Action, res.Reason, tc.wantReason)
			}
			if res.Status != tc.wantStatus {
				t.Errorf("Status=%q，期望 %q", res.Status, tc.wantStatus)
			}

			// G-2：拒绝也留行，且 reject_reason 落的是机器可读码。
			if n := countWhere(t, pool,
				`SELECT count(*) FROM `+tc.table+` WHERE id = $1 AND user_id = $2`,
				res.ID, tc.userID); n != 1 {
				t.Fatalf("拒绝行没落库（rows=%d）", n)
			}
			var reason string
			if err := pool.QueryRow(context.Background(),
				`SELECT coalesce(reject_reason, '') FROM `+tc.table+` WHERE id = $1`,
				res.ID).Scan(&reason); err != nil {
				t.Fatalf("read reject_reason: %v", err)
			}
			if reason != tc.wantReason.String() {
				t.Errorf("reject_reason=%q，期望 %q", reason, tc.wantReason)
			}
			// 权威模型版本不动：拒绝不是一次模型变更。
			if version := countWhere(t, pool,
				`SELECT current_version FROM personal_models WHERE user_id = $1`, tc.userID); version != 0 {
				t.Errorf("current_version=%d，拒绝不得 ++", version)
			}
			if n := countWhere(t, pool,
				`SELECT count(*) FROM personal_model_events WHERE aggregate_id = $1 AND event_type = 'candidate_rejected'`,
				res.ID); n != 1 {
				t.Errorf("拒绝事件 %d 条，期望 1", n)
			}
			// 被拒的候选不得留下证据回链（否则 N9 会把拒绝项当依据展示）。
			if n := countWhere(t, pool,
				`SELECT count(*) FROM `+linkTable(tc.kind)+` WHERE `+linkColumn(tc.kind)+` = $1 AND user_id = $2`,
				res.ID, tc.userID); n != 0 {
				t.Errorf("拒绝行有 %d 条证据回链，不该有", n)
			}
		})
	}
}

func linkTable(kind domain.Kind) string {
	switch kind {
	case domain.KindFact:
		return "fact_evidence"
	case domain.KindPattern:
		return "pattern_evidence"
	default:
		return "insight_evidence"
	}
}

func linkColumn(kind domain.Kind) string {
	switch kind {
	case domain.KindFact:
		return "fact_id"
	case domain.KindPattern:
		return "pattern_id"
	default:
		return "insight_id"
	}
}

func TestAdmitInsightWritesGateDetailAndPatternLinks(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n3-insight"
	defer func() { cleanupCanonical(t, pool, userID) }()

	p := plantUser(t, pool, userID)
	svc := serviceFor(pool)

	pc := baseCandidate(p, domain.KindPattern)
	patternRes, err := svc.Admit(context.Background(), pc)
	if err != nil {
		t.Fatalf("Admit pattern: %v", err)
	}
	c := baseCandidate(p, domain.KindInsight)
	c.PatternIDs = []string{patternRes.ID}

	res, err := svc.Admit(context.Background(), c)
	if err != nil {
		t.Fatalf("Admit insight: %v", err)
	}
	if res.Status != "draft" {
		t.Errorf("Status=%q，接纳的 insight 落 draft（发布归 N5）", res.Status)
	}

	var detail string
	if err := pool.QueryRow(context.Background(),
		`SELECT coalesce(gate_detail, '') FROM insights WHERE id = $1`, res.ID).Scan(&detail); err != nil {
		t.Fatalf("read gate_detail: %v", err)
	}
	// gate_detail 落七关结论：D-3 的回链读路径靠它展示「为什么看到这条」，不重算。
	for _, name := range []string{"schema", "evidence", "confidence", "claim_type",
		"temporal", "safety", "duplicate"} {
		if !strings.Contains(detail, name) {
			t.Errorf("gate_detail 缺关 %s：%s", name, detail)
		}
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM insight_patterns WHERE insight_id = $1 AND pattern_id = $2 AND user_id = $3`,
		res.ID, patternRes.ID, userID); n != 1 {
		t.Errorf("insight_patterns=%d，支撑模式未回链", n)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM insight_evidence WHERE insight_id = $1 AND role = 'supporting'`,
		res.ID); n != 2 {
		t.Errorf("insight_evidence=%d，期望 2 条 supporting", n)
	}
	if v := countWhere(t, pool,
		`SELECT current_version FROM personal_models WHERE user_id = $1`, userID); v != 2 {
		t.Errorf("current_version=%d，两次 accept 应 ++ 到 2", v)
	}
}

// 03:310-311「reactivate equivalent pattern, no duplicates」。
func TestAdmitReactivatesPatternOnFingerprint(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n3-reactivate"
	defer func() { cleanupCanonical(t, pool, userID) }()

	p := plantUser(t, pool, userID)
	svc := serviceFor(pool)

	first, err := svc.Admit(context.Background(), baseCandidate(p, domain.KindPattern))
	if err != nil {
		t.Fatalf("Admit 1: %v", err)
	}
	// 第二次同指纹、新证据：必须刷同一行而不是插第二条。
	second := baseCandidate(p, domain.KindPattern)
	second.EvidenceIDs = []string{plantEvidence(t, pool, userID, 0.8, 5)}
	res, err := svc.Admit(context.Background(), second)
	if err != nil {
		t.Fatalf("Admit 2: %v", err)
	}
	if res.Action != domain.ActionReactivate || res.ID != first.ID {
		t.Fatalf("Action=%q ID=%q，期望 reactivate 到 %s", res.Action, res.ID, first.ID)
	}
	if n := countWhere(t, pool, `SELECT count(*) FROM patterns WHERE user_id = $1`, userID); n != 1 {
		t.Errorf("patterns=%d，指纹幂等失效", n)
	}
	// 幂等的库侧兜底：偏唯一索引在，插第二条会先撞 23505 而不是静默重复。
	if n := countWhere(t, pool,
		`SELECT count(*) FROM pg_indexes WHERE tablename = 'patterns'
           AND indexname = 'ix_patterns_user_fingerprint_unique'`); n != 1 {
		t.Errorf("指纹唯一索引不在库上，幂等只剩代码记性")
	}

	var count int64
	var supported bool
	if err := pool.QueryRow(context.Background(),
		`SELECT evidence_count, last_supported_at IS NOT NULL FROM patterns WHERE id = $1`,
		first.ID).Scan(&count, &supported); err != nil {
		t.Fatalf("read pattern: %v", err)
	}
	if count != 3 || !supported {
		t.Errorf("evidence_count=%d last_supported=%v，2+1 条证据应重算成 3", count, supported)
	}
	if res.EvidenceCount != 3 {
		t.Errorf("Result.EvidenceCount=%d，期望仓储重算值 3", res.EvidenceCount)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) - count(DISTINCT evidence_id) FROM pattern_evidence WHERE pattern_id = $1`,
		first.ID); n != 0 {
		t.Errorf("pattern_evidence 有 %d 条重复回链", n)
	}
	if v := countWhere(t, pool,
		`SELECT current_version FROM personal_models WHERE user_id = $1`, userID); v != 2 {
		t.Errorf("current_version=%d，reactivate 也算模型变更", v)
	}
	if n := countWhere(t, pool,
		`SELECT count(*) FROM personal_model_events WHERE event_type = 'pattern_reactivated' AND aggregate_id = $1`,
		first.ID); n != 1 {
		t.Errorf("pattern_reactivated 事件 %d 条", n)
	}
}

// 活事实查重只认非终局行：否则用户纠正一次同 key 就永久锁死。
func TestAdmitFactDuplicateOnlyAgainstLiveRows(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n3-fact-dup"
	defer func() { cleanupCanonical(t, pool, userID) }()

	p := plantUser(t, pool, userID)
	svc := serviceFor(pool)
	c := baseCandidate(p, domain.KindFact)

	if _, err := svc.Admit(context.Background(), c); err != nil {
		t.Fatalf("Admit 1: %v", err)
	}
	second, err := svc.Admit(context.Background(), c)
	if err != nil {
		t.Fatalf("Admit 2: %v", err)
	}
	if second.Action != domain.ActionReject || second.Reason != domain.ReasonDuplicate {
		t.Fatalf("Action=%q Reason=%q，期望 reject/duplicate", second.Action, second.Reason)
	}
	// 第 2 条是拒绝留痕行 ⇒ 共 2 行。
	if n := countWhere(t, pool, `SELECT count(*) FROM personal_facts WHERE user_id = $1`, userID); n != 2 {
		t.Errorf("personal_facts=%d，期望 2", n)
	}

	if _, err := pool.Exec(context.Background(),
		`UPDATE personal_facts SET status = 'invalidated' WHERE user_id = $1`, userID); err != nil {
		t.Fatalf("invalidate: %v", err)
	}
	third, err := svc.Admit(context.Background(), c)
	if err != nil {
		t.Fatalf("Admit 3: %v", err)
	}
	if third.Action != domain.ActionInsert {
		t.Errorf("Action=%q Reason=%q，终局行不该挡住新提法", third.Action, third.Reason)
	}
}

// 跨用户证据必须判 not_owned，不能判 not_found（两个码在 G-1 里是两回事）。
func TestAdmitRejectsForeignEvidence(t *testing.T) {
	pool := poolForTest(t)
	const victim, attacker = "n3-foreign-victim", "n3-foreign-attacker"
	defer func() {
		cleanupCanonical(t, pool, victim)
		cleanupCanonical(t, pool, attacker)
	}()

	victimChain := plantUser(t, pool, victim)
	attackerChain := plantUser(t, pool, attacker)
	c := baseCandidate(attackerChain, domain.KindInsight)
	c.EvidenceIDs = []string{attackerChain.evidenceIDs[0], victimChain.evidenceIDs[0]}

	res, err := serviceFor(pool).Admit(context.Background(), c)
	if err != nil {
		t.Fatalf("Admit: %v", err)
	}
	if res.Reason != domain.ReasonEvidenceNotOwned {
		t.Errorf("Reason=%q，期望 evidence_not_owned", res.Reason)
	}
	// 越权提案不得污染对方模型。
	if n := countWhere(t, pool,
		`SELECT count(*) FROM insight_evidence WHERE user_id = $1`, victim); n != 0 {
		t.Errorf("受害者模型被写入 %d 条回链", n)
	}
}

// 02 §32：被别人抢先 ++ 时整事务回滚，且冲突码原样上抛给 consumer 重试。
func TestAdmitVersionConflictRollsBack(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n3-conflict"
	defer func() { cleanupCanonical(t, pool, userID) }()

	p := plantUser(t, pool, userID)
	repo := NewCanonical(pool)
	if _, err := pool.Exec(context.Background(),
		`UPDATE personal_models SET current_version = 5 WHERE user_id = $1`, userID); err != nil {
		t.Fatalf("preset version: %v", err)
	}

	// 外部连接先 ++（此时事务还没碰过该行，不会互相等锁），
	// 事务再拿读到的旧值 5 去 bump ⇒ 条件更新 0 行。
	if _, err := pool.Exec(context.Background(),
		`UPDATE personal_models SET current_version = current_version + 1 WHERE user_id = $1`,
		userID); err != nil {
		t.Fatalf("racing bump: %v", err)
	}
	err := repo.InTx(context.Background(), func(ctx context.Context, st domain.Store) error {
		if _, err := st.BumpVersion(ctx, p.modelID, 5); errors.Is(err, domain.ErrVersionConflict) {
			return err
		} else {
			return errors.New("期望 ErrVersionConflict，实际: " + errText(err))
		}
	})
	if !errors.Is(err, domain.ErrVersionConflict) {
		t.Fatalf("InTx err=%v，期望 ErrVersionConflict 传出", err)
	}
	// 回滚面：权威行与事件都不许留下。
	for _, probe := range []struct{ table, query string }{
		{"personal_facts", `SELECT count(*) FROM personal_facts WHERE user_id = $1`},
		{"personal_model_events", `SELECT count(*) FROM personal_model_events WHERE user_id = $1`},
		{"personal_model_versions", `SELECT count(*) FROM personal_model_versions WHERE user_id = $1`},
	} {
		if n := countWhere(t, pool, probe.query, userID); n != 0 {
			t.Errorf("%s=%d，冲突事务不该留行", probe.table, n)
		}
	}
	if v := countWhere(t, pool,
		`SELECT current_version FROM personal_models WHERE user_id = $1`, userID); v != 6 {
		t.Errorf("current_version=%d，只应看到外部那一次 ++", v)
	}
}

func errText(err error) string {
	if err == nil {
		return "<nil>"
	}
	return err.Error()
}

// 新用户首投：模型行由 Service 定位/创建，且一人一行（02 §5）。
func TestAdmitCreatesPersonalModelWhenMissing(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n3-new-model"
	defer func() { cleanupCanonical(t, pool, userID) }()

	p := plantUser(t, pool, userID)
	if _, err := pool.Exec(context.Background(),
		`DELETE FROM personal_models WHERE user_id = $1`, userID); err != nil {
		t.Fatalf("delete model: %v", err)
	}
	svc := serviceFor(pool)

	c := baseCandidate(p, domain.KindFact)
	c.PersonalModelID = ""
	res, err := svc.Admit(context.Background(), c)
	if err != nil {
		t.Fatalf("Admit: %v", err)
	}
	// N4 起这条**该**晋升：两条分日的 0.9 证据过得了 03 §7:175-181 的推断门槛
	// （≥2 独立观测 + combined ≥0.75 + 无冲突），不再停在 candidate。
	if res.Status != "active" {
		t.Errorf("Status=%q，够格的推断事实应晋升 active（detail=%q）", res.Status, res.Detail)
	}
	modelID, models := scalar(t, pool,
		`SELECT min(id), count(*) FROM personal_models WHERE user_id = $1`, userID)
	if models != 1 {
		t.Fatalf("personal_models=%d，期望恰好建 1 行", models)
	}
	if bound, rows := scalar(t, pool,
		`SELECT min(personal_model_id), count(*) FROM personal_facts WHERE id = $1`, res.ID); rows != 1 || bound != modelID {
		t.Errorf("事实挂到 %q，模型行是 %q", bound, modelID)
	}

	pc := baseCandidate(p, domain.KindPattern)
	pc.PersonalModelID = ""
	if _, err := svc.Admit(context.Background(), pc); err != nil {
		t.Fatalf("Admit 2: %v", err)
	}
	if n := countWhere(t, pool, `SELECT count(*) FROM personal_models WHERE user_id = $1`, userID); n != 1 {
		t.Errorf("personal_models=%d，一人一模型", n)
	}
}

// D-6：Go 不持向量。这里顺带确认 Gate 写路径没有碰 embedding 类列。
func TestAdmitWritesFullPrecisionValues(t *testing.T) {
	pool := poolForTest(t)
	const userID = "n3-precision"
	defer func() { cleanupCanonical(t, pool, userID) }()

	p := plantUser(t, pool, userID)
	c := baseCandidate(p, domain.KindInsight)
	c.Confidence = 0.9999
	c.Statement = "过去三周的记录时间点后移了：" + strings.Repeat("夜", 30)

	res, err := serviceFor(pool).Admit(context.Background(), c)
	if err != nil {
		t.Fatalf("Admit: %v", err)
	}
	var conf float64
	var stmtLen int64
	if err := pool.QueryRow(context.Background(),
		`SELECT confidence, char_length(statement) FROM insights WHERE id = $1`, res.ID).
		Scan(&conf, &stmtLen); err != nil {
		t.Fatalf("read insight: %v", err)
	}
	if conf < 0.99985 || conf > 0.99995 {
		t.Errorf("confidence=%v，DECIMAL(5,4) 应存成 0.9999", conf)
	}
	if stmtLen != int64(utf8.RuneCountInString(c.Statement)) {
		t.Errorf("statement 字符数=%d", stmtLen)
	}
}
