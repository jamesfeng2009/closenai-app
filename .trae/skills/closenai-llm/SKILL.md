---
name: "closenai-llm"
description: "ClosenAI LLM integration: Gemini 2.5 Flash/Flash-Lite, Doubao, Rule Engine cost layering, context engineering, prompt templates, SSE streaming. Invoke when working on AI messages, prompt design, LLM cost optimization, or context injection."
---

# ClosenAI AI/LLM Integration

LLM 集成规范：Gemini 2.5 Flash/Flash-Lite + 豆包 + Rule Engine 成本分层 + 上下文工程 + Prompt 模板

## Model Selection

| Model | Use Case | Cost | Latency |
|-------|----------|------|---------|
| **Gemini 2.5 Flash** | Real-time chat, personality profiling, growth insights | $0.30/1M input | Fast (streaming) |
| **Gemini 2.5 Flash-Lite** | Active messages (async), pattern detection, contradiction checking | $0.10/1M input | Moderate |
| **Doubao (豆包)** | Active messages (async fallback), Chinese content | Low | Moderate |
| **DashScope text-embedding-v4** | Memory embeddings (512-dim halfvec, MRL 截断) | 按量计费 | Fast |

## Rule Engine + LLM Cost Layering (70% / 30%)

**Core Principle: Most messages don't need LLM. Only messages requiring "understanding" and "insight" call LLM.**

| Message Type | Generator | Share | Cost/msg | Feature |
|-------------|-----------|-------|----------|---------|
| Morning greetings | Rule Engine + templates | ~20% | $0 | P0-F01 |
| Mood check-in reply | Rule Engine + emotion mapping | ~25% | $0 | P0-F03 |
| Daily Discovery | Rule Engine + data comparison | ~15% | $0 | P0-F10 |
| Reminders / Streak | Fixed templates | ~10% | $0 | P0-F04 |
| User chat reply | LLM (Gemini 2.5 Flash, streaming) | ~20% | ~$0.0005 | P0-F01 |
| Personality profile | LLM (Gemini 2.5 Flash, deep) | ~2% | ~$0.007 | P0-F05 |
| Growth insights | LLM (Gemini 2.5 Flash, analytic) | ~5% | ~$0.004 | P0-F07 |
| Active messages (deep) | LLM (Flash-Lite, async) | ~3% | ~$0.0001 | P0-F01 |

**Cost comparison**: All-LLM: ~$900/month → Layered: ~$75/month (86% reduction for 10K DAU)

### Active Message Decision Logic

```
if has_unfinished_topic OR has_3day_insight:
    → LLM generate (Gemini 2.5 Flash-Lite)
else:
    → Rule Engine template
```

LLM failure → fallback to Rule Engine template.

## Context Engineering: Four-Dimension Framework

| Dimension | Short-term Context | Long-term Memory Context |
|-----------|-------------------|------------------------|
| **Write** | Raw message storage + topic/intent tagging | Daily summary + belief extraction + embedding |
| **Select** | Sliding window (N=10) + topic filtering | pgvector Top-5 + status filtering |
| **Compress** | No in-session compression; daily summary across sessions | Daily summary + 7-day insight summary |
| **Isolate** | Topic isolation + intent isolation | Category isolation + status isolation + emotion filtering |

### Sliding Window (P0 Required)

`stream_chat` must inject last N=10 messages (~5 rounds, ~2K tokens). This solves the current issue of only passing system_prompt + current message, which prevents pronoun resolution.

## Real-time Chat Preprocessing Pipeline

```
Serial Pipeline (total ~170ms, first token ~2.5-3.2s):
│
├── Step 1: Message Embedding (~20ms, text-embedding-v4, 512-dim halfvec)
├── Step 2: SQL Drift Quick Check (~25ms, pure SQL, zero cost)
├── Step 3: Flash-Lite Preprocessing Agent (~120ms, timeout 500ms)
│           Pronoun resolution + contradiction pre-check + emotion assessment
└── Flash Streaming Generation (Gemini 2.5 Flash, first token ~2-3s, max_tokens=500)
```

**Degradation**: SQL check fails → skip Step 2; preprocess timeout (>500ms) → skip Step 3; all preprocess fails → equivalent to no real-time detection.

## Context Injection by User Stage

### Day 1 (Cold Start)
- AI opens as social companion, NOT astrology-focused
- If personality type exists: personalized opening
- If not: social opening + first personality discovery question
- Birth info (if available) stored as background knowledge, NOT used in first message

