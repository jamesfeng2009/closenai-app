# 上下文工程规范

> 来源：ClosenAI PRD v2.0 第 4.5 节 + 技术方案设计 v1.0 第 3 节 (SSE 流式对话) + 第 4 节 (记忆层)
> 状态：P0 阶段执行标准（§2-§5 为 P0 必须实现，§6-§7 为 P1 优化方向）
> 关联 spec：`memory-layer.md`（长期记忆唯一权威）、`architecture.md` §3（SSE 流式对话）、`code-architecture.md` §4（Service 职责）、`api-conventions.md` §2（对话接口）、`cost-optimization.md` §1（LLM 成本分层）

---

## 1. 上下文工程总览

### 1.1 问题背景

多轮对话中，LLM 需要历史上下文来理解用户意图（如指代消解："那上海呢？"→ 需要知道之前在讨论什么）。但庞大的上下文也会干扰判断：不相关的历史消息可能让模型误以为用户仍在讨论之前的话题。

ClosenAI 的上下文工程分为**两个独立层面**：

```
┌─────────────────────────────────────────────────────┐
│                  LLM Prompt (Gemini 2.5 Flash)       │
│                                                      │
│  ┌──────────────────┐  ┌──────────────────────────┐ │
│  │  短期对话上下文    │  │  长期记忆上下文            │ │
│  │  (本文档定义)      │  │  (memory-layer.md 定义)   │ │
│  │                   │  │                           │ │
│  │  • 滑动窗口历史    │  │  • Temporal Memory        │ │
│  │  • 话题追踪        │  │  • 信念演变追踪            │ │
│  │  • 指代消解        │  │  • pgvector 语义搜索       │ │
│  │  • 意图驱动选择    │  │  • 每日摘要 + 洞察         │ │
│  └──────────────────┘  └──────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

### 1.2 两层职责划分

| 层面 | 时间跨度 | 数据来源 | 核心问题 | 权威 spec |
|------|---------|---------|---------|-----------|
| 短期对话上下文 | 当前会话（分钟~小时级） | `chat_messages` 表原始消息 | 指代消解、话题连续性、上下文干扰 | **本文档** |
| 长期记忆上下文 | 跨会话（天~月级） | `memories` 表 + `daily_summaries` 表 | 信念演变、记忆检索、记忆版本化 | `memory-layer.md` |

### 1.3 四维度框架

| 维度 | 短期对话上下文（本文档） | 长期记忆上下文（memory-layer.md） |
|------|------------------------|--------------------------------|
| **写 Write** | 原始消息存储 + 话题标记 + 意图标记 | 每日摘要 + 信念提取 + embedding |
| **选 Select** | 滑动窗口 + 话题相关性过滤 | pgvector Top-5 + 状态过滤 |
| **压 Compress** | 原始消息→摘要（跨会话时） | 每日摘要 + 7 天洞察摘要 |
| **隔 Isolate** | 话题隔离 + 意图隔离 | category 隔离 + 状态隔离 + 情绪过滤 |

---

## 2. 写入层 Write

### 2.1 原始消息存储（已有）

每条对话消息存入 `chat_messages` 表，包含 `user_id`、`content`、`role`（user/assistant）、`created_at`。此部分已由 `ChatService.save_user_message` 实现，参见 `architecture.md` §3.1。

### 2.2 话题标记（P0 新增）

每轮对话后，由 Flash-Lite 标记当前话题。可合并到每日摘要调用中（增量成本 ≈ $0），或在实时对话流结束时异步标记。

```sql
-- chat_messages 表扩展字段
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS (
  topic_id     VARCHAR(50),              -- 话题标识（如 work_stress, relationship）
  topic_confidence FLOAT DEFAULT 0.5,   -- 话题分类置信度
  intent_type  VARCHAR(30)               -- 意图类型（reflect, ask_advice, vent, check_in）
);
```

**话题标记 prompt**（合并到摘要调用，增量成本 = $0）：

```python
# backend/services/summary_service.py

TOPIC_EXTRACTION_PROMPT = """
基于以下对话内容，提取：
1. topic_id: 话题标识（英文 snake_case，如 work_stress, relationship, family, health, self_growth）
2. intent_type: 用户意图（reflect | ask_advice | vent | check_in）
3. topic_confidence: 置信度 0.0-1.0

对话内容：
{conversation_text}

输出 JSON：{"topic_id": "...", "intent_type": "...", "topic_confidence": 0.8}
"""
```

### 2.3 意图标记（P1 优化方向）

意图类型用于驱动选择层（§3.3），决定注入哪些记忆和洞察：

| intent_type | 说明 | 记忆注入策略 |
|-------------|------|------------|
| `reflect` | 反思自身状态 | 注入同 category 的 current 信念 |
| `ask_advice` | 寻求建议 | 注入相关 current + superseded 信念 |
| `vent` | 情绪宣泄 | 注入情绪相关记忆，降低信念权重 |
| `check_in` | 日常签到 | 不注入额外记忆，使用 Rule Engine |

---

## 3. 选择层 Select

### 3.1 滑动窗口：对话历史注入（P0 必须实现）

**这是当前最关键的缺口**。现有 `stream_chat` 接口只传 `system_prompt` 和当前 `user_msg`，未注入对话历史，导致 LLM 无法解析任何指代。

**修复方案**：在 `stream_chat` 调用中加入 `history` 参数，传入最近 N 轮对话消息。

```python
# backend/routers/chat.py（修改 architecture.md §3.1 的 stream_chat_message）

