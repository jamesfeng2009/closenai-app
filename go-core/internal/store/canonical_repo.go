// N3 canonical 写读仓储：domain.Store / domain.Transactor 的 PostgreSQL 实现。
//
// 分层口径（V3 07:183-186「repository 持久化领域决策，不做决策」）：本文件只做
// 类型搬运与语句调用，**没有任何判定**。哪个 candidate 该拒、该落什么 status，
// 全部在 internal/domain 里；这里连 if/else 都不许出现业务条件。
//
// sqlc 生成物 internal/store/canonicaldb 禁手改（V3 08 §4），DDL 属主仍是
// backend/alembic（D-7），所以新增语句的次序是：改 db/queries → sqlc generate → 本文件加映射。
package store

import (
	"context"
	"errors"
	"fmt"
	"math/big"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgtype"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/idgen"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store/canonicaldb"
)

// Canonical 持有连接池；每次 Admit 通过 InTx 开一个事务，把 domain.Store 交给领域层。
// q 是**池面**的查询句柄，只服务于 N7 的投递动作（worker 的认领/确认与永久拒绝留痕）——
// 它们不属于任何一次 Admit，也就不能挂在建表的 Canonical 门面之外。
type Canonical struct {
	pool *pgxpool.Pool
	q    *canonicaldb.Queries
}

// 编译期闸：domain 的接口一改，这里先红，而不是等集成测跑到才发现。
var (
	_ domain.Transactor = (*Canonical)(nil)
	_ domain.Store      = (*canonicalTx)(nil)
	_ domain.Reader     = (*canonicalTx)(nil)
)

func NewCanonical(pool *pgxpool.Pool) *Canonical {
	return &Canonical{pool: pool, q: canonicaldb.New(pool)}
}

// InTx 把一次 Admit 的全部读写钉在同一事务：判定与写入之间不换快照，
// 否则「证据存在性」这一关可能读到并发删除的行。
//
// 隔离级别用 Postgres 默认 READ COMMITTED：02 §32 的乐观并发靠
// BumpPersonalModelVersion 的 0 行判定，不靠可重复读；串行化在 4c8G 单机上
// 会把 worker 的重试率抬起来（P3 风险登记）。
func (c *Canonical) InTx(ctx context.Context, fn func(context.Context, domain.Store) error) error {
	tx, err := c.pool.Begin(ctx)
	if err != nil {
		return fmt.Errorf("store: begin: %w", err)
	}
	defer func() { _ = tx.Rollback(ctx) }() // commit 之后 Rollback 返回 ErrTxDone，忽略
	if err := fn(ctx, &canonicalTx{q: canonicaldb.New(tx), tx: tx}); err != nil {
		return err
	}
	if err := tx.Commit(ctx); err != nil {
		return fmt.Errorf("store: commit: %w", err)
	}
	return nil
}

type canonicalTx struct {
	q *canonicaldb.Queries
	// tx 只服务于 sqlc 覆盖不到的 legacy 表（chat_messages 不在 schema 快照里，
	// 见 LastUserActivityAt）；除此之外一律走 q，不许在这里裸写 canonical 表的 SQL。
	tx pgx.Tx
}

// ── domain.Reader ───────────────────────────────────────────────────────────

func (t *canonicalTx) LoadEvidence(ctx context.Context, ids []string) ([]domain.Evidence, error) {
	rows, err := t.q.ListEvidenceForGate(ctx, ids)
	if err != nil {
		return nil, fmt.Errorf("store: load evidence: %w", err)
	}
	out := make([]domain.Evidence, 0, len(rows))
	for _, row := range rows {
		strength, has, err := numericToFloat(row.Strength)
		if err != nil {
			return nil, fmt.Errorf("store: evidence %s strength: %w", row.ID, err)
		}
		out = append(out, domain.Evidence{ID: row.ID, UserID: row.UserID,
			Strength: strength, HasStrength: has, Role: row.Role,
			SourceID: row.SourceRecordID, ObservedAt: timeOf(row.ObservedAt)})
	}
	return out, nil
}

// RecentUserRejection 查用户亲手否决过的同指纹 pattern（Gate 第 9 关的抑制签名）。
// 判定「窗口内算不算抑制」在 domain 里，这里只把天数搬成 interval 参数。
func (t *canonicalTx) RecentUserRejection(ctx context.Context, userID,
	fingerprint string, withinDays int) (string, bool, error) {
	id, err := t.q.RecentUserRejection(ctx, canonicaldb.RecentUserRejectionParams{
		UserID: userID, Fingerprint: textOf(fingerprint), Column3: int32(withinDays)})
	if errors.Is(err, pgx.ErrNoRows) {
		return "", false, nil
	}
	if err != nil {
		return "", false, wrap("recent user rejection", err)
	}
	return id, true, nil
}

func (t *canonicalTx) FindPatternByFingerprint(ctx context.Context, userID,
	fingerprint string) (string, bool, error) {
	row, err := t.q.FindPatternByFingerprint(ctx, canonicaldb.FindPatternByFingerprintParams{
		UserID: userID, Fingerprint: textOf(fingerprint)})
	if errors.Is(err, pgx.ErrNoRows) {
		return "", false, nil
	}
	if err != nil {
		return "", false, fmt.Errorf("store: find pattern: %w", err)
	}
	return row.ID, true, nil
}

func (t *canonicalTx) FindLiveFactKey(ctx context.Context, userID, subjectKey, predicate,
	objectValue string) (string, bool, error) {
	id, err := t.q.FindLiveFactKey(ctx, canonicaldb.FindLiveFactKeyParams{
		UserID: userID, SubjectKey: subjectKey, Predicate: predicate, ObjectValue: objectValue})
	if errors.Is(err, pgx.ErrNoRows) {
		return "", false, nil
	}
	if err != nil {
		return "", false, fmt.Errorf("store: find live fact: %w", err)
	}
	return id, true, nil
}

// ── 聚合根与版本 ────────────────────────────────────────────────────────────

// EnsurePersonalModel 定位该用户的模型行，没有就建（02 §5：一人一模型，
// ix_personal_models_user_unique 兜并发首建）。返回 id 与库里的 current_version。
func (t *canonicalTx) EnsurePersonalModel(ctx context.Context, userID string) (string, int64, error) {
	model, err := t.q.GetPersonalModel(ctx, userID)
	if err == nil {
		return model.ID, model.CurrentVersion, nil
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return "", 0, fmt.Errorf("store: get personal model: %w", err)
	}
	id := idgen.New()
	err = t.q.InsertPersonalModel(ctx, canonicaldb.InsertPersonalModelParams{
		ID: id, UserID: userID})
	if isUniqueViolation(err) {
		// 并发首建：另一个事务已提交该行，READ COMMITTED 下本语句之后的新快照读得到。
		model, err = t.q.GetPersonalModel(ctx, userID)
		if err != nil {
			return "", 0, fmt.Errorf("store: personal model 唯一冲突后重读: %w", err)
		}
		return model.ID, model.CurrentVersion, nil
	}
	if err != nil {
		return "", 0, fmt.Errorf("store: insert personal model: %w", err)
	}
	return id, 0, nil
}

