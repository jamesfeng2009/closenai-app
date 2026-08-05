"""
情绪签到服务 (P0-F03)。

核心链路：情绪签到 → 记录 memory → 更新 Streak → 生成 AI 解读。

次数口径（PRD P0-F03 §9）：
- 签到本身每日可多次；同一本地日重复签到走更新（一日一条记录），不再报 40901
- AI 解读（基础 + 深度）计入统一免费配额（quota_service 每日池），超额 40301

性能策略：
- 先写库 + 规则引擎给出确定性解读（保证 <2s 响应）
- 可选 LLM 深度解读（连续 3 天同情绪 / 连续 2 天负面触发，lite 模型），
  失败自动降级为 None，不影响签到主流程
"""
import logging
from datetime import timezone
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import AppException, ErrorCode
from backend.core.llm import llm
from backend.core.time_utils import local_day_start_utc, local_today, safe_zoneinfo
from backend.models.schemas import (
    MoodCheckinResponse,
    MoodHistoryResponse,
    MoodTrendResponse,
)
from backend.models.user import User
from backend.repositories import memory_repository, mood_repository, user_repository
from backend.services.quota_service import quota_service
from backend.services.safety_service import safety_service
from backend.services.user_service import user_service

logger = logging.getLogger("closenai.mood")

# 情绪 → (标签, 情绪值 1-5)
MOOD_MAP = {
    "😊": ("开心", 5),
    "😌": ("平静", 4),
    "😰": ("焦虑", 2),
    "😢": ("难过", 1),
    "😡": ("生气", 1),
    "😴": ("疲惫", 3),
}

# 情绪英文标签（与 MOOD_MAP 中文标签一一对应；zh* 用户用中文，其余用英文）
MOOD_LABELS_EN = {
    "😊": "Happy",
    "😌": "Calm",
    "😰": "Anxious",
    "😢": "Sad",
    "😡": "Angry",
    "😴": "Tired",
}


def mood_label_for(emoji: str, language: str | None = None) -> str:
    """按语言返回情绪标签：zh*（含 zh-CN）中文，其余英文。"""
    if (language or "").startswith("zh"):
        return MOOD_MAP.get(emoji, ("未知", 3))[0]
    return MOOD_LABELS_EN.get(emoji, "Unknown")


# 规则引擎解读模板（确定性，保证响应速度；按语言两套）
RULE_INTERPRET = {
    "zh": {
        "😊": "今天看起来心情不错，继续保持这份好心情！",
        "😌": "平静的一天，很稳。记得给自己留一点独处的时间。",
        "😰": "感到有些焦虑，这很正常。先深呼吸，慢慢来。",
        "😢": "有点难过，我在这里陪着你。允许自己有情绪。",
        "😡": "感到生气了吗？先别急着做决定，把情绪放一放。",
        "😴": "有点疲惫，好好休息也很重要，别太累自己。",
    },
    "en": {
        "😊": "Looks like you're in a good mood today — keep it up!",
        "😌": "A calm, steady day. Remember to leave a little time just for yourself.",
        "😰": "Feeling a bit anxious is completely normal. Take a deep breath and go slow.",
        "😢": "Feeling a little down — I'm here with you. It's okay to have emotions.",
        "😡": "Feeling angry? Try not to decide anything right now; let the emotion settle first.",
        "😴": "A bit tired — rest matters too. Don't push yourself too hard.",
    },
}