@router.post("/messages/stream")
async def stream_chat_message(
    request: ChatMessageRequest,
    user: dict = Depends(get_current_user),
) -> StreamingResponse:
    async def event_stream() -> AsyncGenerator[str, None]:
        # 1. 存储用户消息
        await chat_service.save_user_message(user["sub"], request.content)

        # 2. 记忆注入（延迟 < 500ms）—— 长期记忆层面，不变
        memories = await memory_service.retrieve_relevant(
            user_id=user["sub"],
            query=request.content,
            limit=5,
        )
        insights = await memory_service.get_recent_insights(user["sub"], days=7)

        # 3. 【新增】对话历史注入（短期上下文层面）
        history = await chat_service.get_recent_history(
            user_id=user["sub"],
            chat_id=request.chat_id,
            limit=10,  # 最近 10 条消息（约 5 轮对话）
        )

        # 4. 构建 LLM 上下文（system + history + user_msg）
        system_prompt = prompt_builder.build_chat_prompt(
            personality=await personality_service.get(user["sub"]),
            memories=memories,
            insights=insights,
        )

        # 5. LLM 流式生成（传入 history）
        async for chunk in llm_service.stream_chat(
            model="gemini-2.5-flash",
            system=system_prompt,
            history=history,          # ← 新增
            user_msg=request.content,
            max_tokens=500,
        ):
            yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream", ...)
```

**滑动窗口大小 N 的选择**：

| N（消息条数） | 约轮次数 | 估算 token 量 | 增量成本/轮 | 适用场景 |
|--------------|---------|-------------|------------|---------|
| 6 | 3 轮 | ~1.2K tokens | ~$0.0002 | 最低限度，覆盖基本指代 |
| **10** | **5 轮** | **~2K tokens** | **~$0.0003** | **推荐值**，平衡成本与上下文质量 |
| 20 | 10 轮 | ~4K tokens | ~$0.0006 | 深度反思场景 |

**推荐 N=10**：Gemini 2.5 Flash 有 1M 上下文窗口，2K tokens 的历史完全无压力，增量成本可忽略。

### 3.2 对话历史查询

```python
# backend/services/chat_service.py

async def get_recent_history(
    self,
    user_id: str,
    chat_id: str,
    limit: int = 10,
) -> list[dict]:
    """
    获取最近 N 条对话消息，按时间正序排列。
    返回格式：[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}, ...]
    """
    messages = await self.chat_repo.get_recent_messages(
        user_id=user_id,
        chat_id=chat_id,
        limit=limit,
    )
    # 按时间正序返回（oldest → newest），供 LLM 理解对话流
    messages.sort(key=lambda m: m.created_at)
    return [
        {"role": m.role, "content": m.content}
        for m in messages
    ]
```

### 3.3 话题相关性过滤（P1 优化方向）

当对话历史超过滑动窗口时，优先保留与当前话题相关的历史消息：

```python
# backend/services/chat_service.py（P1）

async def get_topic_aware_history(
    self,
    user_id: str,
    chat_id: str,
    current_topic: str,
    window_size: int = 10,
) -> list[dict]:
    """
    话题感知的历史检索：
    1. 优先返回与当前话题相同的历史消息（topic_id 匹配）
    2. 不足 window_size 时，用最近的其他话题消息补齐
    """
    # 同话题消息
    same_topic = await self.chat_repo.get_messages_by_topic(
        user_id, chat_id, topic_id=current_topic, limit=window_size
    )
    if len(same_topic) >= window_size:
        return same_topic[:window_size]

    # 补齐：最近的其他话题消息
    recent = await self.chat_repo.get_recent_messages(
        user_id, chat_id, limit=window_size * 2
    )
    filler = [m for m in recent if m.topic_id != current_topic]
    return (same_topic + filler)[:window_size]
```

### 3.4 与长期记忆选择的关系

长期记忆的选择（pgvector 语义搜索）已在 `memory-layer.md` §2.2 定义，不重复。两者的协作：

```
用户消息 "那感情方面呢？"
    │
    ├── 短期选择：滑动窗口取最近 10 条 → LLM 看到 "工作压力" → "同事压力" → 解析"那"指代话题切换
    │
    └── 长期选择：pgvector 搜索 "感情" 相关记忆 → 注入 [当前信念] "重视亲密关系"
```

---

## 4. 压缩层 Compress

### 4.1 会话内：不压缩（P0）

单次会话内，对话历史以原始消息形式注入 LLM。Gemini 2.5 Flash 的 1M 上下文窗口足以容纳数十轮对话，无需在会话内压缩。

### 4.2 跨会话：每日摘要（已有）

跨会话时，原始对话消息通过每日摘要压缩为高密度上下文。此部分已由 `summary_service` 实现，参见 `cost-optimization.md` §2.2 和 `memory-layer.md` §2.1。

```
原始对话消息（~5K tokens/天）
    │
    ▼ 每日摘要（Flash-Lite，~$0.0001/次）
