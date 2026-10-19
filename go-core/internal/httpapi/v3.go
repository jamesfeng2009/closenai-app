// Package-level 的内部面（`/internal/v3/*`）依赖集合与鉴权口。
//
// 这一面**不是**客户端契约的一部分（ADR-002：客户端只认 `/api/v1`，Go 只暴露服务间 API），
// 所以它不进 `contracts/endpoints.md`（那张表由 FastAPI 生成，且
// `test_contract_freeze.py` 断言「表行数 == 路由装饰器数」），登记面在
// ADR 七-10 与本文件的字段注释里。
//
// ── 为什么这条面可以挂在同一个 mux、同一个端口上 ────────────────────────────
// 现网的入口只有两处（`deploy/k8s/50-ingress.yaml` 与本机 Caddy/nginx 反代），
// 它们列出的 path 只有 `/api/v1*`、`/health`，**没有任何一条规则覆盖 `/internal/v3`**
// ⇒ 公网请求在入口就 404，进不到这台进程。集群内则靠 `X-Service-Token` 这一道。
// 分成第二个监听端口要再多开一个 Service＋一套探针与停机顺序，换来的只是
// 「同一件事换一种写法」，而这一道面的判据本来就只有一条：谁拨进来了都得有密钥。
//
// ── 为什么密钥为空时是「不注册」而不是「注册但拒绝」 ─────────────────────────
// 与六个批次同一口径（server.go 的 gated）：没接依赖时那些 path 与「从来没有这个域」同形。
// 内部面的部署顺序天然是「Go 先上、密钥后配」，如果注册了再一律 403，
// BFF 那侧会拿到一片形状陌生的 403；不注册则回 404 信封，与它自己那条
// 「Go 停掉 ⇒ 503」的失败路径同一条可解释的形状。
package httpapi

import (
	"context"
	"crypto/subtle"
	"net/http"
	"strings"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/obs"
)

// 内部面的两个头名（逐字取 ADR-002 与整改计划 §5.2 的口径）。
const (
	serviceTokenHeader = "X-Service-Token"
	internalUserHeader = "X-User-Id"
)

// FeedbackRecorder 是反馈写面需要的那**一个**方法。
//
// 为什么不直接写 `*domain.Service`：单测要能不发一条真 SQL 就验完「六种 type 各回什么码」
// 这一格——那是一张错误映射表，而它的真身（矩阵、留痕、版本账本）已由
// `domain/service_outcome_test.go` 与 store 的真库集成测各自钉住。
// 生产装配（`cmd/go-api/main.go`）填的仍是同一个 `*domain.Service`：
// canonical 的唯一写入口只许有一个实例，否则乐观锁与版本账本会在两条链上各算一份
// （domain/arch_test.go 钉的那条约束）。
type FeedbackRecorder interface {
	RecordUserFeedback(ctx context.Context,
		f domain.FeedbackProposal) (domain.FeedbackReceipt, error)
}

// GoalWriter 是 T4-02 四条写入口需要的**那一组**方法（同样是为了单测不发真 SQL）。
//
// 四个方法都是 `*domain.Service` 上已有的那四条入口，这里只做一次形状收窄：
// 「六种转移各回什么码」是一张错误映射表，它的真身（转移表、留痕、幂等）
// 由 `domain/service_goal_test.go` 与 store 的真库集成测各自钉住。
type GoalWriter interface {
	CreateGoal(ctx context.Context,
		p domain.GoalProposal) (domain.GoalReceipt, error)
	TransitionGoal(ctx context.Context, userID, goalID string,
		verb domain.LifecycleVerb) (domain.GoalReceipt, error)
	CreateAction(ctx context.Context,
		p domain.ActionProposal) (domain.ActionReceipt, error)
	TransitionAction(ctx context.Context, userID, actionID string,
		verb domain.LifecycleVerb) (domain.ActionReceipt, error)
}

// V3 是 `/internal/v3` 这一面的依赖集合（T3-01 读面＋T3-02 反馈写面＋T4-02 目标/行动面
// ＋T4-03 行动结果写面）。
type V3 struct {
	Insights      *domain.InsightBrowser
	Outcomes      FeedbackRecorder
	Goals         *domain.GoalBrowser
	GoalWrites    GoalWriter
	OutcomeWrites OutcomeWriter
}

