"""
对话路由。

提供 Reflection Mode 的 SSE 流式对话接口。

端点：
    POST /api/v1/chat/reflections  — 创建反思对话（SSE 流式响应）
"""
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from backend.models.schemas import ReflectionChatRequest

router = APIRouter()


@router.post("/chat/reflections", response_class=StreamingResponse)
async def create_reflection_chat(request: ReflectionChatRequest):
    """
    创建反思对话。

    以 SSE（Server-Sent Events）流式返回生成的反思引导消息。

    TODO: 接入 message_generator 服务实现流式生成。
    """
    # TODO: 调用 services/message_generator.py 生成流式响应
    async def event_stream():
        yield 'data: {"content": "TODO: 接入消息生成服务"}\n\n'

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
    )