### Day 2-6 (Accumulation)
Priority: yesterday's unfinished topic > general emotional care > personality-related daily topic > astrology transit (only if major transit AND user showed interest)

### Day 7+ (Mature)
Dimensions: memory + personality + (optional) transit. Astrology is enhancement only, integrated when conversation naturally touches related topics.

## Active Message LLM Context

```json
{
  "system": "You are the user's AI friend. Generate a daily message based on the following info...",
  "context": {
    "personality_type": "INTP",
    "recent_memories": ["...", "..."],
    "yesterday_conversation": "User mentioned arguing with coworker",
    "pending_personality_question": true,
    "astrology_data": null,
    "day_number": 3
  }
}
```

## Memory Injection Prompt Template

```javascript
const memoryContext = memories.map(m => {
  if (m.status === 'current') return `[当前信念] ${m.content}`;
  if (m.status === 'superseded') return `[历史信念·已转变] ${m.content} → 转变原因：${m.transition_reason}`;
  if (m.status === 'reverted' && m.reverted_from) {
    return `[回归信念] 用户曾持有此观点，后因...转变，现已回归。`;
  }
  return m.content;
}).join('\n');
```

## Content Safety: Three-Layer Defense（三层防御，提审硬依赖）

> 目标：防 AI 输出违规内容（仇恨/暴力/恐吓/政治宗教争议/NSFW）+ 识别自伤信号并关怀转介。
> 合规依据：App Store 1.2.1(a) 未成年人保护 + 12+ 年龄分级（spec: app-store-compliance.md §1.2）。
> 体验铁律：**用户发送路径零检测、零阻塞**（消息立即上屏+立即进 LLM，同微信体验）；检测全部在输出端与流式生成并行。
> 输入端不做预检：人→AI 场景消息只有用户自己和 AI 可见，输入本身无展示风险；真正风险是 AI 被 jailbreak 后输出违规内容，防御重心在输出端。

| 层 | 防御对象 | 机制 | 延迟/成本 |
|----|---------|------|----------|
| **L1 Prompt 防御** | jailbreak / 恶意引导 / 极端建议 | 所有面向用户的 LLM system prompt 必须含安全边界段落（后述）。对自伤/求助信号：主模型直接以关怀话术回应 + 转介专业求助资源，**不做单独检测调用** | 0 延迟，0 成本 |
| **L2 Provider 原生审核** | 极端违法内容（CSAM/恐怖主义等） | DashScope / Gemini 自带内容审核，命中时 provider 返回空内容或错误 → 走现有 LLM 降级模板兜底，**不额外开发** | 0（provider 侧执行） |
| **L3 输出后置过滤** | L1/L2 漏网的违规输出 | **输出检测唯一事实源：safety 检测模块**（同 quota_service 模式，业务代码不得复制判断逻辑）。流式：按句缓冲检测，与生成流水线并行，用户无感知；非流式（主动消息/签到解读）：全量检测后入库。命中 → 截断并替换安全话术；检测自身失败 → **fail-open 放行** + 记日志（L1/L2 已兜住主要风险），禁止因检测故障阻断对话 | 句级并行 ≈0 感知 |

**禁止事项：**
- ❌ iOS 端敏感词库（15 语言不可维护；词库打包进 App 可反编译泄露；解决不了 AI 输出端风险）
- ❌ 用户输入发送前串行检测（制造 loading，违背体验铁律）
- ❌ 对自伤信号用拒答模板（伦理红线：必须关怀 + 转介）
- ❌ 输出检测 fail-closed（检测服务故障不得阻断主聊天流程）

## Cost Optimization Principles

1. **Rule Engine first**: Always check if message can be template-generated before calling LLM
2. **Async = cheaper model**: Active messages use Flash-Lite (3x cheaper than Flash)
3. **Merge LLM calls**: Contradiction detection can be merged into daily summary generation (incremental cost = $0)
4. **Short outputs**: mood check-ins use `max_tokens=60-100`
5. **Redis cache**: Cache common emoji combo responses

## Multilingual Support (多语言规范)

### 支持语言（15 种）

