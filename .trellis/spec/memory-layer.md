# Memory Layer 规范

> 来源：ClosenAI PRD v2.0 第 4.5 节 + 技术方案设计 v1.0 第 4 节
> 状态：P0 阶段执行标准

---

## 1. Temporal Memory 数据模型

### 1.1 memories 表完整字段

```sql
-- 基础字段（已有）
CREATE TABLE IF NOT EXISTS memories (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL REFERENCES profiles(id),
  content         TEXT NOT NULL,            -- 记忆内容
  embedding       halfvec(768),             -- 768 维 halfvec 向量（Gemini Embedding 原生维度+半精度）
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Temporal Memory 扩展字段（P0 新增）
ALTER TABLE memories ADD COLUMN IF NOT EXISTS (
  status            VARCHAR(20) DEFAULT 'current',   -- current | superseded | reverted
  category          VARCHAR(20) DEFAULT 'factual',   -- values | preference | relationship | goal | factual
  superseded_by     UUID REFERENCES memories(id),    -- 被哪条记忆取代
  superseded_at     TIMESTAMPTZ,                     -- 取代时间
  transition_reason TEXT,                            -- 转变原因（AI 从对话中提取）
  reverted_from     UUID REFERENCES memories(id),    -- 回归时指向原始 superseded 记忆
  emotion_context   JSONB DEFAULT NULL,              -- 转变时的情绪上下文（用于情绪过滤）
  source            VARCHAR(20) DEFAULT 'self',      -- self | others | hypothetical
  conviction        FLOAT DEFAULT 0.5,               -- 0.0-1.0 信念强度
  coexist_with      UUID REFERENCES memories(id),    -- 矛盾共存指向
  drift_score       FLOAT DEFAULT NULL,              -- 渐变漂移分数（周维度更新）
  deleted           BOOLEAN DEFAULT FALSE            -- 软删除标记
);

-- 索引
CREATE INDEX idx_memories_status ON memories(user_id, status);
CREATE INDEX idx_memories_superseded ON memories(user_id, superseded_by)
  WHERE superseded_by IS NOT NULL;
CREATE INDEX idx_memories_category ON memories(user_id, category, status);
CREATE INDEX idx_memories_embedding ON memories
  USING hnsw ((embedding::halfvec(768)) halfvec_cosine_ops);
```

### 1.2 status 三态生命周期

```
                   转变检测（用户表达矛盾）
         current ─────────────────────────▶ superseded
           ▲                                     │
           │                                     │ 回归识别
           │     回归识别（语义匹配）              │ (similarity > 0.80)
           │                                     ▼
           └───────────────────────────── reverted
                                           (不再被搜索)
```

| 状态 | 说明 | 是否参与语义搜索 | 语义搜索角色 |
|------|------|----------------|-------------|
| `current` | 活跃信念，当前有效 | 是 | `[当前信念]` |
| `superseded` | 已被新信念取代 | 是（扩大搜索范围以检测回归） | `[历史信念·已转变]` |
| `reverted` | 回归完成，不再搜索 | 否 | `[回归信念]`（仅通过 reverted_from 引用） |

### 1.3 category 记忆类别

| category | 说明 | 示例 | 矛盾检测范围 |
|----------|------|------|-------------|
| `values` | 价值观 | "重视 work-life balance" | 仅同 category 内检测 |
| `preference` | 偏好 | "喜欢独处" | 仅同 category 内检测 |
| `relationship` | 关系 | "和父亲沟通困难" | 仅同 category 内检测 |
| `goal` | 目标 | "想晋升到管理层" | 仅同 category 内检测 |
| `factual` | 事实信息 | "职业是软件工程师" | 仅同 category 内检测 |

**跨类别共存规则**：不同 category 的记忆可以共存，不检测矛盾。如 `values: "重视家庭"` + `preference: "喜欢独处"` 不矛盾。

---

## 2. 信念演变三阶段流程

### 2.1 阶段 1：转变检测（写入时）

触发条件：每日摘要生成后，在 embedding 写入向量表的同时执行轻量级矛盾检测。

```
① 候选筛选
   从该用户 status='current' 且 category 相同的记忆中，
   用 embedding cosine similarity 搜索 Top-3 最相关记忆（阈值 > 0.75）

② 矛盾判断
   用 Gemini 2.5 Flash-Lite 做一次分类判断（成本 ~$0.00003/次）：
   输入：当前摘要 + 候选记忆（Top-3）
   输出：{ "conflicts_with": "memory_id 或 null", "reason": "矛盾原因" }

③ 情绪过滤
   如果当日情绪签到的情感强度 > 0.8（P0-F03 数据），
   标记 emotion_context，不触发状态变更，仅记录为"待观察"。
   3 天后如无重复矛盾表达，清除标记。

④ 确认转变
   AI 在次日主动消息中自然确认："我记得你之前觉得 A，现在想法变了吗？"
   用户确认后：
     - 旧记忆标记 superseded（superseded_by 指向新记忆）
     - 新记忆标记 current
     - 记录 transition_reason
```

