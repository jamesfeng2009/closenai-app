// GET /api/v1/journey/events 的 Go 侧实现（N8 批 3，journey 域）。
//
// 逐字对应 journey_service.list_events（:885-905）的三段顺序：
//
//	await self.ensure_prefilled(db, user_id)                  # 可能写库
//	events = await journey_repository.list_for_user(db, user_id, limit=50)
//	return {"events": [...], "unseen_count": await count_unseen(db, user_id)}
//
// 两件事只能照抄、不能「优化」：
//  1. **顺序**：预写在读之前，所以第一次访问这个端点的响应里就有六行——
//     把预写挪走或改成后台任务，同一账号切流前后就是 0 行 vs 6 行。
//  2. **count 在列表之后**：Python 在 dict 字面量里现算 `unseen_count`，
//     它看到的是**刚被预写、可能又被 skip 过**的那批行；预写与它是同一事务里的
//     两次查询，谁先谁后决定新行算不算进未读数（新行 status 是 pending/skipped，
//     count 只数 'sent'，所以今天两侧相等——但换判据时要红，所以顺序留着）。
//
// datetime 两列走 pyISOOffset（"+00:00"）而不是批 1 的 pyISO（"Z"）：
// 这条路由**没有** response_model/返回类型注解，FastAPI 走 serialize_response 的
// `field is None` 分支⇒ `jsonable_encoder`⇒`ENCODERS_BY_TYPE[datetime] = isoformat`
// （routing.py:341、encoders.py:53-54、88-90）。批 1 的 discovery 有 pydantic 模型，
// 所以那边才是 pydantic-core 的 `Z`。两处格式不同不是笔误，是两条序列化路径。
package httpapi

import (
	"encoding/json"
	"net/http"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

func (s *Server) handleJourneyEvents(w http.ResponseWriter, r *http.Request) {
	userID, apiErr := s.middleware.RequireBearer(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	if s.batch3.Gate.Delegate(w, r, "journey", userID) {
		return
	}
	rc := fromContext(r.Context())

	if e := s.ensurePrefilled(r.Context(), userID); e != nil {
		writeJSONError(w, r, e)
		return
	}

	rows, err := s.batch3.Store.JourneyEvents(r.Context(), userID, domain.JourneyListLimit)
	if err != nil {
		writeDBError(w, r)
		return
	}

	events := pyArrayValue()
	for _, row := range rows {
		item := pyObjectValue()
		// 九键的书写序就是 :891-901 那个 dict 字面量的顺序。
		item.set("id", pyStringValue(row.ID))
		item.set("day_no", pyIntValue(row.DayNo))
		item.set("event_kind", pyStringValue(row.Kind))
		item.set("status", pyStringValue(row.Status))
		item.set("scheduled_at", pyStringValue(pyISOOffset(row.ScheduledAt)))
		item.set("sent_at", sentAtValue(row))
		item.set("acknowledged", pyBoolValue(row.Acknowledged))
		// parse_json(e.payload, {}) / parse_json(e.memory_refs, [])：
		// **没有** isinstance 检查（与批 2 的 _payload/_refs 不同）——列里存的是数组
		// 就回数组，存的是 5 就回 5。只有「空文本」与「坏 JSON」才回退默认值。
		item.set("payload", parseJSONColumn(row.Payload, pyObjectValue()))
		item.set("memory_refs", parseJSONColumn(row.MemoryRefs, pyArrayValue()))
		events.items = append(events.items, item)
	}

	unseen, err := s.batch3.Store.JourneyUnseenCount(r.Context(), userID)
	if err != nil {
		writeDBError(w, r)
		return
	}

	root := pyObjectValue()
	root.set("events", events)
	root.set("unseen_count", pyIntValue(unseen))
	raw, err := root.json()
	if err != nil {
		// 只有 payload 里有 NaN/Infinity 才能到这里：Python 侧此时 JSONResponse
		// (allow_nan=False) 抛，同样不出 200（批 2 的发散 ② 同形，仍无库内观测点）。
		writeDBError(w, r)
		return
	}
	WriteSuccess(w, json.RawMessage(raw), rc.traceID, rc.userID)
}

// sentAtValue 是 `e.sent_at`：None⇒JSON null（键**不省略**），有值⇒isoformat。
func sentAtValue(row domain.JourneyEventRow) *pyValue {
	if !row.HasSentAt {
		return pyNullValue()
	}
	return pyStringValue(pyISOOffset(row.SentAt))
}