// BumpVersion 走 02 §32 的条件更新：0 行即版本冲突，翻成 domain.ErrVersionConflict，
// 由调用方（MQ consumer）reload/retry，不是加全局用户锁。
func (t *canonicalTx) BumpVersion(ctx context.Context, modelID string, expected int64) (int64, error) {
	version, err := t.q.BumpPersonalModelVersion(ctx, canonicaldb.BumpPersonalModelVersionParams{
		ID: modelID, CurrentVersion: expected})
	if errors.Is(err, pgx.ErrNoRows) {
		return 0, domain.ErrVersionConflict
	}
	if err != nil {
		return 0, fmt.Errorf("store: bump personal model: %w", err)
	}
	return version, nil
}

func (t *canonicalTx) InsertVersion(ctx context.Context, row domain.VersionRow) error {
	return wrap("insert personal_model_versions", t.q.InsertPersonalModelVersion(ctx,
		canonicaldb.InsertPersonalModelVersionParams{
			ID: row.ID, UserID: row.UserID, PersonalModelID: row.PersonalModelID,
			Version: row.Version, Reason: row.Reason, SourceEventID: textOf(row.SourceEvent),
		}))
}

func (t *canonicalTx) AppendEvent(ctx context.Context, row domain.EventRow) error {
	return wrap("insert personal_model_events", t.q.InsertModelEvent(ctx,
		canonicaldb.InsertModelEventParams{
			ID: row.ID, UserID: row.UserID, PersonalModelID: row.PersonalModelID,
			EventType: row.EventType, AggregateType: row.AggregateType,
			AggregateID: textOf(row.AggregateID), ModelVersion: row.ModelVersion,
			Payload: row.Payload, GenerationRunID: textOf(row.GenerationRunID),
		}))
}

// ── 三类权威行写入 ──────────────────────────────────────────────────────────

func (t *canonicalTx) InsertFact(ctx context.Context, row domain.FactRow) error {
	confidence, err := numOf(row.Confidence, row.HasConfidence)
	if err != nil {
		return fmt.Errorf("store: fact %s confidence: %w", row.ID, err)
	}
	return wrap("insert personal_facts", t.q.InsertFact(ctx,
		canonicaldb.InsertFactParams{
			ID: row.ID, UserID: row.UserID, PersonalModelID: row.PersonalModelID,
			SubjectKey: row.SubjectKey, Predicate: row.Predicate,
			ObjectValue: row.ObjectValue, Origin: row.Origin, Status: row.Status,
			Confidence:      confidence,
			FirstObservedAt: timestamptzOf(row.ObservedAt),
			LastObservedAt:  timestamptzOf(row.ObservedAt),
			ModelVersion:    pgtype.Int8{Int64: row.ModelVersion, Valid: true},
			GenerationRunID: textOf(row.GenerationRunID), RejectReason: textOf(row.RejectReason),
		}))
}

func (t *canonicalTx) InsertPattern(ctx context.Context, row domain.PatternRow) error {
	confidence, err := numOf(row.Confidence, row.HasConfidence)
	if err != nil {
		return fmt.Errorf("store: pattern %s confidence: %w", row.ID, err)
	}
	return wrap("insert patterns", t.q.InsertPattern(ctx,
		canonicaldb.InsertPatternParams{
			ID: row.ID, UserID: row.UserID, PersonalModelID: row.PersonalModelID,
			PatternType: row.PatternType, SubjectKey: textOf(row.SubjectKey),
			Scope: textOf(row.Scope), Description: row.Description, Status: row.Status,
			Confidence: confidence, EvidenceCount: int32(row.EvidenceCount),
			ObservedFrom: optTimestamptz(row.WindowFrom, row.HasWindowFrom),
			ObservedTo:   optTimestamptz(row.WindowTo, row.HasWindowTo),
			// last_supported_at 不在这里写：接纳时的支持时间就是 created_at，
			// 之后的刷新只走 ReactivatePattern（03 §11 的生命周期算分归 N4）。
			DetectorVersion: textOf(row.DetectorVersion), Fingerprint: textOf(row.Fingerprint),
			ScoreDetail:     textOf(row.ScoreDetail),
			ModelVersion:    pgtype.Int8{Int64: row.ModelVersion, Valid: true},
			GenerationRunID: textOf(row.GenerationRunID), RejectReason: textOf(row.RejectReason),
		}))
}

func (t *canonicalTx) InsertInsight(ctx context.Context, row domain.InsightRow) error {
	confidence, err := numOf(row.Confidence, row.HasConfidence)
	if err != nil {
		return fmt.Errorf("store: insight %s confidence: %w", row.ID, err)
	}
	return wrap("insert insights", t.q.InsertInsight(ctx,
		canonicaldb.InsertInsightParams{
			ID: row.ID, UserID: row.UserID, PersonalModelID: row.PersonalModelID,
			Statement: row.Statement, ClaimType: row.ClaimType, Status: row.Status,
			Confidence:        confidence,
			TimeFrom:          optTimestamptz(row.WindowFrom, row.HasWindowFrom),
			TimeTo:            optTimestamptz(row.WindowTo, row.HasWindowTo),
			GenerationModel:   textOf(row.GenerationModel),
			GenerationVersion: textOf(row.GenerationVer),
			GenerationRunID:   textOf(row.GenerationRunID),
			RejectReason:      textOf(row.RejectReason), GateDetail: textOf(row.GateDetail),
			ModelVersion: pgtype.Int8{Int64: row.ModelVersion, Valid: true},
		}))
}

// ── 回链 ────────────────────────────────────────────────────────────────────

// LinkEvidence 按 kind 走三张关联表。role 恒为 supporting：contradicting/contextual
// 的判定需要证据语义，属 N4 的算分输入，Gate 不猜。
func (t *canonicalTx) LinkEvidence(ctx context.Context, kind domain.Kind,
	aggregateID, userID string, evidenceIDs []string) error {
	for _, evidenceID := range evidenceIDs {
		var err error
		switch kind {
		case domain.KindFact:
			err = t.q.LinkFactEvidence(ctx, canonicaldb.LinkFactEvidenceParams{
				FactID: aggregateID, EvidenceID: evidenceID, UserID: userID})
		case domain.KindPattern:
			err = t.q.LinkPatternEvidence(ctx, canonicaldb.LinkPatternEvidenceParams{
				PatternID: aggregateID, EvidenceID: evidenceID, UserID: userID})
		case domain.KindInsight:
			err = t.q.LinkInsightEvidence(ctx, canonicaldb.LinkInsightEvidenceParams{
				InsightID: aggregateID, EvidenceID: evidenceID, UserID: userID,
				Role: "supporting"})
		default:
			return fmt.Errorf("store: 未知 kind %q，无法回链证据", kind)
		}
		if err != nil {
			return wrap("link "+string(kind)+" evidence", err)
		}
	}
	return nil
}

