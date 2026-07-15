# 前端本地 SQLite 数据库 Schema 规范

> 来源：ClosenAI PRD v2.0 + 技术方案设计 v1.0
> 状态：P0 阶段执行标准
> 技术栈：React Native (Expo ~51) + expo-sqlite

---

## 1. 设计原则

### 1.1 离线优先

用户打开 App 时先读本地 DB（即时渲染），后台异步拉取远端最新数据。所有列表页 / Dashboard 页的首屏数据必须来自本地 DB，远端数据返回后通过 React state 刷新。

```
App 启动
  |
  +- 1. 读本地 DB -> 立即渲染 UI（< 100ms）
  |
  +- 2. 后台请求远端 API -> 数据有更新则写入本地 DB -> 刷新 UI
```

### 1.2 缓存边界

| 缓存策略 | 适用数据 | 原因 |
|----------|---------|------|
| 只缓存摘要 + 远端引用 | Reflection Card 正文、AI 主动消息全文 | LLM 长文本体积大（500-2000 字），全文缓存浪费存储，用户极少回看 |
| 缓存预览（前 200 字） | chat_history、ai_messages | 满足列表页展示，点击展开时按需从远端拉取 |
| 缓存最近 N 条 | memory_snapshots（最近 5 条）、chat_history（最近 7 天） | 用户只需要最近上下文，历史数据从远端加载 |
| 不缓存 | Auth Token（JWT）、用户密码 | 敏感数据存 Keychain（expo-secure-store），不落 SQLite |

### 1.3 数据冲突策略

统一采用 **last-write-wins**：通过 `updated_at` 字段（ISO 8601 UTC）判断新旧。

```
冲突检测逻辑：
  远端 updated_at > 本地 updated_at -> 用远端覆盖本地
  远端 updated_at <= 本地 updated_at -> 保留本地（本地修改尚未同步）
  本地有、远端无 -> 本地数据为最新（待向上同步）
```

### 1.4 SQLite 类型约定

SQLite 是动态类型系统，本规范使用以下类型亲和性（Type Affinity）约定：

| DDL 类型 | 实际存储 | 用途 |
|----------|---------|------|
| `TEXT` | TEXT | 字符串、UUID、ISO 8601 时间戳（`YYYY-MM-DDTHH:MM:SSZ`） |
| `INTEGER` | INTEGER | 整数、布尔值（0 = false, 1 = true） |
| `REAL` | REAL | 浮点数（情绪强度、维度分数等） |
| `TEXT (JSON)` | TEXT | JSON 序列化字符串（key_patterns 等结构化数据） |

> **时间戳格式**：统一使用 UTC ISO 8601（如 `2026-07-15T09:00:00Z`），由 `strftime('%Y-%m-%dT%H:%M:%SZ', 'now')` 生成默认值。日期字段（`date`, `week_start_date`）使用 `YYYY-MM-DD` 格式。

---

## 2. 表结构定义

### 2.1 user_profile

用户基本信息 + 性格画像快照 + Streak 数据。单行表（只有当前登录用户一条记录）。

```sql
CREATE TABLE IF NOT EXISTS user_profile (
  id                TEXT PRIMARY KEY NOT NULL,          -- 后端 UUID
  email             TEXT NOT NULL,
  display_name      TEXT,
  avatar_url        TEXT,
  language          TEXT NOT NULL DEFAULT 'zh',          -- 'zh' | 'en'
  timezone          TEXT NOT NULL DEFAULT 'Asia/Shanghai',
  -- 性格画像快照
  mbti              TEXT,                                -- 'INTP' | 'ENFP' | ... | NULL（未完成性格发现）
  zodiac_sign       TEXT,                                -- 'Aries' | 'Taurus' | ... | NULL
  enneagram         TEXT,                                -- '1' - '9' | NULL
  -- Streak 数据
  current_streak    INTEGER NOT NULL DEFAULT 0,
  longest_streak    INTEGER NOT NULL DEFAULT 0,
  last_checkin_date TEXT,                                -- 'YYYY-MM-DD'，最近一次签到日期
  -- 元数据
  created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
```

**字段说明：**

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `id` | TEXT | 后端 profiles.id (UUID)，登录后写入本地 | `"a1b2c3d4-..."` |
| `mbti` | TEXT | 性格发现完成后写入，NULL 表示未完成 | `"INTP"` |
| `current_streak` | INTEGER | 当前连续签到天数，每日签到 +1，中断归零 | `15` |
| `last_checkin_date` | TEXT | 判断今日是否已签到、计算 streak 是否中断 | `"2026-07-15"` |

