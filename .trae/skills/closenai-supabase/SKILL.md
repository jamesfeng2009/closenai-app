---
name: "closenai-supabase"
description: "ClosenAI Supabase PostgreSQL + pgvector database: schema design, vector search, HNSW indexes, halfvec optimization, memory versioning, TTL partitions. Invoke when working on database schema, migrations, vector search, or memory storage."
---

# ClosenAI Supabase + pgvector Database

Supabase PostgreSQL + pgvector 数据库规范：表结构设计、向量搜索、HNSW 索引、halfvec 优化、记忆版本化、TTL 分区

## Database Selection

| Stage | Solution | Cost | Storage |
|-------|----------|------|---------|
| **MVP (current)** | Supabase Free (relational) + Pinecone Free (vector, 2GB) | $0 | 500MB + 2GB |
| **Public Beta** | Supabase Pro + pgvector | $25/mo | 8GB unified |
| **Scale (10K+ DAU)** | Supabase Pro (or Hetzner VPS self-hosted) | $25-46/mo | 8GB+ |

**Key optimization**: Daily summary embedding (1.8GB/month) vs per-message embedding (200GB/month) — 100x difference.

## Core Tables

### Memories Table (Extended)

```sql
CREATE TABLE memories (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  content TEXT NOT NULL,
  embedding halfvec(512),  -- text-embedding-v4 MRL 512 维 + halfvec，省 ~66% vs vector(768)
  
  -- Memory versioning (v2.0)
  status VARCHAR(20) DEFAULT 'current',  -- current | superseded | reverted
  category VARCHAR(20) DEFAULT 'factual', -- values | preference | relationship | goal | factual
  superseded_by UUID REFERENCES memories(id),
  superseded_at TIMESTAMPTZ,
  transition_reason TEXT,
  reverted_from UUID REFERENCES memories(id),
  emotion_context JSONB DEFAULT NULL,
  
  -- Drift detection (P0 supplement)
  source VARCHAR(20) DEFAULT 'self',     -- self | others | hypothetical
  conviction FLOAT DEFAULT 0.5,          -- 0.0-1.0 belief strength
  coexist_with UUID REFERENCES memories(id),
  drift_score FLOAT DEFAULT NULL,
  
  -- P1 optimization
  triggering_event_id UUID REFERENCES memories(id),
  
  deleted BOOLEAN DEFAULT false,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- Indexes
CREATE INDEX idx_memories_user_status ON memories(user_id, status);
CREATE INDEX idx_memories_superseded ON memories(user_id, superseded_by)
  WHERE superseded_by IS NOT NULL;
CREATE INDEX idx_memories_category ON memories(user_id, category);
```

### Subscriptions Table

```sql
CREATE TABLE subscriptions (
  user_id UUID PRIMARY KEY REFERENCES users(id),
  tier TEXT NOT NULL,                 -- free / pro / pro+
  product_id TEXT,
  expires_at TIMESTAMPTZ,
  original_transaction_id TEXT,
  updated_at TIMESTAMPTZ DEFAULT now()
);
```

### Receipts Table

```sql
CREATE TABLE receipts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  transaction_id TEXT NOT NULL,
  product_id TEXT,
  payload JSONB,
  status TEXT NOT NULL,               -- pending / verified / refunded / expired
  created_at TIMESTAMPTZ DEFAULT now()
);
```

### Personality Assessment Sessions

```sql
CREATE TABLE personality_assessment_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  current_question_index INT DEFAULT 0,
  answers_json JSONB DEFAULT '[]',
  status VARCHAR(20) DEFAULT 'in_progress',  -- in_progress | completed
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
```

## pgvector Optimization (10K DAU)

### halfvec (Save 50% Storage)

```sql
-- Embedding 列定义：halfvec(512)（text-embedding-v4 MRL 截断 512 维）
-- 2 bytes/dim vs 4 bytes/dim for float32；768→512 再省 1/3
-- fp16 + MRL 512 对余弦召回损失 <1%（短文本记忆检索场景可忽略）

CREATE INDEX ix_memories_embedding_hnsw
  ON memories
  USING hnsw (embedding halfvec_cosine_ops);
```

### 容量估算（512-dim halfvec，Supabase 免费版 500MB）

| Item | Size |
|------|------|
| 单条记忆（数据 ~1KB + HNSW 图 ~1.2KB） | ~2.3-2.6 KB |
| 免费版可承载记忆量 | ~19 万条（768-float4 方案仅 ~6 万条） |
| 召回影响 | MTEB 多语言召回损失 <1% |

### 多语言向量空间（禁止按语言分库）

- Embedding 用 **text-embedding-v4**（多语言模型，100+ 语种），**所有语言共享同一向量空间**：中文存的记忆，用英文 query 也能命中（跨语言检索天然支持）
- **禁止**按语言分表/分库/在检索 SQL 加 `language` 过滤：用户切换语言后历史记忆必须仍可检索
- `memories` 表**不需要** `language` 隔离列；`content` 保留原文语言即可
- 唯一风险：更换 embedding 模型 = 更换向量空间（v3↔v4 不兼容），存量 `memories.embedding` 必须**全量重嵌入**，否则新旧记忆互相检索不到

