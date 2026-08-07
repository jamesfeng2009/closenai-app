import type { SqliteDb } from './types';
import { BaseRepository } from './repository';

export type ChatRole = 'user' | 'assistant';

export interface ChatRow {
  id: string;
  role: ChatRole;
  content_preview: string;
  full_content_url: string | null;
  created_at: string;
}

/** 对话历史仓储（Tier 1 用户原创：本地读写 + 离线 + 双向同步，保留最近 7 天）。 */
export class ChatRepository extends BaseRepository<ChatRow> {
  constructor(db: SqliteDb) {
    super(db, 'chat_history');
  }

  /** 追加一条消息（SSE 流式实时写入 content_preview）。 */
  async add(message: ChatRow): Promise<void> {
    this.upsert(message);
  }

  /** 批量写入（远端历史合并时用）。 */
  async addAll(messages: ChatRow[]): Promise<void> {
    for (const m of messages) {
      this.upsert(m);
    }
  }

  /** 按时间倒序返回最近 N 条消息（对话流）。 */
  async getRecent(limit: number): Promise<ChatRow[]> {
    return this.db.getAllSync<ChatRow>(
      'SELECT * FROM chat_history ORDER BY created_at DESC LIMIT ?',
      [limit]
    );
  }

  /** 按时间倒序返回某角色消息（如助手未读数）。 */
  async countByRole(role: ChatRole): Promise<number> {
    const row = this.db.getFirstSync<{ count: number }>(
      'SELECT COUNT(*) AS count FROM chat_history WHERE role = ?',
      [role]
    );
    return row?.count ?? 0;
  }
}

import { getDatabase } from './database';

export const chatRepo = new ChatRepository(getDatabase());