func (t *canonicalTx) LinkPatterns(ctx context.Context, insightID, userID string,
	patternIDs []string) error {
	for _, patternID := range patternIDs {
		err := t.q.LinkInsightPattern(ctx, canonicaldb.LinkInsightPatternParams{
			InsightID: insightID, PatternID: patternID, UserID: userID})
		if err != nil {
			return wrap("link insight pattern", err)
		}
	}
	return nil
}

// ReactivatePattern：先补链，再按关联表实数重算 evidence_count（03:310-311 的
// 「reactivate, no duplicates」）。status 不在此翻——被拒行复活属 N4。
func (t *canonicalTx) ReactivatePattern(ctx context.Context, userID, patternID string,
	evidenceIDs []string) (int64, error) {
	if err := t.LinkEvidence(ctx, domain.KindPattern, patternID, userID, evidenceIDs); err != nil {
		return 0, err
	}
	count, err := t.q.ReactivatePattern(ctx, canonicaldb.ReactivatePatternParams{
		ID: patternID, UserID: userID})
	if errors.Is(err, pgx.ErrNoRows) {
		return 0, fmt.Errorf("store: 指纹命中的 pattern %s 在写入前消失了", patternID)
	}
	if err != nil {
		return 0, wrap("reactivate pattern", err)
	}
	return int64(count), nil
}

// ── N4：证据算分入口 + insight 重算的读写面 ─────────────────────────────────

// UpsertSourceRecord 登记 source 指针；同 (user_id, source_table, source_id) 复用既有行
// （08 §3 幂等：重跑管道不产生第二条登记），返回库里那一行的 id。
func (t *canonicalTx) UpsertSourceRecord(ctx context.Context,
	row domain.SourceRecordRow) (string, error) {
	id, err := t.q.InsertSourceRecord(ctx, canonicaldb.InsertSourceRecordParams{
		ID: row.ID, UserID: row.UserID, SourceType: row.SourceType,
		SourceTable: row.SourceTable, SourceID: row.SourceID,
		OccurredAt: optTimestamptz(row.OccurredAt, row.HasOccurredAt),
		RevisionOf: pgtype.Text{},
	})
	if err != nil {
		return "", wrap("upsert source record", err)
	}
	return id, nil
}

func (t *canonicalTx) InsertEvidence(ctx context.Context, row domain.EvidenceWrite) error {
	strength, err := numOf(row.Strength, row.HasStrength)
	if err != nil {
		return fmt.Errorf("store: evidence %s strength: %w", row.ID, err)
	}
	metadata := row.Metadata
	if metadata == "" {
		metadata = "{}" // metadata_json 是 NOT NULL DEFAULT '{}'，空串不是合法 JSON
	}
	return wrap("insert evidence", t.q.InsertEvidence(ctx,
		canonicaldb.InsertEvidenceParams{
			ID: row.ID, UserID: row.UserID, SourceRecordID: row.SourceRecordID,
			EvidenceType: row.EvidenceType, Role: row.Role, Strength: strength,
			Observation: textOf(row.Observation), Excerpt: textOf(row.Excerpt),
			MetadataJson: metadata, GenerationRunID: textOf(row.GenerationRunID),
		}))
}

func (t *canonicalTx) GetInsightState(ctx context.Context,
	userID, insightID string) (domain.InsightState, error) {
	row, err := t.q.GetInsight(ctx, canonicaldb.GetInsightParams{
		ID: insightID, UserID: userID})
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.InsightState{}, fmt.Errorf("%w: %s", domain.ErrInsightNotFound, insightID)
	}
	if err != nil {
		return domain.InsightState{}, wrap("get insight", err)
	}
	return domain.InsightState{
		ID: row.ID, UserID: row.UserID, PersonalModelID: row.PersonalModelID,
		Statement: row.Statement, ClaimType: row.ClaimType, Status: row.Status,
		TimeFrom: timeOf(row.TimeFrom), HasTimeFrom: row.TimeFrom.Valid,
		TimeTo: timeOf(row.TimeTo), HasTimeTo: row.TimeTo.Valid,
		ModelVersion: row.ModelVersion.Int64,
	}, nil
}

// LoadInsightEvidence 取一条 insight 的证据。role 取 **evidence.role**（证据自带立场），
// 不是 insight_evidence.role——后者由 Gate 写入且恒为 supporting（N3 不猜立场），
// 拿它判冲突等于永远判不出冲突。
func (t *canonicalTx) LoadInsightEvidence(ctx context.Context,
	userID, insightID string) ([]domain.EvidenceRow, error) {
	rows, err := t.q.ListEvidenceForInsight(ctx, canonicaldb.ListEvidenceForInsightParams{
		UserID: userID, InsightID: insightID})
	if err != nil {
		return nil, wrap("list insight evidence", err)
	}
	out := make([]domain.EvidenceRow, 0, len(rows))
	for _, row := range rows {
		strength, has, err := numericToFloat(row.Strength)
		if err != nil {
			return nil, fmt.Errorf("store: evidence %s strength: %w", row.ID, err)
		}
		out = append(out, domain.EvidenceRow{ID: row.ID, UserID: userID,
			Strength: strength, HasStrength: has, Role: row.EvidenceRole,
			SourceID: row.SourceRecordID, ObservedAt: timeOf(row.ObservedAt)})
	}
	return out, nil
}

func (t *canonicalTx) SetInsightStatus(ctx context.Context, row domain.InsightStatusRow) error {
	_, err := t.q.UpdateInsightStatus(ctx, canonicaldb.UpdateInsightStatusParams{
		ID: row.ID, UserID: row.UserID, Status: row.Status,
		// published_at 传 NULL：COALESCE 保住原发布时间，重算不是重新发布。
		PublishedAt:  pgtype.Timestamptz{},
		RejectReason: textOf(row.RejectReason), GateDetail: textOf(row.GateDetail),
		ModelVersion: pgtype.Int8{Int64: row.ModelVersion, Valid: true},
	})
	return wrap("set insight status", err)
}

// ── N5：Proactive 的评估输入与投递面（domain.Store 的主动触达那几条）─────────
//
// 只搬运，不判定：资格门的一切结论在 internal/domain/proactive_gate.go。

