/**
 * 会话重置（退出登录 / 注销账号 / token 失效）。
 *
 * 正式认证（JWT）场景下：
 * - resetSession（登出 / token 失效）：清 access/refresh token + 用户级缓存，保留引导标记（游客可继续浏览）
 * - resetSessionFull（注销账号）：清 token + 引导标记 + 缓存，回到引导页
 * - 事件源统一在 api.ts（onSessionReset / notifySessionReset），这里 re-export 供 App 订阅
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { clearAuth, notifySessionReset } from './api';

// re-export 事件订阅（App 根组件挂载时订阅，token 失效/登出/注销均触发）
export { onSessionReset } from './api';

const ONBOARDED_KEY = '@closenai/onboarded';

// 用户级缓存（重置会话时必须清除，否则新身份首屏 SWR 会渲染上一账号数据）
const USER_CACHE_KEYS = [
  '@closenai/dashboard_cache', // HomeScreen 仪表盘缓存（streak/等级/昨日反思）
];

/** 登出 / token 失效：清除本机 token + 用户级缓存，保留引导标记（游客可继续浏览 Tabs）。 */
export async function resetSession(): Promise<void> {
  await clearAuth();
  try {
    await AsyncStorage.multiRemove(USER_CACHE_KEYS);
  } catch {
    // ignore
  }
  notifySessionReset();
}

/** 注销账号：清除 token + 引导标记 + 缓存，回到引导页（新设备身份）。 */
export async function resetSessionFull(): Promise<void> {
  await clearAuth();
  try {
    await AsyncStorage.multiRemove([ONBOARDED_KEY, ...USER_CACHE_KEYS]);
  } catch {
    // ignore
  }
  notifySessionReset();
}
