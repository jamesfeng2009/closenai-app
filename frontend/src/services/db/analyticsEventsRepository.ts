import type { SqliteDb } from './types';

export interface AnalyticsEventRow {
  id: number;
  event: string;
  props_json: string | null;
  occurred_at: string;
  synced: number; // 0 未上送, 1 已上送
  event_id: string | null;
  attempts: number;
  next_retry_at: string | null;
  dead_reason: string | null;
  dead_at: string | null;
}

export interface PendingAnalyticsEvent {
  id: number;
  event_id: string;
  event: string;
  properties: Record<string, unknown> | null;
  occurred_at: string;
  attempts: number;
}

/** 客户端生成的幂等键：重试沿用同一个值，服务端据此判 duplicate。 */
export function newAnalyticsEventId(): string {
  const cryptoObj = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
  if (cryptoObj?.randomUUID) return cryptoObj.randomUUID();
  return `evt-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}` +
    `${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * 分析事件队列仓储（仅上送）。
 *
 * 只落**行为元数据**（事件名 + 白名单行为属性），绝不落任何敏感内容
 * （反思文本 / 记忆 / 对话原文 / 情绪具体值）。occurred_at 记录真实发生时间，
 * 离线事件延迟上送也能保证口径准确。
 *
 * T7-02 起每一行带 `event_id`：服务端按它做幂等，重复提交不会让分析平台多收一份。
 * `dead_reason` 非空＝不再重发（永久拒绝或重试耗尽），它不会进 listPending。
 */
export class AnalyticsEventsRepository {
  private db: SqliteDb;

  constructor(db: SqliteDb) {
    this.db = db;
  }

  /** 入队一条行为事件；`eventId` 缺省时现生成一个稳定幂等键。 */
  enqueue(
    event: string,
    properties: Record<string, unknown> | null,
    occurredAt: string,
    eventId: string = newAnalyticsEventId()
  ): void {
    this.db.runSync(
      `INSERT INTO analytics_events
         (event, props_json, occurred_at, synced, event_id)
       VALUES (?, ?, ?, 0, ?)`,
      [event, properties ? JSON.stringify(properties) : null, occurredAt, eventId]
    );
  }

  /**
   * 取这次可以上送的事件：未成功、未进死信、且退避时间已到（按发生时间升序）。
   * 退避中的行留在库里，等下一次 flush 再取。
   */
  listPending(limit: number = 100, nowIso: string = new Date().toISOString()): PendingAnalyticsEvent[] {
    const rows = this.db.getAllSync<AnalyticsEventRow>(
      `SELECT * FROM analytics_events
        WHERE synced = 0
          AND dead_reason IS NULL
          AND (next_retry_at IS NULL OR next_retry_at <= ?)
        ORDER BY occurred_at ASC
        LIMIT ?`,
      [nowIso, limit]
    );
    const pending: PendingAnalyticsEvent[] = [];
    for (const r of rows) {
      let properties: Record<string, unknown> | null = null;
      if (r.props_json) {
        try {
          properties = JSON.parse(r.props_json) as Record<string, unknown>;
        } catch {
          // 坏行不再重试：进死信并跳过，避免一行脏数据永久阻塞 flush 队列。
          this.markDeadLetter(r.id, 'bad_props', nowIso);
          continue;
        }
      }
      if (!r.event_id) {
        this.markDeadLetter(r.id, 'missing_event_id', nowIso);
        continue;
      }
      pending.push({
        id: r.id,
        event_id: r.event_id,
        event: r.event,
        properties,
        occurred_at: r.occurred_at,
        attempts: r.attempts ?? 0,
      });
    }
    return pending;
  }

  /** 批量标记已上送（旧口径保留给清理路径；逐事件确认走 deleteByIds）。 */
  markSynced(ids: number[]): void {
    for (const id of ids) {
      this.db.runSync('UPDATE analytics_events SET synced = 1 WHERE id = ?', [id]);
    }
  }

  /** 清理已上送（synced=1）的事件。 */
  cleanupSynced(): void {
    this.db.runSync('DELETE FROM analytics_events WHERE synced = 1');
  }

  /** 服务端已接收（accepted/duplicate）：本地直接删行。 */
  deleteByIds(ids: number[]): void {
    for (const id of ids) {
      this.db.runSync('DELETE FROM analytics_events WHERE id = ?', [id]);
    }
  }

  /** 可重试失败：累加次数并记下最早可再试时刻。 */
  scheduleRetry(id: number, attempts: number, nextRetryAt: string): void {
    this.db.runSync(
      'UPDATE analytics_events SET attempts = ?, next_retry_at = ? WHERE id = ?',
      [attempts, nextRetryAt, id]
    );
  }

  /** 永久拒绝或重试耗尽：留在库里但不再上送，reason 是机器码。 */
  markDeadLetter(id: number, reason: string, at: string): void {
    this.db.runSync(
      `UPDATE analytics_events
          SET dead_reason = ?, dead_at = ?, next_retry_at = NULL
        WHERE id = ?`,
      [reason, at, id]
    );
  }

  /** 清空全部事件（含未上送与死信）：撤回匿名分析同意时的本地落地动作。 */
  clearAll(): void {
    this.db.runSync('DELETE FROM analytics_events');
  }

  /** 队列里还没成功、也还没进死信的事件数（含退避等待中的）。 */
  countPending(): number {
    const row = this.db.getFirstSync<{ count: number }>(
      `SELECT COUNT(*) AS count FROM analytics_events
        WHERE synced = 0 AND dead_reason IS NULL`
    );
    return row?.count ?? 0;
  }

  countDeadLetters(): number {
    const row = this.db.getFirstSync<{ count: number }>(
      'SELECT COUNT(*) AS count FROM analytics_events WHERE dead_reason IS NOT NULL'
    );
    return row?.count ?? 0;
  }
}

import { getDatabase } from './database';

export const analyticsEventsRepo = new AnalyticsEventsRepository(getDatabase());
