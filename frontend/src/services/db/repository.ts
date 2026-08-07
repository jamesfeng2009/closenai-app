import type { SqliteDb, SqlValue } from './types';

/**
 * 基础仓储：封装常见 CRUD 与 upsert。
 * 泛型 T 要求含 `id` 主键（以 id 列 upsert 去重）。
 * 测试时注入 better-sqlite3 测试库，生产注入 expo-sqlite 库。
 */
export abstract class BaseRepository<T extends { id: string }> {
  protected db: SqliteDb;
  protected table: string;

  constructor(db: SqliteDb, table: string) {
    this.db = db;
    this.table = table;
  }

  async getById(id: string): Promise<T | null> {
    return this.db.getFirstSync<T>(`SELECT * FROM ${this.table} WHERE id = ?`, [id]);
  }

  async getAll(orderBy = 'created_at DESC'): Promise<T[]> {
    return this.db.getAllSync<T>(`SELECT * FROM ${this.table} ORDER BY ${orderBy}`);
  }

  /** 按主键 id upsert：存在则全字段覆盖，不存在则插入。 */
  protected upsert(record: T): void {
    const cols = Object.keys(record) as (keyof T & string)[];
    const placeholders = cols.map(() => '?').join(', ');
    const updates = cols.map((c) => `${c} = excluded.${c}`).join(', ');
    const values = cols.map((c) => record[c] as unknown) as SqlValue[];

    this.db.runSync(
      `INSERT INTO ${this.table} (${cols.join(', ')})
       VALUES (${placeholders})
       ON CONFLICT(id) DO UPDATE SET ${updates}`,
      values
    );
  }
}
