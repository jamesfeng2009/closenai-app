// T4-03：`POST /internal/v3/actions/{id}/outcomes`——把一次用户自述的「行动结果」
// 接到 `domain.Service.RecordActionOutcome` 上。
//
// 与 v3_goals.go 同一层职责：**只做形状与错误映射**。
// origin、targets、词表、同日幂等、传播与 ++版本全部在 domain（action_outcome.go），
// 本文件不许出现第二张「outcome_type → 该 link 谁」的表——那张表的真身是
// `domain.OutcomeTargetRequirement`，工单 §怎么做.3 点名的就是它。
package httpapi

import (
	"errors"
	"net/http"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// maxOutcomeNote 是备注的长度上限。
//
// 与 maxCorrectionText 同一判据：`outcomes.description` 在列上是 `text`，没有 CHECK，
// 上限放在 HTTP 层是因为它的用户后果在前端（一屏输入框），而放开不限长等于让一次
// 粘贴写进权威表。
const maxOutcomeNote = 500

// v3OutcomeRequest 是工单 §怎么做.1 那四格（outcome_type / result / note / occurred_at）
// 再加一格 operation_id。
//
// `occurred_at` 用 `*time.Time`：JSON 解码器自己认 RFC3339，非法字符串走「请求体不合形」
// ⇒ 400，而不是这里再写一遍 time.Parse 的错误映射（v3_goals.go 的同一件事）。
// 同样不许出现 `user_id`——`DisallowUnknownFields` 是 ADR-002 的可证形式。
type v3OutcomeRequest struct {
	OutcomeType string     `json:"outcome_type"`
	Result      string     `json:"result"`
	Note        string     `json:"note"`
	OccurredAt  *time.Time `json:"occurred_at"`
	OperationID string     `json:"operation_id"`
}

type v3OutcomePayload struct {
	OutcomeID   string `json:"outcome_id"`
	ActionID    string `json:"action_id"`
	OutcomeType string `json:"outcome_type"`
	Result      string `json:"result"`
	// Status 是 outcomes.status（recorded / candidate / …），由 domain 按 origin 决定。
	// 这一格必须回：客户端要分清「这条结果记账了」与「只登记成候选、不改模型相信什么」。
	Status          string `json:"status"`
	Changed         bool   `json:"changed"`
	ModelVersion    int64  `json:"model_version"`
	CompletedTarget string `json:"completed_target"`
	// OperationID 是**回显**而不是落库（outcomes 没有 metadata 列，见 action_outcome.go
	// 的登记）；回显让客户端能把一次重试和自己的按钮对上，而不必猜。
	OperationID string `json:"operation_id"`
	Detail      string `json:"detail"`
}

func (s *Server) handleV3ActionOutcome(w http.ResponseWriter, r *http.Request) {
	userID, r, apiErr := s.requireInternalService(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	var body v3OutcomeRequest
	if apiErr := decodeV3Body(r, &body); apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	note := strings.TrimSpace(body.Note)
	if utf8.RuneCountInString(note) > maxOutcomeNote {
		writeJSONError(w, r, apierr.New(apierr.CodeInvalidParams,
			"结果备注过长 / Outcome note too long"))
		return
	}
	// outcome_type / result 的正误**不在这里判**：词表在 domain（OutcomeTypes /
	// OutcomeResults），本文件判一次就多了第二个事实源。这里只把字符串搬进提案，
	// 不认识的由 checkOutcomeProposal 回 ErrInvalidOutcomeProposal ⇒ 400。
	p := domain.ActionOutcomeProposal{
		UserID:      userID,
		ActionID:    r.PathValue("id"),
		Type:        domain.OutcomeType(strings.TrimSpace(body.OutcomeType)),
		Result:      domain.OutcomeResult(strings.TrimSpace(body.Result)),
		Note:        note,
		OperationID: strings.TrimSpace(body.OperationID),
	}
	if body.OccurredAt != nil {
		p.OccurredAt = *body.OccurredAt
	}
	receipt, err := s.v3.OutcomeWrites.RecordActionOutcome(r.Context(), p)
	if err != nil {
		writeJSONError(w, r, v3OutcomeError(err))
		return
	}
	rc := fromContext(r.Context())
	WriteSuccess(w, v3OutcomePayload{
		OutcomeID: receipt.OutcomeID, ActionID: receipt.ActionID,
		OutcomeType: receipt.OutcomeType, Result: receipt.Result,
		Status: receipt.Status, Changed: receipt.Changed,
		ModelVersion: receipt.ModelVersion, CompletedTarget: receipt.CompletedTarget,
		OperationID: receipt.OperationID, Detail: receipt.Detail,
	}, rc.traceID, rc.userID)
}

// v3OutcomeError 把领域错误折成信封码（三格与 v3GoalError 同判据，各自在本文件有测）。
//
// 归属与「不存在」回**同一个** 40402；提案不合形回 400；
// 「这条面上不允许这种 outcome_type」也回 400——它是 §怎么做.3 点名的那一格
// （goal/pattern/proactive 级的结果不挂在行动上），是入参问题而不是状态冲突。
func v3OutcomeError(err error) *apierr.Error {
	switch {
	case errors.Is(err, domain.ErrInvalidOutcomeProposal):
		return apierr.New(apierr.CodeInvalidParams,
			"结果类型或取值不合形 / Invalid outcome_type or result")
	case errors.Is(err, domain.ErrInvalidOwnership):
		return apierr.New(apierr.CodeResourceNotFound, "资源不存在 / Resource not found")
	}
	return asAPIError(err)
}
