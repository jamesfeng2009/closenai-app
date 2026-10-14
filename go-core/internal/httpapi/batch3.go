// N8 批 3（journey 域）在 HTTP 层的依赖面与预写口径。
//
// 接口声明在使用方（httpapi）而不是实现方（store），沿用批 1/批 2。
// 批 3 多一件：GET /journey/events 是**懒写端点**——它先补写剧本再读，
// 所以这个域的第一条读接口会改库。写序与提前返回的每一格都是契约，见 ensurePrefilled。
package httpapi

import (
	"context"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/apierr"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/cutover"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// JourneyStore 是 journey 域需要的七个库动作（六条读/写面 + 一次预写事务）。
type JourneyStore interface {
	JourneyUser(ctx context.Context, userID string) (domain.JourneyUser, error)
	OnboardingBase(ctx context.Context, userID string) (time.Time, bool, error)
	PrefillJourney(ctx context.Context, userID string, rows []domain.JourneyPrefill) error
	JourneyEvents(ctx context.Context, userID string,
		limit int) ([]domain.JourneyEventRow, error)
	JourneyUnseenCount(ctx context.Context, userID string) (int, error)
	JourneyOwner(ctx context.Context, eventID string) (domain.JourneyOwner, error)
	AcknowledgeJourneyEvent(ctx context.Context, eventID string) error
}

// Batch3 是这批切流域的依赖集合。为 nil 时两条路由不注册（见 server.go 的 routes）。
type Batch3 struct {
	Store JourneyStore
	Gate  *cutover.Gate
}

// ensurePrefilled 逐字复现 journey_service.ensure_prefilled（:189-201）。
//
// Python 那一段只有**两个提前返回**（都是静默的 `return`，不是抛错），其余出口全是数据库错误；
// 下面两个 `return nil` 就是那两格，一格都不能升成错误码。
//
// 第一格最容易抄歪：Python 侧 `user = await user_repository.get_user(...)`，
// `if user is None: return` ——用户读不到（含 status='deleted'）时**静默返回**，
// 不报 40401，然后 list_events 照样查 journey_events。于是已注销用户若有历史剧本行，
// 这条端点回的是 200 + 那些行。把它升成 40401 看着更「合理」，但那是改契约：
// 灰度期同一个账号会在两侧看到不同状态码。
//
// 第二格是基准日：`profile.created_at if profile and profile.created_at else user.created_at`，
// 再 `if base is None: return`。datetime 永不为假值，所以这里的判空只可能来自列值为 NULL
// （store 把 NULL 折成 found=false，与 Python 的 falsy 一侧同口径）。
//
// 两格都过了才是 prefill 的循环体：计划由 domain 算（时钟走 s.now()，与批 2 同一个注入点），
// 落库由 store 在一个事务里做。prefill 的返回值 written 在 list_events 里被丢掉，
// 所以这里不计数。
func (s *Server) ensurePrefilled(ctx context.Context, userID string) *apierr.Error {
	user, err := s.batch3.Store.JourneyUser(ctx, userID)
	if err != nil {
		return apierr.New(apierr.CodeDatabaseError, "数据库错误 / Database error")
	}
	if !user.Found {
		return nil
	}

	var base *time.Time
	if at, found, err := s.batch3.Store.OnboardingBase(ctx, userID); err != nil {
		return apierr.New(apierr.CodeDatabaseError, "数据库错误 / Database error")
	} else if found {
		base = &at
	} else if user.HasCreatedAt {
		base = &user.CreatedAt
	}
	if base == nil {
		return nil
	}

	plan := domain.JourneyPrefillPlan(user, base, s.now().UTC())
	rows := make([]domain.JourneyPrefill, 0, len(plan))
	for _, row := range plan {
		payload, err := pyDumpsSpaced(prefillPayload(user, row))
		if err != nil {
			// 三键全是 str/int，编不出来的那条路走不到；留着是为了不静默写进半份文本。
			return apierr.New(apierr.CodeDatabaseError, "数据库错误 / Database error")
		}
		rows = append(rows, domain.JourneyPrefill{
			Kind: row.Kind, DayNo: row.DayNo, ScheduledAt: row.ScheduledAt,
			Payload: payload, Past: row.Past,
		})
	}
	if err := s.batch3.Store.PrefillJourney(ctx, userID, rows); err != nil {
		return apierr.New(apierr.CodeDatabaseError, "数据库错误 / Database error")
	}
	return nil
}
