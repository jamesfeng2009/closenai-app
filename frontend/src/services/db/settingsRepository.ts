import type { SqliteDb } from './types';

/** app_settings 键值表仓储（仅本地，不同步）。键为 TEXT 主键，故不复用 BaseRepository。 */
export class SettingsRepository {
  private db: SqliteDb;

  constructor(db: SqliteDb) {
    this.db = db;
  }

  async get(key: string): Promise<string | null> {
    const row = this.db.getFirstSync<{ value: string }>(
      'SELECT value FROM app_settings WHERE key = ?',
      [key]
    );
    return row?.value ?? null;
  }

  async getJson<T>(key: string): Promise<T | null> {
    const v = await this.get(key);
    if (v == null || v === '') return null;
    try {
      return JSON.parse(v) as T;
    } catch {
      return null;
    }
  }

  async set(key: string, value: string): Promise<void> {
    this.db.runSync(
      `INSERT INTO app_settings (key, value, updated_at)
       VALUES (?, ?, ?)
       ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`,
      [key, value, new Date().toISOString()]
    );
  }

  async setJson(key: string, value: unknown): Promise<void> {
    await this.set(key, JSON.stringify(value));
  }
}

/** 生产单例：绑定 expo-sqlite 数据库。 */
import { getDatabase } from './database';

export const settingsRepo = new SettingsRepository(getDatabase());
