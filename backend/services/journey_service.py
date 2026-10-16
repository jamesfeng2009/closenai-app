"""
第一周剧本服务（PRD §5.19.3 P1-B02 / 架构方案 §二 M2）。

职责：剧本事件的编排（预写 pending）与内容渲染（模板直出 + 引用依据）。
分批调度与事务边界在 CronService.dispatch_journey_events()（架构方案 §二 M2 命名的入口）。

三条硬约定：
1. **预写而非现算**：onboarding 完成事务内一次性写入 D0–D7 的 pending 行
   （架构方案 §五 决策点 1）——可审计、可测试、可按用户查出整条剧本。
2. **引用依据（SLA 工程化，§二 M2 / §六-3）**：正文引用用户原话时必带
   memory_refs=[{memory_id, created_at, excerpt}]，且每个 memory_id 必须真实存在并属于该用户
   （引用错误 = P1 级 bug，pytest 强制）；纯统计类观察不编造引用，其口径落 payload.evidence。
3. **只顺延、不删行**（§5.19.3 频控铁律）：用户沉默、当日已有触达、危机停发 → defer；
   仅"条件不满足"（如 D7 凑不出 2 条原话）→ skipped。

措辞：全部模板直出（§五 决策点 6），渲染出口过 safety_service.filter_template（§六-1）。
"""
import hashlib
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import AppException, ErrorCode
from backend.core.time_utils import (
    local_day_start_utc,
    local_now,
    local_today,
    safe_zoneinfo,
    utc_now,
)
from backend.models.memory import Memory
from backend.models.user import User
from backend.repositories import (
    chat_repository,
    journey_repository,
    memory_repository,
    mood_repository,
    onboarding_repository,
    safety_repository,
    user_repository,
)
from backend.repositories.journey_repository import parse_json
from backend.services.commitment_service import commitment_service
from backend.services.mood_service import mood_label_for
from backend.services.push import push_copywriter, push_service
from backend.ai.safety import safety_service

logger = logging.getLogger("closenai.journey")

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "journey"
_LOCALES = ("zh", "en")
_DEFAULT_LOCALE = "en"
_TEMPLATES: Dict[str, dict] = {}

# D0–D7 剧本时刻表（PRD §5.19.3：D4/D6 无事件，日序即跳过）
SCRIPT_EVENTS: Tuple[Tuple[str, int], ...] = (
    ("day0_read", 0),
    ("day1_checkin_echo", 1),
    ("day2_first_outreach", 2),
    ("day3_weak_assert", 3),
    ("day5_stat", 5),
    ("day7_report", 7),
)

# 走推送的事件（§5.19.3 剧本仅 D2；§5.18 承诺回访 push 深链到对话页，周频控 ≤2 条；
# §5.21 试用 D5 资产盘点也是触达，受同一条频控链约束）
PUSH_KINDS = ("day2_first_outreach", "commitment_follow_up", "trial_d5")

# 试用 D5 触点（PRD §5.21）：试用第 5 天资产盘点；盘点窗口 = 试用一周
TRIAL_D5_OFFSET = 5
TRIAL_D5_WINDOW_DAYS = 7
TRIAL_D5_MAX_MOODS = 3

# 沉默降频：连续 N 天未留下记录 → 剧本顺延隔天（§5.19.3「降频为隔天，永不删除」）
SILENT_DEFER_DAYS = 2
DEFER_DAYS = 1

# 弱断言最低数据量（PRD §5.19.3 D3 = "基于 2 天数据"）
WEAK_ASSERT_DAYS = 3
WEAK_ASSERT_MIN_DAYS_WITH_DATA = 2
WEAK_ASSERT_MIN_NIGHT = 2
WEAK_ASSERT_NIGHT_RATIO = 0.6

# D7 报告必须引用 2 条原话（§5.19.3）；引用条数上限（正文不堆砌）
REPORT_QUOTES = 2
EXCERPT_MAX = 80
# D5 "被记住的句子"变体的最低记忆条数（低于它这条观察没有信息量）
DAY5_MIN_MEMORIES = 3

# "晚上" 判定区间（D3/D5 的数据性观察口径，与模板 window 文案对应）
NIGHT_START_HOUR = 19
NIGHT_END_HOUR = 5

# 引用槽位在过闸时的惰性占位符：用户原话不进 AI 文案闸（见模板库 _comment）
_QUOTE_KEYS = ("q0", "q1")


def _quote_token(key: str) -> str:
    """每个引用槽位一个独立占位符，回填时不会串位。"""
    return "⟦" + key + "⟧"


def locale_for(language: Optional[str]) -> str:
    return "zh" if (language or "").lower().startswith("zh") else "en"


