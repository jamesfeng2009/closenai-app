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
from backend.core.db_route import CommitBeforeResponseRoute
from backend.core.errors import AppException, ErrorCode
from backend.core.language import get_request_language
from backend.core.response import success
from backend.database import get_db
from backend.models.schemas import ChatStreamRequest
from backend.repositories import chat_repository, memory_repository
from backend.services.chat_service import chat_service
from backend.services.commitment_service import commitment_service
from backend.services.memory_service import memory_service
from backend.services.message_generator import MessageGenerator, message_generator
from backend.services.user_service import user_service

logger = logging.getLogger("closenai.chat")

router = APIRouter(route_class=CommitBeforeResponseRoute)


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

    # 阶段一事务编排已下沉 chat_service（router 不持有事务边界/不直连 repository）：
    # 配额校验 + 扣减 + 用户消息落库在独立短事务内原子完成（B6：避免行锁横跨 LLM 流）。
    try:
        await chat_service.prepare_chat_turn(
            user_id, request.content, request.message_type
        )
    except AppException as e:
        if e.code == int(ErrorCode.QUOTA_EXCEEDED):
            # 配额超限：返回只含错误事件的 SSE 流（保持客户端 SSE 错误处理契约）
            async def quota_error_stream():
                yield _sse_event("error", {
                    "code": int(ErrorCode.QUOTA_EXCEEDED),
                    "message": e.message,
                })
            return StreamingResponse(
                quota_error_stream(),
                media_type="text/event-stream",
                headers=_stream_headers(),
            )
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

            # 承诺捕获与负向退出（§5.18）：Rule 抽取零 LLM，内部降级不阻断
            await commitment_service.on_user_message(db, user_id, request.content)

            yield _sse_event("done", {})

        except Exception:
            # 脱敏：内部异常细节只落日志，不下发客户端
            logger.exception("chat stream 失败 user=%s", user_id)
            yield _sse_event("error", {
                "code": int(ErrorCode.INTERNAL_ERROR),
                "message": "生成失败，请稍后再试 / Generation failed, please try again later",
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
    """查询对话历史（role 映射为前端契约 user/ai，附 AI 生成标识用于合规标注）。

    memory_refs：正文引用了用户原话的消息（第一周剧本等模板直出触达）附引用依据，
    前端据此渲染「记忆依据 ▾」（PRD §5.19.3 引用依据全局规范：每句可回溯到原话+日期）。
    归属校验在 memory_repository.list_by_ids 内强制，解析不到的 ID 直接丢弃。
    """
    messages = await chat_repository.get_history(db, user_id, limit=limit)

    wanted = {
        mid for m in messages for mid in (m.memory_ids or "").split(",") if mid
    }
    by_id = {}
    if wanted:
        by_id = {
            mem.id: mem for mem in await memory_repository.list_by_ids(db, user_id, list(wanted))
        }

    history = []
    for m in reversed(messages):
        refs = [
            {
                "memory_id": mem.id,
                "created_at": mem.created_at,
                "excerpt": " ".join(mem.content.split()),
            }
            for mem in (by_id.get(mid) for mid in (m.memory_ids or "").split(",") if mid)
            if mem is not None
        ]
        history.append(
            {
                "id": m.id,
                "role": "ai" if m.role == "assistant" else "user",
                "content": m.content,
                "is_ai_generated": bool(getattr(m, "ai_generated", m.role == "assistant")),
                # message_type=proactive 标记主动消息，前端据此同步进本地 ai_messages 表（消息中心）
                "message_type": getattr(m, "message_type", None),
                "created_at": m.created_at,
                "memory_refs": refs,
            }
        )
    return success(history)