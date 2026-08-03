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
| **Gemini Embedding** | Memory embeddings (768-dim) | $0.0075/1M tokens | Fast |

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
├── Step 1: Message Embedding (~20ms, gemini-embedding-001, 768-dim)
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

## Cost Optimization Principles

1. **Rule Engine first**: Always check if message can be template-generated before calling LLM
2. **Async = cheaper model**: Active messages use Flash-Lite (3x cheaper than Flash)
3. **Merge LLM calls**: Contradiction detection can be merged into daily summary generation (incremental cost = $0)
4. **Short outputs**: mood check-ins use `max_tokens=60-100`
5. **Redis cache**: Cache common emoji combo responses