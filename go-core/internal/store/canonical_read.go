package store

import (
	"context"
	"errors"
	"fmt"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/store/canonicaldb"
)

// ── N9 权威读路径（05 §18 的依据展开端点；裁决七-7）───────────────────────────
//
// 这一组方法挂在**池面** `*Canonical` 上而不是事务面：读路径不是一次 Admit，
// 没有要一起提交的改写，把它塞进 `InTx` 等于为一件不存在的事占一条连接
// （与 N7 的 outbox 认领/确认同构，见 canonical_repo.go 的「池面动作」段）。
//
// 这里**没有任何判断**：状态可见性、裁剪上限、memory_refs 的构造全在
// `domain.InsightExplainer`（07:183-186「repository 持久化领域决策，不做决策」）。

// 编译期闸：读面接口一改，这里先红，而不是等 httpapi 装配时才发现。
var _ domain.InsightEvidenceReader = (*Canonical)(nil)

func (c *Canonical) GetInsightForReading(ctx context.Context,
	userID, insightID string) (domain.InsightReadable, error) {
	row, err := c.q.GetInsight(ctx, canonicaldb.GetInsightParams{
		ID: insightID, UserID: userID})
	if errors.Is(err, pgx.ErrNoRows) {
		// 与事务面同一条错误：别人的洞察与不存在的洞察必须是同一个形状（N9-4）。
		return domain.InsightReadable{}, fmt.Errorf("%w: %s", domain.ErrInsightNotFound, insightID)
	}
	if err != nil {
		return domain.InsightReadable{}, wrap("get insight for reading", err)
	}
	confidence, hasConfidence, err := numericToFloat(row.Confidence)
	if err != nil {
		return domain.InsightReadable{}, fmt.Errorf("store: insight %s confidence: %w", row.ID, err)
	}
	return domain.InsightReadable{
		ID: row.ID, UserID: row.UserID, Statement: row.Statement,
		ClaimType: row.ClaimType, Status: row.Status,
		Confidence: confidence, HasConfidence: hasConfidence,
		TimeFrom: timeOf(row.TimeFrom), HasTimeFrom: row.TimeFrom.Valid,
		TimeTo: timeOf(row.TimeTo), HasTimeTo: row.TimeTo.Valid,
	}, nil
}

func (c *Canonical) ListLiveEvidenceForInsight(ctx context.Context,
	userID, insightID string) ([]domain.EvidenceItem, error) {
	rows, err := c.q.ListLiveEvidenceForInsight(ctx,
		canonicaldb.ListLiveEvidenceForInsightParams{
			UserID: userID, InsightID: insightID})
	if err != nil {
		return nil, wrap("list live insight evidence", err)
	}
	items := make([]domain.EvidenceItem, 0, len(rows))
	for _, row := range rows {
		item, err := evidenceItem(row.ID, row.EvidenceType, row.EvidenceRole,
			row.Strength, row.Excerpt, row.ObservedAt, domain.EvidenceSource{
				RecordID: row.SourceRecordID, SourceType: row.SourceType,
				SourceTable: row.SourceTable, SourceID: row.SourceID,
			})
		if err != nil {
			return nil, fmt.Errorf("store: evidence %s: %w", row.ID, err)
		}
		items = append(items, item)
	}
	return items, nil
}

func (c *Canonical) CountInsightEvidenceSupport(ctx context.Context,
	userID, insightID string) (domain.SupportCounts, error) {
	row, err := c.q.CountInsightEvidenceSupport(ctx,
		canonicaldb.CountInsightEvidenceSupportParams{
			UserID: userID, InsightID: insightID})
	if err != nil {
		return domain.SupportCounts{}, wrap("count insight evidence support", err)
	}
	return domain.SupportCounts{Total: row.TotalSupport, Live: row.LiveSupport}, nil
}

func (c *Canonical) ListPatternsForInsightReading(ctx context.Context,
	userID, insightID string) ([]domain.PatternReadable, error) {
	rows, err := c.q.ListPatternsForInsight(ctx,
		canonicaldb.ListPatternsForInsightParams{
			UserID: userID, InsightID: insightID})
	if err != nil {
		return nil, wrap("list insight patterns", err)
	}
	patterns := make([]domain.PatternReadable, 0, len(rows))
	for _, row := range rows {
		confidence, hasConfidence, err := numericToFloat(row.Confidence)
		if err != nil {
			return nil, fmt.Errorf("store: pattern %s confidence: %w", row.ID, err)
		}
		patterns = append(patterns, domain.PatternReadable{
			ID: row.ID, PatternType: row.PatternType, Scope: textValue(row.Scope),
			Description: row.Description, Status: row.Status,
			Confidence: confidence, HasConfidence: hasConfidence,
			EvidenceCount: int64(row.EvidenceCount),
		})
	}
	return patterns, nil
}

// ListLivePatternEvidenceForInsight 是 02 §37 第二跳的一次往返：
// 不在应用层循环调 pattern 查询（N9-7，4c8G 单机不付 N+1 的钱）。
func (c *Canonical) ListLivePatternEvidenceForInsight(ctx context.Context,
	userID, insightID string) ([]domain.PatternEvidenceLink, error) {
	rows, err := c.q.ListLiveEvidenceForInsightPatterns(ctx,
		canonicaldb.ListLiveEvidenceForInsightPatternsParams{
			UserID: userID, InsightID: insightID})
	if err != nil {
		return nil, wrap("list live pattern evidence for insight", err)
	}
	links := make([]domain.PatternEvidenceLink, 0, len(rows))
	for _, row := range rows {
		item, err := evidenceItem(row.ID, row.EvidenceType, row.EvidenceRole,
			row.Strength, row.Excerpt, row.ObservedAt, domain.EvidenceSource{
				RecordID: row.SourceRecordID, SourceType: row.SourceType,
				SourceTable: row.SourceTable, SourceID: row.SourceID,
			})
		if err != nil {
			return nil, fmt.Errorf("store: evidence %s: %w", row.ID, err)
		}
		links = append(links, domain.PatternEvidenceLink{PatternID: row.PatternID, Item: item})
	}
	return links, nil
}

// evidenceItem 是两条一跳/二跳查询共用的搬运行：两个行结构字段同名，
// 但 sqlc 生成的是各自匿名结构，所以这里按值收而不是硬造一个接口。
func evidenceItem(id, evidenceType, role string, strength pgtype.Numeric,
	excerpt pgtype.Text, observedAt pgtype.Timestamptz,
	source domain.EvidenceSource) (domain.EvidenceItem, error) {
	value, hasValue, err := numericToFloat(strength)
	if err != nil {
		return domain.EvidenceItem{}, err
	}
	return domain.EvidenceItem{
		ID: id, EvidenceType: evidenceType, Role: role,
		Strength: value, HasStrength: hasValue,
		Excerpt: textValue(excerpt), HasExcerpt: excerpt.Valid && excerpt.String != "",
		ObservedAt: timeOf(observedAt), HasObservedAt: observedAt.Valid,
		Source: source,
	}, nil
}

// textValue：NULL 与空串都搬成空串，可空区分由调用方看 Valid（domain 只判 HasExcerpt）。
func textValue(t pgtype.Text) string {
	if !t.Valid {
		return ""
	}
	return t.String
}
