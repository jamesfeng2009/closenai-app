# 成本优化规范

> 来源：ClosenAI 技术方案设计 v1.0 第 2 节 (AI 消息系统) + 第 4 节 (数据存储) + 第 9 节 (成本估算)
> 状态：P0 阶段执行标准

---

## 1. LLM 成本控制

### 1.1 Rule Engine + LLM 成本分层架构（70% / 30%）

核心原则：**绝大多数消息不需要 LLM，只有需要"理解"和"洞察"的消息才调 LLM**。

| 消息类型 | 生成方式 | 占比（预估） | 单条成本 | 对应功能 |
|----------|---------|-------------|---------|---------|
| 早安问候 / 日常寒暄 | Rule Engine + 模板 + 变量填充 | ~20% | $0 | P0-F01 主动消息（基础版） |
| 情绪签到回复 | Rule Engine + 情绪映射模板 | ~25% | $0 | P0-F03 情绪签到 |
| Daily Discovery | Rule Engine + 数据对比 + 模板 | ~15% | $0 | P0-F10 每日发现 |
| 提醒 / Streak 通知 | 固定模板 | ~10% | $0 | P0-F04 Streak |
| 用户对话回复 | LLM（Gemini 2.5 Flash，流式） | ~20% | ~$0.0005/条 | P0-F01 实时对话 |
| 性格画像生成 | LLM（Gemini 2.5 Flash，深度推理） | ~2% | ~$0.007/次 | P0-F05 性格画像 |
| 成长洞察 / Pattern Discovery | LLM（Gemini 2.5 Flash，分析推理） | ~5% | ~$0.004/条 | P0-F07 / P0-F10 |
| 主动消息（深度版） | LLM（Gemini 2.5 Flash-Lite，异步） | ~3% | ~$0.0001/条 | P0-F01 主动消息（引用洞察时） |

**成本对比**：原方案（全 LLM）1 万 DAU 月成本 ~$900。分层后 Rule Engine 处理 70% 消息（$0），LLM 仅处理 30%，GPT 方案月成本 ~$532；切换至 Gemini 2.5 Flash/Flash-Lite 后月成本降至 ~$75，**降幅 86%**。

### 1.2 分层决策原则

```
Rule Engine 适用场景：
  ✓ 有明确输入→输出映射（情绪→回复模板、数据对比→发现文案、天数→里程碑文案）
  ✓ 用户不在等待（主动消息、推送通知）
  ✓ 高频低价值消息（寒暄、提醒）

LLM 适用场景：
  ✓ 需要"理解"用户自由文本
  ✓ 需要"推理"跨时间模式
  ✓ 需要"生成"个性化深度内容
  ✓ 用户在等待实时回复（对话）
```

### 1.3 模型选择策略

| 场景 | 模型 | 定价 | 原因 |
|------|------|------|------|
| 主动消息（异步） | Gemini 2.5 Flash-Lite | $0.10/1M input, $0.40/1M output | 用户不在等，低成本批量生成，无实时压力 |
| 实时对话（流式） | Gemini 2.5 Flash | $0.30/1M input, $2.50/1M output | 平衡速度与成本，首 token ~2.5-3.2s 准实时（含预处理 ~170ms，详见 context-engineering.md §13） |
| 性格画像生成 | Gemini 2.5 Flash | $0.30/1M input, $2.50/1M output | 深度推理，频率低（~2%），单次成本可接受 |
| 成长洞察 / Pattern Discovery | Gemini 2.5 Flash | $0.30/1M input, $2.50/1M output | 跨时间模式分析需要更强能力 |
| 矛盾检测（记忆） | Gemini 2.5 Flash-Lite | $0.10/1M input, $0.40/1M output | 轻量级分类判断，~$0.00003/次 |
| 情绪签到解读（基础版） | Rule Engine | $0 | 情绪映射模板库（每种情绪 10+ 模板） |
| 情绪签到解读（深度版） | Gemini 2.5 Flash-Lite | $0.10/1M input, $0.40/1M output | 仅连续异常时触发（连续 3 天同一情绪） |

### 1.4 主动消息成本优化

主动消息是后端异步生成，用户不在等，可以用 Gemini 2.5 Flash-Lite 而非 Gemini 2.5 Flash。

```
假设 1 万 DAU，每人每天 1 条主动消息：
  每条 ~500 tokens input + 150 tokens output
  Gemini 2.5 Flash-Lite: $0.10/1M input + $0.40/1M output
  月成本 = 10000 × 30 × (500/1M × $0.10 + 150/1M × $0.40)
         = 10000 × 30 × ($0.00005 + $0.00006)
         = 10000 × 30 × $0.00011
         = ~$33/月

  vs Gemini 2.5 Flash: $0.30/1M input + $2.50/1M output
  月成本 = 10000 × 30 × (500/1M × $0.30 + 150/1M × $2.50)
         = ~$157.50/月

  节省：~$124.50/月（79% 降幅）

  开发阶段额外优势：Gemini 2.5 Flash 有免费额度（RPM 限制内），开发零成本。
```

