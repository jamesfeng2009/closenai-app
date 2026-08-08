import type { SqliteDb } from './types';
import { BaseRepository } from './repository';

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
}

import { getDatabase } from './database';

export const aiMessagesRepo = new AiMessagesRepository(getDatabase());