每日摘要（~500 tokens/天）
    │
    ├── → 信念提取 → memories 表（embedding）
    └── → insights 表（7 天洞察摘要）
```

### 4.3 滑动窗口溢出处理（P1 优化方向）

当单次会话超过 N=10 轮对话时，较早的消息需要压缩：

```python
# backend/services/chat_service.py（P1）

async def get_compressed_history(
    self,
    user_id: str,
    chat_id: str,
    window_size: int = 10,
) -> list[dict]:
    """
    滑动窗口溢出时的压缩策略：
    - 最近 N 条：原始消息（完整保留）
    - N 之前的消息：如果同一会话内，取会话内摘要（如有）
    - 跨会话消息：依赖每日摘要（memory-layer.md 已处理）
    """
    recent = await self.get_recent_history(user_id, chat_id, limit=window_size)
    return recent  # P0 阶段：仅返回最近 N 条，溢出部分丢弃
    # P1 阶段：溢出部分生成会话内摘要并注入
```

### 4.4 注入上下文的 token 预算

| 上下文组成部分 | 估算 token 量 | 来源 |
|--------------|-------------|------|
| System prompt（性格+指令） | ~300 tokens | PromptBuilder |
| 长期记忆（Top-5） | ~500 tokens | MemoryService |
| 7 天洞察摘要 | ~300 tokens | MemoryService |
| 对话历史（N=10） | ~2,000 tokens | ChatService（新增） |
| 当前用户消息 | ~100 tokens | 请求体 |
| **总计** | **~3,200 tokens** | 远低于 1M 窗口 |

---

## 5. 隔离层 Isolate

### 5.1 已有的隔离机制（长期记忆层面）

以下隔离机制已在 `memory-layer.md` 中定义，不重复：

- **category 隔离**：仅在相同 category 内检测矛盾（§1.3）
- **状态隔离**：`reverted` 状态的记忆不参与语义搜索（§1.2）
- **情绪过滤**：情感强度 > 0.8 时不标记信念转变（§2.3）

### 5.2 话题隔离（P1 优化方向）

当用户切换话题时，通过 prompt 标注帮助 LLM 理解上下文边界：

```python
# backend/services/prompt_builder.py（P1）

def build_chat_prompt_with_topics(
    personality: dict,
    memories: list,
    insights: list,
    history: list[dict],       # 带话题标记的历史消息
) -> str:
    """
    在历史消息中标注话题切换点，帮助 LLM 区分不同话题的上下文。
    """
    history_text = []
    prev_topic = None
    for msg in history:
        if msg.get("topic_id") and msg["topic_id"] != prev_topic:
            history_text.append(f"[话题切换：{msg['topic_id']}]")
            prev_topic = msg["topic_id"]
        history_text.append(f"{msg['role']}: {msg['content']}")

    return f"""
你是一个帮助用户追踪个人成长的 AI 助手。

用户性格画像：{personality['type']}

相关记忆：
{format_memories(memories)}

最近洞察：
{format_insights(insights)}

对话历史：
{chr(10).join(history_text)}
"""
```

**话题切换标注效果**：

```
[话题切换：work_stress]
user: 最近工作压力好大
assistant: 看到你选了焦虑。作为习惯用逻辑分析的人...

[话题切换：colleague_stress]
user: 我同事小李也压力很大
assistant: 你提到了小李...

[话题切换：relationship]
user: 那感情方面呢？    ← LLM 看到 [话题切换：relationship]，明确知道用户切换了话题
```

### 5.3 意图隔离（P2 优化方向）

不同意图注入不同的上下文内容，避免无关上下文干扰：

| intent_type | 注入的记忆范围 | 注入的洞察范围 | 对话历史 |
|-------------|-------------|-------------|---------|
| `reflect` | 同 category 的 current 信念 | 最近 7 天 | 全部 |
| `ask_advice` | current + superseded 信念 | 最近 30 天 | 全部 |
| `vent` | 情绪相关记忆 | 今日情绪 | 最近 3 轮 |
| `check_in` | 不注入 | 不注入 | 不注入（Rule Engine 处理） |

---

## 6. Agent 转交

### 6.1 运行时多 Agent 架构

ClosenAI 的 Agent 架构采用**单一对话 Agent + 异步辅助 Agent**模式，不存在复杂的 Agent 链式转交：

```
用户消息
    │
    ├── 【实时路径】预处理 (Flash-Lite + SQL) → 对话 Agent (Flash)
    │       │
    │       ├── 准实时预处理（~170ms，详见 §13）
    │       └── 流式回复用户（首 token ~2.5-3.2s）
    │
    └── 【异步路径】对话结束后触发
            ├── 摘要 Agent (Flash-Lite) → 每日摘要 + 信念提取
            ├── 矛盾检测 Agent (Flash-Lite) → 转变检测
            └── 漂移检测 Agent (纯 SQL) → 每周漂移分数
