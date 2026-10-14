// GET /api/v1/discovery/daily 的 Go 侧实现（N8 批 1，discovery 域）。
//
// 判定的阈值、分支次序与两套文案都在 domain.CompareWeeks 里（逐字移植
// backend/services/discovery_service.py），本文件只负责三件事：
// 解析请求语言、按 7/14 天窗口取行、把结果包成五键信封。
//
// 窗口取的是**两条独立查询**而不是查一次 14 天再切——Python 就是
// get_history(days=7) 与 get_history(days=14) 各一次、后者再按 cutoff 过滤，
// 灰度期两侧必须打同样的 SQL，否则「契约 diff 为空」无从谈起。
package httpapi

import (
	"net/http"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

func (s *Server) handleDiscoveryDaily(w http.ResponseWriter, r *http.Request) {
	userID, apiErr := s.middleware.RequireBearer(r)
	if apiErr != nil {
		writeJSONError(w, r, apiErr)
		return
	}
	if s.batch1.Gate.Delegate(w, r, "discovery", userID) {
		return
	}
	rc := fromContext(r.Context())

	// get_request_language 在 FastAPI 里是**先于**端点函数执行的依赖，
	// 所以它读库失败也不影响请求（Python 侧整段包在 except Exception 里）。
	language := s.requestLanguage(r, userID)

	profile, err := s.batch1.Users.UserProfile(r.Context(), userID)
	if err != nil {
		writeDBError(w, r)
		return
	}
	// user_repository.require_user：不存在或已注销 ⇒ 40401，与 analytics 的
	// 「静默丢弃」不同一条路（那边是 get_user 返回 None）。
	if !profile.Found {
		writeJSONError(w, r, apierr.New(apierr.CodeUserNotFound,
			"用户不存在 / User not found"))
		return
	}

	// utc_now() 两侧都是 tz-aware UTC；timedelta(days=n) 是精确的 n*86400 秒。
	now := time.Now().UTC()
	cutoff := now.Add(-discoveryWeek)
	thisWeek, err := s.batch1.Moods.MoodCheckins(r.Context(), userID, cutoff)
	if err != nil {
		writeDBError(w, r)
		return
	}
	recent, err := s.batch1.Moods.MoodCheckins(r.Context(), userID, now.Add(-discoveryFortnight))
	if err != nil {
		writeDBError(w, r)
		return
	}
	var lastWeek []domain.Checkin
	for _, row := range recent {
		if row.CreatedAt.Before(cutoff) {
			lastWeek = append(lastWeek, row)
		}
	}

	discovery := domain.CompareWeeks(thisWeek, lastWeek, language)
	payload := discoveryPayload{GeneratedAt: pyISO(now)}
	if discovery != nil {
		payload.HasDiscovery = true
		payload.Content = &discovery.Content
		payload.ComparisonType = &discovery.ComparisonType
		payload.Priority = &discovery.Priority
	}
	WriteSuccess(w, payload, rc.traceID, rc.userID)
}

const (
	discoveryWeek      = 7 * 24 * time.Hour
	discoveryFortnight = 14 * 24 * time.Hour
)

// discoveryPayload 的键序就是 DailyDiscoveryResponse 的字段声明序
// （backend/models/schemas.py:417-423）；三个 Optional 无值时回 null 而不是省略。
type discoveryPayload struct {
	HasDiscovery   bool    `json:"has_discovery"`
	Content        *string `json:"content"`
	ComparisonType *string `json:"comparison_type"`
	Priority       *string `json:"priority"`
	GeneratedAt    string  `json:"generated_at"`
}

// writeDBError 是读面故障的统一落点：50003 是 contracts/endpoints.md §四 里
// 已有的码，不新造错误码。
func writeDBError(w http.ResponseWriter, r *http.Request) {
	writeJSONError(w, r, apierr.New(apierr.CodeDatabaseError, "数据库错误 / Database error"))
}