### 1.5 LLM 失败的 Fallback 策略

```python
# 主动消息失败 → fallback 到 Rule Engine 模板
async def generate_active_message(user: User) -> str:
    try:
        return await llm_service.chat(
            model="gemini-2.5-flash-lite",
            messages=build_prompt(user),
            timeout=30,
        )
    except (LLMTimeoutError, LLMError):
        # Fallback: Rule Engine 模板消息（$0 成本）
        return rule_engine.generate_greeting(user)

# 矛盾检测合并到摘要调用 → 增量成本 = 0
async def generate_daily_summary(user: User) -> str:
    # 在摘要 prompt 中追加"检测是否与已有记忆矛盾"指令
    # 一次 LLM 调用同时完成：摘要生成 + 矛盾检测
    prompt = build_summary_prompt(user, include_conflict_detection=True)
    return await llm_service.chat(model="gemini-2.5-flash-lite", messages=prompt)
```

---

## 2. Embedding 降维策略

### 2.1 每日摘要 embedding（非每条消息）

不对每条聊天消息生成 embedding（~200GB/月），采用每日对话摘要 embedding 策略：

| 策略 | 存储量（1 万 DAU/月） | 说明 |
|------|---------------------|------|
| 每条消息 embedding | ~200GB | 不可行 |
| **每日摘要 embedding** | ~1.8GB | 每日 23:00 生成 50-100 字摘要 → embedding → 存入向量表 |

```
存储估算：
  1 万 DAU × 1 条摘要/天 × 6KB = 60MB/天 = ~1.8GB/月
  vs 每条消息方案 200GB/月，相差 100 倍
```

### 2.2 三件套优化（768 维 halfvec）

**问题**：Gemini Embedding 默认 768 维 float32 向量，10K DAU 90 天稳态需 ~12GB，必须优化。

**解决方案**：

#### ① 使用 Gemini Embedding 768 维（原生维度）

- Google Gemini Embedding 原生输出 768 维，无需降维参数
- 定价 $0.0075/1M tokens（vs OpenAI text-embedding-3-small $0.02/1M，降 62%）
- 聊天记忆场景：在单个用户 ~90 天摘要库（几千行）里做 Top-5 召回，768 维召回精度足够

```python
# 生成 768 维 embedding（Gemini Embedding 原生 768 维）
from google import genai

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
response = await client.aio.models.embed_content(
    model="gemini-embedding-001",
    contents=text,
)
embedding = response.embeddings[0].values  # 768 维向量
```

#### ② halfvec 替代 float32（再省 50%）

- pgvector 原生支持 `halfvec(768)` 类型，2 字节/维（vs float32 的 4 字节）
- fp16 对余弦相似度召回影响可忽略

```sql
-- 使用 halfvec(768) 类型
ALTER TABLE memories ALTER COLUMN embedding TYPE halfvec(768);

-- HNSW 索引
CREATE INDEX idx_memories_embedding ON memories
  USING hnsw ((embedding::halfvec(768)) halfvec_cosine_ops);
```

#### ③ 90 天 TTL + 分区 DROP（稳态控制）

- 按 `created_at` 月度分区，`DROP TABLE` 旧分区——秒级、无 VACUUM 膨胀
- **不要用 DELETE + VACUUM**：HNSW 索引大批量 DELETE 后需 REINDEX

```sql
-- 按月分区
CREATE TABLE memories (
    id UUID DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    content TEXT NOT NULL,
    embedding halfvec(768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- ... 其他字段
) PARTITION BY RANGE (created_at);

-- 创建当月分区
CREATE TABLE memories_2026_07 PARTITION OF memories
  FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');

-- 清理 90 天前的数据：直接 DROP 分区（秒级，无 VACUUM）
DROP TABLE IF EXISTS memories_2026_04;
```

### 2.3 8GB 预算分配（10K DAU，优化后）

| 项目 | 占用 |
|------|------|
| 系统/PG 内置 + 关系表索引 | ~0.5 GB |
| 其他 P0 数据（聊天/成长/签到，90 天） | ~4.1 GB（或下沉后 ~0.9 GB） |
| Embedding（768-dim halfvec, HNSW, 90 天） | ~4.4-5.0 GB |
| **总计** | **~9.0-9.6 GB（超 8GB，需启用聊天日志下沉降至 ~5.8 GB）** |

#### ④ 聊天日志下沉（可选，进一步省空间）

