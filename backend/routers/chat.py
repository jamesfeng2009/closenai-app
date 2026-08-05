"""
对话路由 (P0-F01)。

端点：
    POST /api/v1/chat/stream     — SSE 流式对话
    POST /api/v1/chat/proactive  — 主动消息生成（内部）
    GET  /api/v1/chat/history    — 对话历史
"""
import json
import logging
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.auth import get_current_user
from backend.core.errors import ErrorCode
from backend.core.language import get_request_language
from backend.core.response import success
from backend.database import get_db
from backend.models.schemas import ChatStreamRequest
from backend.repositories import chat_repository, user_repository
from backend.services.memory_service import memory_service
from backend.services.message_generator import MessageGenerator, message_generator
from backend.services.quota_service import quota_service
from backend.services.user_service import user_service

logger = logging.getLogger("closenai.chat")

router = APIRouter()


def get_message_generator() -> MessageGenerator:
    return message_generator


@router.post("/chat/stream", response_class=StreamingResponse)
async def stream_chat(
    request: ChatStreamRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
    language: str = Depends(get_request_language),
    generator: MessageGenerator = Depends(get_message_generator),
):
    """
    SSE 流式对话。

    0. AI 数据共享同意检查（合规 5.1.2(i)：未同意返回 403 AI_CONSENT_REQUIRED）
    1. 配额检查（免费用户每日 5 条，用户本地时区 00:00 重置）
    2. 保存用户消息
    3. 查询记忆上下文 + 注入
    4. 调用 LLM 流式生成（SSE 逐 token）
    5. 保存 AI 回复
    """
    # 合规前置检查：未同意数据共享则直接 403（在进入 SSE 流之前，前端按信封错误处理）
    await user_service.require_ai_consent(db, user_id)

    async def event_stream():
        try:
            # 行锁串行化：配额校验与写消息在同一事务内完成，防并发超配额
            user = await user_repository.get_or_create_user(db, user_id, for_update=True)

            # 配额检查：免费用户今日 LLM 消耗（对话 + 主动消息 + 签到解读）达上限则拒绝
            if await quota_service.is_daily_quota_exceeded(db, user):
                yield f"data: {json.dumps({'type': 'error', 'code': int(ErrorCode.QUOTA_EXCEEDED), 'message': '今天的次数已经用完，请明天再来'})}\n\n"
                return

            # 保存用户消息（并累计聊天次数，首页"聊天次数"展示）
            await chat_repository.create(db, user_id, "user", request.content, message_type=request.message_type)
            await user_repository.increment_chat_count(db, user_id)

            # 记忆上下文注入
            memories = await generator.get_memory_context(db, user_id, request.content)
            if memories:
                yield f"data: {json.dumps({'type': 'memory_context', 'memories': memories[:3]})}\n\n"

            # 流式生成（PRD：端到端响应 < 3s，不做人为延迟）
            full = []
            async for chunk in generator.generate_chat_stream(
                db=db,
                user_id=user_id,
                user_input=request.content,
                message_type=request.message_type,
                language=language,
            ):
                full.append(chunk)
                yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"

            # 保存 AI 回复
            reply = "".join(full)
            if reply:
                await chat_repository.create(
                    db, user_id, "assistant", reply, message_type="reply", ai_generated=True
                )

            # 对话后自动提取记忆（P0-F02）：token 流已结束，不阻塞 SSE；
            # 内部全程 try/except 降级，失败仅记日志
            await memory_service.extract_and_store(db, user_id, request.content, language)

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception:
            # 脱敏：内部异常细节只落日志，不下发客户端
            logger.exception("chat stream 失败 user=%s", user_id)
            yield f"data: {json.dumps({'type': 'error', 'code': int(ErrorCode.INTERNAL_ERROR), 'message': '生成失败，请稍后再试'})}\n\n"

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
    db: AsyncSession = Depends(get_db),
    language: str = Depends(get_request_language),
    user_id: str = Depends(get_current_user),
    generator: MessageGenerator = Depends(get_message_generator),
):
    """
    生成主动消息（Cron Job 内部调用）。

    四层公式：
    ① 引用记忆 ② 发现模式 ③ 情绪关联 ④ 互动
    """
    msg = await generator.generate_proactive_message(db, user_id, language)
    return success({"message": msg})


@router.get("/chat/history")
async def chat_history(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """查询对话历史（role 映射为前端契约 user/ai，附 AI 生成标识用于合规标注）。"""
    messages = await chat_repository.get_history(db, user_id, limit=limit)
    history = [
        {
            "id": m.id,
            "role": "ai" if m.role == "assistant" else "user",
            "content": m.content,
            "is_ai_generated": bool(getattr(m, "ai_generated", m.role == "assistant")),
            "created_at": m.created_at,
        }
        for m in reversed(messages)
    ]
    return success(history)