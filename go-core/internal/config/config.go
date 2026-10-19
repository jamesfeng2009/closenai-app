// Package config reads the runtime configuration from environment variables.
//
// The variable names are the cross-language contract: backend/config.py is a
// pydantic BaseSettings whose fields map to upper-case env vars, so Go reads
// the same JWT_SECRET / JWT_ALGORITHM / ... instead of inventing its own names.
package config

import (
	"errors"
	"fmt"
	"os"
	"strconv"
)

type Config struct {
	AppName     string
	Environment string
	DatabaseURL string
	JWTSecret   string
	// JWTAlgorithm 是**校验位、不是旋钮**（D-G13 的登记口径）：跨语言基线就是 HS256，
	// 签发处 internal/auth/token.go 硬编码 SigningMethodHS256、**从不读这个字段**。
	// 所以把它改成 RS256 的后果是「Go 拒绝启动」，而不是「换了算法」。
	// 真要换算法必须两侧同批改签发与校验，并在 contracts 里重录基线——
	// 不要给这个键插一根没人用的分支：那只会多出一条「拨了不响的假旋钮」。
	JWTAlgorithm           string
	JWTAccessExpireMinutes int
	JWTRefreshExpireDays   int
	HTTPAddr               string
	ShutdownTimeoutSeconds int
	// PosthogAPIKey / PosthogHost 与 backend/config.py:159-160 同名同默认值：
	// 未配 key ⇒ 中继整条 no-op（红线：绝不因分析平台缺席让业务请求失败）。
	// host 的默认值是 Python 侧的字面量（eu.i.posthog.com），不是 posthog-python 的
	// app.posthog.com——两侧要打到同一个收件箱，只能以 settings 为准。
	PosthogAPIKey string
	PosthogHost   string
	// SiteURL / AppStoreURL 与 backend/config.py 的 `site_url` / `app_store_url` 同名同默认值：
	// onboarding 的 share_url 由它们拼出，且**落进 first_read 列**（restore 之后按列复现、
	// 不再回查设置），所以默认值抄歪会让两侧给同一个账号写出不同的深链。
	// AppStoreURL 默认空是 U3-b 的收口：原先两侧都抄着 `id6740000000` 这个占位，
	// 而它会拼进用户发出去的分享文案与 PDF——点进去 404，归因断在这。
	// 上架后由 APP_STORE_URL 注入真链；未注入时一律走 ShareLandingURL 的站点回落。
	// Python 的 BaseSettings 没有 env_prefix ⇒ 变量名就是大写字段名，无前缀。
	SiteURL     string
	AppStoreURL string
	// CutoverPyAPIURL 与各域百分比是 N8 的灰度面，全带 CLOSEN_ 前缀：
	// Python 侧没有对应字段（它不知道自己被切了多少），所以它们**不进**跨语言变量名单。
	// 默认 0＝Go 一条都不自己答，只当代理，于是「部署了但没开灰度」是安全态。
	CutoverPyAPIURL   string
	CutoverAnalytics  int
	CutoverDiscovery  int
	CutoverCurriculum int
	// CutoverJourney 是 N8 批 3（journey 域）的放量键，口径与上面三个相同。
	CutoverJourney int
	// CutoverOnboarding 是 N8 批 4（onboarding 域）的放量键，口径同上。
	CutoverOnboarding int
	// CutoverMood / CutoverReflections 是 N8 批 5 的两条放量键：键是**域级**、
	// Ingress 的一刀是**端点级**（决策点 D-5）。CLOSEN_CUTOVER_MOOD 一条盖
	// /mood/history 与 /mood/trend，同域的 POST /mood/checkin 根本不在这个键的面上。
	// CLOSEN_CUTOVER_REFLECTIONS 本批**只有键、没有 path**（决策点 D-10：Ingress 的
	// path 匹配不看方法，而 /api/v1/reflections 的 GET 与 POST 共用一个 path）⇒
	// 在它没有入口规则之前，这个键调到多少都不放量，方向是安全的「没切」。
	CutoverMood        int
	CutoverReflections int
	// CutoverMemories 是 N8 批 6（memories 两条纯读）的放量键，与 REFLECTIONS 同一档：
	// **只有键、没有 Ingress path**（决策点 D-10——`/api/v1/memories` 同 path 还挂 POST、
	// `/api/v1/memories/{memory_id}` 同 path 还挂 DELETE，一刀下去会把写侧方法搬进
	// 只注册了 GET 的这台 mux，405 与百分比无关）。
	// 所以在它没有入口规则之前，这个键调到多少都不放量，方向是安全的「没切」。
	CutoverMemories int
	// InsightEvidenceRead 是 N9 依据展开端点的回退开关（D-3「旧口径留回退开关」）。
	// 这是本结构里唯一的 Go-only 变量：CLOSEN_ 前缀与 domain 的数值闸同源，
	// Python 侧没有对应字段，所以它不进上面的跨语言名单口径。
	InsightEvidenceRead string
	// GoServiceToken 是 `/internal/v3/*` 这一面的服务间凭据（ADR-002：Go 只暴露内部面，
	// 鉴权用 `X-Service-Token`）。变量名刻意**不带 CLOSEN_ 前缀**：它两侧都读
	// （Go 在这里，Python 在 backend/config.py 的 `go_service_token`），
	// 而带前缀的那个约定是「只有 Go 认得的键」（对照 CLOSEN_PY_API_URL）。
	//
	// 空串的语义是「这一面不存在」，不是「不校验」：内部面今天没有部署侧的密钥，
	// 拿默认值放行等于把一个未鉴权的写入口挂在一个 ClusterIP Service 上，
	// 而集群里任何 Pod 都能拨。判据在 httpapi 的注册面（v3.go 的 gated），
	// 不在这里报错——go-api 今天还要服务 N8 那十条切流域的路由，不能因为没配内部密钥就拒绝启动。
	// 它属敏感项：只进 Secret `closenai-runtime`，不进 `10-config.yaml`。
	GoServiceToken string
}

