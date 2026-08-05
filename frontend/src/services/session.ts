/**
 * 会话重置（退出登录 / 注销账号）。
 *
 * 设备级身份（X-User-Id）场景下：
 * - 重置 = 清除本地用户 ID + 引导标记，生成新身份
 * - App 通过 onSessionReset 订阅，重置后回到引导页
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { resetApiUser } from './api';

const ONBOARDED_KEY = '@closenai/onboarded';

// 用户级缓存（重置会话时必须清除，否则新身份首屏 SWR 会渲染上一账号数据）
const USER_CACHE_KEYS = [
  ONBOARDED_KEY,
  '@closenai/dashboard_cache', // HomeScreen 仪表盘缓存（streak/等级/昨日反思）
];

const listeners = new Set<() => void>();

/** 订阅会话重置事件（App 根组件挂载时订阅，返回取消函数）。 */
export function onSessionReset(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** 重置会话：清除本地身份、引导标记与用户级缓存，并通知订阅者（回到引导页）。 */
export async function resetSession(): Promise<void> {
  await resetApiUser();
  try {
    await AsyncStorage.multiRemove(USER_CACHE_KEYS);
  } catch {
    // ignore
  }
  listeners.forEach((fn) => fn());
}
