// 切流域从 legacy 表搬进来的行形状（N8 批 1）。
//
// 这里只放「判定要读的那几列」，不是 ORM 模型：Python 侧的 User/MoodCheckin 有几十列，
// 两个域的实际判据只有 status='active' 的存在性、language、analytics_consent、
// mood_emoji 与 created_at。列搬多了会把「谁真的依赖这列」这个问题藏起来。
package domain

// UserProfile 是 user_repository.get_user 那条 WHERE 的存在性 + 两个同意/偏好位。
// Found=false 同时覆盖 Python 侧的两种用法：get_user 返回 None，以及 require_user 抛 40401。
type UserProfile struct {
	Language         string
	AnalyticsConsent bool
	Found            bool
}