```

### 6.2 Agent 间上下文传递

| Agent 间转交 | 传递的上下文 | 传递方式 | 现有状态 |
|-------------|------------|---------|---------|
| 对话 Agent → 摘要 Agent | 当日全部对话消息 | DB 读取（`chat_messages` 表） | ✓ 已有 |
| 摘要 Agent → 矛盾检测 Agent | 摘要 + Top-3 候选记忆 | 内存传递（同一异步任务） | ✓ 已有 |
| 摘要 Agent → 漂移检测 Agent | 每周摘要 embedding | DB 读取（`daily_summaries` 表） | ✓ 已有 |
| 对话 Agent → 指代消解 Agent | 当前消息 + 最近 N 轮历史 | 内存传递（前置步骤） | **P1 新增** |

### 6.3 指代消解 Agent（P1 新增）

指代消解不需要独立的 Agent 实体，作为对话 Agent 的前置步骤实现：

```python
# backend/services/chat_service.py（P1）

async def resolve_references(
    self,
    user_msg: str,
    history: list[dict],
) -> str:
    """
    指代消解：将"那上海呢？"扩展为"我想聊聊上海的情况"。
    使用 Flash-Lite，延迟 ~100ms，成本 ~$0.0001/次。
    如果消解失败或无指代，返回原始消息。
    """
    if not history or len(history) < 2:
        return user_msg  # 无历史，无需消解

    resolved = await self.llm_service.chat(
        model="gemini-2.5-flash-lite",
        messages=[{
            "role": "user",
            "content": f"""
根据对话历史，将用户最新消息中的指代词替换为具体内容。
如果最新消息没有指代词，原样返回。

对话历史：
{format_history(history)}

最新消息：{user_msg}

输出（仅输出消解后的消息，不要解释）：
"""
        }],
        max_tokens=100,
    )
    return resolved.strip() or user_msg
```

**集成到对话流**：

```python
# 在 stream_chat_message 的 event_stream 中（P1）
# 3.5 指代消解（Flash-Lite，~100ms）
resolved_msg = await chat_service.resolve_references(
    user_msg=request.content,
    history=history,
)
# 使用 resolved_msg 进行记忆检索和 LLM 生成
```

### 6.4 不采用复杂 Agent 链的原因

ClosenAI 是反思追踪工具，不是通用 Agent 平台。复杂的多 Agent 链（如 Planner → Executor → Reviewer）会引入不必要的延迟和成本。单对话 Agent + 异步辅助 Agent 的模式已满足需求：

- 实时路径只有 1 个 LLM 调用（Flash 流式），延迟可控
- 异步路径的 Agent 间通过 DB 传递上下文，无实时耦合
- 指代消解作为前置步骤，不增加 Agent 实体复杂度

---

## 7. 模型改写

### 7.1 改写场景总览

| 改写场景 | 模型 | 触发时机 | 输入 → 输出 | 现有状态 |
|---------|------|---------|------------|---------|
| 原始对话 → 每日摘要 | Flash-Lite | 每日凌晨（异步） | ~5K tokens → ~500 tokens | ✓ 已有 |
| 摘要 → 信念提取 | Flash-Lite | 摘要后（异步） | 摘要 → content/category/source/conviction | ✓ 已有 |
| 摘要 → embedding | text-embedding-004 | 摘要后（异步） | 摘要文本 → 768 维向量 | ✓ 已有 |
| 矛盾判断分类 | Flash-Lite | 摘要后（异步） | 摘要 + Top-3 → conflict/no-conflict | ✓ 已有 |
| 话题标记 | Flash-Lite | 摘要时合并（异步） | 对话 → topic_id/intent_type | §2.2 新增 |
| **指代消解** | **Flash-Lite** | **每轮对话前（实时）** | **消息+历史 → 消解后消息** | **§6.3 新增** |

### 7.2 改写成本控制

所有改写场景均使用 Flash-Lite（$0.10/$0.40 per 1M tokens），单次成本 < $0.0001。指代消解为实时路径中唯一的额外 LLM 调用，延迟 ~100ms，可接受。

**成本汇总**（1 万 DAU）：

| 改写场景 | 频率 | 单次成本 | 日成本 | 月成本 |
|---------|------|---------|-------|-------|
| 每日摘要 | 1 次/用户/天 | $0.0001 | $1.0 | $30 |
| 信念提取 | 合并到摘要 | $0 | $0 | $0 |
| 矛盾检测 | 1 次/用户/天 | $0.00003 | $0.3 | $9 |
| 话题标记 | 合并到摘要 | $0 | $0 | $0 |
| 指代消解（P1） | ~3 次/用户/天 | $0.0001 | $3.0 | $90 |
| **总计** | | | **$4.3** | **$129** |

指代消解是增量成本最大的项（$90/月），但可通过以下方式优化：
- 仅在检测到指代词时触发（如"那""它""这个""前面提到的"）
- 2 轮以内对话不触发（无足够历史上下文）

---

## 8. 数据模型扩展

### 8.1 chat_messages 表扩展（P0）

```sql
-- 短期对话上下文扩展字段
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS (
  topic_id          VARCHAR(50),              -- 话题标识
  topic_confidence  FLOAT DEFAULT 0.5,       -- 话题分类置信度
  intent_type       VARCHAR(30)               -- 意图类型
);