### 2.2 阶段 2：回归识别（读取时）

在记忆注入阶段，语义搜索范围从 `status='current'` 扩展为 `status IN ('current', 'superseded')`：

```sql
-- 记忆注入查询
SELECT id, content, status, category, transition_reason,
       superseded_at, reverted_from,
       1 - (embedding <=> $query_vector) AS similarity
FROM memories
WHERE user_id = $user_id
  AND status IN ('current', 'superseded')  -- 扩大搜索范围
  AND deleted = FALSE
ORDER BY embedding <=> $query_vector
LIMIT 5;
```

当 Top-5 结果中包含 `status='superseded'` 且 similarity > 0.80 时，触发回归识别：

1. 标记旧记忆为 `reverted`（不再被搜索到，避免重复匹配）
2. 新记忆 `reverted_from` 指向原记忆 ID
3. 记忆注入 prompt 追加回归认知上下文

### 2.3 阶段 3：情绪过滤

| 触发条件 | AI 行为 | 记忆操作 |
|----------|--------|---------|
| 矛盾表达发生在高强度情绪状态下（情感强度 > 0.8） | 不标记转变，记录为"当天情绪波动"。下次对话仍引用原 current 信念 | 不改变记忆状态，仅附加 emotion_context 标记 |

3 天后如无重复矛盾表达，清除 emotion_context 标记。

---

## 3. 渐变漂移检测

### 3.1 问题

渐变漂移——没有任何单条消息与 A 直接矛盾，但 3 个月后整体立场已反转。单条矛盾检测会完全漏检。

```
Day 1:   "我觉得 work-life balance 最重要"（A，conviction=0.8）
Day 30:  "最近也开始觉得想要拼搏一下"
Day 60:  "可能趁年轻多冲一冲"
Day 90:  "我觉得晋升更重要"（B）
→ 没有任何单条与 A 矛盾，但立场已反转
```

### 3.2 周维度 Cron Job 检测

每周日凌晨执行：对每条 current 信念计算最近 4 周漂移分数。

```sql
-- 每周日凌晨执行：对每条 current 信念计算最近 4 周漂移分数
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
HAVING MAX(CASE WHEN week = 1 THEN sim END) -
       MAX(CASE WHEN week = 4 THEN sim END) > 0.15;  -- 漂移阈值
-- drift_score > 0.15 时触发渐变提醒，写入 AI 主动消息队列
```

### 3.3 触发后的 AI 行为

AI 在 Weekly Insight 中主动确认：
> "我注意到你最近越来越多地提到拼搏和晋升，你对 work-life balance 的想法是不是在变化？"

---

## 4. source / conviction / coexist_with 字段

### 4.1 source 字段（记忆来源标记）

| source 值 | 说明 | 参与矛盾检测 | 参与语义搜索 |
|-----------|------|-------------|-------------|
| `self` | 用户自身信念 | 是 | 是 |
| `others` | 引用他人观点（如"我朋友觉得..."） | 否 | 是（AI 可引用"你朋友曾说过..."） |
| `hypothetical` | 假设性表达（如"如果...我会..."） | 否 | 是 |

**实现**：`source` 字段在每日摘要生成时由 LLM 一次提取（在摘要 prompt 中追加"标注每条信念的来源：self/others/hypothetical"）。增量成本 = 0（合并到已有摘要调用）。

### 4.2 conviction 字段（信念强度）

- 范围：0.0 - 1.0，默认 0.5
- LLM 在摘要生成时评估用户表达的确定程度
- `conviction >= 0.4` 的记忆参与渐变漂移检测
- 低 conviction 记忆（< 0.4）不触发转变流程，仅存储

### 4.3 coexist_with 字段（矛盾共存指向）

当两个不同来源但同 category 的记忆存在表面矛盾但实际共存时（如"重视家庭"values + "经常加班"goal），通过 `coexist_with` 互相指向，标记为共存而非矛盾。

---

## 5. 记忆注入 Prompt 模板

### 5.1 按状态注入