// OutcomeWriter 是 T4-03 那一条结果入口需要的**那一个**方法（收窄理由与 GoalWriter 同：
// 「四种 result 各回什么码、跨用户回 404、同日不再写第二行」是一张映射表，
// 它的真身由 `domain/action_outcome_test.go` 与 store 的真库集成测各自钉住）。
type OutcomeWriter interface {
	RecordActionOutcome(ctx context.Context,
		p domain.ActionOutcomeProposal) (domain.ActionOutcomeReceipt, error)
}

// enabled 是这一面的注册判据：依赖齐＋密钥非空。
//
// 五格依赖一起判而不是每面各判一次：这一面是一次部署单元——密钥、canonical 池、
// domain.Service 同批配上。分开判会让「goals 忘了装配」表现为 insights 正常而
// goals 那几条 404，而那与「从来没做过这个域」同形，没人会去查。
//
// 这一条判据从 T4-03 起有了一张逐格的闸（v3_internal_enabled_test.go：每格单独置 nil，
// 整面都必须消失）。T4-02 结案行登记的缺陷①「摘掉一格没有任何测会红」就是缺这张表，
// 而那一条登记在案的根因在本票被真的实现了一次：五格 AND 少一格＝装配漏一个域。
func (s *Server) internalEnabled() bool {
	return s.v3 != nil && s.v3.Insights != nil && s.v3.Outcomes != nil &&
		s.v3.Goals != nil && s.v3.GoalWrites != nil && s.v3.OutcomeWrites != nil &&
		s.cfg.GoServiceToken != ""
}

// requireInternalService 是这一面唯一的鉴权口。
//
// 三格各挡一种坏法：
//   - 密钥不合 ⇒ 403 而不是 401：401 的语义是「客户端请重新认证」，而这里的
//     客户端是我们的 BFF，它没有「重新认证」这个动作可做；
//   - `X-User-Id` 缺失 ⇒ 401（ADR-002「Go 一律不信 body 里的 user_id」的另一半：
//     身份**只**从这一格进来）；
//   - 密钥未配置 ⇒ 同样 403，兜住「注册判据与校验判据哪天被分开改」这种退化。
//
// 比对用 `subtle.ConstantTimeCompare`：这条面在集群内，但「比长度就会露怯」
// 这类旁路不该留给以后的人去踩。
//
// 失败时**原样带回 r**而不是 nil：三个 handler 都写成
// `userID, r, apiErr := s.requireInternalService(r)`，返回 nil 会把 nil 赋进 r，
// 于是紧接着的 `writeJSONError(w, r, apiErr)` 在 `r.Context()` 上炸——
// 中间件把它兜成 500，「凭据无效」这个 403 就从响应里消失了。
// 这条不是假想：2026-10-19 本文件第一版就是这么写的，被 v3_test.go 的
// TestV3RejectsMissingOrWrongServiceToken 打成 500/500 抓到。
func (s *Server) requireInternalService(r *http.Request) (string, *http.Request, *apierr.Error) {
	got := r.Header.Get(serviceTokenHeader)
	want := s.cfg.GoServiceToken
	if want == "" || subtle.ConstantTimeCompare([]byte(got), []byte(want)) != 1 {
		return "", r, apierr.New(apierr.CodeForbidden, "服务间凭据无效 / Invalid service token")
	}
	userID := strings.TrimSpace(r.Header.Get(internalUserHeader))
	// "anonymous" 是中间件在没有身份时的占位值；它一旦从头部传进来，
	// 查的就会是所有账号里那颗不存在的主键，看起来像「这个用户没有洞察」——
	// 那是最难查的一种空。
	if userID == "" || userID == anonymous {
		return "", r, apierr.New(apierr.CodeUnauthorized, "缺少用户身份 / Missing user id")
	}
	ctx := r.Context()
	ctx = context.WithValue(ctx, ctxUserID, userID)
	ctx = obs.WithUser(ctx, userID)
	return userID, r.WithContext(ctx), nil
}