### 2.2 daily_reflections

每日 Reflection Card 记录。`summary` 缓存在本地，完整正文通过 `full_content_url` 按需从远端拉取。

```sql
CREATE TABLE IF NOT EXISTS daily_reflections (
  id                TEXT PRIMARY KEY NOT NULL,
  date              TEXT NOT NULL,                       -- 'YYYY-MM-DD'，当天日期
  mood              TEXT,                                -- 'happy' | 'calm' | 'anxious' | 'sad' | 'angry' | 'tired' | NULL
  mood_emoji        TEXT,                                -- emoji 字符 | NULL
  summary           TEXT,                                -- AI 生成的摘要（本地缓存，200 字以内）
  full_content_url  TEXT,                                -- 远端完整内容 URL，按需拉取
  is_ai_generated   INTEGER NOT NULL DEFAULT 0,          -- 0 = 用户手写, 1 = AI 辅助生成
  created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
```

**设计说明：**
- `summary` 是 AI 生成的 200 字摘要，缓存后首页 / 列表页直接展示，无需请求远端
- `full_content_url` 指向后端存储的完整 Reflection Card 正文，用户点击「展开全文」时才请求
- 每天至多 1 条记录（通过 `date` 唯一约束 + 应用层校验）

### 2.3 mood_checkins

情绪签到记录。`ai_interpretation` 缓存 AI 即时解读，避免重复请求后端。

```sql
CREATE TABLE IF NOT EXISTS mood_checkins (
  id                TEXT PRIMARY KEY NOT NULL,
  date              TEXT NOT NULL,                       -- 'YYYY-MM-DD'
  emoji             TEXT NOT NULL,                       -- 6 表情快捷回复之一
  intensity         REAL NOT NULL DEFAULT 0.5,           -- 0.0 - 1.0，情绪强度
  note              TEXT,                                -- 用户附带的文字备注（可选）
  ai_interpretation TEXT,                                -- AI 即时解读缓存（P0-F03 基础解读 / 深度解读）
  created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
```

**字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `emoji` | TEXT | 6 表情快捷回复之一（P0-F03），NOT NULL |
| `intensity` | REAL | 0.0-1.0，对应 memory-layer.md 的情感强度阈值（> 0.8 触发情绪过滤） |
| `ai_interpretation` | TEXT | AI 解读文本缓存；基础解读由本地模板填充，深度解读由后端 LLM 生成后同步 |

### 2.4 ai_messages

AI 每日主动消息缓存。`content_preview` 是前 200 字预览，完整内容按需拉取。

```sql
CREATE TABLE IF NOT EXISTS ai_messages (
  id                TEXT PRIMARY KEY NOT NULL,
  scheduled_date    TEXT NOT NULL,                       -- 'YYYY-MM-DD'，计划发送日期（用户本地 9:00 所在日）
  content_preview   TEXT NOT NULL,                       -- 前 200 字预览
  full_content_url  TEXT,                                -- 完整内容远端 URL
  priority          TEXT NOT NULL DEFAULT 'normal',      -- 'low' | 'normal' | 'high'
  is_read           INTEGER NOT NULL DEFAULT 0,          -- 0 = 未读, 1 = 已读
  read_at           TEXT,                                -- 首次阅读时间戳
  created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
```

**设计说明：**
- `content_preview` 用于对话流列表展示，用户展开消息时才通过 `full_content_url` 拉取全文
- `priority` 对应后端 Rule Engine 的消息优先级（greeting < mood_checkin < llm_deep）
- `is_read` 用于首页未读小红点计数

### 2.5 growth_dimensions

成长维度数据。对应 P0-F05 Growth Dashboard 的 6 维度量化指标。

```sql
CREATE TABLE IF NOT EXISTS growth_dimensions (
  id               TEXT PRIMARY KEY NOT NULL,
  dimension_name   TEXT NOT NULL,                        -- 维度标识符
  dimension_value  REAL NOT NULL,                        -- 0.0 - 100.0
  date_recorded    TEXT NOT NULL,                        -- 'YYYY-MM-DD'
  created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
```

**dimension_name 枚举值：**