def load_template(language: Optional[str]) -> dict:
    """加载模板库（进程内缓存；非 zh 一律回退 en，与前端 i18n fallback 同口径）。"""
    locale = locale_for(language)
    if locale not in _TEMPLATES:
        path = DATA_DIR / f"{locale}.json"
        if not path.exists():
            if locale == _DEFAULT_LOCALE:
                logger.error("剧本模板库缺失：%s", path)
                raise AppException(ErrorCode.INTERNAL_ERROR, "Journey templates missing")
            return load_template(_DEFAULT_LOCALE)
        try:
            _TEMPLATES[locale] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.error("剧本模板库解析失败 %s: %s", path, exc)
            raise AppException(ErrorCode.INTERNAL_ERROR, "Journey templates unreadable")
    return _TEMPLATES[locale]


def _clean(value: Optional[str]) -> str:
    """压平空白并截断（原话进正文/引用依据前的统一处理，避免换行破坏版式）。"""
    text = " ".join((value or "").split()).strip()
    if len(text) > EXCERPT_MAX:
        text = text[:EXCERPT_MAX].rstrip() + "…"
    return text


def _local_date(dt: datetime, zone) -> date:
    aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(zone).date()


@dataclass
class Rendered:
    """一条事件的可投递渲染结果。"""

    text: str
    memory_refs: List[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    quotes: List[dict] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    title: Optional[str] = None
    reward_key: Optional[str] = None


class JourneyService:
    """第一周剧本（journey_events）编排与渲染。"""

    # ── 预写 ──────────────────────────────────────────────────────────

    async def prefill(
        self, db: AsyncSession, user: User, base_day: Optional[date] = None
    ) -> int:
        """在 onboarding 完成事务内预写 D0–D7 pending 行，返回写入行数。

        base_day = 剧本第 0 天（默认用户本地今日）。已过去的剧本日不追溯补发
        （"第一周"的时刻表过了就是过了），直接置 skipped——永不删行，审计仍在。
        幂等由仓库层 (user_id, event_kind, day_no) 查重保证：重放/重试不产生第二条。
        """
        day0 = base_day or local_today(user.timezone)
        today = local_today(user.timezone)
        written = 0
        for kind, offset in SCRIPT_EVENTS:
            scheduled = self._push_moment(user, day0 + timedelta(days=offset))
            event = await journey_repository.schedule(
                db,
                user_id=user.id,
                event_kind=kind,
                day_no=offset,
                scheduled_at=scheduled,
                payload={"kind": kind, "day_no": offset, "language": user.language or "en"},
            )
            written += 1
            if day0 + timedelta(days=offset) < today and event.status == "pending":
                await journey_repository.skip(db, event.id)
        return written

    async def ensure_prefilled(self, db: AsyncSession, user_id: str) -> None:
        """补写入口：老用户（B2 上线前完成 onboarding）首次打开剧本接口时补齐剧本。

        基准日取初读卡的生成日（= onboarding 完成日），无卡则退回账号创建日。
        """
        user = await user_repository.get_user(db, user_id)
        if user is None:
            return
        profile = await onboarding_repository.get(db, user_id)
        base = (profile.created_at if profile and profile.created_at else user.created_at)
        if base is None:
            return
        await self.prefill(db, user, base_day=_local_date(base, safe_zoneinfo(user.timezone)))

    async def schedule_trial_d5(self, db: AsyncSession, user: User, trial_start: datetime) -> None:
        """试用第 5 天资产盘点触点预写（PRD §5.21 D5）。

        trial_start = 试用开始时刻（UTC，iap 侧按 expires_at - TRIAL_LENGTH_DAYS 折算）；
        落点 = 开始日（用户本地日）+5 天的本地推送窗口。时刻已过（恢复购买时试用已过
        第 5 天）不追溯补发；幂等由 (user, trial_d5, 5) 仓库查重保证——Apple intro
        offer 一人一次，重放 verify 不产生第二条。
        """
        aware = trial_start if trial_start.tzinfo else trial_start.replace(tzinfo=timezone.utc)
        start_day = _local_date(aware, safe_zoneinfo(user.timezone))
        scheduled_at = self._push_moment(user, start_day + timedelta(days=TRIAL_D5_OFFSET))
        if scheduled_at <= utc_now():
            return
        await journey_repository.schedule(
            db,
            user_id=user.id,
            event_kind="trial_d5",
            day_no=TRIAL_D5_OFFSET,
            scheduled_at=scheduled_at,
            payload={
                "kind": "trial_d5",
                "day_no": TRIAL_D5_OFFSET,
                "language": user.language or "en",
            },
        )

    def _push_moment(self, user: User, day: date) -> datetime:
        """剧本日 `day` 的用户本地推送窗口时刻（UTC）。

        与 §5.19.3 一致：主动触达只落在用户偏好时刻，不用服务器时钟的"现在"。
        不加 cron 主动消息那样的分钟抖动——那层抖动是为 LLM 成本削峰，剧本零 LLM。
        """
        zone = safe_zoneinfo(user.timezone)
        try:
            hh, mm = (user.push_time or "09:00").split(":")
            hour, minute = min(max(int(hh), 0), 23), min(max(int(mm), 0), 59)
        except (ValueError, IndexError):
            hour, minute = 9, 0
        return datetime.combine(day, dt_time(hour, minute), tzinfo=zone).astimezone(
            timezone.utc
        )

    def _next_window(self, user: User) -> datetime:
        """下一个推送窗口（今天已过则明天），用于顺延。"""
        now_local = local_now(user.timezone)
        day = now_local.date() + timedelta(days=1) if self._push_moment(
            user, now_local.date()
        ) <= utc_now() else now_local.date()
        return self._push_moment(user, day)

    # ── 调度投递 ──────────────────────────────────────────────────────

    async def deliver(self, db: AsyncSession, event_id: str) -> str:
        """投递一条到期事件。返回 "sent" / "skipped" / "deferred"。

        事务边界由调用方（cron 调度循环）控制，异常向上抛出由其回滚。
        """
        event = await journey_repository.get(db, event_id)
        if event is None or event.status != "pending":
            return "skipped"
        user = await user_repository.get_user(db, event.user_id)
        if user is None:
            await journey_repository.skip(db, event.id)
            return "skipped"
        # 合规 5.1.2(i)：未同意 AI 数据共享 → 不做主动触达（与主动消息/跟进同口径）
        if not user.ai_consent:
            await journey_repository.skip(db, event.id)
            return "skipped"

        day_start = local_day_start_utc(user.timezone)
        day_end = day_start + timedelta(days=1)

        # 频控 1：每个本地日至多一条剧本触达（顺延，不删行）
        if await journey_repository.has_sent_on(db, user.id, day_start, day_end):
            return await self._defer(db, event, user, "今日已有剧本触达")
        # 频控 2：与 LLM 主动消息互斥，同日不叠发两条 assistant 开场
        if await chat_repository.count_today(
            db, user.id, role="assistant", message_type="proactive", start=day_start
        ):
            return await self._defer(db, event, user, "今日已有主动消息")
        # §5.16 ④：危机转介命中当日停推（停发≠取消，顺延到下一个窗口）
        if await safety_repository.has_hit_since(db, user.id, day_start):
            return await self._defer(db, event, user, "危机转介命中当日")
        # 沉默降频（§5.19.3）
        if await self._days_silent(db, user) >= SILENT_DEFER_DAYS:
            return await self._defer(db, event, user, "用户沉默，降频为隔天")
        # 承诺回访（§5.18）周频控：每用户每周 ≤2 条，超出顺延一周（pending 保留）
        if (
            event.event_kind == "commitment_follow_up"
            and await commitment_service.weekly_sent_count(db, user)
            >= commitment_service.COMMITMENT_WEEKLY_LIMIT
        ):
            return await self._defer(db, event, user, "承诺回访周频控", days=7)

        rendered = await self.render(db, event, user)
        if rendered is None:
            # 条件不满足（数据不足）：置 skipped 不重排——泛泛问候不如不发
            await journey_repository.skip(db, event.id)
            return "skipped"

        message = await chat_repository.create(
            db,
            user.id,
            "assistant",
            rendered.text,
            message_type="proactive",
            ai_generated=False,
            memory_ids=[r["memory_id"] for r in rendered.memory_refs] or None,
        )
        await journey_repository.set_payload(
            db,
            event.id,
            payload={
                # 保留预写期的承诺定位键（memory_id/captured_day 等），投递期字段覆盖
                **(parse_json(event.payload, {}) if isinstance(event.payload, str) else {}),
                "kind": event.event_kind,
                "day_no": event.day_no,
                "language": locale_for(user.language),
                "title": rendered.title,
                "body": rendered.text,
                "stats": rendered.stats,
                "quotes": rendered.quotes,
                "evidence": rendered.evidence,
                "reward_key": rendered.reward_key,
                "message_id": message.id,
            },
            memory_refs=rendered.memory_refs,
        )
        await journey_repository.mark_sent(db, event.id, utc_now())

        if event.event_kind == "commitment_follow_up":
            # §5.18：回访计数 +1 并按同型间隔预写下一条（同承诺 ≤2 次，muted 即止）
            await commitment_service.on_follow_up_delivered(db, user, event)
            # 消费面在对话流（研发标注"前端零新界面"）：发送即已读，不进报告页红点
            await journey_repository.acknowledge(db, event.id)

        if event.event_kind in PUSH_KINDS and user.push_enabled and user.push_frequency != "off":
            await self._push(db, user, rendered.text)
        return "sent"

    async def _defer(
        self, db: AsyncSession, event, user: User, reason: str, days: int = DEFER_DAYS
    ) -> str:
        """顺延一条事件（永不删行）。days=7 用于承诺回访周频控（顺延一周）。"""
        until = event.scheduled_at
        if until is not None:
            aware = until if until.tzinfo else until.replace(tzinfo=timezone.utc)
            until = aware + timedelta(days=days)
        now = utc_now()
        if until is None or until <= now:
            until = self._next_window(user)
        await journey_repository.defer(db, event.id, until)
        logger.info("剧本事件顺延 user=%s kind=%s 原因=%s", user.id, event.event_kind, reason)
        return "deferred"

    async def _push(self, db: AsyncSession, user: User, text: str) -> None:
        """剧本推送（仅 D2）：复用主动消息推送场景，正文即消息本身；失败安全降级。"""
        try:
            title, body = await push_copywriter.build_copy(
                db, user, "proactive", message=text
            )
            await push_service.send_to_user(
                db, user.id, title, body, data={"type": "journey"}
            )
        except Exception as exc:  # noqa: BLE001 - 推送故障不回滚已写入的消息
            logger.warning("剧本推送失败 user=%s: %s", user.id, exc)

    async def _days_silent(self, db: AsyncSession, user: User) -> int:
        """连续未留下记录的本地日历天数（无历史记录时按注册日起算）。"""
        last = await chat_repository.get_last_user_message(db, user.id)
        today = local_today(user.timezone)
        zone = safe_zoneinfo(user.timezone)
        base = _local_date(last.created_at, zone) if last else _local_date(
            user.created_at or utc_now(), zone
        )
        return max((today - base).days, 0)

    # ── 渲染 ──────────────────────────────────────────────────────────

    async def render(
        self, db: AsyncSession, event, user: User
    ) -> Optional[Rendered]:
        """按 event_kind 渲染正文与引用依据；数据不足返回 None（调用方置 skipped）。"""
        kind = event.event_kind
        if kind == "day0_read":
            return await self._render_day0(db, user)
        if kind == "day1_checkin_echo":
            return await self._render_day1(db, user)
        if kind == "day2_first_outreach":
            return await self._render_day2(db, user)
        if kind == "day3_weak_assert":
            return await self._render_day3(db, user)
        if kind == "day5_stat":
            return await self._render_day5(db, user)
        if kind == "day7_report":
            return await self._render_day7(db, user)
        if kind == "level_reward":
            return await self._render_level_reward(db, event, user)
        if kind == "commitment_follow_up":
            return await self._render_commitment_follow_up(db, event, user)
        if kind == "trial_d5":
            return await self._render_trial_d5(db, user)
        # 其它 kind 由各自任务分派，不在此处兜底发文案
        logger.info("未知剧本事件 kind=%s，跳过 user=%s", kind, user.id)
        return None

    def _t(self, language: Optional[str]) -> dict:
        return load_template(language)

    def _gate(self, text: str, language: Optional[str]) -> str:
        """渲染出口过闸（§六-1）：命中黑名单/推理越界 → 空串，调用方按"无内容可发"处理。"""
        return safety_service.filter_template(text, language or "en")

    def _format(self, tpl: str, ctx: dict, *, gating: bool) -> str:
        """槽位填充；gating=True 时把原话槽位替换为惰性占位（原话不是 AI 产出）。"""
        values = dict(ctx)
        if gating:
            for key in _QUOTE_KEYS:
                if key in values:
                    values[key] = _quote_token(key)
        return tpl.format(**values)

    def _gate_tpl(self, tpl: str, ctx: dict, language: Optional[str]) -> str:
        """填充 → 过闸 → 回填原话；返回 "" 表示模板被闸拦下（调用方按无内容处理）。

        原话只在过闸后才注入：用户写的句子不是 AI 产出，不该由它决定这条消
        息发不发（"因为 TA ..."这类句子会被 attribution 正则误杀）。
        """
        gated = self._gate(self._format(tpl, ctx, gating=True), language)
        if not gated:
            return ""
        for key in _QUOTE_KEYS:
            value = ctx.get(key)
            if value:
                gated = gated.replace(_quote_token(key), str(value))
        return gated

    async def _quote_memories(
        self, db: AsyncSession, user: User, start: datetime, end: datetime, need: int
    ) -> List[Tuple[Memory, str]]:
        """取 need 条可引用的用户原话（近→远），排除危机触发内容。

        排除是刻意的：把"我不想活"这类原话在报告里复述给用户，既不安全也无必要——
        宁可少一条引用，也不复述危机内容（措辞规范 §二）。
        """
        candidates = await memory_repository.list_between(
            db, user.id, start, end, limit=need * 10
        )
        picked: List[Tuple[Memory, str]] = []
        for memory in candidates:
            excerpt = _clean(memory.content)
            if not excerpt:
                continue
            if safety_service.crisis_intercept(excerpt, user.language, user.timezone):
                continue
            picked.append((memory, excerpt))
            if len(picked) >= need:
                break
        return picked

    @staticmethod
    def _refs(picked: List[Tuple[Memory, str]]) -> List[dict]:
        return [
            {
                "memory_id": memory.id,
                "created_at": memory.created_at.isoformat() if memory.created_at else None,
                "excerpt": excerpt,
            }
            for memory, excerpt in picked
        ]

    @staticmethod
    def _quotes_payload(picked: List[Tuple[Memory, str]], user: User) -> List[dict]:
        zone = safe_zoneinfo(user.timezone)
        return [
            {
                "memory_id": memory.id,
                "text": excerpt,
                "date": _local_date(memory.created_at or utc_now(), zone).isoformat(),
            }
            for memory, excerpt in picked
        ]

    def _date_label(self, memory: Memory, user: User) -> str:
        """引用日期标签（"8/12" 式，PRD §5.19.3 引用依据样式）。"""
        local = _local_date(memory.created_at or utc_now(), safe_zoneinfo(user.timezone))
        return f"{local.month}/{local.day}"

    def _closing(self, options: List[str], user: User, event_id: str) -> str:
        """收束句确定性选取（同事件重放结果一致，零 LLM）。"""
        if len(options) == 1:
            return options[0]
        digest = hashlib.sha256(f"{user.id}:{event_id}".encode()).hexdigest()
        return options[int(digest, 16) % len(options)]

    async def _activity(
        self, db: AsyncSession, user: User, days: int
    ) -> dict:
        """近 N 天（用户本地日）的记录统计：条数 / 夜间条数 / 有记录天数 / 常用签到情绪。

        口径：用户消息 + 情绪签到（与 cron 活跃时段统计同源），全部确定性计算。
        """
        now = utc_now()
        start = now - timedelta(days=days)
        zone = safe_zoneinfo(user.timezone)
        times = await chat_repository.list_recent_activity(db, user.id, start)
        checkins = await mood_repository.get_history(db, user.id, days=days)
        hours: List[int] = []
        dates: set = set()
        for dt in list(times) + [c.created_at for c in checkins]:
            local = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            local = local.astimezone(zone)
            hours.append(local.hour)
            dates.add(local.date())
        night = sum(1 for h in hours if h >= NIGHT_START_HOUR or h < NIGHT_END_HOUR)
        checkin_dates = {_local_date(c.created_at, zone) for c in checkins}
        top_emoji = Counter(c.mood_emoji for c in checkins).most_common(1)
        return {
            "days": days,
            "entries": len(hours),
            "night_entries": night,
            "active_dates": len(dates),
            "checkin_days": len(checkin_dates),
            "chats": await chat_repository.count_between(db, user.id, start, now),
            "top_mood_emoji": top_emoji[0][0] if top_emoji else None,
        }

    # ── 各日渲染 ──────────────────────────────────────────────────────

    async def _render_trial_d5(self, db: AsyncSession, user: User) -> Optional[Rendered]:
        """试用第 5 天资产盘点（PRD §5.21 D5）：盘点式正向损失，禁到期/续订恐吓。

        「模式」= 盘点窗口内签到情绪词的重复出现（确定性统计，不编造）；无签到数据
        回退纯记录数盘点；两者皆无走 plain（零 LLM，模板直出）。
        """
        tpl = self._t(user.language)["trial_d5"]
        activity = await self._activity(db, user, TRIAL_D5_WINDOW_DAYS)
        entries = activity["entries"]
        checkins = await mood_repository.get_history(db, user.id, days=TRIAL_D5_WINDOW_DAYS)
        labels: List[str] = []
        for emoji, _count in Counter(c.mood_emoji for c in checkins).most_common():
            label = mood_label_for(emoji, user.language)
            if label and label not in labels:
                labels.append(label)
            if len(labels) >= TRIAL_D5_MAX_MOODS:
                break

        stats = {"entries": entries, "mood_patterns": len(labels)}
        if labels:
            sep = "、" if locale_for(user.language) == "zh" else ", "
            text = self._gate_tpl(
                tpl["with_moods"],
                {"n": entries, "m": len(labels), "moods": sep.join(labels)},
                user.language,
            )
        elif entries > 0:
            text = self._gate_tpl(tpl["with_stats"], {"n": entries}, user.language)
        else:
            text = self._gate(tpl["plain"], user.language)
        if not text:
            return None
        return Rendered(
            text=text,
            evidence={"source": "trial_inventory", "window_days": TRIAL_D5_WINDOW_DAYS},
            stats=stats,
        )

    async def _render_day0(self, db: AsyncSession, user: User) -> Optional[Rendered]:
        """D0 性格初读卡（M1 已生成，这里只把"卡已就绪"落到对话流与红点）。"""
        tpl = self._t(user.language)["day0_read"]
        profile = await onboarding_repository.get(db, user.id)
        card = parse_json(profile.first_read if profile else None, {})
        assertions = (card.get("assertions") if isinstance(card, dict) else None) or []
        gated = ""
        if assertions:
            gated = self._gate_tpl(
                tpl["with_card"], {"n": len(assertions)}, user.language
            )
        if not gated:
            gated = self._gate(tpl["plain"], user.language)
        if not gated:
            return None
        return Rendered(
            text=gated,
            evidence={"source": "first_read", "assertion_count": len(assertions)},
            stats={"assertion_count": len(assertions)},
        )

    async def _render_day1(self, db: AsyncSession, user: User) -> Optional[Rendered]:
        """D1 回应首次签到的情绪词（PRD §5.19.3：无签到则这一格空着，不发泛泛问候）。"""
        recent = await mood_repository.get_recent(db, user.id, limit=1)
        if not recent:
            return None
        checkin = recent[0]
        zone = safe_zoneinfo(user.timezone)
        if (local_today(user.timezone) - _local_date(checkin.created_at, zone)).days > 1:
            return None
        tpl = self._t(user.language)["day1_checkin_echo"]
        label = mood_label_for(checkin.mood_emoji, user.language)
        text = self._gate_tpl(tpl["with_mood"], {"mood": label}, user.language)
        if not text:
            return None
        return Rendered(
            text=text,
            evidence={"source": "mood_checkin", "mood_emoji": checkin.mood_emoji},
            stats={"mood_emoji": checkin.mood_emoji, "mood_label": label},
        )

    async def _render_day2(self, db: AsyncSession, user: User) -> Optional[Rendered]:
        """D2 AI 第一次主动开口（§5.19.3 唯一走推送的一条）。

        优先复述昨日签到情绪词（PRD 样例"昨天你说累了，今天呢？"），
        无签到则退到昨日一条原话（带 memory_refs），两者都没有才走无断言的兜底问句。
        """
        tpl = self._t(user.language)["day2_first_outreach"]
        day_start = local_day_start_utc(user.timezone)
        yesterday_start = day_start - timedelta(days=1)

        yesterday_emoji = None
        for record in await mood_repository.get_recent(db, user.id, limit=5):
            aware = record.created_at
            aware = aware if aware.tzinfo else aware.replace(tzinfo=timezone.utc)
            if yesterday_start <= aware < day_start:
                yesterday_emoji = record.mood_emoji
                break

        picked = await self._quote_memories(db, user, yesterday_start, day_start, 1)
        refs: List[dict] = []
        quotes: List[dict] = []
        if yesterday_emoji:
            key, ctx = "with_mood", {"mood": mood_label_for(yesterday_emoji, user.language)}
            evidence = {"source": "mood_checkin", "mood_emoji": yesterday_emoji}
            stats = {"mood_emoji": yesterday_emoji}
        elif picked:
            memory, excerpt = picked[0]
            key, ctx = "with_quote", {"q0": excerpt}
            evidence = {"source": "memory", "memory_id": memory.id}
            stats = {"mood_emoji": None}
            refs = self._refs(picked)
            quotes = self._quotes_payload(picked, user)
        else:
            key, ctx = "plain", {}
            evidence = {"source": "none"}
            stats = {"mood_emoji": None}

        gated = self._gate_tpl(tpl[key], ctx, user.language)
        if not gated:
            return None
        return Rendered(
            text=gated, memory_refs=refs, quotes=quotes, evidence=evidence, stats=stats
        )

    async def _render_day3(self, db: AsyncSession, user: User) -> Optional[Rendered]:
        """D3 首条弱断言：措辞必带"我注意到 / 可能"，且只在数据够用时出（PRD §5.19.3）。"""
        tpl = self._t(user.language)["day3_weak_assert"]
        act = await self._activity(db, user, WEAK_ASSERT_DAYS)
        total, night, active_dates = act["entries"], act["night_entries"], act["active_dates"]
        base_ctx = {"days": WEAK_ASSERT_DAYS, "window": self._t(user.language)["window"]}
        if (
            active_dates >= WEAK_ASSERT_MIN_DAYS_WITH_DATA
            and night >= WEAK_ASSERT_MIN_NIGHT
            and night / max(total, 1) >= WEAK_ASSERT_NIGHT_RATIO
        ):
            key, ctx = "night", {**base_ctx, "n": night}
        elif total >= 3:
            key, ctx = "volume", {**base_ctx, "n": total, "k": night}
        else:
            return None
        text = self._gate_tpl(tpl[key], ctx, user.language)
        if not text:
            return None
        return Rendered(text=text, stats=act, evidence={"source": "activity_stats", "variant": key})

    async def _render_day5(self, db: AsyncSession, user: User) -> Optional[Rendered]:
        """D5 "你知道吗"：一个数据性观察（PRD §5.19.3）。

        三个变体都是纯统计陈述、不引用原话，因此 memory_refs 恒为空——
        引用只在正文真的复述原话时才出现（CI 不变量：有 refs ⟺ 正文含引号槽位）。
        """
        tpl = self._t(user.language)["day5_stat"]
        act = await self._activity(db, user, 5)
        window = self._t(user.language)["window"]
        if act["night_entries"] >= WEAK_ASSERT_MIN_NIGHT:
            key = "night"
            ctx = {"days": 5, "n": act["night_entries"], "window": window}
        elif act["active_dates"] >= 5:
            key = "daily"
            ctx = {"days": 5}
        else:
            mem_n = await memory_repository.count_since(db, user.id, utc_now() - timedelta(days=5))
            if mem_n < DAY5_MIN_MEMORIES:
                return None
            key = "memories"
            ctx = {"n": mem_n}
        text = self._gate_tpl(tpl[key], ctx, user.language)
        if not text:
            return None
        return Rendered(
            text=text,
            stats=act,
            evidence={"source": "activity_stats", "variant": key},
        )

    async def _render_day7(self, db: AsyncSession, user: User) -> Optional[Rendered]:
        """D7《第一周观察报告》：2 条原话 + 周统计 + 一句收束（v1 纯模板，§五 决策点 2）。"""
        section = self._t(user.language)["day7_report"]
        now = utc_now()
        picked = await self._quote_memories(
            db, user, now - timedelta(days=7), now, REPORT_QUOTES
        )
        if len(picked) < REPORT_QUOTES:
            return None
        act = await self._activity(db, user, 7)
        ctx = {
            "msg_n": act["chats"],
            "mood_n": act["checkin_days"],
            "mood": mood_label_for(act["top_mood_emoji"], user.language)
            if act["top_mood_emoji"]
            else self._fallback_word(user.language),
            "q0": picked[0][1],
            "d0": self._date_label(picked[0][0], user),
            "q1": picked[1][1],
            "d1": self._date_label(picked[1][0], user),
            "closing": self._closing(section["closings"], user, "day7_report"),
        }
        gated = self._gate_tpl(section["body"], ctx, user.language)
        if not gated:
            return None
        return Rendered(
            text=gated,
            memory_refs=self._refs(picked),
            quotes=self._quotes_payload(picked, user),
            stats=act,
            evidence={"source": "memory+activity", "quote_count": len(picked)},
            title=section["title"],
        )

    def _fallback_word(self, language: Optional[str]) -> str:
        return "未知" if locale_for(language) == "zh" else "unknown"

    async def _render_level_reward(
        self, db: AsyncSession, event, user: User
    ) -> Optional[Rendered]:
        """§5.15a 等级情感回报正文（A3 预写 kind=level_reward，payload.reward_key 分派）。"""
        payload = parse_json(event.payload, {})
        reward_key = payload.get("reward_key") if isinstance(payload, dict) else None
        if reward_key not in ("memory_echo", "monthly_story_preview", "letter_90d"):
            logger.info("level_reward 未知 reward_key=%s user=%s", reward_key, user.id)
            return None
        tpl = self._t(user.language)["level_reward"]
        now = utc_now()
        if reward_key == "memory_echo":
            picked = await self._quote_memories(
                db, user, now - timedelta(days=90), now, 1
            )
            if not picked:
                text = self._gate(tpl["memory_echo_plain"], user.language)
                if not text:
                    return None
                return Rendered(
                    text=text, evidence={"source": "none", "reward_key": reward_key}
                )
            memory, excerpt = picked[0]
            ctx = {"q0": excerpt, "d0": self._date_label(memory, user)}
            text = self._gate_tpl(tpl["memory_echo"], ctx, user.language)
            if not text:
                return None
            return Rendered(
                text=text,
                memory_refs=self._refs(picked),
                quotes=self._quotes_payload(picked, user),
                evidence={"source": "memory", "reward_key": reward_key},
                reward_key=reward_key,
            )

        window_days = 30 if reward_key == "monthly_story_preview" else 90
        if reward_key == "monthly_story_preview":
            act = await self._activity(db, user, window_days)
            mem_n = await memory_repository.count_since(
                db, user.id, now - timedelta(days=window_days)
            )
            ctx = {"days": act["active_dates"], "msg_n": act["chats"], "mem_n": mem_n}
            text = self._gate_tpl(tpl["monthly_story_preview"], ctx, user.language)
            if not text:
                return None
            return Rendered(
                text=text,
                stats=act,
                evidence={"source": "activity_stats", "reward_key": reward_key},
                reward_key=reward_key,
            )

        picked = await self._quote_memories(
            db, user, now - timedelta(days=window_days), now, REPORT_QUOTES
        )
        act = await self._activity(db, user, window_days)
        if len(picked) < REPORT_QUOTES:
            ctx = {
                "days": act["active_dates"],
                "msg_n": act["chats"],
                "closing": self._closing(tpl["letter_closings"], user, event.id),
            }
            text = self._gate_tpl(tpl["letter_90d_plain"], ctx, user.language)
            if not text:
                return None
            return Rendered(
                text=text,
                stats=act,
                evidence={"source": "activity_stats", "reward_key": reward_key},
                reward_key=reward_key,
            )
        ctx = {
            "days": act["active_dates"],
            "msg_n": act["chats"],
            "q0": picked[0][1],
            "d0": self._date_label(picked[0][0], user),
            "q1": picked[1][1],
            "d1": self._date_label(picked[1][0], user),
            "closing": self._closing(tpl["letter_closings"], user, event.id),
        }
        text = self._gate_tpl(tpl["letter_90d"], ctx, user.language)
        if not text:
            return None
        return Rendered(
            text=text,
            memory_refs=self._refs(picked),
            quotes=self._quotes_payload(picked, user),
            stats=act,
            evidence={"source": "memory+activity", "reward_key": reward_key},
            reward_key=reward_key,
        )

    async def _render_commitment_follow_up(
        self, db: AsyncSession, event, user: User
    ) -> Optional[Rendered]:
        """§5.18 承诺回访（四层公式：问候 → 承诺回放 → 关心提问 → 开放 CTA）。

        模板直出零 LLM；承诺原话是 C 级证据，memory_refs 硬不变量照常成立。
        已 muted（负向退出）或原话命中危机词 → 返回 None（skip，沉默陪伴，永不删行）。
        """
        payload = parse_json(event.payload, {})
        memory_id = payload.get("memory_id") if isinstance(payload, dict) else None
        memory = await memory_repository.get(db, memory_id, user.id) if memory_id else None
        if memory is None:
            return None
        commitment = commitment_service.parse_labels(memory.labels).get("commitment")
        if not isinstance(commitment, dict) or commitment.get("muted"):
            return None
        if safety_service.crisis_intercept(memory.content, user.language, user.timezone):
            return None
        kind_key = "event" if commitment.get("kind") == "event" else "habit"
        tpl = self._t(user.language)["commitment_follow_up"]
        excerpt = _clean(memory.content)
        d0 = None
        captured_day = payload.get("captured_day") if isinstance(payload, dict) else None
        if captured_day:
            try:
                day = date.fromisoformat(str(captured_day))
                d0 = f"{day.month}/{day.day}"  # 捕获时已按用户本地日历日落 ISO
            except ValueError:
                d0 = None
        text = self._gate_tpl(
            tpl[kind_key], {"q0": excerpt, "d0": d0 or self._date_label(memory, user)}, user.language
        )
        if not text:
            return None
        picked = [(memory, excerpt)]
        return Rendered(
            text=text,
            memory_refs=self._refs(picked),
            quotes=self._quotes_payload(picked, user),
            evidence={
                "source": "commitment",
                "kind": kind_key,
                "memory_id": memory.id,
                "follow_up_count": int(commitment.get("follow_up_count") or 0),
            },
            stats={"commitment_kind": kind_key},
        )

    # ── 前端消费 ──────────────────────────────────────────────────────

    async def list_events(self, db: AsyncSession, user_id: str) -> dict:
        """GET /journey/events 载荷：全剧本状态（红点 + Week1ReportScreen 数据源）。"""
        await self.ensure_prefilled(db, user_id)
        events = await journey_repository.list_for_user(db, user_id, limit=50)
        return {
            "events": [
                {
                    "id": e.id,
                    "day_no": e.day_no,
                    "event_kind": e.event_kind,
                    "status": e.status,
                    "scheduled_at": e.scheduled_at,
                    "sent_at": e.sent_at,
                    "acknowledged": bool(e.acknowledged),
                    "payload": parse_json(e.payload, {}),
                    "memory_refs": parse_json(e.memory_refs, []),
                }
                for e in events
            ],
            "unseen_count": await journey_repository.count_unseen(db, user_id),
        }

    async def acknowledge(self, db: AsyncSession, user_id: str, event_id: str) -> None:
        """标记已读（红点消费）。归属校验：非本用户事件按不存在处理，不泄露他人 ID 有效性。"""
        event = await journey_repository.get(db, event_id)
        if event is None or event.user_id != user_id:
            raise AppException(ErrorCode.RESOURCE_NOT_FOUND, "事件不存在 / Event not found")
        await journey_repository.acknowledge(db, event_id)


journey_service = JourneyService()
