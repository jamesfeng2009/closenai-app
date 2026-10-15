// GET /api/v1/mood/history 的 Go 侧实现（N8 批 5，mood 域）。
//
// 对应 mood_service.get_history（:306-319）：一条查询、零 users 读、零文案。
// 三件事必须照抄：
//  1. `limit=30` 写在 service 层，端点**没有** days/limit 参数——所以
//     `/mood/history?days=3` 是 200 且与不带参数逐字同形（夹具 history-ignores-query）。
//     Go 若把 days 接过来当真参数，就会在越界时多出 400 面。
//  2. `streak_updated=0` 是**硬编码**（:317），不是「本行没更新所以 0」。
//  3. `deep_interpretation` / `level_up` 两键恒 null：它们只在 POST /mood/checkin
//     的响应里赋值，而这两列不在本端点读的列里。
//
// 序列化形状：data 是 `MoodHistoryResponse`（**pydantic 模型**），FastAPI 的
// response_model 路径走 pydantic-core 的 json mode，所以 created_at 是 `Z` 形
// （`pyISO`，批 1 同一条路径），不是批 3/批 4 的 `+00:00`。分界不在「哪一批」，
// 在「这条路由有没有 pydantic 模型」——批 5 三条全有，故三条全用 pyISO。
package httpapi

import (
	"net/http"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

func (s *Server) handleMoodHistory(w http.ResponseWriter, r *http.Request) {
	userID, apiErr := s.middleware.RequireBearer(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	if s.batch5.Gate.Delegate(w, r, cutoverMood, userID) {
		return
	}
	rc := fromContext(r.Context())

	rows, err := s.batch5.Moods.MoodHistory(r.Context(), userID, domain.MoodHistoryLimit)
	if err != nil {
		writeDBError(w, r)
		return
	}

	// 空态必须是 []：MoodHistoryResponse(checkins=[]) 给的是空数组，
	// 而 Go 的 nil 切片给 null（夹具 history-empty / history-anonymous 钉的就是这条）。
	checkins := make([]moodCheckinPayload, 0, len(rows))
	for _, row := range rows {
		checkins = append(checkins, moodCheckinPayload{
			ID:               row.ID,
			MoodEmoji:        row.Emoji,
			MoodLabel:        row.Label,
			AIInterpretation: row.Interpretation,
			StreakUpdated:    domain.StreakUpdatedInHistory,
			CreatedAt:        pyISO(row.CreatedAt),
		})
	}
	WriteSuccess(w, moodHistoryPayload{Checkins: checkins}, rc.traceID, rc.userID)
}

// moodHistoryPayload / moodCheckinPayload 的字段序就是
// MoodHistoryResponse 与 MoodCheckinResponse 的声明序（models/schemas.py:96-112）。
type moodHistoryPayload struct {
	Checkins []moodCheckinPayload `json:"checkins"`
}

type moodCheckinPayload struct {
	ID               string `json:"id"`
	MoodEmoji        string `json:"mood_emoji"`
	MoodLabel        string `json:"mood_label"`
	AIInterpretation string `json:"ai_interpretation"`
	StreakUpdated    int    `json:"streak_updated"`
	CreatedAt        string `json:"created_at"`
	// 两个 Optional 无值时回 null 而不是省略键；本端点两条路径都不给值，
	// 所以留 any 而不是猜它们各自的形状。
	DeepInterpretation any `json:"deep_interpretation"`
	LevelUp            any `json:"level_up"`
}
