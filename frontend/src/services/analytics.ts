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
import type { AnalyticsAckResult, AnalyticsBatchResponse } from './api';
import { analyticsEventsRepo } from './db/analyticsEventsRepository';
import type { PendingAnalyticsEvent } from './db/analyticsEventsRepository';

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
  // 失败原因的机器码（T1-04 付费交付可观测）；只允许枚举值，不许塞 error.message。
  'reason',
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

/** 可重试失败最多重试几次；到顶进死信，不再无限重发。 */
export const MAX_FLUSH_ATTEMPTS = 5;
const RETRY_BASE_MS = 30_000;
const RETRY_MAX_MS = 60 * 60 * 1000;

/** 第 attempts 次失败后的退避时长（指数退避，封顶 1 小时）。 */
export function retryDelayMs(attempts: number): number {
  return Math.min(RETRY_BASE_MS * 2 ** Math.max(attempts - 1, 0), RETRY_MAX_MS);
}

export interface FlushResult {
  /** 服务端回报真正落到分析平台的事件数（不是请求条数）。 */
  relayed: number;
  /** 本次尝试上送的条数；不用它代表成功。 */
  attempted: number;
  accepted: number;
  duplicate: number;
  retryable: number;
  permanent: number;
  /** retryable + permanent：本次没落地的两种原因。 */
  dropped: number;
  /** 仍在本机等待上送（含退避等待中）的事件数。 */
  queued: number;
}

function emptyResult(queued: number): FlushResult {
  return {
    relayed: 0, attempted: 0, accepted: 0, duplicate: 0,
    retryable: 0, permanent: 0, dropped: 0, queued,
  };
}

/**
 * 可重试失败：累加次数并按退避排下一次；到顶则进死信（reason 保留触发原因）。
 * 返回 1 让调用方把这条计入 retryable。
 */
function scheduleRetry(
  event: PendingAnalyticsEvent,
  reason: string,
  nowMs: number,
): number {
  const attempts = event.attempts + 1;
  if (attempts >= MAX_FLUSH_ATTEMPTS) {
    analyticsEventsRepo.markDeadLetter(
      event.id, `retry_exhausted:${reason}`, new Date(nowMs).toISOString()
    );
    return 1;
  }
  analyticsEventsRepo.scheduleRetry(
    event.id,
    attempts,
    new Date(nowMs + retryDelayMs(attempts)).toISOString()
  );
  return 1;
}

/**
 * 批量上送待发送事件，按**逐事件结果**落库（T7-02）。
 *
 * - accepted / duplicate → 删除本地行；
 * - retryable → 保留并退避（到顶进死信，不会无限重发）；
 * - permanent → 直接进死信，reason 用服务端的机器码；
 * - 响应里没有 results（旧后端）或某条缺结果 → 按 retryable 处理，绝不当作成功删除。
 *
 * 返回分项计数；`attempted` 只是本次发了几条，不代表成功。
 */
export async function flushAnalytics(): Promise<FlushResult> {
  if (flushing) return emptyResult(analyticsEventsRepo.countPending());
  flushing = true;
  try {
    const pending = analyticsEventsRepo.listPending();
    if (pending.length === 0) return emptyResult(analyticsEventsRepo.countPending());

    const payload = pending.map((p) => ({
      event: p.event,
      properties: p.properties,
      occurred_at: p.occurred_at,
      event_id: p.event_id,
    }));

    let res: AnalyticsBatchResponse;
    try {
      res = await postAnalyticsBatch(payload);
    } catch {
      // 网络 / 服务端失败：整批保留，按下一次退避重试。
      const nowMs = Date.now();
      for (const p of pending) scheduleRetry(p, 'network', nowMs);
      return {
        relayed: 0, attempted: pending.length, accepted: 0, duplicate: 0,
        retryable: pending.length, permanent: 0, dropped: pending.length,
        queued: analyticsEventsRepo.countPending(),
      };
    }

    const byEventId = new Map<string, AnalyticsAckResult>();
    for (const ack of res.results ?? []) {
      if (ack.event_id) byEventId.set(ack.event_id, ack);
    }
    const acceptedIds: number[] = [];
    const counts = { accepted: 0, duplicate: 0, retryable: 0, permanent: 0 };
    const nowMs = Date.now();
    const nowIso = new Date(nowMs).toISOString();

    for (const p of pending) {
      const ack = byEventId.get(p.event_id);
      if (!ack) {
        // 旧后端没有 results，或这一条没被回报：状态未知，必须留着。
        counts.retryable += scheduleRetry(p, 'unconfirmed', nowMs);
        continue;
      }
      switch (ack.status) {
        case 'accepted':
          counts.accepted += 1;
          acceptedIds.push(p.id);
          break;
        case 'duplicate':
          counts.duplicate += 1;
          acceptedIds.push(p.id);
          break;
        case 'permanent':
          counts.permanent += 1;
          analyticsEventsRepo.markDeadLetter(p.id, ack.reason ?? 'permanent', nowIso);
          break;
        default:
          counts.retryable += scheduleRetry(p, ack.reason ?? 'retryable', nowMs);
      }
    }

    if (acceptedIds.length > 0) analyticsEventsRepo.deleteByIds(acceptedIds);
    analyticsEventsRepo.cleanupSynced();

    return {
      relayed: res.relayed ?? 0,
      attempted: pending.length,
      accepted: counts.accepted,
      duplicate: counts.duplicate,
      retryable: counts.retryable,
      permanent: counts.permanent,
      dropped: counts.retryable + counts.permanent,
      queued: analyticsEventsRepo.countPending(),
    };
  } finally {
    flushing = false;
  }
}