原始聊天消息存 Supabase Storage（Pro 含 100GB），PG 只存摘要 + 外键引用。"其他 P0 数据"从 ~4.1GB 降到 ~0.9GB，给 embedding 留更多余量。

---

## 3. 90 天 TTL 策略

### 3.1 统一 TTL 规则

| 数据类型 | TTL | 清理方式 | 说明 |
|----------|-----|---------|------|
| 对话消息（chat_messages） | 90 天 | 分区 DROP | 原始聊天记录，可下沉到 Storage |
| 每日摘要 embedding | 90 天 | 分区 DROP | pgvector 向量数据 |
| 情绪签到（mood_checkins） | 90 天 | 分区 DROP | 聚合后保留趋势，原始记录可清 |
| 成长洞察（growth_records） | 永久 | 不清理 | L4 成长数据永久保存 |
| 性格画像（personality） | 永久 | 不清理 | 缓慢演化，需保留历史版本 |
| 记忆（memories） | 按 category 差异化 | 分区 DROP + 冷存储 | 见下方 Category TTL |

### 3.2 Memory Category 差异化 TTL（P1 优化方向）

完整的 Category TTL 表和归档策略参见 `memory-layer.md` §8（**唯一权威来源**）。

**成本影响**：preference 类记忆从 90 天缩短到 30 天，节省 ~30% 记忆存储。

## 4. 存储优化

### 4.1 性格名片零图像生成成本

旧版灵魂伴侣画像依赖 AI 出图（$0.03/张），新版性格名片完全去除：

```javascript
// 性格名片用 RN View + captureRef 截屏实现，零图像生成成本
import ViewShot from 'react-native-view-shot';

const capturePersonalityCard = async () => {
  const uri = await ViewShot.captureRef(cardRef, {
    format: 'png',
    quality: 0.9,
  });
  // 直接保存到相册或分享，无 AI 图像生成调用
  return uri;
};
```

### 4.2 Redis 缓存策略

```python
# 情绪签到常见 emoji 组合缓存，命中时直接返回模板解读（$0 成本）
async def get_mood_interpretation(emoji: str, personality: str) -> str:
    cache_key = f"mood:{emoji}:{personality}"
    cached = await redis.get(cache_key)
    if cached:
        return cached  # 缓存命中，$0 成本

    # 缓存未命中，走 Rule Engine 模板（仍然 $0）
    result = rule_engine.generate_mood_reply(emoji, personality)
    await redis.setex(cache_key, 86400, result)  # 缓存 24 小时
    return result
```

### 4.3 Rule Engine 模板复用

随 DAU 增长，Rule Engine 消息占比进一步提升（模板可复用），边际成本趋近于零。

| 缓存层 | 缓存内容 | TTL | 命中率预估 |
|--------|---------|-----|-----------|
| Redis | 情绪签到 emoji 组合解读 | 24 小时 | ~80%（常见情绪组合） |
| Redis | 用户性格画像 | 1 小时 | ~95%（画像刷新频率低） |
| Redis | 验证码 | 5 分钟 | — |
| 内存 | Rule Engine 模板 | 进程生命周期 | 100% |

---

## 5. 成本汇总（1 万 DAU）

### 5.1 月度成本

| 项目 | 月成本 | 说明 |
|------|--------|------|
| AI 消息（Rule Engine 70% + LLM 30%） | -$75 | Gemini 2.5 Flash 对话/画像/洞察 + Flash-Lite 异步任务 |
| 基础设施（Supabase Pro + Vercel） | -$200 | Supabase Pro $25 + Vercel Pro $20 + 其他 |
| 推送（APNs/FCM + QStash） | -$50 | QStash Pro $20 + 推送基础设施 |
| **总成本** | **-$325** | 1 万 DAU 月运营成本 |

### 5.2 毛利率分析

| 项目 | 月金额 |
|------|--------|
| Pro 转化率 5% × $4.99/月 | +$2,495（500 Pro 用户） |
| 总成本 | -$325 |
| **月毛利** | **+$2,170** |
| **毛利率** | **87%** |

### 5.3 VPS 迁移后的成本对比

| DAU | 全 VPS (Hetzner) | Serverless (现有) | 节省 |
|-----|-----------------|------------------|------|
| 1,000 | ~$14/月 | ~$65/月 | 79% |
| 3,000 | ~$21/月 | ~$75/月 | 72% |
| 5,000 | ~$27/月 | ~$90/月 | 70% |
| 10,000 | ~$46/月 | ~$116/月 | 60% |

**关键结论**：Rule Engine 处理 70% 消息（$0 成本）+ Gemini 低成本定价是盈利的关键。开发阶段 Gemini 2.5 Flash 免费额度进一步降低试错成本。Pro 转化率 5% 是保守估计（Replika 约 5-8%），如果达到 8% 则月毛利可达 $3,670。
