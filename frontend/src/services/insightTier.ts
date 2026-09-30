/**
 * 洞察分级呈现（PRD §5.20 G01）：锁「理解的呈现」，不锁原始数据。
 *
 * 免费态 = 模糊/聚合版 + 「解锁高清」角标（点击进 Paywall）；Pro 态 = 高清。
 * 判定唯一口径：Home 用 dashboard.is_pro（后端同源），Insights 用 profile.tier，
 * 两者最终都过这一个函数，避免 `!== 'free'` 内联判定语义漂移。
 */

/**
 * tier 是否为清晰态（Pro 及以上）。
 * 后端新增付费档位时向前兼容：一切非 'free' 都解锁。
 * tier 未知（null/undefined/空，如游客、profile 未拉到）按免费视角锁定，
 * 宁可先锁后解，不给免费用户闪一帧高清。
 */
export function isProTier(tier: string | null | undefined): boolean {
  return !!tier && tier !== 'free';
}