### TTL Partition Strategy

```sql
-- Monthly partitions, DROP old partitions (seconds, no VACUUM bloat)
-- DO NOT use DELETE + VACUUM: HNSW indexes need REINDEX after mass DELETE

-- Category-differentiated TTL (P1):
-- values: 365 days
-- relationship: 180 days
-- goal: 90 days
-- preference: 30 days
-- factual: 365 days
```

## Memory Search Queries

### Basic Semantic Search (Current Beliefs)

```sql
SELECT id, content, status, category,
       1 - (embedding <=> $query_vector) AS similarity
FROM memories
WHERE user_id = $user_id
  AND status = 'current'
  AND deleted = false
ORDER BY embedding <=> $query_vector
LIMIT 5;
```

### Extended Search (Including Superseded for Regression Detection)

```sql
SELECT id, content, status, category, transition_reason,
       superseded_at, reverted_from,
       1 - (embedding <=> $query_vector) AS similarity
FROM memories
WHERE user_id = $user_id
  AND status IN ('current', 'superseded')  -- expanded range
  AND deleted = false
ORDER BY embedding <=> $query_vector
LIMIT 5;
```

### Drift Detection (Weekly Cron)

```sql
WITH weekly_similarities AS (
  SELECT m.id AS memory_id,
         ds.week,
         1 - (ds.embedding <=> m.embedding) AS sim
  FROM memories m
  JOIN daily_summaries ds ON ds.user_id = m.user_id
  WHERE m.status = 'current'
    AND m.source = 'self'
    AND m.conviction >= 0.4
    AND ds.created_at >= NOW() - INTERVAL '28 days'
)
SELECT memory_id,
       (MAX(CASE WHEN week = 1 THEN sim END) -
        MAX(CASE WHEN week = 4 THEN sim END)) AS drift_score
FROM weekly_similarities
GROUP BY memory_id
HAVING (MAX(CASE WHEN week = 1 THEN sim END) -
        MAX(CASE WHEN week = 4 THEN sim END)) > 0.15;
```

## Chat Offload Strategy

Raw chat messages can be offloaded to Supabase Storage (Pro includes 100GB):

```sql
-- PG stores only summary + foreign key reference
-- Raw messages stored in Supabase Storage as JSONL
-- Reduces relational data from ~4.1GB to ~0.9GB
```

## Alembic Migration Management（版本管理）

> ⚠️ **建表必须通过 Alembic 迁移，禁止手动在 Supabase SQL Editor 粘贴 DDL。**
> 所有改动走 `alembic revision --autogenerate` + `alembic upgrade head`，保证可回滚、可追溯。

### 项目约定

- 依赖：`sqlalchemy>=2.0.0` + `asyncpg>=0.29.0` + `alembic>=1.13.0` + `pgvector>=0.3.0`
- 依赖必须**同时**写入两处：`pyproject.toml`（Vercel 读取）+ `backend/requirements.txt`（本地开发）
- 连接串：`DATABASE_URL` 从 `.env` 读取，**Vercel Serverless 用 Transaction pooler（端口 6543）**
- 认证：自建 JWT（HS256），不使用 Supabase Auth
- 数据库访问：直接走 SQLAlchemy ORM（asyncpg），**不使用 supabase-py REST**

### 目录结构

```
backend/
├── database.py              # async engine + Base + get_db
├── alembic.ini              # Alembic 配置（不硬编码连接串）
├── alembic/
│   ├── env.py               # 从 backend.config 读取 DATABASE_URL
│   ├── script.py.mako
│   └── versions/            # 迁移文件
└── models/                  # SQLAlchemy ORM 模型（每表一个文件）
    ├── __init__.py          # 导出 Base + 所有模型（供 autogenerate 发现）
    ├── user.py  memory.py  chat.py  mood.py
    ├── growth.py  personality.py  subscription.py  push.py
```

### 常用命令

```bash
# 从 backend/ 目录执行
cd backend

# 生成迁移（自动对比 models 与数据库）
alembic revision --autogenerate -m "description"

# 预览将执行的 SQL（不连接数据库）
alembic upgrade head --sql

# 应用迁移
alembic upgrade head

# 回滚一步
alembic downgrade -1
```

### RLS 约定

- **不开 RLS**：后端通过 service_role 语义访问（FastAPI 层用 `Depends(get_current_user)` 校验），
  RLS 被绕过且会增加 migration 复杂度
- 若未来前端直连 Supabase（Realtime）再给特定表开 RLS

### pgvector 字段

- ORM 用 `pgvector.sqlalchemy.HALFVEC(512)` 定义 embedding 字段
- 迁移中需先 `CREATE EXTENSION IF NOT EXISTS vector;` 再建表

## Migration Path

### Supabase Auth → Self-built JWT

User data migration is straightforward because:
- `user_id` is UUID, all business tables reference it, unchanged after migration
- No passwords to migrate (OAuth + OTP only)
- Export `auth.users` + `auth.identities` → import to self-built tables

**Best practices for current Supabase usage:**
- Don't store business data in `auth.users` — use `public.profiles`
- Use UUID user_id (not auto-increment)
- Don't deeply depend on Supabase Auth special features
- Abstract auth logic in a single service file