"""
对话路由 (P0-F01)。

端点：
    POST /api/v1/chat/stream     — SSE 流式对话
    GET  /api/v1/chat/history    — 对话历史

注：主动消息生成仅供 cron 内部链路调用（services/message_generator），
不暴露 HTTP 端点——避免绕过 ai_consent / 配额校验被脚本化刷 LLM 成本。
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
from backend.database import SessionLocal, get_db
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


def _sse_event(event_type: str, payload: dict) -> str:
    """格式化 SSE 事件行：data: {type, **payload}\\n\\n。"""
    return f"data: {json.dumps({'type': event_type, **payload})}\n\n"


def _stream_headers() -> dict:
    """SSE 流式响应标准头。"""
    return {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }


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

    # ── B6 修复：拆分事务，避免 FOR UPDATE 行锁持有整个 LLM 流式生成期间（3-10s）──
    # 阶段一（独立短事务 prep_session，持有行锁）：配额检查 + 扣减 + 写入用户消息 →
    #   commit 释放锁。用独立 session 而非在请求级 db 上中途 commit，避免破坏 get_db()
    #   「请求结束统一 commit/rollback」的事务契约。配额校验+扣减（写用户消息+累加
    #   chat_count）在同一事务内原子完成；commit 后用户消息已落库、配额已计数。
    #   若阶段二 LLM 失败，用户消息保留、配额仍计数（用户已发起请求，消耗一次对话
    #   轮数是合理语义；原「整事务回滚」会因持锁期间过长阻塞同用户并发，故拆分）。
    async with SessionLocal() as prep_session:
        try:
            user = await user_repository.get_or_create_user(
                prep_session, user_id, for_update=True
            )
            # 配额检查（B2）：对话入口只受对话轮数约束，统一池耗尽不阻断对话
            if await quota_service.is_chat_quota_exceeded(prep_session, user):
                # 配额超限：返回只含错误事件的 SSE 流（保持客户端 SSE 错误处理契约）
                async def quota_error_stream():
                    yield _sse_event("error", {
                        "code": int(ErrorCode.QUOTA_EXCEEDED),
                        "message": "今天的次数已经用完，请明天再来",
                    })
                return StreamingResponse(
                    quota_error_stream(),
                    media_type="text/event-stream",
                    headers=_stream_headers(),
                )
            # 保存用户消息（并累计聊天次数，首页"聊天次数"展示）
            await chat_repository.create(
                prep_session, user_id, "user", request.content,
                message_type=request.message_type,
            )
            await user_repository.increment_chat_count(prep_session, user_id)
            await prep_session.commit()
        except Exception:
            await prep_session.rollback()
            raise

    # 阶段二（无锁 SSE 流式生成）：用请求级 db session，不持有用户行锁，
    # 同用户并发请求不被 LLM 耗时（3-10s）阻塞。AI 回复写入与记忆提取由 get_db
    # 在流结束后统一 commit；阶段一已 commit 的用户消息对 db 可见（READ COMMITTED）。
    async def event_stream():
        try:
            # 记忆上下文注入
            memories = await generator.get_memory_context(db, user_id, request.content)
            if memories:
                yield _sse_event("memory_context", {"memories": memories[:3]})

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
                yield _sse_event("token", {"content": chunk})

            # 保存 AI 回复
            reply = "".join(full)
            if reply:
                await chat_repository.create(
                    db, user_id, "assistant", reply, message_type="reply", ai_generated=True
                )

            # 对话后自动提取记忆（P0-F02）：token 流已结束，不阻塞 SSE；
            # 内部全程 try/except 降级，失败仅记日志
            await memory_service.extract_and_store(db, user_id, request.content, language)

            yield _sse_event("done", {})

        except Exception:
            # 脱敏：内部异常细节只落日志，不下发客户端
            logger.exception("chat stream 失败 user=%s", user_id)
            yield _sse_event("error", {
                "code": int(ErrorCode.INTERNAL_ERROR),
                "message": "生成失败，请稍后再试",
            })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=_stream_headers(),
    )


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