| dimension_name | 对应 Dashboard 维度 | 数据来源 | 更新频率 |
|----------------|---------------------|----------|---------|
| `mood_avg` | Emotional Stability | 情绪签到方差 + 对话情绪分析 | 每日 |
| `social_score` | Social Connection | 对话话题分类（社交/独处） | 每日 |
| `confidence` | Confidence | 对话中确定性/犹豫性语言占比 | 每 3 天 |
| `stress_level` | Stress Level | 焦虑词频 + 工作话题占比 | 每日 |
| `curiosity` | Curiosity | 首次出现的新话题数量 | 每日 |
| `purpose` | Purpose | 涉及目标/计划/未来的对话占比 | 每周 |
| `energy_avg` | （扩展维度） | 对话活跃度 + 签到频率 | 每日 |
| `reflection_count` | （统计维度） | 当日 Reflection Card 数量 | 每日 |
| `insight_count` | （统计维度） | 累计洞察数量 | 每日 |

### 2.6 weekly_insights

每周洞察缓存。对应 P0-F05 Weekly Insight（每周日晚 8:00 生成）。

```sql
CREATE TABLE IF NOT EXISTS weekly_insights (
  id              TEXT PRIMARY KEY NOT NULL,
  week_start_date TEXT NOT NULL,                         -- 'YYYY-MM-DD'，周一日期
  week_end_date   TEXT NOT NULL,                         -- 'YYYY-MM-DD'，周日日期
  summary         TEXT,                                  -- 周报摘要文本
  key_patterns    TEXT,                                  -- JSON 数组字符串
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
```

**key_patterns JSON 结构示例：**

```json
[
  {
    "pattern": "你在水星逆行期间焦虑签到增加 40%，但每次聊完后情绪好转",
    "insight_level": "L2",
    "category": "mood_pattern"
  },
  {
    "pattern": "你的 INTP 特质让你用分析来缓解焦虑——这在大多数时候有效",
    "insight_level": "L2",
    "category": "personality_insight"
  }
]
```

### 2.7 memory_snapshots

记忆快照缓存。只缓存最近 5 条记忆的摘要，不缓存全文。对应后端 Temporal Memory 的记忆列表。

```sql
CREATE TABLE IF NOT EXISTS memory_snapshots (
  id               TEXT PRIMARY KEY NOT NULL,            -- 本地 UUID
  memory_id_remote TEXT NOT NULL,                        -- 后端 memories.id (UUID)
  content_summary  TEXT NOT NULL,                        -- 记忆摘要（50-100 字，非全文）
  category         TEXT NOT NULL DEFAULT 'factual',      -- 'values' | 'preference' | 'relationship' | 'goal' | 'factual'
  status           TEXT NOT NULL DEFAULT 'current',      -- 'current' | 'superseded' | 'reverted'
  created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
```

**设计说明：**
- 本地表最多保留 5 条最近记忆的摘要，超出时删除最旧的（由应用层 `LIMIT 5` + 删除逻辑控制）
- `status` 对应 Temporal Memory 的三态生命周期（详见 memory-layer.md）：
  - `current`：活跃信念，当前有效
  - `superseded`：已被新信念取代
  - `reverted`：回归完成，不再搜索
- `memory_id_remote` 用于点击展开时从远端拉取记忆全文

```sql
-- 可选：防止同一远端记忆重复缓存
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_snapshots_remote_id
  ON memory_snapshots(memory_id_remote);
```

### 2.8 chat_history

对话历史缓存。只缓存最近 7 天的对话预览，超过的从远端加载。

```sql
CREATE TABLE IF NOT EXISTS chat_history (
  id                TEXT PRIMARY KEY NOT NULL,
  role              TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
  content_preview   TEXT NOT NULL,                       -- 前 200 字预览
  full_content_url  TEXT,                                -- 完整内容远端 URL（长消息按需拉取）
  created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
```

**设计说明：**
- `role` 只有 `user` / `assistant` 两种，使用 CHECK 约束保证数据完整性
- `content_preview` 限制前 200 字，用于对话流列表展示
- 超过 7 天的记录在每次同步时自动清理（见第 4 节数据清理策略）
- SSE 流式消息实时写入 `content_preview`，流结束后更新为完整预览

### 2.9 app_settings

应用本地设置。键值对存储，不同步到远端（用户偏好仅本地生效，language 等通过 profile 同步）。

```sql
CREATE TABLE IF NOT EXISTS app_settings (
  key         TEXT PRIMARY KEY NOT NULL,
  value       TEXT NOT NULL,
  updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
```

**预置 key 列表：**

