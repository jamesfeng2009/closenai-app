/**
 * 洞察分级呈现口径（PRD §5.20）：锁「理解的呈现」，不锁原始数据。
 * isProTier 是 Home/Insights 两屏共用的唯一判定（避免内联 `!== 'free'` 语义漂移）。
 */
import { isProTier } from '../src/services/insightTier';

describe('isProTier：洞察清晰态判定', () => {
  it('free 锁定（免费态模糊 + 解锁角标）', () => {
    expect(isProTier('free')).toBe(false);
  });

  it('pro / pro_plus 解锁', () => {
    expect(isProTier('pro')).toBe(true);
    expect(isProTier('pro_plus')).toBe(true);
  });

  it('tier 未知（null/undefined/空）按免费视角锁定，不闪高清', () => {
    expect(isProTier(null)).toBe(false);
    expect(isProTier(undefined)).toBe(false);
    expect(isProTier('')).toBe(false);
  });

  it('非 free 的任意 tier 值都解锁（后端新增档位向前兼容）', () => {
    expect(isProTier('ultimate')).toBe(true);
  });
});
