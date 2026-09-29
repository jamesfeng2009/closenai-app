import type { SqliteDb } from './types';

/** 当前数据库版本（PRAGMA user_version）。 */
export const CURRENT_DB_VERSION = 2;

/** V1: 初始建表 */
export function migrateV1(db: SqliteDb): void {
  db.execSync(`
    -- user_profile
    CREATE TABLE IF NOT EXISTS user_profile (
      id                TEXT PRIMARY KEY NOT NULL,
      email             TEXT NOT NULL,
      display_name      TEXT,
      avatar_url        TEXT,
      language          TEXT NOT NULL DEFAULT 'zh',
      timezone          TEXT NOT NULL DEFAULT 'Asia/Shanghai',
      mbti              TEXT,
      enneagram         TEXT,
      current_streak    INTEGER NOT NULL DEFAULT 0,
      longest_streak    INTEGER NOT NULL DEFAULT 0,
      last_checkin_date TEXT,
      created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
      updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );

    -- daily_reflections
    CREATE TABLE IF NOT EXISTS daily_reflections (
      id                TEXT PRIMARY KEY NOT NULL,
      date              TEXT NOT NULL,
      mood              TEXT,
      mood_emoji        TEXT,
      summary           TEXT,
      full_content_url  TEXT,
      is_ai_generated   INTEGER NOT NULL DEFAULT 0,
      created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
      updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );

    -- mood_checkins
    CREATE TABLE IF NOT EXISTS mood_checkins (
      id                TEXT PRIMARY KEY NOT NULL,
      date              TEXT NOT NULL,
      emoji             TEXT NOT NULL,
      intensity         REAL NOT NULL DEFAULT 0.5,
      note              TEXT,
      ai_interpretation TEXT,
      created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );

    -- ai_messages
    CREATE TABLE IF NOT EXISTS ai_messages (
      id                TEXT PRIMARY KEY NOT NULL,
      scheduled_date    TEXT NOT NULL,
      content_preview   TEXT NOT NULL,
      full_content_url  TEXT,
      priority          TEXT NOT NULL DEFAULT 'normal',
      is_read           INTEGER NOT NULL DEFAULT 0,
      read_at           TEXT,
      created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );

    -- growth_dimensions
    CREATE TABLE IF NOT EXISTS growth_dimensions (
      id               TEXT PRIMARY KEY NOT NULL,
      dimension_name   TEXT NOT NULL,
      dimension_value  REAL NOT NULL,
      date_recorded    TEXT NOT NULL,
      created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );

    -- weekly_insights
    CREATE TABLE IF NOT EXISTS weekly_insights (
      id              TEXT PRIMARY KEY NOT NULL,
      week_start_date TEXT NOT NULL,
      week_end_date   TEXT NOT NULL,
      summary         TEXT,
      key_patterns    TEXT,
      created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
      updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );

    -- memory_snapshots
    CREATE TABLE IF NOT EXISTS memory_snapshots (
      id               TEXT PRIMARY KEY NOT NULL,
      memory_id_remote TEXT NOT NULL,
      content_summary  TEXT NOT NULL,
      category         TEXT NOT NULL DEFAULT 'factual',
      status           TEXT NOT NULL DEFAULT 'current',
      created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
      updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );

    -- chat_history
    CREATE TABLE IF NOT EXISTS chat_history (
      id                TEXT PRIMARY KEY NOT NULL,
      role              TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
      content_preview   TEXT NOT NULL,
      full_content_url  TEXT,
      created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );

    -- app_settings
    CREATE TABLE IF NOT EXISTS app_settings (
      key         TEXT PRIMARY KEY NOT NULL,
      value       TEXT NOT NULL,
      updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );

    -- sync_metadata
    CREATE TABLE IF NOT EXISTS sync_metadata (
      table_name      TEXT PRIMARY KEY NOT NULL,
      last_synced_at  TEXT,
      record_count    INTEGER NOT NULL DEFAULT 0
    );

    -- 索引
    CREATE INDEX IF NOT EXISTS idx_daily_reflections_date
      ON daily_reflections(date DESC);
    CREATE INDEX IF NOT EXISTS idx_mood_checkins_date
      ON mood_checkins(date DESC);
    CREATE INDEX IF NOT EXISTS idx_ai_messages_scheduled_date
      ON ai_messages(scheduled_date DESC);
    CREATE INDEX IF NOT EXISTS idx_ai_messages_is_read
      ON ai_messages(is_read, scheduled_date DESC);
    CREATE INDEX IF NOT EXISTS idx_growth_dimensions_name_date
      ON growth_dimensions(dimension_name, date_recorded DESC);
    CREATE INDEX IF NOT EXISTS idx_weekly_insights_week
      ON weekly_insights(week_start_date DESC);
    CREATE INDEX IF NOT EXISTS idx_memory_snapshots_status
      ON memory_snapshots(status);
    CREATE INDEX IF NOT EXISTS idx_memory_snapshots_updated
      ON memory_snapshots(updated_at DESC);
    CREATE INDEX IF NOT EXISTS idx_chat_history_created
      ON chat_history(created_at DESC);

    -- 预置数据
    INSERT OR IGNORE INTO app_settings (key, value) VALUES
      ('language', 'zh'),
      ('notification_enabled', 'true'),
      ('notification_time', '09:00'),
      ('theme', 'system'),
      ('last_sync_at', ''),
      ('onboarding_completed', 'false');

    INSERT OR IGNORE INTO sync_metadata (table_name, last_synced_at, record_count) VALUES
      ('user_profile',       NULL, 0),
      ('daily_reflections',  NULL, 0),
      ('mood_checkins',      NULL, 0),
      ('ai_messages',        NULL, 0),
      ('growth_dimensions',  NULL, 0),
      ('weekly_insights',    NULL, 0),
      ('memory_snapshots',   NULL, 0),
      ('chat_history',       NULL, 0);
  `);
}