| key | value 示例 | 说明 |
|-----|-----------|------|
| `language` | `"zh"` | App 界面语言 |
| `notification_enabled` | `"true"` | 推送通知开关 |
| `notification_time` | `"09:00"` | 每日主动消息偏好时间 |
| `theme` | `"system"` | `'light'` / `'dark'` / `'system'` |
| `last_sync_at` | `"2026-07-15T09:00:00Z"` | 最近一次完整同步时间 |
| `onboarding_completed` | `"false"` | 引导页是否完成 |

**初始化预置数据：**

```sql
INSERT OR IGNORE INTO app_settings (key, value) VALUES
  ('language', 'zh'),
  ('notification_enabled', 'true'),
  ('notification_time', '09:00'),
  ('theme', 'system'),
  ('last_sync_at', ''),
  ('onboarding_completed', 'false');
```

### 2.10 sync_metadata

同步元数据。记录每张表最后一次同步时间和本地记录数。

```sql
CREATE TABLE IF NOT EXISTS sync_metadata (
  table_name      TEXT PRIMARY KEY NOT NULL,
  last_synced_at  TEXT,                                  -- 最近一次同步时间戳
  record_count    INTEGER NOT NULL DEFAULT 0             -- 本地当前记录数
);
```

**初始化预置数据：**

```sql
INSERT OR IGNORE INTO sync_metadata (table_name, last_synced_at, record_count) VALUES
  ('user_profile',       NULL, 0),
  ('daily_reflections',  NULL, 0),
  ('mood_checkins',      NULL, 0),
  ('ai_messages',        NULL, 0),
  ('growth_dimensions',  NULL, 0),
  ('weekly_insights',    NULL, 0),
  ('memory_snapshots',   NULL, 0),
  ('chat_history',       NULL, 0);
```

---

## 3. 索引定义

### 3.1 索引一览

```sql
-- ============================================================
-- 索引定义
-- 所有索引使用 IF NOT EXISTS，保证迁移脚本可重复执行
-- ============================================================

-- user_profile：单行表，无需额外索引（主键已索引）

-- daily_reflections：按日期查询首页 Dashboard
CREATE INDEX IF NOT EXISTS idx_daily_reflections_date
  ON daily_reflections(date DESC);

-- mood_checkins：按日期查询签到历史 + 趋势图
CREATE INDEX IF NOT EXISTS idx_mood_checkins_date
  ON mood_checkins(date DESC);

-- ai_messages：按日期查询消息列表 + 未读筛选
CREATE INDEX IF NOT EXISTS idx_ai_messages_scheduled_date
  ON ai_messages(scheduled_date DESC);
CREATE INDEX IF NOT EXISTS idx_ai_messages_is_read
  ON ai_messages(is_read, scheduled_date DESC);

-- growth_dimensions：按维度名 + 日期查询趋势曲线
CREATE INDEX IF NOT EXISTS idx_growth_dimensions_name_date
  ON growth_dimensions(dimension_name, date_recorded DESC);

-- weekly_insights：按周查询周报列表
CREATE INDEX IF NOT EXISTS idx_weekly_insights_week
  ON weekly_insights(week_start_date DESC);

-- memory_snapshots：按状态筛选 + 按更新时间排序（最近 5 条）
CREATE INDEX IF NOT EXISTS idx_memory_snapshots_status
  ON memory_snapshots(status);
CREATE INDEX IF NOT EXISTS idx_memory_snapshots_updated
  ON memory_snapshots(updated_at DESC);

-- chat_history：按时间倒序查询对话流
CREATE INDEX IF NOT EXISTS idx_chat_history_created
  ON chat_history(created_at DESC);

-- app_settings / sync_metadata：主键已索引，无需额外索引
```

### 3.2 索引设计依据

| 表 | 索引字段 | 查询场景 |
|----|---------|---------|
| `daily_reflections` | `date DESC` | 首页按日期倒序展示 Reflection 列表 |
| `mood_checkins` | `date DESC` | 成长 Dashboard 情绪趋势图（取最近 30 天） |
| `ai_messages` | `is_read, scheduled_date DESC` | 首页未读消息筛选 + 对话流按日期排序 |
| `growth_dimensions` | `dimension_name, date_recorded DESC` | Dashboard 按维度查趋势曲线 |
| `memory_snapshots` | `status` / `updated_at DESC` | 按状态筛选 current 记忆 + 取最近 5 条 |
| `chat_history` | `created_at DESC` | 对话流按时间倒序展示 |

---

## 4. 数据同步策略

### 4.1 同步方向分类

