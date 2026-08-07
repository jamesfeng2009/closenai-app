import Database from 'better-sqlite3';
import { runMigrations } from '../../src/services/db/migrations';
import type { SqliteDb, SqlValue } from '../../src/services/db/types';

/**
 * 创建内存 SQLite 测试库（better-sqlite3 实现 expo-sqlite 同步 API 子集），
 * 并自动跑完迁移。每个测试用例应新建独立的库，避免相互污染。
 */
export function createTestDb(): SqliteDb {
  const raw = new Database(':memory:');

  const db: SqliteDb = {
    execSync(sql: string) {
      raw.exec(sql);
    },
    runSync(sql: string, params: SqlValue[] = []) {
      const info = raw.prepare(sql).run(...params);
      return { lastInsertRowId: Number(info.lastInsertRowid), changes: info.changes };
    },
    getAllSync<T>(sql: string, params: SqlValue[] = []) {
      return raw.prepare(sql).all(...params) as T[];
    },
    getFirstSync<T>(sql: string, params: SqlValue[] = []) {
      return (raw.prepare(sql).get(...params) as T) ?? null;
    },
    closeSync() {
      raw.close();
    },
  };

  runMigrations(db);
  return db;
}