func (t *canonicalTx) InsertProactive(ctx context.Context, row domain.ProactiveRow) error {
	score, err := numOf(row.EligibilityScore, true)
	if err != nil {
		return fmt.Errorf("store: proactive %s eligibility_score: %w", row.ID, err)
	}
	return wrap("insert proactive", t.q.InsertProactive(ctx, canonicaldb.InsertProactiveParams{
		ID: row.ID, UserID: row.UserID, PersonalModelID: row.PersonalModelID,
		Status: row.Status, TriggerType: row.TriggerType, Message: textOf(row.Message),
		ScheduledAt:      optTimestamptz(row.ScheduledAt, row.HasScheduledAt),
		ExpiresAt:        optTimestamptz(row.ExpiresAt, row.HasExpiresAt),
		EligibilityScore: score, EligibilityDetail: textOf(row.EligibilityDetail),
		BlockReason:       textOf(row.BlockReason),
		RejectReason:      textOf(row.RejectReason),
		GenerationModel:   textOf(row.GenerationModel),
		GenerationVersion: textOf(row.GenerationVersion),
		GenerationRunID:   textOf(row.GenerationRunID),
		ModelVersion:      pgtype.Int8{Int64: row.ModelVersion, Valid: true},
		JourneyEventID:    textOf(row.JourneyEventID),
	}))
}

func (t *canonicalTx) GetProactiveState(ctx context.Context,
	userID, proactiveID string) (domain.ProactiveState, error) {
	row, err := t.q.GetProactiveState(ctx, canonicaldb.GetProactiveStateParams{
		ID: proactiveID, UserID: userID})
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.ProactiveState{}, fmt.Errorf("%w: %s", domain.ErrProactiveNotFound, proactiveID)
	}
	if err != nil {
		return domain.ProactiveState{}, wrap("get proactive state", err)
	}
	eligibility, hasEligibility, err := numericToFloat(row.EligibilityScore)
	if err != nil {
		return domain.ProactiveState{}, fmt.Errorf("store: proactive %s: %w", proactiveID, err)
	}
	return domain.ProactiveState{
		ID: row.ID, UserID: row.UserID, PersonalModelID: row.PersonalModelID,
		Status: row.Status, TriggerType: row.TriggerType, Message: row.Message.String,
		ScheduledAt: timeOf(row.ScheduledAt), HasScheduledAt: row.ScheduledAt.Valid,
		ExpiresAt: timeOf(row.ExpiresAt), HasExpiresAt: row.ExpiresAt.Valid,
		Eligibility: eligibility, HasEligibility: hasEligibility,
		Detail:          row.EligibilityDetail.String,
		BlockReason:     row.BlockReason.String,
		RejectReason:    row.RejectReason.String,
		JourneyEventID:  row.JourneyEventID.String,
		GenerationRunID: row.GenerationRunID.String,
		ModelVersion:    row.ModelVersion.Int64,
	}, nil
}

// ListRefStates 一次取齐七类引用行的当前状态。UNION 只返回查得到的行，
// 「报了却没回来」由 Freshness 关在集合层面判（internal/domain/proactive_gate.go）。
func (t *canonicalTx) ListRefStates(ctx context.Context, userID string,
	ids []string) ([]domain.RefState, error) {
	if len(ids) == 0 {
		return nil, nil
	}
	rows, err := t.q.ListRefStates(ctx, canonicaldb.ListRefStatesParams{
		UserID: userID, Column2: ids})
	if err != nil {
		return nil, wrap("list ref states", err)
	}
	out := make([]domain.RefState, 0, len(rows))
	for _, row := range rows {
		out = append(out, domain.RefState{
			RefType:  domain.RefType(row.RefType),
			ID:       row.ID,
			Status:   row.Status,
			AnchorAt: timeOf(row.AnchorAt),
			HasTime:  row.AnchorAt.Valid,
		})
	}
	return out, nil
}

func (t *canonicalTx) GetProactiveUserPrefs(ctx context.Context,
	userID string) (domain.UserPrefs, error) {
	row, err := t.q.GetUserNotificationPrefs(ctx, userID)
	if errors.Is(err, pgx.ErrNoRows) {
		// 不是故障：偏好行查不到是「无法确认同意」这一判定输入，Found=false 交领域层判。
		return domain.UserPrefs{UserID: userID}, nil
	}
	if err != nil {
		return domain.UserPrefs{}, wrap("get user prefs", err)
	}
	return domain.UserPrefs{
		UserID: row.ID, Timezone: row.Timezone, PushEnabled: row.PushEnabled,
		PushFrequency: row.PushFrequency, AIConsent: row.AiConsent, Tier: row.Tier,
		QuotaUsed: int(row.DailyQuotaUsed), CreatedAt: timeOf(row.CreatedAt), Found: true,
	}, nil
}

func (t *canonicalTx) ListProactiveHistory(ctx context.Context, userID string,
	since time.Time) ([]domain.ProactiveSendRow, error) {
	rows, err := t.q.ListProactiveHistory(ctx, canonicaldb.ListProactiveHistoryParams{
		UserID: userID, SentAt: timestamptzOf(since)})
	if err != nil {
		return nil, wrap("list proactive history", err)
	}
	out := make([]domain.ProactiveSendRow, 0, len(rows))
	for _, row := range rows {
		out = append(out, domain.ProactiveSendRow{
			ID: row.ID, Status: row.Status, TriggerType: row.TriggerType,
			Message:     row.Message.String,
			ScheduledAt: timeOf(row.ScheduledAt), HasScheduledAt: row.ScheduledAt.Valid,
			SentAt: timeOf(row.SentAt), HasSentAt: row.SentAt.Valid,
			RespondedAt: timeOf(row.RespondedAt), HasResponded: row.RespondedAt.Valid,
			Detail: row.EligibilityDetail.String,
		})
	}
	return out, nil
}

// LastUserActivityAt 是沉默阶梯的基准读数。chat_messages 是 legacy 表、不在 sqlc 的
// schema 快照里（见 db/queries/canonical.sql 同一条注释），所以这里是手写 SQL，
// 先例 internal/store/users.go:21-23「legacy 表 1:1 翻译」。口径与现行链
// chat_repository.get_last_user_message 一致：只认 role='user' 的消息。
func (t *canonicalTx) LastUserActivityAt(ctx context.Context,
	userID string) (time.Time, bool, error) {
	const lastUserMessageAt = `
		SELECT max(created_at) FROM chat_messages WHERE user_id = $1 AND role = 'user'`
	if t.tx == nil {
		return time.Time{}, false, errors.New(
			"store: LastUserActivityAt 需要事务连接，canonicalTx 未持有 pgx.Tx")
	}
	var at pgtype.Timestamptz
	if err := t.tx.QueryRow(ctx, lastUserMessageAt, userID).Scan(&at); err != nil {
		return time.Time{}, false, wrap("last user message at", err)
	}
	return at.Time, at.Valid, nil
}