-- 索引：按会话+时间查询对话历史
CREATE INDEX IF NOT EXISTS idx_chat_messages_chat_time
  ON chat_messages(user_id, chat_id, created_at DESC);

-- 索引：按话题查询（P1 话题相关性过滤用）
CREATE INDEX IF NOT EXISTS idx_chat_messages_topic
  ON chat_messages(user_id, chat_id, topic_id)
  WHERE topic_id IS NOT NULL;
```

### 8.2 不新增独立表

短期对话上下文管理不需要独立的表：
- 对话历史 → 复用 `chat_messages` 表（已有）
- 话题标记 → 扩展 `chat_messages` 表字段（§8.1）
- 会话内摘要（P1）→ 复用 `daily_summaries` 表或新增 `session_summaries`（P1 再定）

---

## 9. API 接口变更

### 9.1 请求体扩展（P0）

`api-conventions.md` §2.1 的请求体不变，对话历史由后端自动从 DB 查询，前端无需传递：

```json
{
  "content": "那感情方面呢？",
  "chat_id": "uuid-xxx",
  "context": {
    "mood_emoji": "😰",
    "is_mood_checkin": false
  }
}
```

后端在处理请求时自动查询该 `chat_id` 下最近 N 条消息作为对话历史。

### 9.2 SSE 响应不变

SSE 流式响应格式不变，参见 `api-conventions.md` §2.1。

---

## 10. 性能与成本影响

### 10.1 延迟影响

| 操作 | 额外延迟 | 频率 | 影响 |
|------|---------|------|------|
| 对话历史查询（DB） | ~20-50ms | 每次对话 | 可忽略（与记忆注入并行） |
| 对话历史注入 LLM | 0ms（仅增加 input token） | 每次对话 | 首 token 延迟不变 |
| 指代消解（P1） | ~100ms | 每次对话 | 首 token 延迟 +100ms（可接受） |
| 话题标记 | 0ms（合并到摘要调用） | 每日 1 次 | 无影响 |

**P0 阶段**：对话历史查询 + 注入的额外延迟 < 50ms，不影响 < 3 秒首 token 的要求。

### 10.2 成本影响

| 操作 | 增量成本/轮 | 频率 | 月成本（1 万 DAU） |
|------|-----------|------|-------------------|
| 对话历史注入（多 ~2K input tokens） | ~$0.0003 | ~3 轮/用户/天 | $90 |
| 话题标记 | $0（合并到摘要） | 1 次/用户/天 | $0 |
| 指代消解（P1） | ~$0.0001 | ~3 次/用户/天 | $90 |
| **P0 总计** | | | **$90/月** |
| **P1 总计** | | | **$180/月** |

P0 增量成本 $90/月，相对于现有 LLM 成本 ~$75/月，增幅 120%。但这是多轮对话正常工作的必要成本——没有对话历史，LLM 无法理解任何指代，对话质量会严重下降。

### 10.3 成本优化策略

| 策略 | 节省 | 代价 | 优先级 |
|------|------|------|--------|
| 仅在检测到指代词时触发指代消解 | -$54/月（60%） | 少量指代漏检 | P1 |
| Rule Engine 消息不注入对话历史 | -$18/月（20%） | Rule Engine 回复可能不够连贯 | P2 |
| 会话内摘要替代原始历史（P1 溢出时） | -$10/月 | 摘要丢失细节 | P1 |

---

## 11. 优先级与实施计划

### 11.1 P0（必须实现）

| 任务 | 工作量 | 关联任务 | 验收标准 |
|------|--------|---------|---------|
| `chat_messages` 表扩展 `topic_id` 字段 | 小 | - | DDL 执行成功 |
| `ChatService.get_recent_history()` 实现 | 小 | P0-F01 | 返回最近 N 条消息，按时间正序 |
| `stream_chat` 注入 `history` 参数 | 小 | P0-F01 | LLM 能解析 3 轮内的指代 |
| `LLMService.stream_chat()` 支持 `history` 参数 | 小 | P0-F01 | history 正确传入 Gemini API |
| 对话历史查询索引 | 小 | - | 查询延迟 < 50ms |

### 11.2 P1（优化方向）

| 任务 | 工作量 | 验收标准 |
|------|--------|---------|
| 话题标记（合并到摘要调用） | 中 | 话题分类准确率 > 80% |
| 话题切换标注（prompt 内） | 小 | LLM 能识别话题边界 |
| 指代消解 Agent | 中 | 指代消解准确率 > 70% |
| 话题相关性过滤 | 中 | 同话题历史优先返回 |
| 滑动窗口溢出压缩 | 中 | 超过 N 轮时生成会话内摘要 |

### 11.3 P2（远期优化）

| 任务 | 工作量 | 验收标准 |
|------|--------|---------|
| 意图分类体系 | 大 | 4 种意图分类准确率 > 75% |
| 意图驱动选择 | 大 | 不同意图注入不同上下文 |
| 意图隔离 | 大 | vent 意图不注入无关信念 |


---

## 13. 实时检测方案（准实时串行架构）

> 本节定义对话进行时的实时矛盾检测、漂移检测和指代消解方案。
> 设计原则：ClosenAI 是反思追踪工具，用户预期是"准实时"（2-5 秒可接受），不需要 IM 级秒回。
> 因此采用串行架构而非激进并行，换取更简单的实现和更精准的检测结果。

### 13.1 延迟预算重新定义

ClosenAI 的产品定位决定了用户对延迟的预期与 IM 不同：

| 产品类型 | 用户预期延迟 | ClosenAI 适用 |
|---------|------------|-------------|
| 即时通讯（微信/QQ） | < 500ms | 否 |
| AI 聊天（ChatGPT） | 1-3 秒 | 参考 |
| **反思追踪（ClosenAI）** | **2-5 秒完全可接受** | **是** |

用户在 Reflect Tab 写深度反思内容时，AI "思考" 1-2 秒再回复反而更自然。因此首 token 延迟预算从 3 秒放宽到 **5 秒**，预处理总预算放宽到 **500ms**（含降级超时）。

### 13.2 准实时串行流水线

采用串行三步预处理，每步的输出供下一步使用，检测更精准：

```
用户消息
    │
    ├── Step 1: Embedding API（~20ms）
    │     用户消息 → 768 维向量（gemini-embedding-001）
    │
    ├── Step 2: SQL 漂移快查（~25ms）
    │     用 Step 1 的向量查询：
    │     • 语义距离：当前消息 vs current 信念
    │     • 漂移趋势：最近摘要 vs 信念距离变化
    │     • 回归信号：superseded 信念 similarity > 0.80
    │
    ├── Step 3: Flash-Lite 预处理 Agent（~120ms，超时 500ms）
    │     输入：用户消息 + 对话历史 + current 信念 + Step 2 漂移信号
    │     一次调用三件事：
    │     • 指代消解："那上海呢" → "上海限号"
    │     • 矛盾预检：用户消息 vs current 信念
    │     • 情绪评估：intensity 0.0-1.0
    │     （拿到漂移信号后，可做更精准的 severity 判断）
    │
    ├── PromptBuilder 增强（~5ms）
    │     追加【矛盾感知】【漂移信号】【情绪状态】到 system prompt
    │
    └── Flash 流式生成（首 token ~2-3s）
         总预处理 ~170ms，首 token ~2.5-3.2s
