import type { SqliteDb } from './types';
import { BaseRepository } from './repository';

export interface ComboReportRow {
  id: string;
  period: string;
  report: string; // JSON（完整报告）
  generated_at: string | null;
  updated_at: string;
}

/** Pro 组合报告缓存（Tier 2 AI 计算：服务端为准，本地只读缓存最新一份）。 */
export class ComboReportRepository extends BaseRepository<ComboReportRow> {
  constructor(db: SqliteDb) {
    super(db, 'combo_report');
  }

  /** 取最新一份报告。 */
  async getLatest(): Promise<ComboReportRow | null> {
    return this.db.getFirstSync<ComboReportRow>(
      'SELECT * FROM combo_report ORDER BY updated_at DESC LIMIT 1'
    );
  }

  /** 服务端结果覆盖本地；仅保留最新 1 份。 */
  async cache(row: ComboReportRow): Promise<void> {
    this.upsert(row);
    this.db.runSync(
      `DELETE FROM combo_report WHERE id NOT IN (
         SELECT id FROM combo_report ORDER BY updated_at DESC LIMIT 1
       )`
    );
  }
}

import { getDatabase } from './database';

export const comboReportRepo = new ComboReportRepository(getDatabase());
