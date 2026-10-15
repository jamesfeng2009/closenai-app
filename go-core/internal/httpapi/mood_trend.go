// GET /api/v1/mood/trend 的 Go 侧实现（N8 批 5，mood 域）。
//
// 调用序列逐字对应 mood_service.get_trend（:323-338）：
//
//	user = await user_repository.get_user(db, user_id)          # 可能 None
//	rows = await mood_repository.count_by_day(db, user_id, days, tz=user.timezone if user else None)
//	day_map = 按 created_at 升序、同一本地日历日只取首行
//	summary = _trend_summary(list(day_map.values()), language)
//
// 四条判据各有各的来源，混在一起就会抄歪：
//  1. **get_user 不是 require_user**：不存在/已注销都只得到 `None`⇒时区 None⇒UTC，
//     响应仍是 200（夹具 trend-deleted-3 用 deleted 账号量到 UTC 那一侧的标签）。
//  2. **当日取首个**取的是 created_at **升序**的第一行，不是「最后一次签到」；
//     所以同一天两行只会出一条标签，值取更早那行（夹具 trend-sameday）。
//  3. 时区名非法一样回退 UTC，不报错（time_utils.safe_zoneinfo）。
//  4. `avg >= 4` / `avg >= 3` 是**浮点**比较，且 4.0 归 high、3.0 归 mid
//     （夹具 trend-exactly3 特意种成 [2,5] 让均值恰好等于 3）。
//
// 顺序上还有一件：`days` 的校验与 `language` 的读库分属两层（FastAPI 先解子依赖、
// 后转 query 参数，fastapi/dependencies/utils.py:619 与 :684），这里按同一个顺序走：
// 先解析语言（有 X-Language 时**一次库都不读**），再校验 days，最后才碰 store。
//
// created_at 走 `pyISO`（"Z"）与批 1 同路、与批 3/批 4 的 `+00:00` 不同：分界不在批次，
// 在「这条路由的 data 是不是 pydantic 模型」——MoodTrendResponse 是，
// 所以 FastAPI 用 pydantic-core 的 json mode 序列化；journey/onboarding 那两条
// 没有 response_model，才落到 jsonable_encoder 的 `isoformat()`。
// 本域三条里只有 trend 的响应**不含** datetime，所以这条分界只在
// mood_history.go 与 reflections_list.go 里需要执行。
package httpapi

import (
	"net/http"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

func (s *Server) handleMoodTrend(w http.ResponseWriter, r *http.Request) {
	userID, apiErr := s.middleware.RequireBearer(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	if s.batch5.Gate.Delegate(w, r, cutoverMood, userID) {
		return
	}
	rc := fromContext(r.Context())

	language := s.requestLanguage(r, userID, s.batch5.Users)

	days, verr := queryInt(r, "days", domain.MoodTrendDaysSpec)
	if verr != nil {
		writeJSONError(w, r, verr.AsError())
		return
	}

	timezone, _, err := s.batch5.Moods.MoodUserTimezone(r.Context(), userID)
	if err != nil {
		writeDBError(w, r)
		return
	}
	now := s.now().UTC()
	rows, err := s.batch5.Moods.MoodTrendRows(r.Context(), userID, moodDayWindow(now, days))
	if err != nil {
		writeDBError(w, r)
		return
	}

	labels, values := domain.MoodTrend(rows, timezone)
	WriteSuccess(w, moodTrendPayload{
		Labels:  labels,
		Values:  values,
		Summary: domain.MoodTrendSummary(values, language),
	}, rc.traceID, rc.userID)
}

// moodTrendPayload 对应 MoodTrendResponse（schemas.py:115-119）。
// labels/values 必须是**非 nil 切片**：空态两侧都给 []（夹具 trend-empty），
// 而 Go 的 nil 切片 marshal 成 null。domain.MoodTrend 已经保证了这一点。
type moodTrendPayload struct {
	Labels  []string `json:"labels"`
	Values  []int    `json:"values"`
	Summary string   `json:"summary"`
}

// moodDayWindow 是 count_by_day 的 `utc_now() - timedelta(days=days)`：
// timedelta 的天数是精确的 n*86400 秒，不做日历加法。
func moodDayWindow(now time.Time, days int64) time.Time {
	return now.Add(-time.Duration(days) * 24 * time.Hour)
}