func (t *canonicalTx) RecentCorrections(ctx context.Context, userID string,
	withinDays int) ([]string, error) {
	ids, err := t.q.RecentCorrectionTargets(ctx, canonicaldb.RecentCorrectionTargetsParams{
		UserID:    userID,
		Column2:   domain.ProactiveNegativeFeedback,
		CreatedAt: timestamptzOf(time.Now().UTC().AddDate(0, 0, -withinDays)),
	})
	return ids, wrap("recent corrections", err)
}

func (t *canonicalTx) SetProactiveStatus(ctx context.Context,
	row domain.ProactiveStatusRow) error {
	_, err := t.q.SetProactiveVerdict(ctx, canonicaldb.SetProactiveVerdictParams{
		ID: row.ID, UserID: row.UserID, Status: row.Status,
		BlockReason:       textOf(row.BlockReason),
		RejectReason:      textOf(row.RejectReason),
		EligibilityDetail: textOf(row.Detail),
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return fmt.Errorf("%w: %s", domain.ErrProactiveTerminal, row.ID)
	}
	return wrap("set proactive status", err)
}

func (t *canonicalTx) MarkProactiveSent(ctx context.Context,
	userID, proactiveID, detail string) (time.Time, error) {
	sentAt, err := t.q.MarkProactiveSentWithDetail(ctx,
		canonicaldb.MarkProactiveSentWithDetailParams{
			ID: proactiveID, UserID: userID, EligibilityDetail: textOf(detail)})
	if errors.Is(err, pgx.ErrNoRows) {
		return time.Time{}, fmt.Errorf("%w: %s", domain.ErrProactiveTerminal, proactiveID)
	}
	if err != nil {
		return time.Time{}, wrap("mark proactive sent", err)
	}
	return timeOf(sentAt), nil
}

func (t *canonicalTx) ExpireProactive(ctx context.Context, userID, proactiveID string) error {
	_, err := t.q.ExpireProactive(ctx, canonicaldb.ExpireProactiveParams{
		ID: proactiveID, UserID: userID})
	if errors.Is(err, pgx.ErrNoRows) {
		return fmt.Errorf("%w: %s", domain.ErrProactiveTerminal, proactiveID)
	}
	return wrap("expire proactive", err)
}

// LinkProactiveRefs 写两张回链表。goal/commitment/outcome 没有链表（N2 DDL 如此），
// 它们的引用只进 eligibility_detail 的 packet。
func (t *canonicalTx) LinkProactiveRefs(ctx context.Context, proactiveID, userID string,
	refs []domain.RefState) error {
	for _, ref := range refs {
		switch ref.RefType {
		case domain.RefInsight:
			if err := t.q.LinkProactiveInsight(ctx, canonicaldb.LinkProactiveInsightParams{
				ProactiveID: proactiveID, InsightID: ref.ID, UserID: userID}); err != nil {
				return wrap("link proactive insight", err)
			}
		case domain.RefPattern:
			if err := t.q.LinkProactivePattern(ctx, canonicaldb.LinkProactivePatternParams{
				ProactiveID: proactiveID, PatternID: ref.ID, UserID: userID}); err != nil {
				return wrap("link proactive pattern", err)
			}
		}
	}
	return nil
}

// ── 类型搬运：SQL NULL ⇄ Go ─────────────────────────────────────────────────

// ── N6：Outcome 闭环 / 版本化 / 源失效传播的写读面 ───────────────────────────
// 与前面每一段同口径：这里只搬类型与调语句，状态机、版本口径、传播规则全在
// internal/domain/service_outcome.go（07:183-186「repository 持久化领域决策，不做决策」）。

func (t *canonicalTx) InsertOutcome(ctx context.Context, row domain.OutcomeRow) error {
	return wrap("insert outcome", t.q.InsertOutcome(ctx, canonicaldb.InsertOutcomeParams{
		ID:              row.ID,
		UserID:          row.UserID,
		OutcomeType:     row.OutcomeType,
		Status:          row.Status,
		Result:          row.Result,
		Description:     textOf(row.Description),
		OccurredAt:      optTimestamptz(row.OccurredAt, row.HasOccurredAt),
		Origin:          row.Origin,
		ModelVersion:    pgtype.Int8{Int64: row.ModelVersion, Valid: true},
		GenerationRunID: textOf(row.GenerationRunID),
	}))
}

func (t *canonicalTx) LinkOutcomeTargets(ctx context.Context, outcomeID, userID string,
	targets []domain.TargetRef) error {
	for _, target := range targets {
		if err := t.q.LinkOutcome(ctx, canonicaldb.LinkOutcomeParams{
			OutcomeID: outcomeID, TargetType: string(target.Type),
			TargetID: target.ID, UserID: userID}); err != nil {
			return wrap("link outcome target", err)
		}
	}
	return nil
}

func (t *canonicalTx) MarkOutcomeStatus(ctx context.Context, userID, outcomeID string,
	status domain.OutcomeStatus) error {
	_, err := t.q.MarkOutcomeStatus(ctx, canonicaldb.MarkOutcomeStatusParams{
		ID: outcomeID, UserID: userID, Status: string(status)})
	if errors.Is(err, pgx.ErrNoRows) {
		// 0 行有两种事实：行不属于该用户，或它已不在可改写态（守卫只放行
		// recorded/candidate）。两者对领域层是同一个结论——这次改写没有发生。
		return fmt.Errorf("%w: %s", domain.ErrOutcomeNotFound, outcomeID)
	}
	return wrap("mark outcome status", err)
}

func (t *canonicalTx) InsertFeedback(ctx context.Context, row domain.FeedbackRow) error {
	return wrap("insert user feedback", t.q.InsertUserFeedback(ctx,
		canonicaldb.InsertUserFeedbackParams{
			ID: row.ID, UserID: row.UserID, FeedbackType: row.FeedbackType,
			TargetType: row.TargetType, TargetID: row.TargetID,
			CorrectionText: textOf(row.CorrectionText), MetadataJson: row.Metadata,
		}))
}

func (t *canonicalTx) LatestFeedbackForTarget(ctx context.Context, userID string,
	target domain.RefType, targetID string) (domain.LatestFeedback, error) {
	row, err := t.q.LatestFeedbackForTarget(ctx, canonicaldb.LatestFeedbackForTargetParams{
		UserID: userID, TargetType: string(target), TargetID: targetID})
	if errors.Is(err, pgx.ErrNoRows) {
		// 没有历史表态不是故障，是「第一次表态」这一判定输入。
		return domain.LatestFeedback{}, nil
	}
	if err != nil {
		return domain.LatestFeedback{}, wrap("latest feedback", err)
	}
	return domain.LatestFeedback{ID: row.ID, FeedbackType: row.FeedbackType, Found: true}, nil
}

func (t *canonicalTx) GetPatternState(ctx context.Context,
	userID, patternID string) (domain.PatternState, error) {
	row, err := t.q.GetPatternForUpdate(ctx, canonicaldb.GetPatternForUpdateParams{
		ID: patternID, UserID: userID})
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.PatternState{}, fmt.Errorf("%w: %s", domain.ErrPatternNotFound, patternID)
	}
	if err != nil {
		return domain.PatternState{}, wrap("get pattern state", err)
	}
	confidence, hasConfidence, err := numericToFloat(row.Confidence)
	if err != nil {
		return domain.PatternState{}, fmt.Errorf("store: pattern %s: %w", patternID, err)
	}
	return domain.PatternState{
		ID: row.ID, PersonalModelID: row.PersonalModelID,
		Status: domain.PatternStatus(row.Status), Confidence: confidence,
		HasConfidence: hasConfidence, EvidenceCount: int64(row.EvidenceCount),
		ScoreDetail: row.ScoreDetail.String, ModelVersion: row.ModelVersion.Int64,
	}, nil
}