```

**串行优于并行的原因**：Step 3 的 Flash-Lite 可以拿到 Step 2 的漂移信号作为输入。例如 SQL 查到"漂移分数 0.18"，Flash-Lite 就知道这不是突发矛盾而是渐变积累，severity 标低一些。并行架构做不到这种信息传递。

### 13.3 Step 1：消息 Embedding

```python
# backend/services/chat_service.py

async def get_message_embedding(self, text: str) -> list[float]:
    """
    生成用户消息的 768 维 embedding。
    用于 SQL 漂移快查和增强记忆检索精度。
    延迟 ~20ms，成本 ~$0.000007/次。
    """
    response = await self.llm_service.embed(
        model="gemini-embedding-001",
        contents=text,
    )
    return response.embeddings[0].values  # 768 维向量
```

### 13.4 Step 2：实时漂移快查（纯 SQL）

异步漂移检测（memory-layer.md §3.2）是每周 Cron Job 计算 4 周漂移分数。实时版本是一个**轻量快速信号**，用单条 SQL 查询三种信号：

```sql
-- 输入：$user_id, $msg_embedding（Step 1 产出的消息向量）
-- 延迟 ~25ms，成本 $0（纯 SQL）

SELECT
  -- 信号 1：当前消息 vs 每条 current 信念的语义距离
  m.id,
  m.content AS belief_content,
  m.conviction,
  1 - (m.embedding <=> $msg_embedding) AS current_similarity,

  -- 信号 2：最近 7 天摘要 vs 该信念的距离变化趋势
  (SELECT 1 - (ds_latest.embedding <=> m.embedding)
   FROM daily_summaries ds_latest
   WHERE ds_latest.user_id = m.user_id
   ORDER BY ds_latest.created_at DESC LIMIT 1
  ) AS latest_summary_similarity,

  -- 信号 3：回归检测——是否有 superseded 信念与新消息高度相似
  (SELECT ms.content
   FROM memories ms
   WHERE ms.user_id = m.user_id
     AND ms.status = 'superseded'
     AND 1 - (ms.embedding <=> $msg_embedding) > 0.80
   LIMIT 1
  ) AS regression_belief

FROM memories m
WHERE m.user_id = $user_id
  AND m.status = 'current'
  AND m.source = 'self'
  AND m.deleted = FALSE
ORDER BY m.embedding <=> $msg_embedding
LIMIT 5;
```

**三种信号的含义与阈值**：

| 信号 | 含义 | 触发阈值 | 传给 Flash-Lite 的作用 |
|------|------|---------|---------------------|
| current_similarity | 当前消息与 current 信念的相关度 | > 0.75 时高度相关 | 高相关 + 矛盾 → severity=high |
| latest_summary_similarity | 最近摘要与信念的距离 | 比历史均值低 0.15+ | 漂移趋势 → severity 降级为 medium |
| regression_belief | superseded 信念与新消息高度相似 | similarity > 0.80 | 回归信号 → 追加 [回归信念] hint |

### 13.5 Step 3：Flash-Lite 预处理 Agent（三合一）

将指代消解、矛盾预检、情绪评估合并到**同一个 Flash-Lite 调用**，同时输入 Step 2 的漂移信号：

```python
# backend/services/chat_service.py

