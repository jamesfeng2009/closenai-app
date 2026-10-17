// N8 批 1（analytics + discovery）在 HTTP 层的依赖面与两处逐字口径。
//
// 接口声明在使用方（httpapi）而不是实现方（store），是为了让「线上形状测」能用假件跑：
// 切流域的**响应字节**才是契约，SQL 是否命中真实表由 store 的实库测单独证。
package httpapi

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/cutover"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/posthog"
)

// UserProfileReader 读活跃用户的语言偏好与 analytics 同意位（store.PG 的实现）。
type UserProfileReader interface {
	UserProfile(ctx context.Context, userID string) (domain.UserProfile, error)
}

// MoodReader 读签到行（created_at 倒序），是 discovery 对比的唯一数据来源。
type MoodReader interface {
	MoodCheckins(ctx context.Context, userID string, since time.Time) ([]domain.Checkin, error)
}

// Relay 把一条已匿名化的事件送出去。返回值**就是** relayed 计数的依据（D-G18）：
// nil＝这条真的到了 capture 端点，error（含未配 key）＝这条没出门、落 dropped。
type Relay interface {
	Capture(ctx context.Context, ev posthog.Event) error
}

// Batch1 是这批切流域的依赖集合。为 nil 时两条路由不注册（见 server.go 的 routes）。
type Batch1 struct {
	Users UserProfileReader
	Moods MoodReader
	Relay Relay
	Gate  *cutover.Gate
}

// SupportedLanguages 逐字对照 backend/config.py:77-81 的 supported_languages。
// 这张表决定 X-Language 与 users.language 的取值能不能生效，
// 由 backend/tests/test_n8_batch1_contract.py 与 Python 侧集合做双向差集断言。
var SupportedLanguages = []string{
	"en", "zh", "es", "ko", "ja", "ru",
	"id", "ms", "th", "de", "fr", "it", "pt",
	"vi", "km",
}

// normalizeLanguageCode 是 core/language.py::resolve_request_language 的口径：
// 取第一个 '-' 前的一段、转小写；不在表里的一律视为「没给语言」。
func normalizeLanguageCode(raw string) (string, bool) {
	code := strings.ToLower(strings.SplitN(strings.TrimSpace(raw), "-", 2)[0])
	if code == "" {
		return "", false
	}
	return code, isSupportedLanguage(code)
}

// isSupportedLanguage 是 `code in settings.supported_languages` 的字面 membership。
// 单独拆出来是因为 Python 的**两级**口径不同：header 侧先归一化再查表（所以
// 'zh-CN'→zh 生效），库偏好侧拿原始值直接查表（所以 users.language='zh-CN' 整条
// 落回 en）。夹具的 discovery-zh-region-db-falls-back-en 与
// discovery-header-unsupported-region 分别钉住这两侧。
func isSupportedLanguage(code string) bool {
	for _, supported := range SupportedLanguages {
		if code == supported {
			return true
		}
	}
	return false
}

// requestLanguage 复现 get_request_language 的三级优先：
// ① X-Language 头（归一化后受支持才生效）② users.language（**原文**查表，不归一化）
// ③ "en"。读库失败不阻塞请求，与 Python 侧的 except Exception 同。
//
// reader 由调用方给（批 1 传 batch1.Users、批 5 传 batch5.Users）：FastAPI 先解子依赖、
// 后校验 query 参数，所以 trend 那条即便最终 400 也会走到这一步——传对 reader
// 才能让「回放时一次库都不许读」这条断言继续成立。
func (s *Server) requestLanguage(r *http.Request, userID string, users UserProfileReader) string {
	if code, ok := normalizeLanguageCode(r.Header.Get("X-Language")); ok {
		return code
	}
	if users != nil {
		// get_language：用户不存在或 language 为空串时返回 "en"（user_service.py:253-258）。
		dbLang := "en"
		if profile, err := users.UserProfile(r.Context(), userID); err == nil &&
			profile.Found && profile.Language != "" {
			dbLang = profile.Language
		}
		if isSupportedLanguage(dbLang) {
			return dbLang
		}
	}
	return "en"
}

// pyISO 是 jsonable_encoder 对 tz-aware datetime 的输出格式（实测 2026-10-14）：
// UTC 一律写 "Z"；微秒非零时**固定 6 位**（100000µs → ".100000"，不是 ".1"），
// 微秒为零时整段小数省略。Go 的 RFC3339Nano 会去尾零，所以不能直接用。
func pyISO(t time.Time) string {
	utc := t.UTC()
	out := utc.Format("2006-01-02T15:04:05")
	if micros := utc.Nanosecond() / 1000; micros != 0 {
		out += fmt.Sprintf(".%06d", micros)
	}
	return out + "Z"
}