const defaultDatabaseURL = "sqlite+aiosqlite:///./closenai.db"

// ShareLandingURL 是分享落地的唯一取法，与 Python 侧 `settings.share_landing_url` 同口径：
// 上架前给站点、上架后给 App Store。消费方不许直接读 AppStoreURL——未配置时那会发出
// 一个空字符串，卡片照样生成成功，只有用户点不动（这种坏法没有报错）。
func (c Config) ShareLandingURL() string {
	if c.AppStoreURL != "" {
		return c.AppStoreURL
	}
	return c.SiteURL
}

func Load() (Config, error) {
	cfg := Config{
		AppName:                envString("APP_NAME", "ClosenAI"),
		Environment:            envString("ENVIRONMENT", "development"),
		DatabaseURL:            envString("DATABASE_URL", defaultDatabaseURL),
		JWTSecret:              envString("JWT_SECRET", ""),
		JWTAlgorithm:           envString("JWT_ALGORITHM", "HS256"),
		JWTAccessExpireMinutes: envInt("JWT_ACCESS_EXPIRE_MINUTES", 30),
		JWTRefreshExpireDays:   envInt("JWT_REFRESH_EXPIRE_DAYS", 30),
		HTTPAddr:               envString("HTTP_ADDR", ":8000"),
		ShutdownTimeoutSeconds: envInt("SHUTDOWN_TIMEOUT_SECONDS", 15),
		InsightEvidenceRead:    envString("CLOSEN_INSIGHT_EVIDENCE_READ", "on"),
		GoServiceToken:         envString("GO_SERVICE_TOKEN", ""),
		PosthogAPIKey:          envString("POSTHOG_API_KEY", ""),
		PosthogHost:            envString("POSTHOG_HOST", "https://eu.i.posthog.com"),
		SiteURL:                envString("SITE_URL", "https://closenai.app"),
		AppStoreURL:            envString("APP_STORE_URL", ""),
		CutoverPyAPIURL:        envString("CLOSEN_PY_API_URL", ""),
		CutoverAnalytics:       envInt("CLOSEN_CUTOVER_ANALYTICS", 0),
		CutoverDiscovery:       envInt("CLOSEN_CUTOVER_DISCOVERY", 0),
		CutoverCurriculum:      envInt("CLOSEN_CUTOVER_CURRICULUM", 0),
		CutoverJourney:         envInt("CLOSEN_CUTOVER_JOURNEY", 0),
		CutoverOnboarding:      envInt("CLOSEN_CUTOVER_ONBOARDING", 0),
		CutoverMood:            envInt("CLOSEN_CUTOVER_MOOD", 0),
		CutoverReflections:     envInt("CLOSEN_CUTOVER_REFLECTIONS", 0),
		CutoverMemories:        envInt("CLOSEN_CUTOVER_MEMORIES", 0),
	}
	if cfg.JWTAlgorithm != "HS256" {
		return cfg, errors.New("unsupported JWT_ALGORITHM: cross-language baseline is HS256")
	}
	if cfg.JWTAccessExpireMinutes <= 0 || cfg.JWTRefreshExpireDays <= 0 {
		return cfg, errors.New("JWT expiry settings must be positive")
	}
	// 灰度百分比越界一律拒绝启动而不是夹紧：把 150 当 100、把 -5 当 0 都是
	// 「运维以为切了/以为关了，其实另一回事」，与上面的回退开关同一条口径。
	for name, pct := range map[string]int{
		"CLOSEN_CUTOVER_ANALYTICS":   cfg.CutoverAnalytics,
		"CLOSEN_CUTOVER_DISCOVERY":   cfg.CutoverDiscovery,
		"CLOSEN_CUTOVER_CURRICULUM":  cfg.CutoverCurriculum,
		"CLOSEN_CUTOVER_JOURNEY":     cfg.CutoverJourney,
		"CLOSEN_CUTOVER_ONBOARDING":  cfg.CutoverOnboarding,
		"CLOSEN_CUTOVER_MOOD":        cfg.CutoverMood,
		"CLOSEN_CUTOVER_REFLECTIONS": cfg.CutoverReflections,
		"CLOSEN_CUTOVER_MEMORIES":    cfg.CutoverMemories,
	} {
		if pct < 0 || pct > 100 {
			return cfg, fmt.Errorf("%s must be 0..100, got %d", name, pct)
		}
	}
	// 开关只有两个合法值：拼错的 "off " / "false" 一律拒绝启动，而不是静默当成 on——
	// 一个「以为关了其实没关」的回退开关比没有开关更糟（N9 回退开关落点）。
	switch cfg.InsightEvidenceRead {
	case "on", "off":
	default:
		return cfg, errors.New(
			"unsupported CLOSEN_INSIGHT_EVIDENCE_READ: only on|off are accepted")
	}
	return cfg, nil
}

// RequireAuthSecret mirrors backend/main.py::_validate_startup_config: production
// refuses to boot without a secret, development starts and lets tokens fail closed.
func (c Config) RequireAuthSecret() error {
	if c.IsProduction() && c.JWTSecret == "" {
		return errors.New("JWT_SECRET is required in production")
	}
	return nil
}

func (c Config) IsProduction() bool {
	switch c.Environment {
	case "production", "prod":
		return true
	}
	return false
}

func envString(key, fallback string) string {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		return v
	}
	return fallback
}

func envInt(key string, fallback int) int {
	raw, ok := os.LookupEnv(key)
	if !ok || raw == "" {
		return fallback
	}
	v, err := strconv.Atoi(raw)
	if err != nil {
		return fallback
	}
	return v
}
