// N8 批 4（onboarding 域）在 HTTP 层的依赖面与切流口径。
//
// 这个域是四条已切流域里**唯一一次请求写三处**的：
//
//	users.referral_code            （惰性生成，**只有账号存在才写**）
//	onboarding_profiles            （SELECT-then-INSERT/UPDATE，**无条件写**）
//	journey_events 六行            （prefill，**只有账号存在才写**）
//
// 「条件不同源」是本批最容易抄歪的地方：first_read_service.submit 里 upsert 那一行
// 不在 `if user is not None` 块内（:189-191），而推荐码与 prefill 在（:183-188、:194-195）。
// 于是同一个「已注销/不存在」账号：Python 回 200 并留下一行 profile、share_url 用
// app_store_url 原文（U3-b 起＝ShareLandingURL：未配商店链时为站点根）；Go 若把三处写成同一个条件，就要么少一行 profile、
// 要么干脆 40401。灰度期表现为「同一个用户切流前后 profile 行数不同」。
//
// 接口声明在使用方（httpapi）而不是实现方（store），沿用批 1/2/3。
package httpapi

import (
	"context"
	"strings"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/cutover"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// OnboardingStore 是 onboarding 域需要的两个库动作。
//
// 三处写的输入面（OnboardingSubmitInput／OnboardingWrite）放在 domain 而不是这里：
// store 不能反向依赖 httpapi（domain/arch_test.go 的分层不变量），
// 沿用批 3 把 JourneyPrefill 行形状放 domain 的做法。
type OnboardingStore interface {
	// SubmitOnboarding 在**一个事务**里落三处写，返回 profile.id（响应体的 id 键）。
	// build 会被调用**至少一次**：推荐码在事务里现定，撞码时带着新码再折一遍文本。
	SubmitOnboarding(ctx context.Context, userID string,
		build domain.OnboardingSubmitBuilder) (string, error)
	// OnboardingProfile 读恢复用的那一行（不存在时 Found=false，不是错误）。
	OnboardingProfile(ctx context.Context, userID string) (domain.OnboardingProfile, error)
}

// Batch4 是这批切流域的依赖集合。为 nil 时两条路由不注册（见 server.go 的 routes）。
type Batch4 struct {
	Store OnboardingStore
	Gate  *cutover.Gate
}

// referralLink 是 backend/core/referral.py:34-39：`f"{site_url.rstrip('/')}/r/{code}"`。
// 剥的是**一串**斜杠而不是一个：site_url 配成 "https://x//" 时 Python 也得到单斜杠站点，
// 所以这里是 TrimRight 而不是 TrimSuffix。
func referralLink(siteURL, code string) string {
	return strings.TrimRight(siteURL, "/") + "/r/" + code
}
