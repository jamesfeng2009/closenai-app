/**
 * 数据共享四态（合规 5.1.2(i)）。
 *
 * 「数据共享」不是一件事，是两件独立同意：
 * - ai_consent：把书写内容发给 LLM 处理（不给就不能用 AI 功能）
 * - analytics_consent：匿名行为元数据上报（页面/功能使用次数，不含内容）
 *
 * 界面上只显示「已同意 / 已拒绝」会把两件事说成一件事：用户以为拒绝了分析，
 * 其实只撤了 AI（或反之）。所以取值必须是 2×2 的四态，而不是任一边单独决定。
 *
 * 纯模块（不含 JSX）便于在 node 环境直接断言真值表。
 */

export type SharingState = 'both' | 'ai_only' | 'analytics_only' | 'none';

/** 四态 → i18n 键。 */
export const SHARING_STATE_KEYS: Record<SharingState, string> = {
  both: 'profile.sharingBoth',
  ai_only: 'profile.sharingAiOnly',
  analytics_only: 'profile.sharingAnalyticsOnly',
  none: 'profile.sharingNone',
};

export function sharingState(ai: boolean, analytics: boolean): SharingState {
  if (ai && analytics) return 'both';
  if (ai) return 'ai_only';
  if (analytics) return 'analytics_only';
  return 'none';
}

/** 四态里有没有同意项：全拒绝时值文案要用中性色，不能标成「已同意」的绿。 */
export function sharingHasConsent(state: SharingState): boolean {
  return state !== 'none';
}
