// POST /api/v1/curriculum/{event_id}/read 的 Go 侧实现（N8 批 2，curriculum 域）。
//
// 契约要点（逐条对 curriculum_service.py:201-211）：
//  1. 越权判定在**取行之后**：行不存在、属于别人、event_kind 不是 curriculum 三种情形
//     合成同一条 403「无权访问该主题卡」——不给「不存在」单开一个码，
//     否则这个端点变成 id 枚举 oracle。
//  2. 幂等：read_at 已有真值时**不写库**，第二次调用与第一次回同样的字节。
//  3. 写回整份 payload 文本（Python 是 dict 改一个键后 json.dumps 整份），
//     所以其余键的位置与值必须原样保留——pyValue 的文档序在这里就是数据格式。
package httpapi

import (
	"encoding/json"
	"net/http"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// forbiddenCardMessage 逐字抄 curriculum_service.py:205。
const forbiddenCardMessage = "无权访问该主题卡"

func (s *Server) handleCurriculumRead(w http.ResponseWriter, r *http.Request) {
	userID, apiErr := s.middleware.RequireBearer(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	if s.batch2.Gate.Delegate(w, r, "curriculum", userID) {
		return
	}
	rc := fromContext(r.Context())

	user, e := s.requireCurriculumConsent(r.Context(), userID)
	if e != nil {
		writeJSONError(w, r, e)
		return
	}

	// journey_repository.get 不按 user_id 过滤：越权是业务判定，不是查询条件。
	event, err := s.batch2.Store.JourneyEventOwner(r.Context(), r.PathValue("event_id"))
	if err != nil {
		writeDBError(w, r)
		return
	}
	if !event.Found || event.UserID != user.ID || event.Kind != domain.CurriculumKind {
		writeJSONError(w, r, apierr.New(apierr.CodeForbidden, forbiddenCardMessage))
		return
	}

	payload := payloadObject(event.Payload)
	if !pyTruthy(payloadGet(payload, "read_at")) {
		payload.set("read_at", pyStringValue(pyISOOffset(s.now().UTC())))
		text, err := payload.json()
		if err != nil {
			writeDBError(w, r)
			return
		}
		if err := s.batch2.Store.SetJourneyPayload(r.Context(), event.ID, text); err != nil {
			writeDBError(w, r)
			return
		}
	}

	out := pyObjectValue()
	out.set("id", pyStringValue(event.ID))
	out.set("read", pyBoolValue(true))
	raw, err := out.json()
	if err != nil {
		writeDBError(w, r)
		return
	}
	WriteSuccess(w, json.RawMessage(raw), rc.traceID, rc.userID)
}
