/** 本地数据库抽象接口：只暴露 Repository 所需的 expo-sqlite 同步 API 子集。 */
export type SqlValue = string | number | null | boolean | Uint8Array;

export interface SqliteRunResult {
  lastInsertRowId: number;
  changes: number;
}

export interface SqliteDb {
  execSync(sql: string): void;
  runSync(sql: string, params?: SqlValue[]): SqliteRunResult;
  getAllSync<T = Record<string, unknown>>(sql: string, params?: SqlValue[]): T[];
  getFirstSync<T = Record<string, unknown>>(sql: string, params?: SqlValue[]): T | null;
  closeSync(): void;
}
