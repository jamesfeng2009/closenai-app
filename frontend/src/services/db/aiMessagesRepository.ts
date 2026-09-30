import type { SqliteDb } from './types';
import { BaseRepository } from './repository';
import { toLocalDate } from '../../utils/datetime';

export interface AiMessageRow {
  id: string;
  scheduled_date: string;
  content_preview: string;
  full_content_url: string | null;
  priority: string;
  is_read: number; // SQLite 布尔：0 未读 / 1 已读
  read_at: string | null;
  created_at: string;
}

/** AI 主动消息仓储（Tier 2 展示缓存：本地列表 + 已读状态，纯本地不调后端）。 */
export class AiMessagesRepository extends BaseRepository<AiMessageRow> {
  constructor(db: SqliteDb) {
    super(db, 'ai_messages');
  }

  /** 消息列表（按预计发送日期倒序，最新在顶）。 */
  async getAll(): Promise<AiMessageRow[]> {
    return super.getAll('scheduled_date DESC');
  }

  /** 未读数（Reflect 头部角标）。 */
  async getUnreadCount(): Promise<number> {
    const row = this.db.getFirstSync<{ count: number }>(
      'SELECT COUNT(*) AS count FROM ai_messages WHERE is_read = 0'
    );
    return row?.count ?? 0;
  }

  /** 标记单条已读（更新 is_read/read_at）。 */
  async markAsRead(id: string): Promise<void> {
    this.db.runSync('UPDATE ai_messages SET is_read = 1, read_at = ? WHERE id = ?', [
      new Date().toISOString(),
      id,
    ]);
  }

  /** 推送到达落库（按 message_id upsert，后续 /chat/history 同步覆盖为全文）。 */
  async upsertFromPush(id: string, title: string, body: string, receivedAt?: string): Promise<void> {
    const created_at = receivedAt ?? new Date().toISOString();
    this.upsert({
      id,
      scheduled_date: toLocalDate(created_at),
      content_preview: body || title,
      full_content_url: null,
      priority: 'normal',
      is_read: 0,
      read_at: null,
      created_at,
    });
  }

  /** /chat/history 同步落库（message_type=proactive 的消息，id 与服务端一致）。 */
  async upsertFromRemote(id: string, content: string, createdAt: string): Promise<void> {
    this.upsert({
      id,
      scheduled_date: toLocalDate(createdAt),
      content_preview: content,
      full_content_url: null,
      priority: 'normal',
      is_read: 0,
      read_at: null,
      created_at: createdAt,
    });
  }
}

import { getDatabase } from './database';

export const aiMessagesRepo = new AiMessagesRepository(getDatabase());
