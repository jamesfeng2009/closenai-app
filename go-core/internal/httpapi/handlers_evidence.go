package httpapi

import (
	"errors"
	"net/http"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// evidenceReadOff 是 D-3 回退开关的关闭值（`CLOSEN_INSIGHT_EVIDENCE_READ=off`）。
// 放在 Go 侧而不是只靠 Ingress 权重：N8 之后如果这条新读路径单独出问题，
// 关它不必回滚整个 Go 部署；而旧口径（Python 的 memory_refs + GET /memories）
// 一行没动，关掉就等于回到 D-3 拍板前的世界。
const evidenceReadOff = "off"

// N9 权威读路径：05 §18 的
// `GET /v1/personal-model/insights/:id/evidence`，落到本项目前缀是
// `/api/v1/personal-model/insights/{id}/evidence`（N9-2）。
//
// 这条端点**不在** contracts/endpoints.md §五 的冻结登记表里（N9-10）：那张表由
// `scripts/export_contract.py` 从 FastAPI 生成，且 `test_contract_freeze.py` 断言
// 「表行数 == 路由装饰器数」，往里加一行 Go 专有端点会直接打红 N0 的契约闸。
// 登记口径沿用上面 `/healthz` 的先例（就地注释 + endpoints.md §八 的说明文字）。

type evidencePayload struct {
	Insight         insightWire        `json:"insight"`
	EvidenceSummary summaryWire        `json:"evidence_summary"`
	Evidence        []evidenceWire     `json:"evidence"`
	Truncated       bool               `json:"truncated"`
	Patterns        []patternGroupWire `json:"patterns"`
}

type insightWire struct {
	ID         string         `json:"id"`
	Statement  string         `json:"statement"`
	ClaimType  string         `json:"claim_type"`
	Status     string         `json:"status"`
	Confidence *float64       `json:"confidence"`
	TimeRange  *timeRangeWire `json:"time_range"`
}

type timeRangeWire struct {
	From *time.Time `json:"from"`
	To   *time.Time `json:"to"`
}

// summaryWire 是 05 §19 的 `evidence_summary`：count 是**活支撑总数**（裁剪前），
// 所以响应里的 `evidence` 短于 count 时 `truncated` 一定为 true（N9-11 的
// 「如实给 truncated + 全量计数」）。strength 是数值而不是 "strong" 字符串（N9-12）。
type summaryWire struct {
	Count          int      `json:"count"`
	WithdrawnCount int      `json:"withdrawn_count"`
	Strength       *float64 `json:"strength"`
}

type evidenceWire struct {
	EvidenceID   string         `json:"evidence_id"`
	EvidenceType string         `json:"evidence_type"`
	Role         string         `json:"role"`
	Strength     *float64       `json:"strength"`
	Excerpt      *string        `json:"excerpt"`
	ObservedAt   *time.Time     `json:"observed_at"`
	Source       sourceWire     `json:"source"`
	MemoryRef    *memoryRefWire `json:"memory_ref"`
}

type sourceWire struct {
	RecordID    string `json:"record_id"`
	SourceType  string `json:"source_type"`
	SourceTable string `json:"source_table"`
	SourceID    string `json:"source_id"`
}

// memoryRefWire 的三键逐字沿用前端既有口径（N9-8）：
// `contracts/frontend-compat.md:91` 的 `memory_refs[].{memory_id,excerpt,created_at}`。
// 改名 = 依据回链静默消失（那张表给这条风险标的是「最危险的一类」）。
type memoryRefWire struct {
	MemoryID  string     `json:"memory_id"`
	Excerpt   *string    `json:"excerpt"`
	CreatedAt *time.Time `json:"created_at"`
}

type patternGroupWire struct {
	ID            string         `json:"id"`
	PatternType   string         `json:"pattern_type"`
	Scope         *string        `json:"scope"`
	Description   string         `json:"description"`
	Status        string         `json:"status"`
	Confidence    *float64       `json:"confidence"`
	EvidenceCount int64          `json:"evidence_count"`
	Truncated     bool           `json:"truncated"`
	Evidence      []evidenceWire `json:"evidence"`
}

func (s *Server) handleInsightEvidence(w http.ResponseWriter, r *http.Request) {
	// 两道守卫都走信封 404：kill switch 关闭时不是 503 也不是 403，而是
	// 「这条路径当前不提供」——与它从未存在过同形（D-3 的回退开关语义）。
	if s.evidence == nil || s.cfg.InsightEvidenceRead == evidenceReadOff {
		s.handleNotFound(w, r)
		return
	}
	userID, apiErr := s.middleware.RequireBearer(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	view, err := s.evidence.Explain(r.Context(), userID, r.PathValue("id"))
	if err != nil {
		if errors.Is(err, domain.ErrInsightNotFound) ||
			errors.Is(err, domain.ErrInsightNotVisible) {
			// 两种情形回**同一个** 40402：回 403 或给两条不同文案，
			// 等于替旁人确认「这颗 id 是真的」（N9-4）。
			writeJSONError(w, r, apierr.New(apierr.CodeResourceNotFound,
				"资源不存在 / Resource not found"))
			return
		}
		writeJSONError(w, r, asAPIError(err))
		return
	}
	rc := fromContext(r.Context())
	WriteSuccess(w, evidencePayloadOf(view), rc.traceID, rc.userID)
}

// evidencePayloadOf 是视图 → 线上形状的映射，不含任何判断
// （可见性、裁剪、memory_ref 的构造都在 domain.InsightExplainer 里）。
func evidencePayloadOf(view domain.InsightEvidenceView) evidencePayload {
	items := make([]evidenceWire, 0, len(view.Evidence))
	for _, item := range view.Evidence {
		items = append(items, evidenceWireOf(item))
	}
	groups := make([]patternGroupWire, 0, len(view.Patterns))
	for _, group := range view.Patterns {
		inner := make([]evidenceWire, 0, len(group.Evidence))
		for _, item := range group.Evidence {
			inner = append(inner, evidenceWireOf(item))
		}
		pattern := group.Pattern
		groups = append(groups, patternGroupWire{
			ID: pattern.ID, PatternType: pattern.PatternType,
			Scope: optionalString(pattern.Scope), Description: pattern.Description,
			Status: pattern.Status, Confidence: optionalFloat(pattern.Confidence, pattern.HasConfidence),
			EvidenceCount: pattern.EvidenceCount, Truncated: group.Truncated, Evidence: inner,
		})
	}
	insight := view.Insight
	wire := evidencePayload{
		Insight: insightWire{
			ID: insight.ID, Statement: insight.Statement,
			ClaimType: insight.ClaimType, Status: insight.Status,
			Confidence: optionalFloat(insight.Confidence, insight.HasConfidence),
		},
		EvidenceSummary: summaryWire{
			Count: view.Summary.Count, WithdrawnCount: view.Summary.WithdrawnCount,
			Strength: optionalFloat(view.Summary.Strength, view.Summary.HasStrength),
		},
		Evidence:  items,
		Truncated: view.Truncated,
		Patterns:  groups,
	}
	if insight.HasTimeFrom || insight.HasTimeTo {
		wire.Insight.TimeRange = &timeRangeWire{
			From: optionalTime(insight.TimeFrom, insight.HasTimeFrom),
			To:   optionalTime(insight.TimeTo, insight.HasTimeTo),
		}
	}
	return wire
}

func evidenceWireOf(item domain.EvidenceItem) evidenceWire {
	wire := evidenceWire{
		EvidenceID: item.ID, EvidenceType: item.EvidenceType, Role: item.Role,
		Strength:   optionalFloat(item.Strength, item.HasStrength),
		Excerpt:    optionalStringOf(item.Excerpt, item.HasExcerpt),
		ObservedAt: optionalTime(item.ObservedAt, item.HasObservedAt),
		Source: sourceWire{
			RecordID: item.Source.RecordID, SourceType: item.Source.SourceType,
			SourceTable: item.Source.SourceTable, SourceID: item.Source.SourceID,
		},
	}
	if item.MemoryRef != nil {
		wire.MemoryRef = &memoryRefWire{
			MemoryID:  item.MemoryRef.MemoryID,
			Excerpt:   optionalString(item.MemoryRef.Excerpt),
			CreatedAt: optionalTime(item.MemoryRef.CreatedAt, item.MemoryRef.HasCreatedAt),
		}
	}
	return wire
}

func optionalFloat(value float64, valid bool) *float64 {
	if !valid {
		return nil
	}
	rounded := float64(int64(value*10000+0.5)) / 10000
	return &rounded
}

func optionalString(value string) *string {
	return optionalStringOf(value, value != "")
}

func optionalStringOf(value string, valid bool) *string {
	if !valid {
		return nil
	}
	return &value
}

func optionalTime(value time.Time, valid bool) *time.Time {
	if !valid || value.IsZero() {
		return nil
	}
	return &value
}
