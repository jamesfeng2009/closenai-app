/**
 * 分析事件服务（本地优先队列 + 批量上送后端）。
 *
 * 原则（见 frontend-sqlite-schema §2.16）：
 * - 只落**行为元数据**，绝不在本地持久化任何敏感内容（反思文本 / 记忆 /
 *   对话原文 / 情绪具体值）——track() 用白名单就地剔除，双保险。
 * - occurred_at 记录真实发生时间，离线事件延迟上送也能保证口径准确。
 * - 在线时 flush() 批量上送后端 /analytics/batch（后端校验 consent +
 *   匿名化后转发 PostHog），成功后标记 synced 并清理；网络失败保留待重试。
 * - 前端不装 PostHog SDK、不直连第三方，token 不进客户端。
 */

import { postAnalyticsBatch } from './api';
import { analyticsEventsRepo } from './db/analyticsEventsRepository';

// 允许入队的匿名行为属性键（白名单）：仅行为元数据，与后端 ALLOWED_PROPERTY_KEYS 对齐。
const ALLOWED_PROPERTY_KEYS = new Set([
  'screen',
  'screen_from',
  'screen_to',
  'section',
  'feature',
  'action',
  'source',
  'trigger',
  'result',
  'duration_ms',
  'count',
  'value',
  'tier',
  'language',
  'platform',
  'app_version',
  'locale',
  'is_new_user',
  'streak_count',
  'relationship_level',
]);

function sanitize(properties?: Record<string, unknown> | null): Record<string, unknown> | null {
  if (!properties) return null;
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(properties)) {
    if (ALLOWED_PROPERTY_KEYS.has(key) && value != null) {
      out[key] = value;
    }
  }
  return Object.keys(out).length > 0 ? out : null;
}

/** 记录一条行为事件（仅行为元数据，无敏感内容）。 */
export function track(event: string, properties?: Record<string, unknown>): void {
  if (!analyticsConsentAllowed) return;
  analyticsEventsRepo.enqueue(event, sanitize(properties), new Date().toISOString());
}

/**
 * 匿名分析同意（users.analytics_consent，与 ai_consent 独立的一项同意）。
 *
 * 默认允许入队：本地队列只存匿名行为元数据，出网仍由后端按 analytics_consent 整批
 * 丢弃（analytics_service.relay_batch），所以「尚未从 profile 灌入」这段时间不会有
 * 任何数据离开设备；灌入 false 之后 track() 直接停，撤回才算真的生效。
 */
let analyticsConsentAllowed = true;

export function setAnalyticsConsentAllowed(allowed: boolean): void {
  analyticsConsentAllowed = allowed;
}

export function analyticsConsentAllowedValue(): boolean {
  return analyticsConsentAllowed;
}

/** 撤回分析同意时清空本地未上送事件（连同 setAnalyticsConsentAllowed(false) 一起用）。 */
export function clearAnalyticsQueue(): void {
  analyticsEventsRepo.clearAll();
}

// 并发上送锁（防多入口同时 flush 重复发送）。
let flushing = false;

export interface FlushResult {
  relayed: number;
  sent: number;
}

/**
 * 批量上送待发送事件。成功（含被后端丢弃）→ 标记 synced 并清理；
 * 网络/服务端异常 → 保留待重试。返回 {relayed, sent}。
 */
export async function flushAnalytics(): Promise<FlushResult> {
  if (flushing) return { relayed: 0, sent: 0 };
  flushing = true;
  try {
    const pending = analyticsEventsRepo.listPending();
    if (pending.length === 0) return { relayed: 0, sent: 0 };
    const payload = pending.map((p) => ({
      event: p.event,
      properties: p.properties,
      occurred_at: p.occurred_at,
    }));
    try {
      const res = await postAnalyticsBatch(payload);
      analyticsEventsRepo.markSynced(pending.map((p) => p.id));
      analyticsEventsRepo.cleanupSynced();
      return { relayed: res.relayed ?? 0, sent: pending.length };
    } catch {
      // 网络/服务端失败：保留事件待下次重试
      return { relayed: 0, sent: 0 };
    }
  } finally {
    flushing = false;
  }
}
