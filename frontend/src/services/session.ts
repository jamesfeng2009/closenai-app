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
import { getDatabase } from './db/database';

// re-export 事件订阅（App 根组件挂载时订阅，token 失效/登出/注销均触发）
export { onSessionReset } from './api';

const ONBOARDED_KEY = '@closenai/onboarded';

// 用户级本地表（不分用户隔离，登出/注销必须清空，否则同设备换账号会渲染上一账号
// 缓存数据；注销还涉及 consent 文案「彻底删除所有数据」的合规承诺）。
// analytics_events 为设备级行为队列（无敏感内容），保留以维持上送连续性。
const USER_TABLES = [
  'user_profile',
  'daily_reflections',
  'mood_checkins',
  'ai_messages',
  'growth_dimensions',
  'weekly_insights',
  'memory_snapshots',
  'chat_history',
  'personality_assessment',
  'personality_profile',
  'combo_report',
  'daily_discovery',
  'subscription_status',
];

// app_settings 中的用户级缓存 key（dashboard SWR 缓存存于 SQLite 而非 AsyncStorage）；
// 语言/主题/通知等设备偏好保留。
const USER_SETTING_KEYS = ['@closenai/dashboard_cache'];

/** 清空 SQLite 用户级数据（单事务；失败不阻断会话重置）。
 *
 *  withAnalyticsEvents=true 仅注销使用：analytics_events 是设备级行为队列，
 *  登出后新账号要继续用同一队列，但注销意味着「本机不再持有该身份的任何痕迹」——
 *  未上送的本地事件 distinct_id 仍是那个已删除的 user_id，留着既无意义也违背
 *  「全部记录彻底删除」的承诺。
 */
function clearUserLocalData(withAnalyticsEvents = false): void {
  try {
    const db = getDatabase();
    const stmts = [
      ...USER_TABLES.map((t) => `DELETE FROM ${t};`),
      ...USER_SETTING_KEYS.map(
        (k) => `DELETE FROM app_settings WHERE key = '${k}';`
      ),
      ...(withAnalyticsEvents ? ['DELETE FROM analytics_events;'] : []),
      // 重置同步水位，避免新账号复用上一账号的 last_synced_at
      'UPDATE sync_metadata SET last_synced_at = NULL, record_count = 0;',
    ];
    db.execSync(`BEGIN;\n${stmts.join('\n')}\nCOMMIT;`);
  } catch {
    // ignore：本地清理失败不阻断登出/注销主流程
  }
}

/** 登出 / token 失效：清除本机 token + 用户级缓存（含 SQLite），保留引导标记（游客可继续浏览 Tabs）。 */
export async function resetSession(): Promise<void> {
  await clearAuth();
  clearUserLocalData();
  notifySessionReset();
}

/** 注销账号：清除 token + 引导标记 + 全部用户级本地数据（含本地行为队列），回到引导页。 */
export async function resetSessionFull(): Promise<void> {
  await clearAuth();
  clearUserLocalData(true);
  try {
    await AsyncStorage.removeItem(ONBOARDED_KEY);
  } catch {
    // ignore
  }
  notifySessionReset();
}