func (t *canonicalTx) UpdatePatternLifecycle(ctx context.Context,
	row domain.PatternLifecycleRow) error {
	confidence, err := numOf(row.Confidence, row.HasConfidence)
	if err != nil {
		return fmt.Errorf("store: pattern %s confidence: %w", row.ID, err)
	}
	_, err = t.q.UpdatePatternLifecycle(ctx, canonicaldb.UpdatePatternLifecycleParams{
		ID: row.ID, UserID: row.UserID, Status: row.Status, Confidence: confidence,
		EvidenceCount: int32(row.EvidenceCount), ScoreDetail: textOf(row.ScoreDetail),
		ConfirmedAt:  optTimestamptz(time.Now().UTC(), row.ConfirmAt),
		RejectedAt:   optTimestamptz(time.Now().UTC(), row.RejectAt),
		ExpiredAt:    optTimestamptz(time.Now().UTC(), row.ExpireAt),
		RejectReason: textOf(row.RejectReason),
		ModelVersion: pgtype.Int8{Int64: row.ModelVersion, Valid: true},
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return fmt.Errorf("%w: %s", domain.ErrPatternNotFound, row.ID)
	}
	return wrap("update pattern lifecycle", err)
}

func (t *canonicalTx) UpdateFactStatus(ctx context.Context, row domain.FactStatusRow) error {
	now := time.Now().UTC()
	_, err := t.q.UpdateFactStatus(ctx, canonicaldb.UpdateFactStatusParams{
		ID: row.ID, UserID: row.UserID, Status: row.Status,
		ConfirmedAt:   optTimestamptz(now, row.Status == "active"),
		InvalidatedAt: optTimestamptz(now, row.InvalidateAt),
		RejectReason:  textOf(row.RejectReason),
		ModelVersion:  pgtype.Int8{Int64: row.ModelVersion, Valid: true},
	})
	if errors.Is(err, pgx.ErrNoRows) {
		return fmt.Errorf("%w: %s", domain.ErrFactNotFound, row.ID)
	}
	return wrap("update fact status", err)
}

func (t *canonicalTx) CompleteGoal(ctx context.Context, userID, goalID string) error {
	_, err := t.q.CompleteGoal(ctx, canonicaldb.CompleteGoalParams{ID: goalID, UserID: userID})
	if errors.Is(err, pgx.ErrNoRows) {
		return fmt.Errorf("%w: goal %s", domain.ErrInvalidOwnership, goalID)
	}
	return wrap("complete goal", err)
}

func (t *canonicalTx) CompleteCommitment(ctx context.Context, userID, commitmentID string) error {
	_, err := t.q.CompleteCommitment(ctx,
		canonicaldb.CompleteCommitmentParams{ID: commitmentID, UserID: userID})
	if errors.Is(err, pgx.ErrNoRows) {
		return fmt.Errorf("%w: commitment %s", domain.ErrInvalidOwnership, commitmentID)
	}
	return wrap("complete commitment", err)
}

// SkipCommitment 是 T4-01 交付的库面：**只有这一列的写法在这里，判据不在**。
//
// 为什么这一条不挂进 `domain.Store` 接口：接口挂上去就意味着 `active → skipped`
// 这条状态转移在 domain 里有了门却没有守卫，而 T4-02 的 `TransitionAction`
// 才是那个守卫（终态不许回退、重复同态幂等返回当前）。T4-01 先把「跳到哪张列、
// 时间怎么写」钉死并让它被真库测跑过一遍，T4-02 接线时这一格的坏法已经有闸兜着。
//
// 幂等那一半在 SQL 的 `COALESCE(skipped_at, now())` 上而不是在这里：
// 同一句跳过重复执行时，库里留的是**第一次**的时间。「什么时候跳过的」是审计事实，
// 被第二次点击刷掉等于改写历史；而 domain 层的「已跳过就直接返回当前」只兜得住
// 顺序执行的那一半，并发那半只有列上的 COALESCE 兜得住。
func (t *canonicalTx) SkipCommitment(ctx context.Context, userID, commitmentID string) error {
	_, err := t.q.SkipCommitment(ctx,
		canonicaldb.SkipCommitmentParams{ID: commitmentID, UserID: userID})
	if errors.Is(err, pgx.ErrNoRows) {
		return fmt.Errorf("%w: commitment %s", domain.ErrInvalidOwnership, commitmentID)
	}
	return wrap("skip commitment", err)
}

func (t *canonicalTx) ListInsightsForPattern(ctx context.Context, userID,
	patternID string) ([]domain.PatternInsightRef, error) {
	rows, err := t.q.ListInsightsForPattern(ctx, canonicaldb.ListInsightsForPatternParams{
		UserID: userID, PatternID: patternID})
	if err != nil {
		return nil, wrap("list insights for pattern", err)
	}
	out := make([]domain.PatternInsightRef, 0, len(rows))
	for _, row := range rows {
		out = append(out, domain.PatternInsightRef{ID: row.ID, Status: row.Status})
	}
	return out, nil
}

func (t *canonicalTx) ListQueuedProactives(ctx context.Context,
	userID string) ([]domain.QueuedProactive, error) {
	rows, err := t.q.ListQueuedProactives(ctx, userID)
	if err != nil {
		return nil, wrap("list queued proactives", err)
	}
	out := make([]domain.QueuedProactive, 0, len(rows))
	for _, row := range rows {
		out = append(out, domain.QueuedProactive{ID: row.ID, Status: row.Status,
			Trigger: row.TriggerType, Detail: row.EligibilityDetail.String})
	}
	return out, nil
}

func (t *canonicalTx) ListDerivedSupportForEvidence(ctx context.Context, userID,
	evidenceID string) ([]domain.DerivedSupport, error) {
	rows, err := t.q.ListDerivedSupportForEvidence(ctx,
		canonicaldb.ListDerivedSupportForEvidenceParams{UserID: userID, EvidenceID: evidenceID})
	if err != nil {
		return nil, wrap("list derived support", err)
	}
	out := make([]domain.DerivedSupport, 0, len(rows))
	for _, row := range rows {
		out = append(out, domain.DerivedSupport{Kind: domain.Kind(row.Kind), ID: row.ID,
			Status: row.Status, TotalSupport: row.TotalSupport, LiveSupport: row.LiveSupport})
	}
	return out, nil
}

// ── T2-06：source.deleted 的传播面（登记行只翻状态、不删行）─────────────────

// SourceRecordByPointer 按指针取登记行。`found=false` 与 error 分家（同 SourceRowOwner
// 的那条口径）：「这条来源从没登记过」是结论，「查库失败」才是错误。
func (t *canonicalTx) SourceRecordByPointer(ctx context.Context, userID, sourceTable,
	sourceID string) (domain.SourceRecordState, error) {
	row, err := t.q.GetSourceRecordByPointer(ctx, canonicaldb.GetSourceRecordByPointerParams{
		UserID: userID, SourceTable: sourceTable, SourceID: sourceID})
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.SourceRecordState{}, nil
	}
	if err != nil {
		return domain.SourceRecordState{}, wrap("get source record by pointer", err)
	}
	return domain.SourceRecordState{ID: row.ID, Status: row.Status, Found: true}, nil
}

