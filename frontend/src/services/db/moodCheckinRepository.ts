import type { MoodCheckinResponse } from '../../types';
import { toLocalDate } from '../../utils/datetime';
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

  /**
   * 把服务端签到响应合并进本地行（同 id upsert）。
   *
   * 服务端契约只回 emoji / 解读 / 时间，没有 note 和 intensity（schemas.py
   * MoodCheckinResponse）。这两个字段是本机用户产的：无条件写 null / 0.5 会
   * 在同 id 覆盖时抹掉用户写过的备注和历史强度。所以既有行的这两列一律保留，
   * 只有调用方明确拿着用户刚输入的 note（ReflectScreen）时才覆盖。
   */
  async mergeRemoteCheckin(
    checkin: MoodCheckinResponse,
    local: { note?: string | null; ai_interpretation?: string | null } = {},
  ): Promise<void> {
    const existing = await this.getById(checkin.id);
    this.upsert({
      id: checkin.id,
      // 既有行的本地日不随请求时区变化漂移；新行才按 created_at 折算
      date: existing?.date ?? toLocalDate(checkin.created_at),
      emoji: checkin.mood_emoji,
      intensity: existing?.intensity ?? 0.5,
      note: local.note !== undefined ? local.note : existing?.note ?? null,
      ai_interpretation:
        local.ai_interpretation !== undefined
          ? local.ai_interpretation
          : checkin.ai_interpretation || existing?.ai_interpretation || null,
      created_at: checkin.created_at,
    });
  }

  /** 历史批量同步：逐条合并，同样保留服务端未提供的本地字段。 */
  async mergeRemoteHistory(checkins: MoodCheckinResponse[]): Promise<void> {
    for (const checkin of checkins) {
      await this.mergeRemoteCheckin(checkin);
    }
  }
}

import { getDatabase } from './database';

export const moodCheckinRepo = new MoodCheckinRepository(getDatabase());
