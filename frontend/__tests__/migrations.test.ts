import { createTestDb } from './helpers/testDb';
import { runMigrations } from '../src/services/db/migrations';

const ALL_TABLES = [
  'user_profile', 'daily_reflections', 'mood_checkins', 'ai_messages',
  'growth_dimensions', 'weekly_insights', 'memory_snapshots', 'chat_history',
  'app_settings', 'sync_metadata', 'personality_assessment', 'personality_profile',
  'combo_report', 'daily_discovery', 'subscription_status', 'analytics_events',
];

describe('数据库迁移 (V1 + V2)', () => {
  it('创建全部 16 张表', () => {
    const db = createTestDb();
    const tables = db
      .getAllSync<{ name: string }>(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
      )
      .map((r) => r.name);
    expect(tables).toEqual(expect.arrayContaining(ALL_TABLES));
    db.closeSync();
  });

  it('预置 12 张 sync_metadata 同步表', () => {
    const db = createTestDb();
    const rows = db.getAllSync<{ table_name: string }>('SELECT table_name FROM sync_metadata');
    expect(rows).toHaveLength(12);
    expect(rows.map((r) => r.table_name)).toEqual(
      expect.arrayContaining([
        'user_profile', 'mood_checkins', 'chat_history',
        'personality_assessment', 'combo_report', 'daily_discovery',
      ])
    );
    db.closeSync();
  });

  it('预置 app_settings 默认值', () => {
    const db = createTestDb();
    const lang = db.getFirstSync<{ value: string }>(
      "SELECT value FROM app_settings WHERE key = 'language'"
    );
    expect(lang?.value).toBe('zh');
    db.closeSync();
  });

  it('user_version 更新为 2，且迁移幂等（重复执行不报错）', () => {
    const db = createTestDb();
    expect(runMigrations(db)).not.toThrow;
    const ver = db.getFirstSync<{ user_version: number }>('PRAGMA user_version');
    expect(ver?.user_version).toBe(2);
    db.closeSync();
  });
});
