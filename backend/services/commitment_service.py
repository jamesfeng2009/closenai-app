"""承诺捕获与回访调度（PRD §5.18 承诺回访，C3 收尾）。

原则（PRD §5.18 合规边界）：
- 只记录不当场确认：Rule Engine 关键词抽取（$0 成本、零 LLM），不打断对话
- 回访永不变成催促：每用户每周 ≤2 条；同一承诺 ≤2 次；负向回复 → muted 沉默陪伴
- 不做打卡：不打勾、不完成率、不任务列表——承诺状态只随记忆行（labels JSON，无新表）

labels JSON 结构（memories.labels TEXT 列）：
    {"labels": ["commitment"],
     "commitment": {"kind": "habit"|"event", "follow_up_count": 0, "muted": false}}

调度：捕获时预写首条 journey_events（kind=commitment_follow_up，习惯类捕获后 7 天、
事件类次日，落用户本地推送窗口）；投递成功后由 journey_service 回调
on_follow_up_delivered 决定是否预写下一条。负向回复把最近被回访的承诺 muted，
pending 事件到点后由渲染层 skip（只顺延/跳过，永不删行）。
"""
import json
import logging
import re
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.time_utils import local_today, safe_zoneinfo
from backend.models.user import User
from backend.repositories import (
    journey_repository,
    memory_repository,
    user_repository,
)
from backend.repositories.journey_repository import parse_json
from backend.services.memory_service import memory_service
from backend.services.safety_service import safety_service

logger = logging.getLogger("closenai.commitment")

# 频控（PRD §5.18）：每用户每周 ≤2 条回访；同一承诺 ≤2 次
COMMITMENT_WEEKLY_LIMIT = 2
COMMITMENT_MAX_FOLLOW_UPS = 2

# 原话摘录上限（与 journey_service._clean 同口径）
EXCERPT_MAX = 80

# 意图表述关键词（Rule Engine，$0 成本）。中英各一组，命中即候选承诺
INTENT_PATTERNS_ZH = (
    "我想开始", "我要开始", "想开始", "打算", "计划", "准备", "想尝试", "想试着",
    "想养成", "想坚持", "决定", "要少", "想少", "想戒", "戒掉", "不再",
)
INTENT_PATTERNS_EN = (
    "i want to start", "i'm going to", "im going to", "i am going to", "i plan to",
    "i'll try", "i will try", "i decided to", "trying to", "want to quit",
    "cut down on", "i'd like to start",
)

# 事件类标记：出现具体时间点 → 一次性事件（其余缺省习惯类，回访更晚、更不催促）
EVENT_MARKERS_ZH = ("明天", "后天", "下周", "下个月", "月底", "月初")
EVENT_RE_ZH = re.compile(r"周[一二三四五六日天]|\d{1,2}月\d{1,2}[日号]")
EVENT_MARKERS_EN = ("tomorrow", "next week", "next month", "end of the month")
EVENT_RE_EN = re.compile(r"(on|next|this) (mon|tues|wednes|thurs|fri|satur|sun)day", re.I)

# 负向退出话术（PRD §5.18：用户回应负向 → 立即停止该承诺回访）
NEGATIVE_PATTERNS_ZH = ("别问了", "别提了", "不要问了", "别再问了", "没做别问", "不想说这个")
NEGATIVE_PATTERNS_EN = ("stop asking", "don't ask", "dont ask", "quit asking", "drop it")

_SENT_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")


def _clean(text: str) -> str:
    """压平空白并截断（承诺原话进正文/引用前的统一处理）。"""
    text = " ".join((text or "").split()).strip()
    if len(text) > EXCERPT_MAX:
        text = text[:EXCERPT_MAX].rstrip() + "…"
    return text


def _is_zh(language: Optional[str]) -> bool:
    return (language or "").lower().startswith("zh")


