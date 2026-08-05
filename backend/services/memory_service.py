"""
记忆服务 (P0-F02)。

长期记忆 + pgvector 语义检索 + 三态版本化（current/superseded/reverted）。

记忆注入链路：
    chat / mood 沉淀 → create_memory（去重加固 + LLM 转变检测 + 情绪过滤）→ 检索注入（回归识别）

三阶段处理（spec §2）：
    ① 转变检测（写入时）：同 category current 记忆 Top-3（0.75<sim<0.85）经 lite LLM
       判断矛盾，矛盾则版本化（sudden）
    ② 回归识别（读取时）：语义搜索含 superseded，sim>0.80 触发 revert，幂等
    ③ 情绪过滤：当日强负面情绪（MOOD_MAP 值≤1）不做状态变更，仅标记 emotion_context
"""
import json
import logging
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import AppException, ErrorCode
from backend.core.llm import llm
from backend.core.time_utils import local_day_start_utc, utc_now
from backend.models.memory import Memory
from backend.models.schemas import MemoryResponse
from backend.repositories import memory_repository, mood_repository, user_repository

logger = logging.getLogger("closenai.memory")

# 重复表达：相似度 ≥ 该阈值视为同一信念的重复表达，不新建记忆，仅加固 conviction
DEDUP_SIMILARITY_THRESHOLD = 0.85
# 转变检测：候选区间下界（> 0.75 且 < 0.85，排除重复表达区间）
CONFLICT_SIMILARITY_THRESHOLD = 0.75
# 回归识别：superseded 记忆相似度 > 该阈值触发 revert
REVERT_SIMILARITY_THRESHOLD = 0.80
# 重复表达加固步长（封顶 1.0）
CONVICTION_REINFORCE_STEP = 0.05
# 情绪过滤：当日签到为强负面情绪（MOOD_MAP 值 ≤ 该阈值）时不做状态变更
HIGH_NEGATIVE_MOOD_VALUE = 1
# 情绪过滤标记
EMOTION_CONTEXT_PENDING = "pending_high_emotion"

VALID_CATEGORIES = {"values", "preference", "relationship", "goal", "factual"}
VALID_SOURCES = {"self", "others", "hypothetical"}


def _to_response(memory: Memory) -> MemoryResponse:
    """ORM → 响应模型。"""
    return MemoryResponse(
        id=memory.id,
        user_id=memory.user_id,
        content=memory.content,
        category=memory.category,
        status=memory.status,
        source=memory.source,
        conviction=memory.conviction_score,
        created_at=memory.created_at,
        superseded_at=memory.superseded_at,
        transition_reason=memory.transition_reason,
    )


def _to_response_from_row(row) -> MemoryResponse:
    """pgvector 检索行（含 similarity）→ 响应模型。"""
    return MemoryResponse(
        id=row["id"],
        user_id=row["user_id"],
        content=row["content"],
        category=row["category"],
        status=row["status"],
        source=row["source"],
        conviction=row["conviction_score"],
        created_at=row["created_at"],
        superseded_at=row["superseded_at"],
        transition_reason=row["transition_reason"],
        similarity=row.get("similarity"),
    )


def _parse_json_object(text: str) -> Optional[dict]:
    """从 LLM 输出中解析单个 JSON 对象（容错 markdown 围栏/前后噪声）。"""
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def _parse_json_array(text: str) -> List[dict]:
    """从 LLM 输出中解析 JSON 数组（容错 markdown 围栏/前后噪声）。"""
    if not text:
        return []
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


