import type { SqliteDb } from './types';

export interface AnalyticsEventRow {
  id: number;
  event: string;
  props_json: string | null;
  occurred_at: string;
  synced: number; // 0 未上送, 1 已上送
}

export interface PendingAnalyticsEvent {
  id: number;
  event: string;
  properties: Record<string, unknown> | null;
  occurred_at: string;
}

/**
 * 分析事件队列仓储（仅上送）。
 *
 * 只落**行为元数据**（事件名 + 白名单行为属性），绝不落任何敏感内容
 * （反思文本 / 记忆 / 对话原文 / 情绪具体值）。occurred_at 记录真实发生时间，
 * 离线事件延迟上送也能保证口径准确。按 synced 批量上送后端后清理。
 */
export class AnalyticsEventsRepository {
  private db: SqliteDb;

  constructor(db: SqliteDb) {
    this.db = db;
  }

  /** 入队一条行为事件。 */
  enqueue(event: string, properties: Record<string, unknown> | null, occurredAt: string): void {
    this.db.runSync(
      'INSERT INTO analytics_events (event, props_json, occurred_at, synced) VALUES (?, ?, ?, 0)',
      [event, properties ? JSON.stringify(properties) : null, occurredAt]
    );
  }

  /** 取待上送事件（synced=0，按发生时间升序，最多 limit 条）。 */
  listPending(limit: number = 100): PendingAnalyticsEvent[] {
    const rows = this.db.getAllSync<AnalyticsEventRow>(
      'SELECT * FROM analytics_events WHERE synced = 0 ORDER BY occurred_at ASC LIMIT ?',
      [limit]
    );
    return rows.map((r) => ({
      id: r.id,
      event: r.event,
      properties: r.props_json ? (JSON.parse(r.props_json) as Record<string, unknown>) : null,
      occurred_at: r.occurred_at,
    }));
  }

  /** 批量标记已上送。 */
  markSynced(ids: number[]): void {
    for (const id of ids) {
      this.db.runSync('UPDATE analytics_events SET synced = 1 WHERE id = ?', [id]);
    }
  }

  /** 清理已上送（synced=1）的事件。 */
  cleanupSynced(): void {
    this.db.runSync('DELETE FROM analytics_events WHERE synced = 1');
  }

  countPending(): number {
    const row = this.db.getFirstSync<{ count: number }>(
      'SELECT COUNT(*) AS count FROM analytics_events WHERE synced = 0'
    );
    return row?.count ?? 0;
  }
}

import { getDatabase } from './database';

export const analyticsEventsRepo = new AnalyticsEventsRepository(getDatabase());
