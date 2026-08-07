import * as SQLite from 'expo-sqlite';
import { runMigrations } from './migrations';
import type { SqliteDb } from './types';

let db: SqliteDb | null = null;

/** 生产数据库单例（expo-sqlite）。打开时自动执行迁移。 */
export function getDatabase(): SqliteDb {
  if (!db) {
    db = SQLite.openDatabaseSync('closenai.db') as unknown as SqliteDb;
    runMigrations(db);
  }
  return db;
}

export function closeDatabase(): void {
  db?.closeSync();
  db = null;
}
