// GET /api/v1/curriculum/week 的 Go 侧实现（N8 批 2，curriculum 域）。
//
// 周窗口与出卡顺序在 domain.BuildCurriculumWeek（逐字移植 curriculum_service.py:164-199），
// 本文件负责的是另一半契约：payload / memory_refs 两个**文本列**要按 CPython 的
// json.loads→json.dumps 走一趟再进响应。FastAPI 那边拿到的就是解析后的 Python 对象，
// 所以「原样把 TEXT 贴进响应」是错的——键序、数字面、转义集都得是 Python 的形状。
package httpapi

import (
	"encoding/json"
	"net/http"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

func (s *Server) handleCurriculumWeek(w http.ResponseWriter, r *http.Request) {
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

	now := s.now().UTC()
	monday, start, end, today := domain.CurriculumWindow(user.Timezone, now)
	rows, err := s.batch2.Store.CurriculumCards(r.Context(), userID,
		domain.CurriculumKind, start, end, domain.CurriculumListLimit)
	if err != nil {
		writeDBError(w, r)
		return
	}

	// 每行只解析一次：Python 的 _payload(event) 调一次，read 与三个内容键同源。
	// 解析两遍会让我们在「read_at 与 title 来自同一份快照」这件事上比 Python 多一个假设。
	cards := make([]domain.CurriculumCard, 0, len(rows))
	parsed := make(map[string]*pyValue, len(rows))
	for _, row := range rows {
		payload := payloadObject(row.Payload)
		cards = append(cards, domain.CurriculumCard{
			ID: row.ID, DayNo: row.DayNo,
			Payload: row.Payload, MemoryRefs: row.MemoryRefs,
			HasReadAt: pyTruthy(payloadGet(payload, "read_at")),
		})
		parsed[row.ID] = payload
	}
	week := domain.BuildCurriculumWeek(cards, monday, today)

	days := pyArrayValue()
	for _, day := range week.Days {
		item := pyObjectValue()
		item.set("id", pyStringValue(day.ID))
		item.set("date", pyStringValue(day.Date))
		item.set("day_no", pyIntValue(day.DayNo))
		item.set("revealed", pyBoolValue(day.Revealed))
		item.set("read", pyBoolValue(day.Read))
		item.set("memory_refs", refsArray(day.MemoryRefs))
		payload := parsed[day.ID]
		// 键序就是 get_week 里那个 dict 字面量的书写序（:188-198）。
		for _, key := range []string{"title", "assertion", "question"} {
			item.set(key, payloadGet(payload, key))
		}
		days.items = append(days.items, item)
	}

	root := pyObjectValue()
	root.set("week_start", pyStringValue(week.WeekStart))
	root.set("days", days)
	root.set("unread_count", pyIntValue(week.Unread))
	raw, err := root.json()
	if err != nil {
		// 只有一条路能到这里：payload 里有 NaN/Infinity。Python 侧此时是
		// JSONResponse(allow_nan=False) 抛 ValueError，同样不出 200。
		writeDBError(w, r)
		return
	}
	WriteSuccess(w, json.RawMessage(raw), rc.traceID, rc.userID)
}