/** V2: 分层模型扩展表（personality_assessment / personality_profile / combo_report / daily_discovery / subscription_status / analytics_events） */
export function migrateV2(db: SqliteDb): void {
  db.execSync(`
    -- personality_assessment (Tier 1)：评估进度，离线断点续答
    CREATE TABLE IF NOT EXISTS personality_assessment (
      id              TEXT PRIMARY KEY NOT NULL,
      status          TEXT NOT NULL DEFAULT 'not_started',
      current_index   INTEGER NOT NULL DEFAULT 0,
      total_questions INTEGER NOT NULL DEFAULT 8,
      answers         TEXT,
      updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );

    -- personality_profile (Tier 2)：画像结果缓存
    CREATE TABLE IF NOT EXISTS personality_profile (
      id           TEXT PRIMARY KEY NOT NULL,
      mbti         TEXT,
      enneagram    TEXT,
      summary      TEXT,
      refreshed_at TEXT,
      created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
      updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );

    -- combo_report (Tier 2)：组合报告缓存
    CREATE TABLE IF NOT EXISTS combo_report (
      id           TEXT PRIMARY KEY NOT NULL,
      period       TEXT NOT NULL DEFAULT 'monthly',
      report       TEXT NOT NULL,
      generated_at TEXT,
      updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );

    -- daily_discovery (Tier 2)：每日发现缓存
    CREATE TABLE IF NOT EXISTS daily_discovery (
      id         TEXT PRIMARY KEY NOT NULL,
      date       TEXT NOT NULL,
      content    TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );

    -- subscription_status (Tier 3)：订阅展示副本，不用于 gate 判断
    CREATE TABLE IF NOT EXISTS subscription_status (
      id         TEXT PRIMARY KEY NOT NULL,
      plan       TEXT,
      expires_at TEXT,
      is_active  INTEGER NOT NULL DEFAULT 0,
      cached_at  TEXT,
      updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );

    -- analytics_events：分析事件队列（仅行为元数据，不落敏感内容）
    CREATE TABLE IF NOT EXISTS analytics_events (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      event       TEXT NOT NULL,
      props_json  TEXT,
      occurred_at TEXT NOT NULL,
      synced      INTEGER NOT NULL DEFAULT 0
    );

    -- 索引
    CREATE INDEX IF NOT EXISTS idx_daily_discovery_date
      ON daily_discovery(date DESC);
    CREATE INDEX IF NOT EXISTS idx_analytics_events_synced
      ON analytics_events(synced);

    -- sync_metadata 预置新增同步表
    INSERT OR IGNORE INTO sync_metadata (table_name, last_synced_at, record_count) VALUES
      ('personality_assessment', NULL, 0),
      ('personality_profile',    NULL, 0),
      ('combo_report',           NULL, 0),
      ('daily_discovery',        NULL, 0);
  `);
}

/**
 * 按版本号顺序执行迁移，并更新 PRAGMA user_version。
 * 迁移必须幂等，失败时由调用方负责事务回滚。
 */
export function runMigrations(db: SqliteDb): void {
  const row = db.getFirstSync<{ user_version: number }>('PRAGMA user_version');
  let version = row?.user_version ?? 0;

  if (version < 1) {
    migrateV1(db);
    version = 1;
  }
  if (version < 2) {
    migrateV2(db);
    version = 2;
  }

  db.execSync(`PRAGMA user_version = ${version}`);
}