| Code | 语言 | 地区 | 文化敏感度 |
|------|------|------|-----------|
| `en` | English | 全球 | 通用 |
| `zh` | 中文 | 中国大陆/港台 | 语气、节日 |
| `es` | Español | 西语区 | 热情、亲密称呼 |
| `ko` | 한국어 | 韩国 | 敬语体系（존경어/존칭） |
| `ja` | 日本語 | 日本 | 敬语（です/ます）、语气 |
| `ru` | Русский | 俄罗斯 | 正式/非正式 (Вы/ты) |
| `id` | Bahasa Indonesia | 印尼 | 温和、友善 |
| `ms` | Bahasa Melayu | 马来西亚 | 温和、友善 |
| `th` | ไทย | 泰国 | 礼貌词（ครับ/ค่ะ） |
| `de` | Deutsch | 德国 | 正式/非正式 (Sie/du) |
| `fr` | Français | 法国 | 正式/非正式 (vous/tu) |
| `it` | Italiano | 意大利 | 亲昵、热情 |
| `pt` | Português | 巴西/葡萄牙 | 地区差异（BR vs PT） |
| `vi` | Tiếng Việt | 越南 | Bạn 敬称、温和 |
| `km` | ខ្មែរ | 柬埔寨 | អ្នក 礼貌正式体 |

### 模型多语言支持矩阵（2026-08 核实）

| 环节 | 实际使用 | vi | km | 依据 |
|------|---------|----|----|------|
| 对话/画像/洞察 LLM | DashScope Qwen3 系列（qwen3.6-flash / qwen-flash） | ✅ | ✅ | Qwen3 官方 119 语言列表（Austroasiatic: Vietnamese, Khmer） |
| 记忆 Embedding | text-embedding-v4（Qwen3-Embedding） | ✅ | ✅ | 官方 100+ 语种；**v3 只保证 50+ 主流语种（km 不明确），禁止回退 v3** |
| 备选 Gemini 2.5 Flash | provider=gemini | ✅ | 以 Google 官方语言列表为准，接入前实测 | Gemini 多语言文档 |

> ⚠️ Embedding 模型更换 = 向量空间更换：v3↔v4 不兼容，存量 memories.embedding 必须全量重嵌入，否则新旧记忆互相检索不到。

### 核心原则

1. **语言跟随（Language Follow）**：AI 必须用用户的语言回复。用户说什么语言，AI 就用什么语言。
   - 用户 `users.language` 字段存储（默认 `en`，可在设置中修改）
   - 首次交互时检测：若用户已发消息，用传入语言；否则用 `Accept-Language` 或默认 `en`
2. **语言注入 prompt**：所有 LLM 调用 system prompt 必须含 `language` 指令，防止 AI 用错语言
3. **主动消息**：按用户 `language` 生成，禁止所有用户收到同一语言
4. **文化敏感度**：不同语言/地区注意称呼、敬语、节日、时区。AI 陪伴语气需贴合当地文化（如韩语用敬语、日语用です/ます）

### Prompt 模板（语言指令）

```json
{
  "system": "You are the user's AI friend. Reply in the user's language exclusively.\nLanguage: {language}\nUser's language code: {locale}\n\n{context}"
}
```

### 语言检测与存储

**优先级（Header 优先 + 库兜底）**：
1. `X-Language` header（前端每次请求携带，实时、无状态）
2. `users.language`（数据库偏好，跨设备/重装兜底）
3. 默认 `en`

- 解析入口：`backend/core/language.py` 的 `get_request_language` 依赖（与 `X-User-Id` header 透传模式一致）
- `X-Language` 值归一化：`zh-CN → zh`（取 `-` 前段小写），仅接受 `supported_languages` 内的码
- 前端所有请求必须带 `X-Language` header（取当前 i18n 语言）
- `users.language` 字段：`en` | `zh` | `es` | `ko` | `ja` | `ru` | `id` | `ms` | `th` | `de` | `fr` | `it` | `pt` | `vi` | `km`
- 用户可在 ProfileScreen 设置中手动切换，后端更新 `users.language`
- **前端首次启动用 `expo-localization` 读取系统语言做默认值**：系统语言在 15 种支持列表内 → 用之（`zh-CN/zh-Hans → zh`）；不在 → 默认 `en`（见 `frontend/src/i18n/index.ts` 的 `detectLanguage()`）。初始化后所有请求自动携带对应 `X-Language`，无需额外处理

### 避免踩坑

- ❌ 不要用单一语言生成所有内容（用户会流失）
- ❌ 不要用英文 prompt 生成中文内容再翻译（质量差、成本高）
- ✅ 直接让模型用目标语言生成（模型原生多语言，不翻译）
- 支持的语言切换不影响 LLM 成本（token 数相同）