PREPROCESS_PROMPT = """
你是 ClosenAI 的上下文预处理模块。请基于对话历史、已有信念和漂移信号，
对用户最新消息做三项分析，输出一个 JSON。

分析任务：
1. 指代消解：将消息中的指代词（那、它、这个、前面提到的）替换为具体内容。
   如果没有指代词，原样返回。
2. 矛盾预检：判断用户消息是否与"已有信念"中的某条 [当前信念] 矛盾。
   注意区分：self 表达 vs others 引用 vs hypothetical 假设。
   只标记 self 来源的矛盾。
   如果漂移信号显示渐变趋势，severity 降为 medium。
3. 情绪评估：评估用户当前消息的情感强度 0.0-1.0。
   >0.8 视为情绪化表达。

对话历史：
{history_formatted}

已有信念：
{memories_formatted}

漂移信号：
{drift_signals_formatted}

用户最新消息：{user_msg}

输出 JSON：
{
  "resolved_msg": "消解后的消息（无指代则原样）",
  "contradiction": {
    "detected": true/false,
    "contradicted_belief": "被矛盾的信念内容或null",
    "reason": "矛盾原因或null",
    "severity": "high/medium/low/null"
  },
  "emotion": {
    "intensity": 0.0-1.0,
    "is_emotional": true/false
  }
}
"""

async def preprocess_message(
    self,
    user_msg: str,
    history: list[dict],
    memories: list[dict],
    drift_signals: dict,
) -> dict:
    """
    Flash-Lite 预处理：指代消解 + 矛盾预检 + 情绪评估。
    一次调用三件事，延迟 ~120ms，成本 ~$0.00003/次。
    超时 500ms 降级为返回原始消息（不阻断主流程）。
    """
    try:
        result = await self.llm_service.chat(
            model="gemini-2.5-flash-lite",
            messages=[{
                "role": "user",
                "content": PREPROCESS_PROMPT.format(
                    history_formatted=format_history(history),
                    memories_formatted=format_memories(memories),
                    drift_signals_formatted=format_drift(drift_signals),
                    user_msg=user_msg,
                ),
            }],
            max_tokens=150,
            timeout=0.5,  # 500ms 超时
        )
        return json.loads(result)
    except (LLMTimeoutError, LLMError, json.JSONDecodeError):
        # 降级：返回原始消息，无矛盾/情绪信号
        return {
            "resolved_msg": user_msg,
            "contradiction": {"detected": False, "contradicted_belief": None,
                              "reason": None, "severity": None},
            "emotion": {"intensity": 0.5, "is_emotional": False},
        }
```

### 13.6 PromptBuilder 增强

将预处理结果追加到 system prompt 的【实时上下文增强】区域：

```python
# backend/services/prompt_builder.py

def build_chat_prompt_enhanced(
    personality: dict,
    memories: list,
    insights: list,
    history: list[dict],
    preprocessing: dict,    # Step 3 输出
    drift_signals: dict,    # Step 2 输出
) -> str:
    """增强版 system prompt，包含实时检测信号。"""

    # 基础区域
    prompt = f"""
你是用户的 AI 朋友，帮助追踪个人成长。
用户性格画像：{personality['type']}
相关记忆：{format_memories(memories)}
最近洞察：{format_insights(insights)}
对话历史：{format_history(history)}
"""

    # 实时上下文增强区域
    enhancement = "\n【实时上下文增强】\n"

    # 矛盾感知
    if preprocessing["contradiction"]["detected"]:
        severity = preprocessing["contradiction"]["severity"]
        enhancement += f"⚠️ 矛盾感知（{severity}）：\n"
        enhancement += f"  用户当前表达可能与已有信念矛盾——\n"
        enhancement += f"  被矛盾的信念：\"{preprocessing['contradiction']['contradicted_belief']}\"\n"
        enhancement += f"  矛盾原因：{preprocessing['contradiction']['reason']}\n"
        if preprocessing["emotion"]["is_emotional"]:
            enhancement += "  用户当前处于情绪化状态，请轻轻带过，不追问转变原因。\n"
        else:
            enhancement += "  请在回复中自然地提及这个矛盾，好奇地询问转变原因。\n"
        enhancement += "  注意：不要下结论说用户\"变了\"——可能只是探讨。\n\n"

    # 漂移信号
    if drift_signals.get("drift_detected"):
        enhancement += f"📊 漂移信号：\n"
        enhancement += f"  该信念最近 7 天的语义距离呈增大趋势（漂移分数 {drift_signals['drift_score']:.2f}）。\n"
        enhancement += f"  用户可能在缓慢调整立场。可以温和地提及。\n\n"

    # 回归信号
    if drift_signals.get("regression_belief"):
        enhancement += f"🔄 回归信号：\n"
        enhancement += f"  用户似乎回到了之前的观点：\"{drift_signals['regression_belief']}\"\n"
        enhancement += f"  可以在回复中自然体现回归认知。\n\n"

    prompt += enhancement
    return prompt
