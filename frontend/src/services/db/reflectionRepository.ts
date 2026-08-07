import type { SqliteDb } from './types';
import { BaseRepository } from './repository';

export interface ReflectionRow {
  id: string;
  date: string;
  mood: string | null;
  mood_emoji: string | null;
  summary: string | null;
  full_content_url: string | null;
  is_ai_generated: number;
  created_at: string;
  updated_at: string;
}

/** 每日反思仓储（Tier 1 用户原创：本地读写 + 离线 + 双向同步）。 */
export class ReflectionRepository extends BaseRepository<ReflectionRow> {
  constructor(db: SqliteDb) {
    super(db, 'daily_reflections');
  }

  /** 按日期查当日反思（每天至多 1 条）。 */
  async getByDate(date: string): Promise<ReflectionRow | null> {
    return this.db.getFirstSync<ReflectionRow>(
      'SELECT * FROM daily_reflections WHERE date = ?',
      [date]
    );
  }

  /** 按日期倒序返回最近 N 条反思（首页/列表）。 */
  async getRecent(limit: number): Promise<ReflectionRow[]> {
    return this.db.getAllSync<ReflectionRow>(
      'SELECT * FROM daily_reflections ORDER BY date DESC LIMIT ?',
      [limit]
    );
  }

  async save(reflection: ReflectionRow): Promise<void> {
    this.upsert(reflection);
  }
}

import { getDatabase } from './database';

export const reflectionRepo = new ReflectionRepository(getDatabase());
