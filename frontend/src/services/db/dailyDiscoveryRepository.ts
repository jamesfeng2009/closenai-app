import type { SqliteDb } from './types';
import { BaseRepository } from './repository';

export interface DailyDiscoveryRow {
  id: string;
  date: string;
  content: string; // JSON（发现卡片内容）
  created_at: string;
}

/** 每日发现缓存（Tier 2 AI 计算：服务端为准，本地只读缓存最近 N 天）。 */
export class DailyDiscoveryRepository extends BaseRepository<DailyDiscoveryRow> {
  constructor(db: SqliteDb) {
    super(db, 'daily_discovery');
  }

  /** 按日期查当日发现。 */
  async getByDate(date: string): Promise<DailyDiscoveryRow | null> {
    return this.db.getFirstSync<DailyDiscoveryRow>(
      'SELECT * FROM daily_discovery WHERE date = ?',
      [date]
    );
  }

  /** 按日期倒序返回最近 N 天发现。 */
  async getRecent(limit: number): Promise<DailyDiscoveryRow[]> {
    return this.db.getAllSync<DailyDiscoveryRow>(
      'SELECT * FROM daily_discovery ORDER BY date DESC LIMIT ?',
      [limit]
    );
  }

  /** 服务端结果覆盖本地（远端总是胜出）。 */
  async cache(row: DailyDiscoveryRow): Promise<void> {
    this.upsert(row);
  }
}

import { getDatabase } from './database';

export const dailyDiscoveryRepo = new DailyDiscoveryRepository(getDatabase());
