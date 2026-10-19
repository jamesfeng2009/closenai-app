// T3-01：`/internal/v3/insights` 的读面 handler。
//
// 这一层**只做形状与错误映射**，可见性、状态集合、翻页判据全在 domain.InsightBrowser
// （分层口径同 handlers_evidence.go：store 搬行、domain 判断、httpapi 编码）。
// 两条端点都不出现在 contracts/endpoints.md 的冻结登记表里：那张表由 FastAPI 生成，
// 客户端契约里没有这几条 path（ADR-002），登记面是 ADR 七-10。
package httpapi

import (
	"errors"
	"net/http"
	"strconv"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

type v3InsightListPayload struct {
	Items []v3InsightListItemWire `json:"items"`
	// NextCursor 空串＝后面没有行了。它是**不透明串**：调用方不该解析或拼装它，
	// 排序键将来换成 confidence 也不动调用方（insight_browse.go 的 Encode 注释同口径）。
	NextCursor string `json:"next_cursor"`
}

type v3InsightListItemWire struct {
	ID            string    `json:"id"`
	Statement     string    `json:"statement"`
	ClaimType     string    `json:"claim_type"`
	Status        string    `json:"status"`
	Confidence    *float64  `json:"confidence"`
	PublishedAt   time.Time `json:"published_at"`
	EvidenceCount int64     `json:"evidence_count"`
}

// v3InsightDetailPayload 比工单 §怎么做.4 多的两格是有意的：
// `evidence_summary`/`truncated` 是 N9 那条端点的既有形状，详情复用同一个解释器视图，
// 不给它们就等于让同一颗洞察在两个面上给出不一致的依据条数（裁剪前 vs 裁剪后）。
type v3InsightDetailPayload struct {
	ID              string             `json:"id"`
	Statement       string             `json:"statement"`
	ClaimType       string             `json:"claim_type"`
	Status          string             `json:"status"`
	Confidence      *float64           `json:"confidence"`
	TimeRange       *timeRangeWire     `json:"time_range"`
	EvidenceSummary summaryWire        `json:"evidence_summary"`
	Evidence        []evidenceWire     `json:"evidence"`
	Truncated       bool               `json:"truncated"`
	Patterns        []patternGroupWire `json:"patterns"`
	// published_at 单列在最后：它来自列表那一页的排序键，详情视图（N9 的 InsightReadable）
	// 没有这一列，**留 null 而不是猜一个**——假装有值比空着更难归因。
	PublishedAt *time.Time `json:"published_at"`
}

func (s *Server) handleV3Insights(w http.ResponseWriter, r *http.Request) {
	userID, r, apiErr := s.requireInternalService(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	q := r.URL.Query()
	limit, apiErr := parseV3Limit(q.Get("limit"))
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	view, err := s.v3.Insights.List(r.Context(), userID, q.Get("status"), limit, q.Get("cursor"))
	if err != nil {
		if errors.Is(err, domain.ErrInsightCursorInvalid) {
			// 显式 400，不是「当成第一页」：静默回第一页会让翻页方永远拿不到后面的行，
			// 而它看到的状态码是 200（insight_browse.go:57 同一条理由）。
			writeJSONError(w, r, apierr.New(apierr.CodeInvalidParams,
				"翻页游标无法识别 / Unrecognized page cursor"))
			return
		}
		writeJSONError(w, r, asAPIError(err))
		return
	}
	items := make([]v3InsightListItemWire, 0, len(view.Items))
	for _, item := range view.Items {
		items = append(items, v3InsightListItemWire{
			ID: item.ID, Statement: item.Statement, ClaimType: item.ClaimType,
			Status: item.Status, Confidence: optionalFloat(item.Confidence, item.HasConfidence),
			PublishedAt: item.PublishedAt, EvidenceCount: item.EvidenceCount,
		})
	}
	rc := fromContext(r.Context())
	WriteSuccess(w, v3InsightListPayload{Items: items, NextCursor: view.NextCursor},
		rc.traceID, rc.userID)
}

func (s *Server) handleV3InsightDetail(w http.ResponseWriter, r *http.Request) {
	userID, r, apiErr := s.requireInternalService(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	view, err := s.v3.Insights.Detail(r.Context(), userID, r.PathValue("id"))
	if err != nil {
		// 与 N9 那条端点回**同一个** 40402：区分「不存在」与「不该给你看」，
		// 等于替旁人确认这颗 id 是真的（handlers_evidence.go:111 的判据一字未变）。
		if errors.Is(err, domain.ErrInsightNotFound) ||
			errors.Is(err, domain.ErrInsightNotVisible) {
			writeJSONError(w, r, apierr.New(apierr.CodeResourceNotFound,
				"资源不存在 / Resource not found"))
			return
		}
		writeJSONError(w, r, asAPIError(err))
		return
	}
	detail := evidencePayloadOf(view)
	payload := v3InsightDetailPayload{
		ID: detail.Insight.ID, Statement: detail.Insight.Statement,
		ClaimType: detail.Insight.ClaimType, Status: detail.Insight.Status,
		Confidence: detail.Insight.Confidence, TimeRange: detail.Insight.TimeRange,
		EvidenceSummary: detail.EvidenceSummary, Evidence: detail.Evidence,
		Truncated: detail.Truncated, Patterns: detail.Patterns,
	}
	// published_at 不在 InsightReadable 里（N9 的取数面没有这一列）：详情按主键取，
	// 时间由列表那一页带来就够了。留 null 而不是猜一个，比假装有值诚实。
	rc := fromContext(r.Context())
	WriteSuccess(w, payload, rc.traceID, rc.userID)
}

// parseV3Limit：非整数一律 400，而不是回默认值。
// 「limit=abc 当成 20」会让调用方的拼写错误在生产里表现为「分页变小了」，
// 那是最难归因的一类空。0 与负数同样拒绝（上限由 domain 收敛，不在这里猜）。
func parseV3Limit(raw string) (int, *apierr.Error) {
	if raw == "" {
		return 0, nil
	}
	n, err := strconv.Atoi(raw)
	if err != nil || n <= 0 {
		return 0, apierr.New(apierr.CodeInvalidParams, "limit 必须是正整数 / limit must be a positive integer")
	}
	return n, nil
}