| 类型 | 说明 | 适用表 |
|------|------|--------|
| **读写双向** | 本地可写，远端也可写，需要冲突解决 | mood_checkins, daily_reflections, chat_history, user_profile |
| **只读缓存** | 本地只读不写，数据来源于远端，远端覆盖本地 | ai_messages, growth_dimensions, weekly_insights, memory_snapshots |
| **仅本地** | 不同步到远端，纯本地配置 | app_settings, sync_metadata |

### 4.2 同步频率

| 触发时机 | 说明 | 涉及表 |
|----------|------|--------|
| **实时**（写入即同步） | 用户操作后立即推送到远端 | mood_checkins（签到后）、chat_history（发消息后） |
| **进入 App 时** | App 启动 / 从后台恢复时全量拉取 | 所有读写双向 + 只读缓存表 |
| **每日定时** | 每日固定时间后台拉取 | growth_dimensions、weekly_insights |
| **推送触发** | 收到远端推送后拉取对应数据 | ai_messages（收到新消息推送） |

### 4.3 冲突解决规则

**统一策略：last-write-wins（基于 `updated_at`）**

```typescript
// 冲突解决伪代码
function resolveConflict(localRecord, remoteRecord) {
  if (!localRecord) return remoteRecord;           // 本地无 -> 直接写入远端
  if (!remoteRecord) return localRecord;            // 远端无 -> 保留本地（待向上同步）
  if (remoteRecord.updated_at > localRecord.updated_at) {
    return remoteRecord;                            // 远端更新 -> 远端覆盖本地
  }
  return localRecord;                               // 本地更新 -> 保留本地
}
```

**特殊情况：**
- `app_settings`：仅本地，无冲突
- `sync_metadata`：仅本地，无冲突
- `ai_messages` / `growth_dimensions` / `weekly_insights` / `memory_snapshots`：只读缓存，远端数据总是覆盖本地（无本地写入，无冲突）

### 4.4 数据清理策略

超过保留期的数据自动清理，控制本地 DB 体积（目标 < 5MB）。

```typescript
/**
 * 数据清理任务
 * 在每次 App 启动同步完成后执行
 */
function cleanupOldData(db: SQLiteDatabase): void {
  const now = new Date();

  // daily_reflections：保留 30 天
  db.runSync(
    `DELETE FROM daily_reflections WHERE date < ?`,
    daysAgoDate(now, 30)
  );

  // mood_checkins：保留 90 天
  db.runSync(
    `DELETE FROM mood_checkins WHERE date < ?`,
    daysAgoDate(now, 90)
  );

  // ai_messages：保留 30 天
  db.runSync(
    `DELETE FROM ai_messages WHERE scheduled_date < ?`,
    daysAgoDate(now, 30)
  );

  // growth_dimensions：保留 90 天
  db.runSync(
    `DELETE FROM growth_dimensions WHERE date_recorded < ?`,
    daysAgoDate(now, 90)
  );

  // weekly_insights：保留 12 周
  db.runSync(
    `DELETE FROM weekly_insights WHERE week_end_date < ?`,
    daysAgoDate(now, 84)
  );

  // chat_history：保留 7 天
  db.runSync(
    `DELETE FROM chat_history WHERE created_at < ?`,
    daysAgoTimestamp(now, 7)
  );

  // memory_snapshots：保留最近 5 条
  db.runSync(
    `DELETE FROM memory_snapshots WHERE id NOT IN (
       SELECT id FROM memory_snapshots
       ORDER BY updated_at DESC
       LIMIT 5
     )`
  );
}
```

### 4.5 同步策略汇总表

| 表名 | 同步方向 | 同步频率 | 本地保留期 | 冲突策略 |
|------|---------|---------|-----------|---------|
| `user_profile` | 读写双向 | 进入 App 时 + 修改后实时 | 永久 | last-write-wins (updated_at) |
| `daily_reflections` | 读写双向 | 进入 App 时 | 30 天 | last-write-wins (updated_at) |
| `mood_checkins` | 读写双向 | 实时（签到后即同步）+ 进入 App 时 | 90 天 | last-write-wins (created_at) |
| `ai_messages` | 只读缓存 | 进入 App 时 + 推送触发 | 30 天 | 远端覆盖本地 |
| `growth_dimensions` | 只读缓存 | 每日定时 + 进入 App 时 | 90 天 | 远端覆盖本地 |
| `weekly_insights` | 只读缓存 | 每日定时 | 12 周 | 远端覆盖本地 |
| `memory_snapshots` | 只读缓存 | 进入 App 时 | 最近 5 条 | 远端覆盖本地 |
| `chat_history` | 读写双向 | 进入 App 时 + 对话后实时 | 7 天 | last-write-wins (created_at) |
| `app_settings` | 仅本地 | 不同步 | 永久 | N/A |
| `sync_metadata` | 仅本地 | 每次同步后更新 | 永久 | N/A |