```

### 13.7 实时检测 → 异步 Agent 衔接

实时检测只做"感知和提示"，不做记忆状态变更。所有 status 变更仍由异步 Agent 在确认后执行：

| 实时检测发现 | 异步 Agent 衔接 | 最终状态变更 |
|-------------|----------------|------------|
| 矛盾（非情绪化） | 当晚矛盾检测 Agent 优先处理该条"待确认"记录，跳过候选筛选（实时已找到矛盾信念），直接走情绪过滤 + 确认转变 | 次日 AI 主动确认 → 用户确认后 current → superseded |
| 矛盾（情绪化） | 当晚矛盾检测 Agent 检测到 emotion_intensity > 0.8，标记 emotion_context，不触发状态变更 | 不标记转变。3 天后观察 |
| 漂移信号 | 实时只是信号提示。每周漂移检测 Cron Job 仍按周维度计算正式 drift_score | 周日 Cron → drift_score > 0.15 → AI 主动确认 |
| 回归信号 | 实时已标记 regression_belief。当晚矛盾检测 Agent 直接走回归识别流程 | superseded → reverted，新记忆 reverted_from 指向 |

### 13.8 降级策略

| 失败场景 | 降级行为 | 影响 |
|---------|---------|------|
| Embedding API 超时 | 跳过 Step 1-2，Flash-Lite 仍执行（无漂移信号输入） | 漂移/回归信号缺失，矛盾预检仍可用 |
| Flash-Lite 超时（>500ms） | 跳过 Step 3，用原始 user_msg | LLM 仍能通过注入的记忆自己发现矛盾（兜底） |
| SQL 查询失败 | 跳过 Step 2 | 不影响 Flash-Lite 预处理（无漂移信号） |
| 所有预处理都失败 | 等价于无实时检测设计 | 异步 Agent 仍正常工作 |

### 13.9 延迟与成本

| 环节 | 延迟 | 成本 | 模型 |
|------|------|------|------|
| Step 1: Embedding | ~20ms | ~$0.000007/次 | gemini-embedding-001 |
| Step 2: SQL 快查 | ~25ms | $0 | PostgreSQL pgvector |
| Step 3: Flash-Lite | ~120ms | ~$0.00003/次 | gemini-2.5-flash-lite |
| Prompt 增强 | ~5ms | $0 | 字符串拼接 |
| **预处理总计** | **~170ms** | **~$0.00004/次** | - |
| Flash 流式生成 | ~2-3s 首 token | ~$0.0005/次 | gemini-2.5-flash |
| **首 token 总延迟** | **~2.5-3.2s** | - | - |

月成本（1 万 DAU，3 轮/用户/天）：预处理 ~$4/月，可忽略。

### 13.10 与异步检测的职责划分

| 职责 | 实时（对话中） | 异步（对话后） |
|------|-------------|-------------|
| 指代消解 | Flash-Lite 实时改写 | 不需要 |
| 矛盾感知 | LLM 在回复中自然提及 | - |
| 矛盾状态变更 | 不做 | 矛盾检测 Agent + 情绪过滤 + 用户确认 |
| 漂移感知 | SQL 快查信号 → LLM 提及 | 每周 Cron 正式计算 drift_score |
| 回归感知 | SQL 查到 superseded similarity > 0.80 | 异步 Agent 正式标记 reverted |
| 情绪过滤 | is_emotional → 降级矛盾 hint | > 0.8 正式过滤，不标记转变 |

**核心原则**：实时只做"感知和提示"，异步才做"状态变更"。三道安全阀（情绪过滤、source 过滤、用户确认）不被绕过。


---

## 12. 与现有 Spec 的关系

| Spec 文件 | 关系 | 说明 |
|-----------|------|------|
| `memory-layer.md` | **互补** | 本文档定义短期对话上下文，memory-layer.md 定义长期记忆。两者互不重叠 |
| `architecture.md` §3.1 | **修改** | `stream_chat_message` 需增加对话历史注入 + 实时预处理步骤（§13） |
| `code-architecture.md` §4 | **扩展** | `ChatService` 新增 `get_recent_history()` 方法 |
| `api-conventions.md` §2 | **不变** | 请求体/响应体格式不变，历史由后端自动查询 |
| `cost-optimization.md` §1 | **补充** | 新增对话历史注入的 token 成本（~$90/月） |
| `app-store-compliance.md` | **不变** | 对话历史仍在加密存储范围内 |

### 12.1 权威边界

- **短期对话上下文**（滑动窗口、话题追踪、指代消解）→ **本文档为唯一权威定义**
- **长期记忆上下文**（Temporal Memory、信念演变、pgvector 搜索）→ **`memory-layer.md` 为唯一权威定义**
- **LLM 成本数据**（单价、月成本估算）→ **`cost-optimization.md` 为唯一权威定义**，本文档仅引用
