// T3-02：`POST /internal/v3/insights/{id}/feedback`——把已经实现六次的领域行为
// （`domain.Service.RecordUserFeedback`，`service_outcome.go:306`）第一次接上 HTTP 调用方。
//
// 这一层的职责边界很窄：**只做校验与错误映射**，状态矩阵、留痕、版本账本、outbox 事件
// 全在 domain 里（工单 §为什么 的那句「领域层已实现，补入口与门禁」就是这条分界）。
// 所以本文件不许出现第二张「feedback_type → 状态」的表：出现了就有两个事实源，
// 而 O-9 矩阵的闸（outcome_test.go）只认 domain 那一侧。
package httpapi

import (
	"bytes"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"unicode/utf8"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// maxCorrectionText 是纠错正文的长度上限。
// **列上没有这个数**：`user_feedback.correction_text` 是 `text`，无 CHECK（0001 之后没加过约束）。
// 上限放在 HTTP 层是因为它的用户后果在前端——一屏输入框装不下更长，
// 而放开不限长等于让一次粘贴写进权威表（前端同值约束见 T3-05）。
const maxCorrectionText = 500

// v3FeedbackRequest 是工单 T3-02 §怎么做.1 的那三个字段，**逐字三个**。
// 解码用 `DisallowUnknownFields`：多传一个键就 400。
// 这不是洁癖——ADR-002 的「一律不信 body 里的 user_id」只有靠这一格才真的可证：
// 静默忽略 `user_id` 的话，客户端会以为自己的身份生效了，而身份来自另一个头。
type v3FeedbackRequest struct {
	Type           string `json:"type"`
	CorrectionText string `json:"correction_text"`
	OperationID    string `json:"operation_id"`
}

type v3FeedbackPayload struct {
	FeedbackID   string `json:"feedback_id"`
	TargetStatus string `json:"target_status"`
	Changed      bool   `json:"changed"`
	// Detail 是 domain 给那句「为什么没改写」的说明串（applyFeedback 的 reason）。
	// 它必须回来：`partially_confirm` 永远 Changed=false，客户端没有这一格就只能猜。
	Detail string `json:"detail"`
}

func (s *Server) handleV3InsightFeedback(w http.ResponseWriter, r *http.Request) {
	userID, r, apiErr := s.requireInternalService(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	raw, err := readBody(r)
	if err != nil {
		writeJSONError(w, r, JSONInvalid().AsError())
		return
	}
	var body v3FeedbackRequest
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&body); err != nil {
		writeJSONError(w, r, apierr.New(apierr.CodeInvalidParams,
			"请求体不合形 / Malformed or unknown request field"))
		return
	}
	feedbackType := domain.FeedbackType(strings.TrimSpace(body.Type))
	if !feedbackType.Valid() {
		writeJSONError(w, r, apierr.New(apierr.CodeInvalidParams,
			"未知的 feedback 类型 / Unknown feedback type"))
		return
	}
	correction := strings.TrimSpace(body.CorrectionText)
	if utf8.RuneCountInString(correction) > maxCorrectionText {
		writeJSONError(w, r, apierr.New(apierr.CodeInvalidParams,
			"纠错说明过长 / Correction text too long"))
		return
	}
	// 工单 §怎么做.4：这是「纠错不能只改前端文字」的落点——四态里只有 `correct`
	// 带正文语义，其余五种表态没有正文可给（给了也只是留痕，不驱动任何改写）。
	if feedbackType == domain.FeedbackCorrect && correction == "" {
		writeJSONError(w, r, apierr.New(apierr.CodeInvalidParams,
			"纠错必须填写说明文字 / correction_text is required for correct"))
		return
	}
	metadata := map[string]string{}
	if op := strings.TrimSpace(body.OperationID); op != "" {
		metadata["operation_id"] = op
	}
	receipt, err := s.v3.Outcomes.RecordUserFeedback(r.Context(), domain.FeedbackProposal{
		UserID:         userID,
		Type:           feedbackType,
		TargetType:     domain.RefInsight,
		TargetID:       r.PathValue("id"),
		CorrectionText: correction,
		Metadata:       metadata,
	})
	if err != nil {
		writeJSONError(w, r, v3FeedbackError(err))
		return
	}
	rc := fromContext(r.Context())
	WriteSuccess(w, v3FeedbackPayload{
		FeedbackID: receipt.ID, TargetStatus: receipt.TargetStatus,
		Changed: receipt.Changed, Detail: receipt.Detail,
	}, rc.traceID, rc.userID)
}

// v3FeedbackError 把领域错误折成信封码。
//
// 归属与「不存在」回**同一个** 40402：分成 403 与 404 就等于告诉旁人这颗 id 存在与否
// （handlers_evidence.go:111 那条判据的同一件事，这条面上也成立）。
func v3FeedbackError(err error) *apierr.Error {
	switch {
	case errors.Is(err, domain.ErrDuplicateFeedback):
		return apierr.New(apierr.CodeConflict,
			"这条表态刚刚已经提交过了 / Duplicate feedback")
	case errors.Is(err, domain.ErrInvalidOwnership):
		return apierr.New(apierr.CodeResourceNotFound, "资源不存在 / Resource not found")
	}
	return asAPIError(err)
}
