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

const listeners = new Set<() => void>();

/** 订阅会话重置事件（App 根组件挂载时订阅，返回取消函数）。 */
export function onSessionReset(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** 重置会话：清除本地身份与引导标记，并通知订阅者（回到引导页）。 */
export async function resetSession(): Promise<void> {
  await resetApiUser();
  try {
    await AsyncStorage.removeItem(ONBOARDED_KEY);
  } catch {
    // ignore
  }
  listeners.forEach((fn) => fn());
}