// MarkSourceRecordDeletedOnce 的单向性在 SQL 谓词里（`status <> 'deleted'`），
// 不在这里补判断——这里只把「影响行数」读成一个布尔。
func (t *canonicalTx) MarkSourceRecordDeletedOnce(ctx context.Context, id, userID string) (bool, error) {
	rows, err := t.q.MarkSourceRecordDeletedOnce(ctx,
		canonicaldb.MarkSourceRecordDeletedOnceParams{ID: id, UserID: userID})
	if err != nil {
		return false, wrap("mark source record deleted", err)
	}
	return rows == 1, nil
}

func (t *canonicalTx) RestoreSourceRecordStatus(ctx context.Context, id, userID,
	status string) (int64, error) {
	rows, err := t.q.RestoreSourceRecordStatus(ctx,
		canonicaldb.RestoreSourceRecordStatusParams{ID: id, UserID: userID, Status: status})
	if err != nil {
		return 0, wrap("restore source record status", err)
	}
	return rows, nil
}

func (t *canonicalTx) ListEvidenceIDsForSource(ctx context.Context, userID,
	sourceRecordID string) ([]string, error) {
	ids, err := t.q.ListEvidenceForSource(ctx, canonicaldb.ListEvidenceForSourceParams{
		UserID: userID, SourceRecordID: sourceRecordID})
	if err != nil {
		return nil, wrap("list evidence for source", err)
	}
	return ids, nil
}

// ── N7 投递面：事务内写入（18 §2「写权威态 + insert outbox」同一个 BEGIN/COMMIT）──

// EnqueueOutbox 写一行待投递消息。
//
// 这里刻意不把 UNIQUE(message_id) 撞车当「重复」咽掉（不像 InsertInboxMessage 那样
// DO NOTHING）：message_id 取事件行 PK（N7-4），同一事件入队两次是编程错误，
// 静默忽略会让「投递面凭空少一行」这类缺陷永远查不出来。
func (t *canonicalTx) EnqueueOutbox(ctx context.Context, row domain.OutboxRow) error {
	_, err := t.q.InsertOutboxMessage(ctx, canonicaldb.InsertOutboxMessageParams{
		ID: row.ID, MessageID: row.MessageID, MessageType: row.MessageType,
		SchemaVersion: int32(row.SchemaVersion), RoutingKey: row.RoutingKey,
		UserID: row.UserID, CorrelationID: row.CorrelationID,
		CausationID: row.CausationID, TraceID: row.TraceID, Payload: row.Payload,
	})
	return wrap("insert outbox_messages", err)
}

// InsertInbox 认领幂等位。0 行（ON CONFLICT DO NOTHING 没插进去）＝该消费者已经
// 见过这条 message_id，业务副作用据此跳过（18 §8 的 duplicate 分支）。
func (t *canonicalTx) InsertInbox(ctx context.Context, row domain.InboxRow) (bool, error) {
	_, err := t.q.InsertInboxMessage(ctx, canonicaldb.InsertInboxMessageParams{
		ID: row.ID, MessageID: row.MessageID, MessageType: row.MessageType,
		Consumer: row.Consumer})
	if errors.Is(err, pgx.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, wrap("insert inbox_messages", err)
	}
	return true, nil
}

func (t *canonicalTx) GetInboxStatus(ctx context.Context, messageID,
	consumer string) (domain.InboxStatus, error) {
	row, err := t.q.GetInboxStatus(ctx, canonicaldb.GetInboxStatusParams{
		MessageID: messageID, Consumer: consumer})
	if errors.Is(err, pgx.ErrNoRows) {
		return domain.InboxStatus{Found: false}, nil
	}
	if err != nil {
		return domain.InboxStatus{}, wrap("get inbox status", err)
	}
	return domain.InboxStatus{Status: row.Status, AttemptCount: int(row.AttemptCount),
		Found: true}, nil
}

// MarkInboxProcessed 把幂等行落到 processed（18 §8 的 COMMIT 前置动作）。
//
// 「本事务刚插入的行现在找不到」不是并发常态而是编程错误，所以 0 行翻成错误让事务
// 回滚，而不是放行一个没有幂等记录的提交。
func (t *canonicalTx) MarkInboxProcessed(ctx context.Context, messageID,
	consumer string) error {
	_, err := t.q.MarkInboxProcessed(ctx, canonicaldb.MarkInboxProcessedParams{
		MessageID: messageID, Consumer: consumer})
	if errors.Is(err, pgx.ErrNoRows) {
		return fmt.Errorf("store: mark inbox processed: 幂等行 %s/%s 不在本事务的写入结果里",
			messageID, consumer)
	}
	return wrap("mark inbox processed", err)
}