class MemoryService:
    """记忆服务。"""

    async def list_memories(
        self,
        db: AsyncSession,
        user_id: str,
        limit: int = 20,
        category: Optional[str] = None,
    ) -> List[MemoryResponse]:
        """查询记忆列表。"""
        memories = await memory_repository.list(db, user_id, limit=limit, category=category)
        return [_to_response(m) for m in memories]

    async def get_memory(
        self, db: AsyncSession, memory_id: str, user_id: str
    ) -> Optional[MemoryResponse]:
        """获取单条记忆（强制归属校验）。"""
        memory = await memory_repository.get(db, memory_id, user_id)
        return _to_response(memory) if memory else None

    async def create_memory(
        self,
        db: AsyncSession,
        user_id: str,
        content: str,
        category: str = "factual",
        source: str = "self",
        conviction: float = 0.5,
        emotion_context: Optional[str] = None,
        language: str = "en",
    ) -> MemoryResponse:
        """创建记忆（含向量化 + 重复表达加固 + LLM 转变检测 + 情绪过滤）。

        - 相似度 ≥ 0.85：同一信念的重复表达，不新建记忆，仅提升旧记忆 conviction
        - 0.75 < 相似度 < 0.85：同 category current Top-3 走 lite LLM 矛盾检测，
          矛盾则版本化（sudden）；当日强负面情绪时仅标记 emotion_context 不变更状态
        - 检测全程 try/except 降级：失败按普通新记忆写入
        """
        embedding = await self._embed_or_none(content)

        if embedding:
            handled = await self._handle_similar_memories(
                db, user_id, content, category, source, conviction, emotion_context, embedding, language
            )
            if handled is not None:
                return handled

        memory = await memory_repository.create(
            db,
            user_id,
            content=content,
            category=category,
            source=source,
            conviction=conviction,
            embedding=embedding,
            emotion_context=emotion_context,
        )
        return _to_response(memory)

    async def _handle_similar_memories(
        self,
        db: AsyncSession,
        user_id: str,
        content: str,
        category: str,
        source: str,
        conviction: float,
        emotion_context: Optional[str],
        embedding: List[float],
        language: str,
    ) -> Optional[MemoryResponse]:
        """写入路径的去重加固与转变检测。返回 None 表示按普通新记忆写入。"""
        try:
            candidates = await memory_repository.semantic_search(
                db, user_id, embedding, limit=5, include_superseded=False, category=category
            )
            if not candidates:
                return None

            # ① 重复表达：相似度 ≥ 0.85 → 不新建，仅加固旧记忆 conviction
            top = candidates[0]
            if (top.get("similarity") or 0) >= DEDUP_SIMILARITY_THRESHOLD:
                new_conviction = min(1.0, (top["conviction_score"] or 0.5) + CONVICTION_REINFORCE_STEP)
                await memory_repository.update_conviction(db, top["id"], user_id, new_conviction)
                row = dict(top)
                row["conviction_score"] = new_conviction
                return _to_response_from_row(row)

            # ② 转变检测：0.75 < sim < 0.85 的同 category current Top-3（仅自身信念参与）
            if source != "self":
                return None
            conflict_candidates = [
                r for r in candidates
                if CONFLICT_SIMILARITY_THRESHOLD < (r.get("similarity") or 0) < DEDUP_SIMILARITY_THRESHOLD
            ][:3]
            if not conflict_candidates:
                return None
            conflict = await self._detect_conflict(content, conflict_candidates, language)
            if not conflict:
                return None
            target_id = conflict.get("conflicts_with")
            target = next((r for r in conflict_candidates if r["id"] == target_id), None)
            if not target:
                return None
            reason_raw = conflict.get("reason")
            reason = str(reason_raw).strip() if reason_raw else ""
            reason = reason or "belief_change"

            # ③ 情绪过滤：当日强负面情绪 → 不变更状态，仅标记候选旧记忆
            if await self._is_high_negative_today(db, user_id):
                await memory_repository.update_emotion_context(
                    db, target["id"], user_id, EMOTION_CONTEXT_PENDING
                )
                return None

            # 确认转变：新记忆 current，旧记忆 superseded（sudden，记录转变原因）
            memory = await memory_repository.create(
                db,
                user_id,
                content=content,
                category=category,
                source=source,
                conviction=conviction,
                embedding=embedding,
                emotion_context=emotion_context,
            )
            await memory_repository.version_out(
                db,
                target["id"],
                user_id,
                status="superseded",
                superseded_by=memory.id,
                superseded_at=utc_now(),
                transition_reason=reason,
            )
            return _to_response(memory)
        except Exception as exc:  # noqa: BLE001  检测失败降级为普通新记忆写入
            logger.warning("记忆转变检测失败，按普通写入降级: %s", exc)
            return None

    async def _detect_conflict(
        self, new_content: str, candidates: List[dict], language: str = "en"
    ) -> Optional[dict]:
        """lite LLM 矛盾分类：{conflicts_with: id|null, reason}；失败返回 None。"""
        cand_desc = "\n".join(f"- id={c['id']}: {c['content']}" for c in candidates)
        prompt = (
            "判断「新表达」是否与下面某条用户已有信念相矛盾（真正的观点/事实转变，"
            "而非补充细节、情绪吐槽或引用他人观点）。\n"
            f"新表达：{new_content}\n"
            f"已有信念：\n{cand_desc}\n"
            '只输出 JSON：{"conflicts_with": "矛盾信念的 id 或 null", "reason": "矛盾原因，无矛盾为 null"}'
        )
        try:
            text = await llm.complete(prompt, language=language, lite=True, max_tokens=120, temperature=0.1)
        except Exception as exc:  # noqa: BLE001  LLM 失败不阻断写入
            logger.warning("矛盾检测 LLM 失败: %s", exc)
            return None
        data = _parse_json_object(text)
        if not data or not data.get("conflicts_with"):
            return None
        return data

    async def _is_high_negative_today(self, db: AsyncSession, user_id: str) -> bool:
        """当日情绪签到是否为强负面情绪（MOOD_MAP 值 ≤ 1，按用户本地日）。"""
        from backend.services.mood_service import MOOD_MAP  # 延迟导入避免循环依赖

        try:
            user = await user_repository.get_user(db, user_id)
            day_start = local_day_start_utc(user.timezone if user else None)
            checkin = await mood_repository.get_today(db, user_id, day_start)
            if not checkin:
                return False
            return MOOD_MAP.get(checkin.mood_emoji, (None, 3))[1] <= HIGH_NEGATIVE_MOOD_VALUE
        except Exception as exc:  # noqa: BLE001  查询失败不做情绪过滤
            logger.warning("情绪过滤查询失败: %s", exc)
            return False

    async def extract_and_store(
        self, db: AsyncSession, user_id: str, message: str, language: str = "en"
    ) -> int:
        """对话后自动提取记忆（lite LLM，全程降级不阻断主流程）。返回落库条数。"""
        try:
            prompt = (
                "从下面这段用户的话中提取值得长期记住的关键信息"
                "（人名、事件、情绪、时间、地点、信念/偏好/目标等）。\n"
                '每条输出：{"content": "简洁陈述", "category": "values|preference|relationship|goal|factual", '
                '"source": "self|others|hypothetical", "conviction": 0.0到1.0}\n'
                "- source: self=用户自身信念/经历；others=引用他人观点；hypothetical=假设性讨论\n"
                "- 没有值得记住的内容时输出空数组 []\n"
                "只输出 JSON 数组，最多 5 条。\n"
                f"用户的话：{message}"
            )
            text = await llm.complete(prompt, language=language, lite=True, max_tokens=300, temperature=0.2)
            stored = 0
            for item in _parse_json_array(text)[:5]:
                content = (item.get("content") or "").strip()
                if not content:
                    continue
                category = item.get("category") if item.get("category") in VALID_CATEGORIES else "factual"
                source = item.get("source") if item.get("source") in VALID_SOURCES else "self"
                try:
                    conviction = min(1.0, max(0.0, float(item.get("conviction", 0.5))))
                except (TypeError, ValueError):
                    conviction = 0.5
                await self.create_memory(
                    db, user_id, content=content, category=category,
                    source=source, conviction=conviction, language=language,
                )
                stored += 1
            return stored
        except Exception as exc:  # noqa: BLE001  提取失败仅记日志
            logger.warning("对话记忆提取失败 user=%s: %s", user_id, exc)
            return 0

    async def semantic_search(
        self,
        db: AsyncSession,
        user_id: str,
        query: str,
        limit: int = 10,
        include_superseded: bool = False,
    ) -> List[MemoryResponse]:
        """语义搜索记忆。"""
        embedding = await self._embed_or_none(query)
        if not embedding:
            return []
        rows = await memory_repository.semantic_search(
            db, user_id, embedding, limit=limit, include_superseded=include_superseded
        )
        return [_to_response_from_row(r) for r in rows]

    async def get_memory_context(
        self,
        db: AsyncSession,
        user_id: str,
        query: str,
        limit: int = 5,
        include_superseded: bool = False,
    ) -> List[MemoryResponse]:
        """获取记忆上下文（用于对话 / 主动消息注入）。

        include_superseded=True 时搜索范围含 superseded（spec §2.2 回归识别）：
        superseded 记忆相似度 > 0.80 触发 revert——对其后继 current 记忆执行回归，
        旧记忆恢复 current 并以 regressed=True 标注（注入 prompt 时标 [回归信念]）。
        幂等：revert 后继记忆变为 reverted 不再被搜到，旧记忆变为 current 不再触发。
        """
        embedding = await self._embed_or_none(query)
        if not embedding:
            return []
        rows = await memory_repository.semantic_search(
            db, user_id, embedding, limit=limit, include_superseded=include_superseded
        )
        responses: List[MemoryResponse] = []
        for row in rows:
            resp = _to_response_from_row(row)
            if (
                include_superseded
                and row["status"] == "superseded"
                and (row.get("similarity") or 0) > REVERT_SIMILARITY_THRESHOLD
                and row.get("superseded_by")
            ):
                try:
                    await self.record_version(
                        db, user_id, row["superseded_by"], "revert", reason="regression_detected"
                    )
                    resp.regressed = True
                except Exception as exc:  # noqa: BLE001  回归失败不影响注入
                    logger.warning("回归识别 revert 失败 memory=%s: %s", row["id"], exc)
            responses.append(resp)
        return responses

    async def record_version(
        self,
        db: AsyncSession,
        user_id: str,
        memory_id: str,
        transition_type: str,
        reason: Optional[str],
        emotion_context: Optional[dict] = None,
        new_content: Optional[str] = None,
    ) -> None:
        """
        记录信念演变。

        - gradual: 渐变漂移（原记忆 superseded，置信度提升）
        - sudden: 突然转变（原记忆 superseded）
        - revert: 回归（当前记忆标记 reverted，恢复被它取代的旧记忆为 current）

        new_content：演变后的新信念内容（gradual/sudden）；缺省时沿用原内容。
        """
        memory = await memory_repository.get(db, memory_id, user_id)
        if not memory:
            raise AppException(ErrorCode.MEMORY_NOT_FOUND, "记忆不存在")

        if transition_type == "revert":
            # 回归：找到被该记忆取代的旧记忆（superseded_by == memory_id），恢复为 current
            old = await memory_repository.get_superseded_by(db, memory_id, user_id)
            await memory_repository.version_out(
                db, memory_id, user_id, status="reverted", transition_reason=reason or "revert"
            )
            if old:
                await memory_repository.restore(db, old.id, reverted_from=memory_id)
            return

        # gradual / sudden：旧记忆 superseded，写入新记忆（有新内容则记录演变后的信念）
        new_conviction = min(1.0, memory.conviction_score + 0.1) if transition_type == "gradual" else memory.conviction_score
        content = (new_content or "").strip() or memory.content
        # 内容变化时重新向量化，保证语义检索命中新信念
        embedding = memory.embedding if content == memory.content else await self._embed_or_none(content)
        new_memory = await memory_repository.create(
            db,
            user_id,
            content=content,
            category=memory.category,
            source=memory.source,
            conviction=new_conviction,
            embedding=embedding,
            emotion_context=memory.emotion_context,
        )
        await memory_repository.version_out(
            db,
            memory_id,
            user_id,
            status="superseded",
            superseded_by=new_memory.id,
            superseded_at=utc_now(),
            transition_reason=reason or transition_type,
        )

    async def _embed_or_none(self, text: str) -> Optional[List[float]]:
        """向量化文本，失败返回 None 不阻塞主流程。"""
        try:
            vectors = await llm.embed([text])
            return vectors[0] if vectors else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("embedding 失败: %s", exc)
            return None


memory_service = MemoryService()
