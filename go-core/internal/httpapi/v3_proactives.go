// T5-04：`GET /internal/v3/proactives`——proactives 的只读审计面。
//
// 这一层**只做形状、参数与错误映射**。「哪几态算超期」的真身是
// `canonical.sql` 的 `ListProactivesForAudit` 谓词，八态词表／limit 的默认与上限／
// 「截断了没有」的真身是 `domain.ProactiveAuditBrowser`——本文件一处都不重述，
// 否则「审计少报」就有了第二个可以漂的地方。
//
// 这一面**不进 contracts/endpoints.md**（那张表由 FastAPI 生成，且
// `test_contract_freeze.py` 断言「表行数 == 路由装饰器数」），也不进 BFF：
// 工单 §怎么做.2 要求「仅供运维/客服，不暴露给客户端」。它靠的还是 ADR-002 那一条：
// path 留在 `/internal/` 前缀下 ⇒ Ingress 无规则覆盖，公网在入口就 404（v3.go 头注释）。
package httpapi

import (
	"net/http"
	"strconv"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
)

// v3ProactiveItemWire 是工单 §怎么做.1 点名的那几格，外加两格有意的补充：
// `expires_at`（没有它「超期」只能靠调用方猜）与 `updated_at`（一行的最后一次改写
// 时刻，排障时第一个要问的就是「它停在某一态多久了」）。
//
// `skip_reason` 那一格**不存在**：两侧 DDL 都没有这一列，「被挡」与「被拒」是
// `block_reason`/`reject_reason` 两格（见 domain/proactive_audit.go 的登记）。
// 时间一律 `*time.Time`：库里 NULL 就回 null，不折成零值时间——「1970 年发送」
// 在审计面上读起来像一条真记录（T3-04 的 U3 打的就是这类缺数据显示成断言的坏法）。
type v3ProactiveItemWire struct {
	ID             string     `json:"id"`
	Status         string     `json:"status"`
	TriggerType    string     `json:"trigger_type"`
	ScheduledAt    *time.Time `json:"scheduled_at"`
	SentAt         *time.Time `json:"sent_at"`
	RespondedAt    *time.Time `json:"responded_at"`
	ExpiresAt      *time.Time `json:"expires_at"`
	BlockReason    *string    `json:"block_reason"`
	RejectReason   *string    `json:"reject_reason"`
	JourneyEventID *string    `json:"journey_event_id"`
	CreatedAt      *time.Time `json:"created_at"`
	UpdatedAt      *time.Time `json:"updated_at"`
}

type v3ProactiveListPayload struct {
	Items     []v3ProactiveItemWire `json:"items"`
	Truncated bool                  `json:"truncated"`
	// Limit 是**实际生效**的那一个（默认值与上限都由 domain 收敛过），不是请求里那串。
	// 回显它而不是回显请求：调用方传 5000 时要知道自己被截到 200，否则他会以为
	// 「200 条就是这个人的全部触达」。
	Limit  int    `json:"limit"`
	Status string `json:"status"`
	Stale  bool   `json:"stale"`
}

// v3AuditQueryKeys 是这一面认的全部查询参数。
//
// **`user_id` 不在里面**：ADR-002 钉的是身份只从 Authorization/X-User-Id 进来，
// 而工单 §怎么做.1 写的是 `?user_id=`。这里采 §怎么做.2＋ADR-002 而不是 §怎么做.1
// 的字面（改判登记在台账），并且**显式拒绝**而不是静默忽略：静默忽略会让值班
// 以为自己查的是 Bob，而屏幕上那几行其实是 Alice 的——审计面最坏的输出就是这个。
var v3AuditQueryKeys = map[string]bool{"status": true, "limit": true, "stale": true}

func (s *Server) handleV3ProactiveAudit(w http.ResponseWriter, r *http.Request) {
	userID, r, apiErr := s.requireInternalService(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	q := r.URL.Query()
	for key := range q {
		if !v3AuditQueryKeys[key] {
			// 不回显 key 本身：这条面在集群内，但把调用方给的任意串拼进响应消息
			// 等于给以后的日志/界面多开一条注入面，而消息本来就该说清允许哪三个。
			writeJSONError(w, r, apierr.New(apierr.CodeInvalidParams,
				"这一面只认 status/limit/stale 三个查询参数，身份一律走 X-User-Id 头 / "+
					"unsupported query parameter; identity comes only from X-User-Id"))
			return
		}
	}
	limit, apiErr := parseV3Limit(q.Get("limit"))
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	stale, apiErr := parseV3Stale(q.Get("stale"))
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	view, err := s.v3.Proactives.List(r.Context(), userID, q.Get("status"), limit, stale)
	if err != nil {
		// 审计面没有「别人的行」这一格可泄：user_id 由 domain 一路带到 SQL，
		// 跨用户在这里不是 403 而是零行（403 等于替旁人确认「这个 id 有触达」）。
		writeJSONError(w, r, asAPIError(err))
		return
	}
	items := make([]v3ProactiveItemWire, 0, len(view.Items))
	for _, row := range view.Items {
		items = append(items, v3ProactiveItemWire{
			ID: row.ID, Status: row.Status, TriggerType: row.TriggerType,
			ScheduledAt:    optionalTime(row.ScheduledAt, !row.ScheduledAt.IsZero()),
			SentAt:         optionalTime(row.SentAt, !row.SentAt.IsZero()),
			RespondedAt:    optionalTime(row.RespondedAt, !row.RespondedAt.IsZero()),
			ExpiresAt:      optionalTime(row.ExpiresAt, !row.ExpiresAt.IsZero()),
			BlockReason:    optionalString(row.BlockReason),
			RejectReason:   optionalString(row.RejectReason),
			JourneyEventID: optionalString(row.JourneyEventID),
			CreatedAt:      optionalTime(row.CreatedAt, !row.CreatedAt.IsZero()),
			UpdatedAt:      optionalTime(row.UpdatedAt, !row.UpdatedAt.IsZero()),
		})
	}
	rc := fromContext(r.Context())
	WriteSuccess(w, v3ProactiveListPayload{
		Items: items, Truncated: view.Truncated, Limit: view.Limit,
		Status: view.AppliedStatus, Stale: view.AppliedStale,
	}, rc.traceID, rc.userID)
}

// parseV3Stale 把 `stale` 收成布尔：空串＝false（不带这一格就是「全量审计」）。
//
// 只认 strconv 的那几个写法，其余一律 400 而不是当 false：把 `stale=yes` 悄悄读成
// false，运维拿着「全量列表」却以为自己看的是超期队列——那是这一面最不能发生的事，
// 因为它唯一的产出就是「少没少报」。
func parseV3Stale(raw string) (bool, *apierr.Error) {
	if raw == "" {
		return false, nil
	}
	stale, err := strconv.ParseBool(raw)
	if err != nil {
		return false, apierr.New(apierr.CodeInvalidParams,
			"stale 只能是 true 或 false / stale must be true or false")
	}
	return stale, nil
}