class CommitmentService:
    """承诺捕获（Rule 抽取）与回访调度（journey_events 预写 + 频控状态）。"""

    COMMITMENT_WEEKLY_LIMIT = COMMITMENT_WEEKLY_LIMIT
    COMMITMENT_MAX_FOLLOW_UPS = COMMITMENT_MAX_FOLLOW_UPS
    # 回访间隔（用户本地日历日）：习惯类捕获后 7 天、事件类次日；
    # 第二次回访在上一次实际投递日基础上按同型间隔顺延（habit 隔周、event 隔天）
    FIRST_OFFSET = {"habit": 7, "event": 1}
    NEXT_INTERVAL = {"habit": 7, "event": 2}

    # ── 抽取 ──────────────────────────────────────────────────────────

    def _intent_sentence(self, message: str, language: Optional[str]) -> Optional[str]:
        """取第一个含意图表述的句子（无则 None）。每条消息最多捕获一条承诺。

        意图形态（PRD §5.18）："我想开始……""打算……""要少……"以及"下周……"
        等带具体时间点的行动计划（时间词本身即触发，宁可多记、由回访频控兜底）。
        """
        zh = _is_zh(language)
        intent = INTENT_PATTERNS_ZH if zh else INTENT_PATTERNS_EN
        markers = EVENT_MARKERS_ZH if zh else EVENT_MARKERS_EN
        marker_re = EVENT_RE_ZH if zh else EVENT_RE_EN
        for part in _SENT_SPLIT_RE.split(message or ""):
            sentence = part.strip()
            if not sentence:
                continue
            lowered = sentence.lower()
            hit = (
                any(p in lowered for p in intent)
                or any(m in (sentence if zh else lowered) for m in markers)
                or bool(marker_re.search(sentence if zh else lowered))
            )
            if hit:
                return sentence
        return None

    def _classify(self, sentence: str, language: Optional[str]) -> str:
        """习惯类 / 事件类：出现具体时间词 → event，缺省 habit（回访更晚更不催促）。"""
        lowered = sentence.lower()
        if _is_zh(language):
            if any(m in sentence for m in EVENT_MARKERS_ZH) or EVENT_RE_ZH.search(sentence):
                return "event"
            return "habit"
        if any(m in lowered for m in EVENT_MARKERS_EN) or EVENT_RE_EN.search(lowered):
            return "event"
        return "habit"

    # ── labels JSON ──────────────────────────────────────────────────

    def parse_labels(self, raw: Optional[str]) -> dict:
        """memories.labels 反序列化（坏数据/空值回退空对象，不抛异常）。"""
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}

    async def _merge_labels(
        self, db: AsyncSession, memory_id: str, user_id: str, commitment: dict
    ) -> None:
        """把承诺标签与调度状态合并写回记忆行（保留既有标签）。"""
        row = await memory_repository.get(db, memory_id, user_id)
        if row is None:
            return
        labels = self.parse_labels(row.labels)
        labels["labels"] = sorted(set(labels.get("labels") or []) | {"commitment"})
        labels["commitment"] = commitment
        await memory_repository.update_labels(
            db, memory_id, user_id, json.dumps(labels, ensure_ascii=False)
        )

    # ── 捕获入口 ─────────────────────────────────────────────────────

    async def on_user_message(self, db: AsyncSession, user_id: str, message: str) -> Optional[str]:
        """用户消息入口（chat 流尾部调用，与记忆提取并列，全程降级不阻断）。

        先处理负向退出（退出优先于捕获），再做承诺捕获。返回捕获的记忆 id 或 None。
        """
        try:
            user = await user_repository.get_user(db, user_id)
            if user is None:
                return None
            await self.handle_negative_reply(db, user, message)
            return await self.capture_from_message(db, user, message)
        except Exception as exc:  # noqa: BLE001 - 承诺链路故障不影响对话主流程
            logger.warning("承诺处理失败 user=%s: %s", user_id, exc)
            return None

    async def capture_from_message(
        self, db: AsyncSession, user: User, message: str
    ) -> Optional[str]:
        """Rule 抽取意图表述 → commitment 记忆 + 首条回访预写。返回记忆 id 或 None。"""
        sentence = self._intent_sentence(message, user.language)
        if not sentence:
            return None
        sentence = _clean(sentence)
        # 危机内容不做承诺素材（§5.16 转介流程照常生效）
        if not sentence or safety_service.crisis_intercept(
            sentence, user.language, user.timezone
        ):
            return None

        response = await memory_service.create_memory(
            db, user.id, content=sentence, category="goal", source="self",
            conviction=0.5, language=user.language or "en",
        )
        row = await memory_repository.get(db, response.id, user.id)
        if row is None:
            return response.id

        existing = self.parse_labels(row.labels).get("commitment")
        commitment = (
            dict(existing)
            if isinstance(existing, dict)
            else {"kind": self._classify(sentence, user.language), "follow_up_count": 0, "muted": False}
        )
        commitment.setdefault("kind", "habit")
        commitment.setdefault("follow_up_count", 0)
        commitment.setdefault("muted", False)
        await self._merge_labels(db, row.id, user.id, commitment)

        # 重复表达（dedup 命中既有承诺）不重复预写；已退出的承诺只记录不回访
        if not commitment["muted"] and not await self._has_pending_follow_up(db, user.id, row.id):
            first = self.FIRST_OFFSET.get(commitment["kind"], 7)
            await self._schedule_follow_up(
                db, user, row.id, day_no=first, interval_days=first,
                captured_day=local_today(user.timezone).isoformat(),
                excerpt=sentence,
            )
        return row.id

    # ── 负向退出 ─────────────────────────────────────────────────────

    async def handle_negative_reply(self, db: AsyncSession, user: User, message: str) -> bool:
        """负向回复 → 最近被回访的承诺 muted（沉默陪伴：继续记录，不再主动提）。

        返回是否命中退出。pending 的下一条回访到点后由渲染层 skip。
        """
        patterns = NEGATIVE_PATTERNS_ZH if _is_zh(user.language) else NEGATIVE_PATTERNS_EN
        lowered = (message or "").lower()
        if not any(p in lowered for p in patterns):
            return False
        event = await journey_repository.last_sent_kind(db, user.id, "commitment_follow_up")
        if event is None:
            return False
        payload = parse_json(event.payload, {})
        memory_id = payload.get("memory_id") if isinstance(payload, dict) else None
        row = await memory_repository.get(db, memory_id, user.id) if memory_id else None
        if row is None:
            return False
        labels = self.parse_labels(row.labels)
        commitment = labels.get("commitment")
        if not isinstance(commitment, dict):
            commitment = {"kind": "habit", "follow_up_count": 0, "muted": False}
        if commitment.get("muted"):
            return True
        commitment["muted"] = True
        await self._merge_labels(db, row.id, user.id, commitment)
        logger.info("承诺回访负向退出 user=%s memory=%s", user.id, row.id)
        return True

    # ── 调度 ─────────────────────────────────────────────────────────

    async def weekly_sent_count(self, db: AsyncSession, user: User) -> int:
        """本周（用户本地自然周，周一起）已发送的承诺回访条数（频控依据）。"""
        zone = safe_zoneinfo(user.timezone)
        today = local_today(user.timezone)
        monday = today - timedelta(days=today.weekday())
        week_start = datetime.combine(monday, dt_time.min, tzinfo=zone).astimezone(
            timezone.utc
        )
        return await journey_repository.count_sent_kind_since(
            db, user.id, "commitment_follow_up", week_start
        )

    async def on_follow_up_delivered(self, db: AsyncSession, user: User, event) -> None:
        """投递成功钩子（journey_service.deliver 在 mark_sent 后调用）。

        follow_up_count +1；未达上限且未退出时预写下一条（同型间隔顺延）。
        """
        payload = parse_json(event.payload, {})
        if not isinstance(payload, dict):
            return
        memory_id = payload.get("memory_id")
        if not memory_id:
            return
        row = await memory_repository.get(db, memory_id, user.id)
        if row is None:
            return
        labels = self.parse_labels(row.labels)
        commitment = labels.get("commitment")
        if not isinstance(commitment, dict):
            commitment = {"kind": "habit", "follow_up_count": 0, "muted": False}
        commitment["follow_up_count"] = int(commitment.get("follow_up_count") or 0) + 1
        await self._merge_labels(db, memory_id, user.id, commitment)
        if commitment["muted"] or commitment["follow_up_count"] >= COMMITMENT_MAX_FOLLOW_UPS:
            return  # 同承诺 ≤2 次或已退出：沉默陪伴，不再预写
        if not await self._has_pending_follow_up(db, user.id, memory_id):
            interval = self.NEXT_INTERVAL.get(commitment["kind"], 7)
            await self._schedule_follow_up(
                db, user, memory_id,
                day_no=int(event.day_no or 0) + interval,
                interval_days=interval,
                captured_day=payload.get("captured_day"),
            )

    async def _has_pending_follow_up(
        self, db: AsyncSession, user_id: str, memory_id: str
    ) -> bool:
        """该承诺是否已有 pending 回访（承诺回访绕过 day_no 幂等，靠此查重防重写）。"""
        for event in await journey_repository.pending_events_for_kind(
            db, user_id, "commitment_follow_up"
        ):
            payload = parse_json(event.payload, {})
            if isinstance(payload, dict) and payload.get("memory_id") == memory_id:
                return True
        return False

    async def _schedule_follow_up(
        self,
        db: AsyncSession,
        user: User,
        memory_id: str,
        day_no: int,
        interval_days: int,
        captured_day: Optional[str] = None,
        excerpt: Optional[str] = None,
    ) -> None:
        """预写一条承诺回访（kind=commitment_follow_up，落本地推送窗口）。

        计划时刻 = 当前本地日 + interval_days 的推送窗口（投递相对节律，顺延自然后移）；
        payload.captured_day 记录承诺原捕获日，供回放措辞 "{d0} 你说过" 使用；
        payload.excerpt 便于事件列表排查。dedupe=False：同一承诺第二条的 day_no 递增、
        不同承诺同日捕获的 day_no 相同，(user, kind, day_no) 幂等键不适用，
        防重写由 _has_pending_follow_up 保证。
        """
        scheduled = self._push_moment_utc(
            user, local_today(user.timezone) + timedelta(days=interval_days)
        )
        await journey_repository.schedule(
            db,
            user_id=user.id,
            event_kind="commitment_follow_up",
            day_no=day_no,
            scheduled_at=scheduled,
            payload={
                "memory_id": memory_id,
                "captured_day": captured_day,
                "excerpt": excerpt,
            },
            dedupe=False,
        )

    def _push_moment_utc(self, user: User, day: date) -> datetime:
        """用户本地 `day` 的推送窗口时刻（UTC），与 journey_service 同口径。"""
        zone = safe_zoneinfo(user.timezone)
        try:
            hh, mm = (user.push_time or "09:00").split(":")
            hour, minute = min(max(int(hh), 0), 23), min(max(int(mm), 0), 59)
        except (ValueError, IndexError):
            hour, minute = 9, 0
        return datetime.combine(day, dt_time(hour, minute), tzinfo=zone).astimezone(
            timezone.utc
        )


commitment_service = CommitmentService()
