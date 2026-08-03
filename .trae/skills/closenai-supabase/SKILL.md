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
  embedding halfvec(768),  -- Gemini embedding, halfvec saves 50% vs float32
  
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
-- Use halfvec instead of vector(768) for embeddings
-- 2 bytes/dim vs 4 bytes/dim for float32
-- fp16 precision loss negligible for cosine similarity recall

CREATE INDEX idx_memories_embedding 
  ON memories 
  USING hnsw ((embedding::halfvec(768)) halfvec_cosine_ops);
```

### 8GB Budget Allocation (Optimized)

| Item | Size |
|------|------|
| System/PG + relational indexes | ~0.5 GB |
| Other P0 data (chat/growth/check-ins, 90 days) | ~4.1 GB (or ~0.9 GB with chat offload) |
| Embedding (768-dim halfvec, HNSW, 90 days) | ~4.4-5.0 GB |
| **Total** | **~9.0-9.6 GB (over 8GB; with chat offload: ~5.8 GB ✅)** |

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