class MoodService:
    """情绪签到服务。"""

    async def checkin(
        self,
        db: AsyncSession,
        user_id: str,
        emoji: str,
        note: Optional[str],
        language: str = "en",
    ) -> MoodCheckinResponse:
        """执行情绪签到。"""
        if emoji not in MOOD_MAP:
            raise AppException(ErrorCode.INVALID_EMOJI, "无效的情绪表情")

        # 0. 确保用户存在（后续时区/配额判断依赖 user 记录）；
        #    行锁串行化：同一用户的并发签到在本地日判重前排队，防双击双记
        user = await user_repository.get_or_create_user(db, user_id, for_update=True)
        day_start = local_day_start_utc(user.timezone)

        # 1. 配额（B2）：签到解读走统一池口径（仅主动消息 + 签到解读），不叠加对话轮数
        if await quota_service.is_proactive_quota_exceeded(db, user):
            raise AppException(ErrorCode.QUOTA_EXCEEDED, "今天的次数已经用完，请明天再来")

        # 2. 更新 Streak（同日重复签到幂等，不重复累加）
        streak = await user_service.update_streak(db, user_id)

        label = mood_label_for(emoji, language)

        # 3. 生成 AI 解读（规则引擎即时 + 可选 LLM，失败回退）
        interpretation = await self._interpret(
            db, user_id, emoji, note, language, label, ai_consent=user.ai_consent
        )

        # 4. 写库：签到每日可多次，但同一本地日仅一条记录——已存在则更新
        existing = await mood_repository.get_today(db, user_id, day_start)
        if existing:
            record = await mood_repository.update(db, existing, emoji, label, interpretation)
        else:
            record, created = await mood_repository.create_once_today(
                db, user_id, emoji, label, interpretation, day_start
            )
            if not created:
                # 并发兜底（SQLite 无行锁）：另一请求已先写入，更新该记录
                record = await mood_repository.update(db, record, emoji, label, interpretation)

        # 5. 若带备注，沉淀为一条记忆（记忆注入）
        if note and note.strip():
            await self._record_memory(db, user_id, note, emoji, label)

        # 6. 深度解读（连续 3 天同情绪 / 连续 2 天负面触发；失败降级 None）
        deep = await self._deep_interpretation(db, user, language)

        return MoodCheckinResponse(
            id=record.id,
            mood_emoji=record.mood_emoji,
            mood_label=record.mood_label,
            ai_interpretation=record.ai_interpretation,
            streak_updated=streak["streak_count"],
            created_at=record.created_at,
            deep_interpretation=deep,
        )

    async def _interpret(
        self,
        db: AsyncSession,
        user_id: str,
        emoji: str,
        note: Optional[str],
        language: str,
        label: str,
        ai_consent: bool = False,
    ) -> str:
        """生成情绪解读：LLM 优先，失败回退规则引擎。

        合规 5.1.2(i)：未同意 AI 数据共享时跳过 LLM（签到记录本身保留），
        只用本地规则模板，用户数据不出服务器。
        """
        zh = (language or "").startswith("zh")
        base = RULE_INTERPRET["zh" if zh else "en"].get(
            emoji, "今天心情如何？" if zh else "How are you feeling today?"
        )
        if not ai_consent:
            return base
        if not note or not note.strip():
            return base
        try:
            prompt = (
                f"用户今天签到情绪为「{label}」{emoji}，备注：{note}\n"
                "请用2-3句话给一句温暖、真诚的回应，不要空洞，不要提建议过度。\n"
                f"{safety_service.boundary(language)}"
            )
            text = await llm.complete(
                prompt,
                language=language,
                lite=True,
                max_tokens=80,
                temperature=0.7,
            )
            # L3 输出过滤：命中回退规则引擎模板
            return await safety_service.check_or(text.strip() or base, language, base)
        except Exception as exc:  # noqa: BLE001  LLM 失败不阻塞签到
            logger.warning("mood LLM 解读失败，回退规则引擎: %s", exc)
            return base

    async def _deep_interpretation(
        self, db: AsyncSession, user: User, language: str
    ) -> Optional[str]:
        """深度解读（PRD §2）：连续 3 天同一情绪 OR 连续 2 天负面（情绪值 ≤2）时触发。

        用 lite LLM 结合近期记忆 + 情绪趋势生成 2-3 句深度解读；
        未触发 / 未同意 AI 数据共享 / LLM 失败均返回 None，不影响签到主流程。
        """
        if not user.ai_consent:
            return None
        trigger = await self._deep_trigger_days(db, user, language)
        if trigger is None:
            return None
        days_desc, zh = trigger
        try:
            memories = await memory_repository.list(db, user.id, limit=5)
            memory_desc = "\n".join(f"- {m.content}" for m in memories) or (
                "暂无记忆。" if zh else "No memories yet."
            )
            prompt = (
                "用户最近连续几天的情绪签到出现了值得关注的模式：\n"
                f"{days_desc}\n"
                f"最近记忆：\n{memory_desc}\n"
                "请结合情绪趋势与近期记忆，生成 2-3 句深度解读：指出这个模式，"
                "语气温和真诚，像朋友一样自然地邀请用户聊聊，不要说教、不要空泛。\n"
                f"{safety_service.boundary(language)}"
            )
            text = await llm.complete(
                prompt,
                language=language,
                lite=True,
                max_tokens=150,
                temperature=0.7,
            )
            # L3 输出过滤：命中降级为 None（不影响签到主流程）
            return await safety_service.check_or(text.strip() or "", language, "") or None
        except Exception as exc:  # noqa: BLE001  深度解读失败不影响签到
            logger.warning("mood 深度解读失败，降级为 None: %s", exc)
            return None

    async def _deep_trigger_days(
        self, db: AsyncSession, user: User, language: str
    ) -> Optional[tuple[str, bool]]:
        """检测深度解读触发条件，返回 (签到模式描述, 是否中文)；未触发返回 None。

        连续按用户本地日历日判定（同日多条取最新一条，正常一日一条）。
        """
        recent = await mood_repository.get_recent(db, user.id, limit=4)
        if len(recent) < 2:
            return None
        zone = safe_zoneinfo(user.timezone)
        day_moods: List[tuple] = []
        seen = set()
        for r in recent:
            aware = r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=timezone.utc)
            d = aware.astimezone(zone).date()
            if d in seen:
                continue
            seen.add(d)
            day_moods.append((d, r.mood_emoji))
        if not day_moods or day_moods[0][0] != local_today(user.timezone):
            return None

        zh = (language or "").startswith("zh")

        def _label(emoji: str) -> str:
            return mood_label_for(emoji, language)

        # 连续 3 天同一情绪（今天 + 前两个连续日历日）
        if len(day_moods) >= 3:
            (d0, e0), (d1, e1), (d2, e2) = day_moods[:3]
            if (d0 - d1).days == 1 and (d1 - d2).days == 1 and e0 == e1 == e2:
                desc = (
                    f"连续 3 天选择「{_label(e0)}」{e0}" if zh
                    else f"Chose \"{_label(e0)}\" {e0} for 3 consecutive days"
                )
                return desc, zh
        # 连续 2 天负面（MOOD_MAP 情绪值 ≤ 2）
        (d0, e0), (d1, e1) = day_moods[0], day_moods[1]
        if (d0 - d1).days == 1:
            negative = all(MOOD_MAP.get(e, (None, 3))[1] <= 2 for e in (e0, e1))
            if negative:
                desc = (
                    f"连续 2 天负面情绪（{_label(e1)}{e1} → {_label(e0)}{e0}）" if zh
                    else f"Negative moods 2 days in a row ({_label(e1)} {e1} → {_label(e0)} {e0})"
                )
                return desc, zh
        return None

    async def _record_memory(
        self, db: AsyncSession, user_id: str, note: str, emoji: str, label: str
    ) -> None:
        """把签到备注沉淀为一条记忆（类别 preference，情绪上下文）。"""
        try:
            from backend.services.memory_service import memory_service
            await memory_service.create_memory(
                db,
                user_id,
                content=note,
                category="preference",
                source="self",
                conviction=0.4,
                emotion_context=emoji,
            )
        except Exception as exc:  # noqa: BLE001  记忆写入失败不影响签到响应
            logger.warning("签到记忆沉淀失败: %s", exc)

    async def get_history(self, db: AsyncSession, user_id: str) -> MoodHistoryResponse:
        """获取最近签到历史。"""
        records = await mood_repository.get_recent(db, user_id, limit=30)
        return MoodHistoryResponse(
            checkins=[
                MoodCheckinResponse(
                    id=r.id,
                    mood_emoji=r.mood_emoji,
                    mood_label=r.mood_label or "",
                    ai_interpretation=r.ai_interpretation or "",
                    streak_updated=0,
                    created_at=r.created_at,
                )
                for r in records
            ]
        )

    async def get_trend(self, db: AsyncSession, user_id: str, days: int = 14, language: str = "en") -> MoodTrendResponse:
        """获取情绪趋势（按天聚合，取每日首个情绪值；总结文案按语言）。"""
        user = await user_repository.get_user(db, user_id)
        rows = await mood_repository.count_by_day(
            db, user_id, days, tz=user.timezone if user else None
        )
        # 按天取首个情绪值
        day_map: dict = {}
        for day, emoji in rows:
            # day 是 date 对象
            key = day.strftime("%m-%d") if hasattr(day, "strftime") else str(day)
            if key not in day_map:
                val = MOOD_MAP.get(emoji, (None, 3))[1]
                day_map[key] = val

        labels = list(day_map.keys())
        values = list(day_map.values())
        summary = self._trend_summary(values, language)
        return MoodTrendResponse(labels=labels, values=values, summary=summary)

    def _trend_summary(self, values: List[int], language: str | None = None) -> str:
        zh = (language or "").startswith("zh")
        if not values:
            return (
                "还没有足够的签到数据，开始记录你的第一天吧。" if zh
                else "Not enough check-in data yet — start recording your first day."
            )
        avg = sum(values) / len(values)
        if avg >= 4:
            return (
                "整体情绪状态不错，保持这个节奏。" if zh
                else "Your overall mood has been great — keep the rhythm going."
            )
        if avg >= 3:
            return (
                "情绪总体平稳，偶尔关注自己的感受。" if zh
                else "Your mood is mostly steady. Check in with yourself now and then."
            )
        return (
            "这段时间情绪有些低落，记得多照顾自己，我在这里。" if zh
            else "Things have felt a bit low lately. Take extra care of yourself — I'm here."
        )


mood_service = MoodService()