---

## 5. 数据迁移

### 5.1 版本管理

使用 SQLite 的 `PRAGMA user_version` 进行版本管理。每次 Schema 变更递增版本号，通过迁移函数按版本号顺序执行。

```sql
-- 查询当前版本
PRAGMA user_version;

-- 设置版本（迁移完成后执行）
PRAGMA user_version = 2;
```

### 5.2 迁移框架

```typescript
/**
 * 数据库迁移管理
 *
 * 迁移规则：
 * 1. 每个版本对应一个迁移函数，按版本号顺序执行
 * 2. 迁移函数必须幂等（可重复执行不报错）
 * 3. 迁移在事务中执行，失败则回滚
 * 4. 迁移完成后更新 PRAGMA user_version
 */

const CURRENT_DB_VERSION = 1;

/** V1: 初始建表 */
function migrateV1(db: SQLiteDatabase): void {
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
      zodiac_sign       TEXT,
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

/** V2: 示例 -- 新增 relationship_level 字段（未来迁移） */
// function migrateV2(db: SQLiteDatabase): void {
//   db.execSync(`
//     ALTER TABLE user_profile ADD COLUMN relationship_level TEXT DEFAULT 'L1';
//     CREATE INDEX IF NOT EXISTS idx_user_profile_relationship
//       ON user_profile(relationship_level);
//   `);
// }

/** 迁移函数注册表 */
const MIGRATIONS: Record<number, (db: SQLiteDatabase) => void> = {
  1: migrateV1,
  // 2: migrateV2,
};

/**
 * 执行数据库迁移
 * 在 App 启动时调用，确保 Schema 为最新版本
 */
export function runMigrations(db: SQLiteDatabase): void {
  const currentVersion = db.getFirstSync<{ user_version: number }>(
    'PRAGMA user_version'
  )?.user_version ?? 0;

  for (let v = currentVersion + 1; v <= CURRENT_DB_VERSION; v++) {
    const migrate = MIGRATIONS[v];
    if (!migrate) continue;

    db.execSync('BEGIN TRANSACTION');
    try {
      migrate(db);
      db.execSync(`PRAGMA user_version = ${v}`);
      db.execSync('COMMIT');
    } catch (error) {
      db.execSync('ROLLBACK');
      throw new Error(`Database migration v${v} failed: ${error}`);
    }
  }
}
```

### 5.3 新增字段迁移策略

未来版本新增字段时遵循以下原则：

1. **新增列必须带 DEFAULT 值**——避免 `NOT NULL` 约束在已有数据上失败
2. **使用 `ALTER TABLE ... ADD COLUMN`**——SQLite 不支持 `MODIFY COLUMN`，只能新增
3. **迁移函数必须幂等**——使用 `IF NOT EXISTS` 或先检查列是否存在
4. **向前兼容**——旧版本 App 读取新字段时忽略未知字段（SQLite 动态类型天然支持）
5. **删除字段**——SQLite 不直接支持 `DROP COLUMN`（3.35+ 支持），需要重建表

```sql
-- 示例：V2 新增 relationship_level 字段
ALTER TABLE user_profile ADD COLUMN relationship_level TEXT DEFAULT 'L1';

-- 示例：V3 新增 emotion_context 到 mood_checkins
ALTER TABLE mood_checkins ADD COLUMN emotion_context TEXT;
```

---

## 6. React Native 集成

### 6.1 依赖安装

```bash
npx expo install expo-sqlite
```

> Expo SDK 51 对应 `expo-sqlite` ~14.0.0，使用同步 API（`openDatabaseSync`），API 调用无需 async/await，简化代码。

### 6.2 Database 单例模式

```typescript
// src/services/database.ts

import * as SQLite from 'expo-sqlite';
import { runMigrations } from './migrations';

const DB_NAME = 'closenai.db';

/**
 * 数据库单例
 *
 * 全局只持有一个 SQLiteDatabase 实例，App 启动时初始化并执行迁移。
 * 使用 lazy initialization，首次调用 getDatabase() 时创建。
 */
let dbInstance: SQLite.SQLiteDatabase | null = null;

/**
 * 获取数据库实例（单例）
 *
 * 首次调用时打开数据库并执行迁移，后续调用直接返回缓存实例。
 */
