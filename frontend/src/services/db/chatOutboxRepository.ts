import type { SqliteDb } from './types';
import { getDatabase } from './database';

export type ChatOutboxStatus = 'pending' | 'sent' | 'discarded';
export type ChatOutboxMessageType = 'reply' | 'mood_checkin';

export interface ChatOutboxRow {
  client_message_id: string;
  content: string;
  message_type: ChatOutboxMessageType;
  created_at: string;
  status: ChatOutboxStatus;
  attempts: number;
  last_error: string | null;
}

/**
 * 对话离线补投队列（P2 #28）。
 *
 * 与 `chat_history` 的区别是职责：chat_history 是「屏幕上有什么」，
 * chat_outbox 是「哪些用户消息还没到服务端」。补投成功后两个状态都要动：
 * outbox 标 sent，chat_history 保留（刷新后还要渲染历史）。
 */
export class ChatOutboxRepository {
  constructor(private db: SqliteDb) {}

  /**
   * 入队。`INSERT OR IGNORE`：同一条消息重复入队（在线发送失败又走离线路径、
   * 或页面重进后重发同一 id）不能把已经 sent 的行打回 pending。
   */
  async enqueue(row: {
    client_message_id: string;
    content: string;
    message_type?: ChatOutboxMessageType;
    created_at: string;
  }): Promise<void> {
    this.db.runSync(
      `INSERT OR IGNORE INTO chat_outbox
         (client_message_id, content, message_type, created_at, status)
       VALUES (?, ?, ?, ?, 'pending')`,
      [
        row.client_message_id,
        row.content,
        row.message_type ?? 'reply',
        row.created_at,
      ]
    );
  }

  /** 按入队顺序取待补投的消息（服务端按同一客户端 id 幂等，重复补投无副作用）。 */
  async listPending(limit = 50): Promise<ChatOutboxRow[]> {
    return this.db.getAllSync<ChatOutboxRow>(
      `SELECT * FROM chat_outbox
        WHERE status = 'pending'
        ORDER BY created_at ASC
        LIMIT ?`,
      [limit]
    );
  }

  async markSent(ids: string[]): Promise<void> {
    this.updateStatus(ids, 'sent');
  }

  /** 服务端永久拒绝（如未同意 AI 数据共享）：不再重发，但行留着可查原因。 */
  async markDiscarded(ids: string[], reason: string | null = null): Promise<void> {
    this.updateStatus(ids, 'discarded', reason);
  }

  /** 可重试失败（网络/5xx）：累计次数与最后一次错误，仍留在 pending。 */
  async recordFailure(ids: string[], error: string): Promise<void> {
    if (ids.length === 0) return;
    this.db.runSync(
      `UPDATE chat_outbox
          SET attempts = attempts + 1, last_error = ?,
              updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
        WHERE client_message_id IN (${placeholders(ids)})`,
      [error, ...ids]
    );
  }

  async countPending(): Promise<number> {
    const row = this.db.getFirstSync<{ count: number }>(
      "SELECT COUNT(*) AS count FROM chat_outbox WHERE status = 'pending'"
    );
    return row?.count ?? 0;
  }

  private updateStatus(
    ids: string[],
    status: ChatOutboxStatus,
    lastError: string | null = null
  ): void {
    if (ids.length === 0) return;
    this.db.runSync(
      `UPDATE chat_outbox
          SET status = ?, last_error = ?,
              updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
        WHERE client_message_id IN (${placeholders(ids)})`,
      [status, lastError, ...ids]
    );
  }
}

function placeholders(values: unknown[]): string {
  return values.map(() => '?').join(', ');
}

export const chatOutboxRepo = new ChatOutboxRepository(getDatabase());