// DeletePublishedOutboxBefore / DeleteProcessedInboxBefore 只搬走已完成的投递
// （18 §13；「哪些状态算已完成」写在 SQL 的 WHERE 里，这里不重判）。
func (t *canonicalTx) DeletePublishedOutboxBefore(ctx context.Context,
	before time.Time) (int64, error) {
	deleted, err := t.q.DeletePublishedOutboxBefore(ctx, timestamptzOf(before))
	if err != nil {
		return 0, wrap("delete published outbox", err)
	}
	return deleted, nil
}

func (t *canonicalTx) DeleteProcessedInboxBefore(ctx context.Context,
	before time.Time) (int64, error) {
	deleted, err := t.q.DeleteProcessedInboxBefore(ctx, timestamptzOf(before))
	if err != nil {
		return 0, wrap("delete processed inbox", err)
	}
	return deleted, nil
}

// ── N7 投递面：池面动作（outbox worker 用，18 §5/§6；不隶属任何一次 Admit）────
//
// 认领语句是自成一体的单条 SQL：CTE 里的 FOR UPDATE SKIP LOCKED 与外层 UPDATE 在
// **同一个隐式事务**里完成，锁只活到语句结束，所以不需要外面再包一层事务——
// 把网络发布留在事务里只会让租约跟着发布超时走。

func (c *Canonical) ClaimOutboxBatch(ctx context.Context, batchLimit,
	leaseSeconds int) ([]domain.OutboxClaim, error) {
	rows, err := c.q.ClaimOutboxBatch(ctx, canonicaldb.ClaimOutboxBatchParams{
		LeaseSeconds: int32(leaseSeconds), BatchLimit: int32(batchLimit)})
	if err != nil {
		return nil, wrap("claim outbox batch", err)
	}
	claims := make([]domain.OutboxClaim, 0, len(rows))
	for _, row := range rows {
		claims = append(claims, domain.OutboxClaim{
			ID: row.ID, MessageID: row.MessageID, MessageType: row.MessageType,
			SchemaVersion: int(row.SchemaVersion), RoutingKey: row.RoutingKey,
			UserID: row.UserID, CorrelationID: row.CorrelationID,
			CausationID: row.CausationID, TraceID: row.TraceID, Payload: row.Payload,
			AttemptCount: int(row.AttemptCount), LeaseExpiresAt: timeOf(row.AvailableAt),
		})
	}
	return claims, nil
}

// MarkOutboxPublished 只在 broker 确认之后调用（18 §6）。返回 false 表示
// `AND status='publishing'` 的租约守卫没放行——这一笔的认领权已被别的 worker 接管，
// 调用方按「我没发布成」处理，不是异常。
func (c *Canonical) MarkOutboxPublished(ctx context.Context, id string) (bool, error) {
	_, err := c.q.MarkOutboxPublished(ctx, id)
	if errors.Is(err, pgx.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, wrap("mark outbox published", err)
	}
	return true, nil
}

// MarkOutboxRetry 置 failed 并把 available_at 推到 now()+retryAfter（N7-2 的
// 一列两用）。退避时长由 domain.DeliveryPolicy.RetryDelay 算好，这里只换算成秒。
func (c *Canonical) MarkOutboxRetry(ctx context.Context, id string,
	retryAfter time.Duration, lastError string) (bool, error) {
	_, err := c.q.MarkOutboxRetry(ctx, canonicaldb.MarkOutboxRetryParams{
		LastError: textOf(lastError), ID: id,
		RetryAfterSeconds: int32(retryAfter / time.Second)})
	if errors.Is(err, pgx.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, wrap("mark outbox retry", err)
	}
	return true, nil
}

func (c *Canonical) MarkOutboxDeadLettered(ctx context.Context, id,
	lastError string) (bool, error) {
	_, err := c.q.MarkOutboxDeadLettered(ctx, canonicaldb.MarkOutboxDeadLetteredParams{
		ID: id, LastError: textOf(lastError)})
	if errors.Is(err, pgx.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, wrap("mark outbox dead lettered", err)
	}
	return true, nil
}

// RecordInboxFailure 是 14 §7 第三类 ACK 的记账面：永久拒绝必须留痕，而这笔写入
// 发生在消费事务**之外**（domain.Transactor 上单独一个方法的原因见那里的注释）。
func (c *Canonical) RecordInboxFailure(ctx context.Context,
	row domain.InboxFailureRow) error {
	_, err := c.q.RecordInboxFailure(ctx, canonicaldb.RecordInboxFailureParams{
		ID: row.ID, MessageID: row.MessageID, MessageType: row.MessageType,
		Consumer: row.Consumer, ErrorCode: textOf(row.ErrorCode)})
	return wrap("record inbox failure", err)
}

func wrap(what string, err error) error {
	if err != nil {
		return fmt.Errorf("store: %s: %w", what, err)
	}
	return nil
}

func isUniqueViolation(err error) bool {
	var pgErr *pgconn.PgError
	return errors.As(err, &pgErr) && pgErr.Code == uniqueViolation
}

func textOf(s string) pgtype.Text {
	return pgtype.Text{String: s, Valid: s != ""}
}

func timestamptzOf(t time.Time) pgtype.Timestamptz {
	return pgtype.Timestamptz{Time: t, Valid: true}
}

// timeOf 把可空 timestamptz 搬成 time.Time；NULL ⇒ 零值（domain 用 IsZero 判缺）。
func timeOf(ts pgtype.Timestamptz) time.Time {
	if !ts.Valid {
		return time.Time{}
	}
	return ts.Time
}

func optTimestamptz(t time.Time, valid bool) pgtype.Timestamptz {
	if !valid || t.IsZero() {
		return pgtype.Timestamptz{}
	}
	return pgtype.Timestamptz{Time: t, Valid: true}
}

// numericOf / numericToFloat 走 DECIMAL(5,4)：与列定义同标度，手工缩放而非 big.Rat，
// 后者会把 9000/10000 约分成 9/10 从而错位三个数量级（N2 集成测同口径）。
func numOf(v float64, valid bool) (pgtype.Numeric, error) {
	if !valid {
		return pgtype.Numeric{}, nil
	}
	if v < 0 || v > 1 {
		return pgtype.Numeric{}, fmt.Errorf("%v 越出 [0,1]，DECIMAL(5,4) 存不下", v)
	}
	return pgtype.Numeric{
		Int:   new(big.Int).SetInt64(int64(v*10000 + 0.5)),
		Exp:   -4,
		Valid: true,
	}, nil
}

func numericToFloat(n pgtype.Numeric) (float64, bool, error) {
	if !n.Valid {
		return 0, false, nil
	}
	value, err := n.Float64Value()
	if err != nil {
		return 0, false, fmt.Errorf("store: numeric -> float64: %w", err)
	}
	if !value.Valid {
		return 0, false, nil
	}
	return value.Float64, true, nil
}
