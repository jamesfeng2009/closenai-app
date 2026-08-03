---
name: "closenai-tdd"
description: "Test-Driven Development discipline for ClosenAI: red-green-refactor cycle, test layering (unit/integration/API), bug-fix regression tests. Invoke during implementation phase for all feature development and bug fixes."
---

# Test-Driven Development (测试驱动开发)

> 层级：执行层
> 调用时机：所有功能实现和 Bug 修复都必须遵循 TDD 纪律

---

## 红-绿-重构循环

### 1. 红 — 先写失败测试

```python
# backend/tests/test_chat_service.py
async def test_stream_message_yields_tokens():
    """描述期望行为：流式消息应逐 token 返回"""
    service = ChatService(
        chat_repo=MockChatRepository(),
        memory_repo=MockMemoryRepository(),
        llm_service=MockLLMService(["Hello", " world"]),
        prompt_builder=MockPromptBuilder(),
    )

    chunks = []
    async for chunk in service.stream_message("user-1", request):
        chunks.append(chunk)

    assert "Hello" in "".join(chunks)
```

运行测试，确认**失败**（因为功能尚未实现）。

### 2. 绿 — 写最少代码使测试通过

```python
# backend/services/chat_service.py
async def stream_message(self, user_id, request):
    await self.chat_repo.save_user_message(user_id, request.content)
    memories = await self.memory_repo.retrieve_relevant(user_id, request.content)
    system_prompt = self.prompt_builder.build_chat_prompt(memories)

    full_response = ""
    async for chunk in self.llm_service.stream_chat(
        model="gemini-2.5-flash",
        system=system_prompt,
        user_msg=request.content,
    ):
        full_response += chunk
        yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\\n\\n"

    await self.chat_repo.save_ai_message(user_id, full_response)
    yield f"data: {json.dumps({'type': 'done'})}\\n\\n"
```

运行测试，确认**通过**。

### 3. 重构 — 优化代码结构

在测试保持绿色的前提下优化代码：
- 提取重复逻辑
- 改善命名
- 优化性能
- 消除魔法数字

## 测试分层

| 层级 | 测试类型 | 示例 |
|------|---------|------|
| 单元测试 | Mock 依赖，测单个 Service 方法 | `test_chat_service_stream` |
| 集成测试 | 真实数据库，测 Repository CRUD | `test_memory_repository_retrieve` |
| API 测试 | TestClient 测路由层 | `test_chat_endpoint_returns_sse` |

## Bug 修复的 TDD

修复 Bug 时，先写**回归测试**（复现 Bug），再修复代码：

1. 写测试复现 Bug（测试失败 = Bug 存在）
2. 修复代码（测试通过 = Bug 已修复）
3. 确认无其他测试被破坏

## ClosenAI 专项测试要点

| 测试场景 | 要点 |
|---------|------|
| SSE 流式对话 | 验证 token 逐条输出，验证 done 信号 |
| 主动消息生成 | 验证 LLM 失败时 fallback 到 Rule Engine 模板 |
| 记忆注入 | 验证 sliding window N=10，验证 superseded 记忆过滤 |
| 推送通知 | 验证 logout 后 push_token 清除 |
| IAP 收据 | 验证伪造收据被拒绝，验证退款后权益回收 |
| 情绪签到 | 验证 Redis 缓存命中时返回模板解读 |

## 可调用的子技能

| 场景 | 调用技能 |
|------|---------|
| 后端测试 | `closenai-backend` |
| 数据库测试 | `closenai-supabase` |
| LLM Mock | `closenai-llm` |
| 前端测试 | `closenai-frontend` |

## 产出

- 测试用例文件
- 测试通过确认
- 回归测试（Bug 修复时）