export function getDatabase(): SQLite.SQLiteDatabase {
  if (dbInstance) {
    return dbInstance;
  }

  dbInstance = SQLite.openDatabaseSync(DB_NAME);

  // 启用外键约束
  dbInstance.execSync('PRAGMA foreign_keys = ON;');

  // 执行迁移
  runMigrations(dbInstance);

  return dbInstance;
}

/**
 * 关闭数据库连接
 * 仅在用户登出 / 切换账号时调用
 */
export function closeDatabase(): void {
  if (dbInstance) {
    dbInstance.closeSync();
    dbInstance = null;
  }
}

/**
 * 重置数据库（用户登出时清除本地数据）
 */
export function resetDatabase(): void {
  closeDatabase();
  SQLite.deleteDatabaseSync(DB_NAME);
  // 重新初始化空数据库
  getDatabase();
}
```

### 6.3 基本 CRUD 封装

```typescript
// src/services/db/repository.ts

import type { SQLiteDatabase } from 'expo-sqlite';

/**
 * 通用 Repository 基类
 *
 * 封装常用的 CRUD 操作，子类继承后只需指定表名。
 */
export abstract class BaseRepository<T> {
  constructor(
    protected readonly db: SQLiteDatabase,
    protected readonly tableName: string
  ) {}

  /**
   * 根据 ID 查询单条记录
   */
  findById(id: string): T | null {
    const result = this.db.getFirstSync<T>(
      `SELECT * FROM ${this.tableName} WHERE id = ?`,
      [id]
    );
    return result ?? null;
  }

  /**
   * 查询所有记录
   */
  findAll(limit: number = 100, offset: number = 0): T[] {
    return this.db.getAllSync<T>(
      `SELECT * FROM ${this.tableName} LIMIT ? OFFSET ?`,
      [limit, offset]
    );
  }

  /**
   * 根据 ID 删除记录
   */
  deleteById(id: string): void {
    this.db.runSync(
      `DELETE FROM ${this.tableName} WHERE id = ?`,
      [id]
    );
  }

  /**
   * 删除所有记录（慎用）
   */
  deleteAll(): void {
    this.db.runSync(`DELETE FROM ${this.tableName}`);
  }

  /**
   * 统计记录数
   */
  count(): number {
    const result = this.db.getFirstSync<{ count: number }>(
      `SELECT COUNT(*) as count FROM ${this.tableName}`
    );
    return result?.count ?? 0;
  }
}
```

### 6.4 具体表 Repository 示例

```typescript
// src/services/db/moodCheckinRepository.ts

import { getDatabase } from './database';
import { BaseRepository } from './repository';

export interface MoodCheckin {
  id: string;
  date: string;
  emoji: string;
  intensity: number;
  note: string | null;
  ai_interpretation: string | null;
  created_at: string;
}

export class MoodCheckinRepository extends BaseRepository<MoodCheckin> {
  constructor() {
    super(getDatabase(), 'mood_checkins');
  }

  /**
   * 插入情绪签到记录
   */
  insert(checkin: Omit<MoodCheckin, 'created_at'>): void {
    this.db.runSync(
      `INSERT INTO mood_checkins (id, date, emoji, intensity, note, ai_interpretation)
       VALUES (?, ?, ?, ?, ?, ?)`,
      [
        checkin.id,
        checkin.date,
        checkin.emoji,
        checkin.intensity,
        checkin.note,
        checkin.ai_interpretation,
      ]
    );
  }

  /**
   * 更新 AI 解读（后端深度解读同步后更新）
   */
  updateAiInterpretation(id: string, interpretation: string): void {
    this.db.runSync(
      `UPDATE mood_checkins SET ai_interpretation = ? WHERE id = ?`,
      [interpretation, id]
    );
  }

  /**
   * 查询最近 N 天的签到记录（用于趋势图）
   */
  getRecentDays(days: number): MoodCheckin[] {
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - days);
    const cutoffStr = cutoff.toISOString().slice(0, 10);

