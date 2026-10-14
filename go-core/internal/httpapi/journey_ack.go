// POST /api/v1/journey/events/acknowledge 的 Go 侧实现（N8 批 3，journey 域）。
//
// 契约面有三处容易各自「顺手改好」的地方，逐条对 journey_service.acknowledge（:907-912）：
//  1. 归属校验失败与「事件不存在」合成**同一条** 40402、同一个文案（:908 的注释就是
//     「不泄露他人 ID 有效性」）。回 403 或给两条文案等于把这条端点变成 id 枚举 oracle。
//  2. 响应体回显的是**请求里的** event_id（routers/journey.py:41 `request.event_id`），
//     不是 `event.id` 列值。批 2 的 curriculum/read 取的是列值，两处不同不是笔误：
//     那边响应里的 id 来自 event.id，这边来自入参。PK 是 VARCHAR(36) 精确比对，
//     所以两侧今天必然相等；这条差异只在「有人往 id 里塞编码」时才显现，
//     照抄各自的出处而不是统一成一种，才是切流期不引入新语义的做法。
//  3. **不**做预写：acknowledge 不调 ensure_prefilled，所以它对未预写的账号只可能
//     40402，不会顺带把六行剧本补出来。
package httpapi

import (
	"encoding/json"
	"net/http"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
)

// forbiddenJourneyMessage 逐字抄 journey_service.py:911。
const forbiddenJourneyMessage = "事件不存在 / Event not found"

func (s *Server) handleJourneyAcknowledge(w http.ResponseWriter, r *http.Request) {
	userID, apiErr := s.middleware.RequireBearer(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	if s.batch3.Gate.Delegate(w, r, "journey", userID) {
		return
	}
	raw, err := readBody(r)
	if err != nil {
		writeJSONError(w, r, asAPIError(err))
		return
	}
	eventID, entries := validateJourneyAckBody(raw, r.Header.Get("Content-Type"))
	if len(entries) > 0 {
		writePyInvalid(w, r, entries)
		return
	}
	rc := fromContext(r.Context())

	// repository.get 不带 user_id 谓词：越权是业务判定，不是查询条件（批 2 同策）。
	owner, err := s.batch3.Store.JourneyOwner(r.Context(), eventID)
	if err != nil {
		writeDBError(w, r)
		return
	}
	if !owner.Found || owner.UserID != userID {
		writeJSONError(w, r, apierr.New(apierr.CodeResourceNotFound, forbiddenJourneyMessage))
		return
	}
	if err := s.batch3.Store.AcknowledgeJourneyEvent(r.Context(), eventID); err != nil {
		writeDBError(w, r)
		return
	}

	out := pyObjectValue()
	out.set("acknowledged", pyStringValue(eventID))
	encoded, err := out.json()
	if err != nil {
		writeJSONError(w, r, apierr.New(apierr.CodeInternalError,
			"服务器内部错误 / Internal server error"))
		return
	}
	WriteSuccess(w, json.RawMessage(encoded), rc.traceID, rc.userID)
}
