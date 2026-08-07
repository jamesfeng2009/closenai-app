import type { SqliteDb } from './types';
import { BaseRepository } from './repository';

export interface MoodCheckinRow {
  id: string;
  date: string;
  emoji: string;
  intensity: number;
  note: string | null;
  ai_interpretation: string | null;
  created_at: string;
}

/** 情绪签到仓储（Tier 1 用户原创：本地读写 + 离线 + 双向同步）。 */
export class MoodCheckinRepository extends BaseRepository<MoodCheckinRow> {
  constructor(db: SqliteDb) {
    super(db, 'mood_checkins');
  }

  async getByDate(date: string): Promise<MoodCheckinRow | null> {
    return this.db.getFirstSync<MoodCheckinRow>(
      'SELECT * FROM mood_checkins WHERE date = ?',
      [date]
    );
  }

  async hasCheckedInToday(date: string): Promise<boolean> {
    const row = await this.getByDate(date);
    return row != null;
  }

  /** 按日期倒序返回最近 N 天签到（供情绪趋势、连击判断）。 */
  async getRecentDays(days: number): Promise<MoodCheckinRow[]> {
    return this.db.getAllSync<MoodCheckinRow>(
      'SELECT * FROM mood_checkins ORDER BY date DESC LIMIT ?',
      [days]
    );
  }

  async save(checkin: MoodCheckinRow): Promise<void> {
    this.upsert(checkin);
  }
}

import { getDatabase } from './database';

export const moodCheckinRepo = new MoodCheckinRepository(getDatabase());