```javascript
// 记忆注入 prompt（扩展后）
const memoryContext = memories.map(m => {
  if (m.status === 'current') {
    return `[当前信念] ${m.content}`;
  }
  if (m.status === 'superseded') {
    return `[历史信念·已转变] ${m.content} → 转变原因：${m.transition_reason}`;
  }
  if (m.status === 'reverted' && m.reverted_from) {
    const original = memories.find(x => x.id === m.reverted_from);
    return `[回归信念] 用户曾持有此观点，后因${original.transition_reason}转变，现已回归。` +
           `请在回复中自然体现这一回归认知，如"你又回到了...上次你是因为..."`;
  }
  return m.content;
}).join('\n');
```

### 5.2 完整注入上下文

```python
# 每次对话时注入的上下文（延迟 < 500ms）
context = {
    "personality_type": "INTP",                    # 性格画像
    "recent_memories": memory_context,             # 最近 5 条相关记忆（按状态标注）
    "recent_insights": insights_7days,             # 最近 7 天洞察摘要（高密度上下文）
    "relationship_level": "L2",                    # 关系等级
    "day_number": 15,                              # 使用天数
    "emotion_context": today_mood,                 # 今日情绪签到
}
```

### 5.3 AI 表达策略（按记忆状态）

| 场景 | AI 表达 | 设计原因 |
|------|---------|---------|
| 全新观点（无历史） | "这个想法很有意思，跟我多说说" | 不假装认识，自然引入 |
| 观点转变（A→B） | "我记得你之前觉得 A，现在转向 B 了，是什么让你改变了想法？" | 体现记忆连续性，引导反思转变原因 |
| 观点回归（A→B→A） | "你又回到了 A 的想法——上次你是因为 Z 才转向 B 的，这次是什么原因？" | 证明 AI 真正在追踪成长轨迹 |
| 情绪化临时表达 | 不标记转变，下次仍以原观点为基础对话 | 避免"今天吵架就觉得一切都不对"被误判为信念转变 |

---

## 6. 边界情况处理

| 边界情况 | 处理方式 |
|----------|---------|
| 同一信念多次反复（A→B→A→B→A） | 每次转变/回归都记录，形成信念演变链。AI 在 Weekly Insight 中呈现"你的 X 观点在过去 3 个月发生了 4 次转变" |
| 矛盾判断误报（用户只是举例引用 A） | AI 确认环节（"你现在想法变了吗？"）作为安全阀。用户否认则不标记转变，记录为"引用提及" |
| 大量 superseded 记忆堆积 | 超过 90 天的 superseded 记忆（非 reverted）归档到冷存储，不再参与语义搜索 |
| 用户主动删除记忆 | 软删除（deleted=true），不物理删除。如果删除的是 current 记忆，对应的 superseded 记忆恢复为 current |
| 跨类别矛盾（价值观 vs 偏好） | 仅在相同 category 内检测矛盾。不同类别的记忆可以共存 |

---

## 7. 性能与成本影响

| 操作 | 额外延迟 | 额外成本 | 频率 |
|------|---------|---------|------|
| 转变检测（写入时） | + 200-400ms（Gemini 2.5 Flash-Lite 调用） | ~$0.00003/次（或合并到摘要调用 = $0） | 每日 1 次/用户 |
| 回归识别（读取时） | 0ms（已有向量搜索，仅扩大 WHERE 范围） | $0 | 每次对话 |
| 情绪过滤 | 0ms（复用已有情感分析数据） | $0 | 每日 1 次/用户 |
| 渐变漂移检测 | 0ms（周维度 Cron Job 异步） | $0（纯 SQL 计算） | 每周 1 次/用户 |
| 存储增量 | - | ~0.1GB/月/1万 DAU（新字段 x 90 天 TTL） | - |

**结论**：记忆版本化的整体增量成本趋近于 0。矛盾检测可合并到已有 LLM 调用，额外延迟不影响对话响应时间（写入时异步执行），存储增量可忽略。

---

## 8. Category 差异化 TTL（P1 优化方向）

> **本节是 Category TTL 的唯一权威定义**。`cost-optimization.md` §3.2 仅引用此节并补充成本影响数据。

| Category | 建议保留期 | 原因 | 归档策略 |
|----------|-----------|------|---------|
| `values`（价值观） | 365 天 | 核心信念，极少突变 | 不归档，superseded 超 365 天移至冷存储 |
| `relationship`（关系） | 180 天 | 重要他人不会频繁变化 | current 保留，superseded 180 天归档 |
| `goal`（目标） | 90 天 | 目标会随阶段调整 | 标准 90 天 TTL |
| `preference`（偏好） | 30 天 | 偏好变化快，旧偏好价值低 | 30 天后归档，仅保留摘要 |
| `factual`（事实信息） | 365 天 | 职业不变、年龄递增等事实 | 不归档（事实信息不会过期） |
