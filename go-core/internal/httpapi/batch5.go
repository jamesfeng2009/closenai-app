// N8 批 5（mood + reflections 两个域）在 HTTP 层的依赖面与切流口径。
//
// 这一批是四个已切流域里**唯一全是纯读**的一家：三条端点加起来只读三张表
// （mood_checkins 两次、users 一次、growth_records 一次），零写入、零 LLM、零外发。
// 所以「未放量时逐字一致」只依赖 0% 真转交这一条，没有批 3/批 4 那种
// 「同一个账号被两侧分别写过」的连带后果。
//
// 域键与 Ingress 一刀的粒度在这里**第一次分开**（决策点 D-5、并于 2026-10-15 收口为 D-10；
// 编号跳过 D-9，因为 tasks.md 的 §R 通信面裁决已经占了那个号）：
// 灰度键仍是域级（CLOSEN_CUTOVER_MOOD 一条盖 mood/history 与 mood/trend），
// 但 Ingress 的 path 只能切到端点级——同域的 POST /mood/checkin 依赖 LLM 与配额，本批不切。
// 于是「域级一刀」在这批不再成立，清单侧改为按端点列 path（pathType: Exact）。
// ↳ reflections 那一刀**没上**：Ingress 的 path 匹配**不看 HTTP 方法**，
// 而 `/api/v1/reflections` 这一个 path 在契约里挂着 GET 与 POST 两个操作，
// Exact 也会把 POST 一起搬进 go-api，而 Go 只注册了 GET ⇒ 当场 405（与百分比无关）。
// 所以下面第三条 `handleReflectionsList` 已实现、已逐字节回放（夹具 reflection 组 11 条），
// 但**从入口不可达**，与 POST /reflections、/reflections/generate 一起归最后一批；
// CLOSEN_CUTOVER_REFLECTIONS 先声明为 0，放量时只改数字这一条不破。
// 这个错位由 backend/tests/test_k8s_manifests.py 里「按 (方法, 路径) 模拟最长前缀路由后，
// 落到 go-api 的每个契约操作都必须在 server.go 带方法注册」这条判据守住。
package httpapi

import (
	"context"
	"time"

	"github.com/jamesfeng2009/closenai-app/go-core/internal/cutover"
	"github.com/jamesfeng2009/closenai-app/go-core/internal/domain"
)

// 两个灰度域键（与 config 的 CLOSEN_CUTOVER_MOOD / CLOSEN_CUTOVER_REFLECTIONS 对齐）。
const (
	cutoverMood        = "mood"
	cutoverReflections = "reflections"
)

// MoodStore 是 mood 域两条端点需要的三个读动作。
//
// 三条分开、不合并成一次查询，是刻意的（口径同批 1 的 discovery_daily.go）：
// Python 侧就是 get_recent / get_user+count_by_day 各打一次，灰度期两侧打同样的 SQL，
// 「契约 diff 为空」才有可比之处。
type MoodStore interface {
	// MoodHistory 是 mood_repository.get_recent(limit=30)：倒序、无窗口。
	MoodHistory(ctx context.Context, userID string, limit int64) ([]domain.MoodCheckinRow, error)
	// MoodTrendRows 是 mood_repository.count_by_day 的 SQL 部分：
	// created_at >= 窗口下界、**升序**（day_map 的「当日取首个」靠这个顺序）。
	MoodTrendRows(ctx context.Context, userID string, since time.Time) ([]domain.Checkin, error)
	// MoodUserTimezone 是 user_repository.get_user 的 WHERE（status='active'）＋一列；
	// found=false 对应 Python 的 `user is None`，此时趋势按 UTC 折算而不是报错。
	MoodUserTimezone(ctx context.Context, userID string) (timezone string, found bool, err error)
}

// ReflectionStore 是 GET /reflections 需要的一条读。
type ReflectionStore interface {
	// Reflections 是 growth_repository.list_by_type(user_id, "reflection", limit)：
	// record_type 精确匹配、倒序、LIMIT。MemoryIDs 给的是**列原文**（NULL⇒空串），
	// 拆分规则在 domain.SplitMemoryIDs（那是 falsy 判据，不是 NULL 判据）。
	Reflections(ctx context.Context, userID string, recordType string,
		limit int64) ([]domain.ReflectionRow, error)
}

// Batch5 是这批切流域的依赖集合。为 nil 时三条路由不注册（见 server.go 的 routes）。
//
// Users 与批 1 的 UserProfileReader 是**同一个接口**：trend 的 summary 走
// get_request_language 的三级优先，读的就是 users.language；两侧在这一步共用
// 同一条 SQL，所以这个 reader 也共用，不再造一个只有一列的形状。
type Batch5 struct {
	Moods       MoodStore
	Reflections ReflectionStore
	Users       UserProfileReader
	Gate        *cutover.Gate
}
