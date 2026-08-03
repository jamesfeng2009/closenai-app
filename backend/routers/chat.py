"""
对话路由 (P0-F01)。

端点：
    POST /api/v1/chat/stream     — SSE 流式对话
    POST /api/v1/chat/proactive  — 主动消息生成（内部）
    GET  /api/v1/chat/history    — 对话历史
"""
import json
import asyncio
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from backend.models.schemas import ChatStreamRequest, ChatHistoryResponse
from backend.services.message_generator import MessageGenerator, message_generator

router = APIRouter()


def get_message_generator() -> MessageGenerator:
    return message_generator


@router.post("/chat/stream", response_class=StreamingResponse)
async def stream_chat(
    request: ChatStreamRequest,
    generator: MessageGenerator = Depends(get_message_generator),
):
    """
    SSE 流式对话。

    1. 验证用户配额（免费 10 轮/天）
    2. 查询记忆上下文（最近 5 条相关记忆 + 7 天洞察）
    3. 构建四层 prompt（记忆 + 性格画像 + 情绪 + 互动）
    4. 调用 LLM 流式生成
    5. 逐 token 推送 SSE 事件

    SSE 事件格式：
        data: {"type":"token","content":"Hello"}
        data: {"type":"memory_context","memories":[...]}
        data: {"type":"done"}
    """
    async def event_stream():
        try:
            # 记忆上下文注入
            memories = await generator.get_memory_context("demo-user", request.content)
            if memories:
                yield f"data: {json.dumps({'type': 'memory_context', 'memories': memories[:3]})}\n\n"

            # 流式生成
            async for chunk in generator.generate_chat_stream(
                user_input=request.content,
                message_type=request.message_type,
            ):
                yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"
                await asyncio.sleep(0.01)  # 模拟逐 token 延迟

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/proactive")
async def generate_proactive_message(
    generator: MessageGenerator = Depends(get_message_generator),
):
    """
    生成主动消息（Cron Job 内部调用）。

    四层公式：
    ① 引用记忆："你昨天提到..."
    ② 发现模式："你这周提到了 3 次..."
    ③ 情绪关联："今天还是感到焦虑吗？"
    ④ 互动：Reflection 草稿预填
    """
    # TODO: 从 JWT 获取 user_id
    user_id = "demo-user"
    msg = await generator.generate_proactive_message(user_id)
    return {"message": msg}


@router.get("/chat/history", response_model=list[ChatHistoryResponse])
async def chat_history(
    limit: int = 20,
    generator: MessageGenerator = Depends(get_message_generator),
):
    """查询对话历史。"""
    # TODO: 从 chat_messages 表查询
    return []