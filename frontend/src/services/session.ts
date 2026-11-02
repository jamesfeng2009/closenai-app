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

/** 本机清理失败的三个落点（给 UI 区分「服务端已完成」与「本机没清干净」）。 */
export type LocalCleanupFailure = 'secure_store' | 'sqlite' | 'onboarding';

export class LocalCleanupError extends Error {
  readonly failures: LocalCleanupFailure[];

  constructor(failures: LocalCleanupFailure[]) {
    super(`local cleanup failed: ${failures.join(', ')}`);
    this.name = 'LocalCleanupError';
    this.failures = failures;
  }
}

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

/** 清空 SQLite 用户级数据（单事务）。
 *
 *  withAnalyticsEvents=true 仅注销使用：analytics_events 是设备级行为队列，
 *  登出后新账号要继续用同一队列，但注销意味着「本机不再持有该身份的任何痕迹」——
 *  未上送的本地事件 distinct_id 仍是那个已删除的 user_id，留着既无意义也违背
 *  「全部记录彻底删除」的承诺。
 *
 *  返回 false 表示事务没走完（已显式回滚）；调用方必须据此上抛，不能静默当作已清。
 */
function clearUserLocalData(withAnalyticsEvents = false): boolean {
  let db: ReturnType<typeof getDatabase> | undefined;
  try {
    db = getDatabase();
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
    return true;
  } catch {
    // 事务可能停在半途：显式回滚，避免只删了一半的库被当成「已清空」
    try {
      db?.execSync('ROLLBACK;');
    } catch {
      // 事务未开启或已结束时 ROLLBACK 自身会失败，忽略
    }
    return false;
  }
}

/** 登出 / token 失效：清除本机 token + 用户级缓存（含 SQLite），保留引导标记（游客可继续浏览 Tabs）。 */
export async function resetSession(): Promise<void> {
  const failures: LocalCleanupFailure[] = [];
  const secureStoreFailures = await clearAuth();
  if (secureStoreFailures.length > 0) failures.push('secure_store');
  if (!clearUserLocalData()) failures.push('sqlite');
  // 即使本机没清干净也要回到登录页：内存里的 token 已置空，继续留在已登录界面更危险
  notifySessionReset();
  if (failures.length > 0) throw new LocalCleanupError(failures);
}

/** 注销账号：清除 token + 引导标记 + 全部用户级本地数据（含本地行为队列），回到引导页。 */
export async function resetSessionFull(): Promise<void> {
  const failures: LocalCleanupFailure[] = [];
  const secureStoreFailures = await clearAuth();
  if (secureStoreFailures.length > 0) failures.push('secure_store');
  if (!clearUserLocalData(true)) failures.push('sqlite');
  try {
    await AsyncStorage.removeItem(ONBOARDED_KEY);
  } catch {
    failures.push('onboarding');
  }
  notifySessionReset();
  if (failures.length > 0) throw new LocalCleanupError(failures);
}
