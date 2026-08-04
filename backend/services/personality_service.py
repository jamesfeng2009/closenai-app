"""
性格画像服务 (P0-F06)。

对话式性格发现：冷启动进度 + MBTI 画像生成 + 刷新配额。

冷启动阶段（依据数据丰富度）：
    破冰 → 观察 → 探索 → 揭示 → 运用
"""
import json
import logging
from datetime import timezone
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import AppException, ErrorCode
from backend.core.llm import llm
from backend.core.time_utils import local_now, utc_now
from backend.models.schemas import (
    PersonalityProfileResponse,
    PersonalityRefreshResponse,
)
from backend.repositories import (
    memory_repository,
    mood_repository,
    personality_repository,
    user_repository,
)
from backend.config import settings

logger = logging.getLogger("closenai.personality")

MBTI_LABELS = {
    "INTP": "逻辑学家", "INTJ": "建筑师", "ENTP": "辩论家", "ENTJ": "指挥官",
    "INFP": "调停者", "INFJ": "提倡者", "ENFP": "竞选者", "ENFJ": "主人公",
    "ISTP": "鉴赏家", "ISFJ": "守卫者", "ESTP": "企业家", "ESFJ": "执政官",
    "ISTJ": "物流师", "ISFP": "探险家", "ESTJ": "总经理", "ESFP": "表演者",
}

COLD_START_STAGES = ["破冰", "观察", "探索", "揭示", "运用"]


class PersonalityService:
    """性格画像服务。"""

    async def get_profile(self, db: AsyncSession, user_id: str) -> PersonalityProfileResponse:
        """获取性格画像（含冷启动进度，无画像时返回冷启动占位）。"""
        await user_repository.get_or_create_user(db, user_id)
        profile = await personality_repository.get_latest(db, user_id)
        quota_remaining = await self._refresh_quota_remaining(db, user_id)
        progress, stage = await self._cold_start(db, user_id)

        if profile:
            observations = self._parse_observations(profile.observations)
            return PersonalityProfileResponse(
                id=profile.id,
                mbti_type=profile.mbti_type,
                mbti_label=MBTI_LABELS.get(profile.mbti_type or "", None),
                observations=observations,
                unique_traits=profile.unique_traits,
                version=profile.version,
                refresh_quota_remaining=quota_remaining,
                cold_start_progress=progress,
                cold_start_stage=stage,
                created_at=profile.created_at,
            )

        # 冷启动占位（尚未生成画像）
        return PersonalityProfileResponse(
            id="",
            mbti_type=None,
            mbti_label=None,
            observations=[],
            unique_traits=None,
            version=0,
            refresh_quota_remaining=quota_remaining,
            cold_start_progress=progress,
            cold_start_stage=stage,
            created_at=utc_now(),
        )

    async def refresh(self, db: AsyncSession, user_id: str) -> PersonalityRefreshResponse:
        """刷新性格画像（配额校验 + LLM 生成）。"""
        await user_repository.get_or_create_user(db, user_id)
        quota_remaining = await self._refresh_quota_remaining(db, user_id)
        if quota_remaining <= 0:
            raise AppException(ErrorCode.QUOTA_EXCEEDED, "性格画像刷新次数已用完")

        user = await user_repository.get_user(db, user_id)
        memories = await memory_repository.list(db, user_id, limit=30)
        checkins = await mood_repository.get_recent(db, user_id, limit=30)

        data_desc = "\n".join(f"- {m.content}" for m in memories) or "暂无记忆。"
        mood_desc = " ".join(f"{c.mood_emoji}{c.mood_label or ''}" for c in checkins) or "暂无情绪记录。"
        prompt = (
            f"根据以下用户数据，推断其 MBTI 类型（返回格式：类型|观察1；观察2；观察3|独特之处1-2句）。\n"
            f"用户语言：{user.language if user else 'en'}\n用户记忆：\n{data_desc}\n最近情绪：{mood_desc}"
        )
        try:
            text = await llm.complete(prompt, language=(user.language if user else "en"), lite=True, max_tokens=200)
        except Exception as exc:  # noqa: BLE001
            logger.warning("性格画像 LLM 失败: %s", exc)
            raise AppException(ErrorCode.LLM_ERROR, "性格画像生成失败，请稍后再试")

        mbti_type, observations, unique_traits = self._parse_llm_result(text)
        versions = await personality_repository.list(db, user_id)
        new_version = (versions[-1].version + 1) if versions else 1
        profile = await personality_repository.create(
            db, user_id, mbti_type, json.dumps(observations, ensure_ascii=False), unique_traits, new_version
        )
        return PersonalityRefreshResponse(
            success=True,
            profile=await self.get_profile(db, user_id),
            message="性格画像已更新",
        )

    async def _refresh_quota_remaining(self, db: AsyncSession, user_id: str) -> int:
        """计算剩余刷新配额（免费 3 次总计，Pro 每月 5 次）。"""
        user = await user_repository.get_user(db, user_id)
        tier = user.tier if user else "free"
        versions = await personality_repository.list(db, user_id)
        if tier in ("pro", "pro_plus"):
            # 本月已刷新次数（按用户本地月份，created_at 统一换算到用户时区）
            now = local_now(user.timezone if user else None)
            month_recent = []
            for v in versions:
                aware = v.created_at if v.created_at.tzinfo else v.created_at.replace(tzinfo=timezone.utc)
                local_dt = aware.astimezone(now.tzinfo)
                if local_dt.year == now.year and local_dt.month == now.month:
                    month_recent.append(v)
            quota = settings.pro_personality_refreshes if tier == "pro" else 999
            return max(0, quota - len(month_recent))
        # free：总计 3 次
        return max(0, settings.free_personality_refreshes - len(versions))

    async def _cold_start(self, db: AsyncSession, user_id: str) -> tuple:
        """计算冷启动进度 (0.0-1.0) 与阶段。"""
        memories = await memory_repository.list(db, user_id, limit=100)
        checkins = await mood_repository.get_recent(db, user_id, limit=100)
        data_points = len(memories) + len(checkins)
        # 数据量映射到进度：0~30 条 → 0.0~1.0
        progress = min(1.0, data_points / 30.0)
        idx = min(int(progress * 5), 4)
        return round(progress, 2), COLD_START_STAGES[idx]

    def _parse_observations(self, raw: Optional[str]) -> List[str]:
        if not raw:
            return []
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except Exception:  # noqa: BLE001
            return []

    def _parse_llm_result(self, text: str) -> tuple:
        """解析 LLM 输出：类型|观察1；观察2|独特之处。默认回退 INTP。"""
        parts = [p.strip() for p in text.split("|")]
        mbti = "INTP"
        # 提取 4 位 MBTI 字母
        candidate = "".join(ch for ch in (parts[0] if parts else "") if ch.upper() in "EISNTFJP")
        if len(candidate) >= 4:
            mbti = candidate[:4].upper()
        observations = parts[1].split("；") if len(parts) > 1 else []
        observations = [o.strip() for o in observations if o.strip()]
        unique_traits = parts[2] if len(parts) > 2 else None
        return mbti, observations, unique_traits


personality_service = PersonalityService()