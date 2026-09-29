/**
 * 页面埋点（H2）：路由变化 → 一条 screen_view 行为事件。
 *
 * 纯函数放在这里、不进 App.tsx，是因为 jest 跑在 node 环境（testEnvironment: 'node'，
 * 且没有装 @testing-library/react-native），.tsx 里的逻辑无法断言。
 * App.tsx 只负责把 NavigationContainer 的状态变化接过来。
 *
 * 口径：
 * - 属性只有 screen / screen_from 两个，都在 analytics.ts 与后端 ALLOWED_PROPERTY_KEYS 白名单内；
 *   路由名是代码里写死的枚举（Growth / Paywall / …），不含任何用户内容。
 * - 同名不重复上报（tab 来回切到同一页不产生噪声事件）。
 * - 首屏 screen_from 缺省，用于区分「App 打开落到的第一页」与「站内跳转」。
 */

/** react-navigation 状态树的最小子集（不 import 类型包，便于在 node 环境直接构造 fixture）。 */
export interface NavStateLike {
  index?: number;
  routes?: { name?: string; state?: NavStateLike }[];
}

/**
 * 取当前可见的叶子路由名。
 * 必须下钻：根 Stack 里嵌着 Tab Navigator，不下钻就只能看到 "Tabs" 而不是 "Growth"。
 * index 越界/缺失时退化为最后一条路由（RN 栈顶语义）。
 */
export function getActiveRouteName(state?: NavStateLike | null): string | null {
  if (!state || !Array.isArray(state.routes) || state.routes.length === 0) return null;
  const rawIndex = typeof state.index === 'number' ? state.index : state.routes.length - 1;
  const index = rawIndex >= 0 && rawIndex < state.routes.length ? rawIndex : state.routes.length - 1;
  const route = state.routes[index];
  if (!route) return null;
  if (route.state) return getActiveRouteName(route.state) ?? route.name ?? null;
  return route.name ?? null;
}

/** 用 type 而非 interface：track() 的入参是 Record<string, unknown>，只有 type 别名才带隐式索引签名。 */
export type ScreenViewProperties = {
  screen: string;
  screen_from?: string;
};

/** 上一次路由 → 当前路由的属性差量；无变化或未知时返回 null（不上报）。 */
export function screenViewProperties(
  previous: string | null,
  current: string | null
): ScreenViewProperties | null {
  if (!current || current === previous) return null;
  return previous ? { screen: current, screen_from: previous } : { screen: current };
}