    return this.db.getAllSync<MoodCheckin>(
      `SELECT * FROM mood_checkins WHERE date >= ? ORDER BY date DESC`,
      [cutoffStr]
    );
  }

  /**
   * 查询今日是否已签到
   */
  hasCheckedInToday(today: string): boolean {
    const result = this.db.getFirstSync<{ count: number }>(
      `SELECT COUNT(*) as count FROM mood_checkins WHERE date = ?`,
      [today]
    );
    return (result?.count ?? 0) > 0;
  }

  /**
   * 检测连续异常情绪（连续 3 天同一情绪 / 连续负面 2 天）
   * 用于触发深度解读（P0-F03）
   */
  checkConsecutiveAnomaly(): { type: 'same_mood' | 'negative_streak'; days: number } | null {
    const recent = this.db.getAllSync<{ emoji: string }>(
      `SELECT emoji FROM mood_checkins ORDER BY date DESC LIMIT 3`
    );

    if (recent.length < 3) return null;

    // 连续 3 天同一情绪
    if (recent[0].emoji === recent[1].emoji && recent[1].emoji === recent[2].emoji) {
      return { type: 'same_mood', days: 3 };
    }

    // 连续负面 2 天
    const negativeEmojis = ['anxious', 'sad', 'angry'];
    if (negativeEmojis.includes(recent[0].emoji) && negativeEmojis.includes(recent[1].emoji)) {
      return { type: 'negative_streak', days: 2 };
    }

    return null;
  }
}

// 导出单例
export const moodCheckinRepo = new MoodCheckinRepository();
```

### 6.5 app_settings 读写封装

```typescript
// src/services/db/settingsRepository.ts

import { getDatabase } from './database';

/**
 * 应用设置读写（键值对存储）
 */
export const settings = {
  db: getDatabase(),

  get(key: string): string | null {
    const result = this.db.getFirstSync<{ value: string }>(
      `SELECT value FROM app_settings WHERE key = ?`,
      [key]
    );
    return result?.value ?? null;
  },

  getBool(key: string): boolean {
    return this.get(key) === 'true';
  },

  set(key: string, value: string): void {
    this.db.runSync(
      `INSERT INTO app_settings (key, value, updated_at)
       VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
       ON CONFLICT(key) DO UPDATE SET
         value = excluded.value,
         updated_at = excluded.updated_at`,
      [key, value]
    );
  },

  setBool(key: string, value: boolean): void {
    this.set(key, value ? 'true' : 'false');
  },
};
```

### 6.6 sync_metadata 更新封装

```typescript
// src/services/db/syncRepository.ts

import { getDatabase } from './database';

/**
 * 同步元数据管理
 */
export const syncMeta = {
  db: getDatabase(),

  /** 获取某张表的最后同步时间 */
  getLastSynced(tableName: string): string | null {
    const result = this.db.getFirstSync<{ last_synced_at: string }>(
      `SELECT last_synced_at FROM sync_metadata WHERE table_name = ?`,
      [tableName]
    );
    return result?.last_synced_at ?? null;
  },

  /** 更新某张表的同步时间和记录数 */
  updateSyncInfo(tableName: string, recordCount: number): void {
    this.db.runSync(
      `UPDATE sync_metadata
       SET last_synced_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
           record_count = ?
       WHERE table_name = ?`,
      [recordCount, tableName]
    );
  },

  /** 判断是否需要同步（距上次同步超过 N 分钟） */
  shouldSync(tableName: string, intervalMinutes: number): boolean {
    const lastSynced = this.getLastSynced(tableName);
    if (!lastSynced) return true;

    const lastTime = new Date(lastSynced).getTime();
    const elapsed = Date.now() - lastTime;
    return elapsed > intervalMinutes * 60 * 1000;
  },
};
```

### 6.7 App 启动初始化流程

```typescript
// src/services/appInit.ts

import { getDatabase } from './database';
import { cleanupOldData } from './db/cleanup';
import { syncFromRemote } from './db/syncService';

/**
 * App 启动初始化流程
 *
 * 在 App.tsx 的 useEffect 中调用，确保：
 * 1. 数据库已初始化并迁移到最新版本
 * 2. 清理过期数据
 * 3. 后台同步远端数据
 */
export async function initApp(): Promise<void> {
  // 1. 初始化数据库（触发迁移）
  const db = getDatabase();

  // 2. 清理过期数据
  cleanupOldData(db);

  // 3. 后台同步（不阻塞 UI 渲染）
  syncFromRemote().catch((error) => {
    console.warn('[AppInit] Background sync failed:', error);
  });

  // 4. 更新 last_sync_at（同步完成后由 syncService 内部更新）
}
```

```typescript
// App.tsx 中的调用示例
import { useEffect, useState } from 'react';
import { initApp } from './src/services/appInit';

export default function App() {
  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    initApp().then(() => setIsReady(true));
  }, []);

  if (!isReady) {
    return <SplashScreen />;
  }

  return <MainNavigator />;
}
```
