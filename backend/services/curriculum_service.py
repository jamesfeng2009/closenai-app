"""
Growth Curriculum 服务（PRD §5.17 / tasks C2）。

AI 侧内容供给主线：每周日 21:00（用户本地）预生成下周 7 张主题卡——
主题标题 + 开场断言（基于真实数据）+ 引导提问。预生成意味着断网或当日
零输入，首页也有新内容；内容全部来自用户自己的数据（近 30 天 Memory +
Pattern），竞品无法复制。

管线（Rule 优先 + LLM 周规划）：
- 素材收集（零 LLM）：近 30 天记忆 + pattern_discovery 成长记录
- Rule 层：直接由素材拼卡（引用原话 + 引导提问，确定性模板）
- LLM 层：素材足够时一次 lite 调用规划整周 7 卡（JSON），ref 编号映射回
  素材行；解析失败/违规卡（filter_template + check_or assertion）回退 Rule 卡

存储复用 journey_events（无新表，PRD 研发标注）：event_kind="curriculum"，
day_no 编码 ISO 周序防跨周幂等键冲突，payload 存卡内容 + read_at，scheduled_at
为各展示日零点（本地）。生成即 sent + acknowledged（首页池拉取式消费，
不推送、不进报告页红点、不被剧本 cron 扫描）。

与睡前复盘共用预生成管线：本周旅程卡素材池同时作为下周睡前复盘素材输入。
"""
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import copy
from backend.core.errors import AppException, ErrorCode
from backend.core.llm import llm
from backend.core.time_utils import (
    local_day_start_utc,
    local_now,
    local_today,
    safe_zoneinfo,
    utc_now,
)
from backend.models.journey import JourneyEvent
from backend.models.user import User
from backend.repositories import (
    growth_repository,
    journey_repository,
    memory_repository,
)
from backend.repositories.journey_repository import parse_json
from backend.services.safety_service import safety_service

logger = logging.getLogger("closenai.curriculum")

KIND = "curriculum"

MATERIAL_WINDOW_DAYS = 30
MATERIAL_MEMORY_LIMIT = 12
MATERIAL_PATTERN_LIMIT = 10
MIN_MATERIALS = 1  # 零素材不生成：内容全部来自用户数据（差异化根基）
WEEK_DAYS = 7

MAX_TOKENS = 900

# Rule 卡模板已迁文案库 curriculum.rule_cards（确定性出口过 filter_template；素材 excerpt 截断注入）

_PLANNED_PROMPT_MARK = "主题卡"  # 测试定位周规划调用的标记词

# Curriculum 专属红线（PRD §5.17：不使用占星、运势措辞）。
# 全局 BANNED_TERMS 只有"星座运势"等复合词，单字"运势/注定"不在其中；
# 本表在卡内容出口做确定性拦截（en 走词边界匹配）。
CURRICULUM_BANNED = {
    "zh": ("运势", "占卜", "算命", "幸运数字", "注定", "命运"),
    "en": ("lucky number", "destiny", "fate", "psychic", "tarot", "predict", "forecast"),
}


def _card_banned(text: str, lang: str) -> bool:
    """卡内容出口确定性闸：全局黑名单/推理越界 + curriculum 专属红线。"""
    if not safety_service.filter_template(text, "zh" if lang == "zh" else "en"):
        return True
    for term in CURRICULUM_BANNED[lang]:
        if lang == "en":
            if re.search(rf"\b{re.escape(term)}", text, re.IGNORECASE):
                return True
        elif term in text:
            return True
    return False


def _lang_key(language: str | None) -> str:
    return "zh" if (language or "").startswith("zh") else "en"


def _week_no(day: date) -> int:
    """ISO 周序号（跨年单调递增）。"""
    iso = day.isocalendar()
    return iso[0] * 53 + iso[1]


def day_no_of(day: date) -> int:
    """curriculum 事件的 day_no 编码：周序 * 7 + 周内序（周一=0）。

    journey_events 幂等键是 (user_id, event_kind, day_no)，每周一批的
    curriculum 用 0-6 会跨周冲突，故编码周序（Integer 范围充足）。
    """
    iso = day.isocalendar()
    return _week_no(day) * 7 + iso[2] - 1


def _target_monday(today: date) -> date:
    """生成目标周的周一：本周一；周三及以后改为下周一（预生成下一周）。"""
    monday = today - timedelta(days=today.weekday())
    if today.weekday() >= 3:
        monday += timedelta(days=7)
    return monday


def _excerpt(text: str, limit: int) -> str:
    clean = (text or "").strip().splitlines()[0]
    return clean[:limit]


class CurriculumService:
    """每周主题旅程服务。"""

    # ── 生成（cron 周日晚触发，幂等可重试）──

    async def generate_weekly(self, db: AsyncSession, user: User) -> dict:
        """预生成目标周 7 张主题卡。已生成本周任一天 → skipped（周规划不重跑）。"""
        today = local_today(user.timezone)
        monday = _target_monday(today)
        first_day_no = day_no_of(monday)

        if await journey_repository.get_by_kind(db, user.id, KIND, first_day_no) is not None:
            return {"status": "skipped", "reason": "already_generated"}

        materials = await self._collect_materials(db, user, today)
        if len(materials) < MIN_MATERIALS:
            return {"status": "skipped", "reason": "no_material"}

        language = user.language or "en"
        lang = _lang_key(language)
        cards = await self._plan_week(db, user, materials, lang, language)

        events = []
        for i, card in enumerate(cards):
            day = monday + timedelta(days=i)
            event = await journey_repository.schedule(
                db,
                user_id=user.id,
                event_kind=KIND,
                day_no=day_no_of(day),
                scheduled_at=local_day_start_utc(user.timezone, day),
                payload={**card, "read_at": None},
                memory_refs=card["memory_refs"],
            )
            # 生成即投递到首页池：sent + acknowledged（拉取式消费，不推送不进红点）
            await journey_repository.mark_sent(db, event.id, utc_now())
            await journey_repository.acknowledge(db, event.id)
            events.append(event)
        await db.flush()
        return {"status": "generated", "cards": cards, "week_start": monday.isoformat()}

    # ── 读取（首页次位卡片）──

    async def get_week(self, db: AsyncSession, user: User) -> dict:
        """当周 7 张卡 + 揭晓状态 + 未读数。未生成周返回空 days（首页显示兜底态）。"""
        today = local_today(user.timezone)
        monday = today - timedelta(days=today.weekday())
        start = local_day_start_utc(user.timezone, monday)
        end = local_day_start_utc(user.timezone, monday + timedelta(days=WEEK_DAYS))
        events = await journey_repository.list_kind_between(db, user.id, KIND, start, end)
        by_day: dict[int, JourneyEvent] = {}
        for e in events:
            idx = e.day_no % WEEK_DAYS
            by_day[idx] = e

        days = []
        unread = 0
        for i in range(WEEK_DAYS):
            day = monday + timedelta(days=i)
            event = by_day.get(i)
            if event is None:
                continue  # 未生成的天不出现在视图里（首页走兜底态）
            payload = self._payload(event)
            revealed = day <= today
            read = bool(payload.get("read_at"))
            if revealed and not read:
                unread += 1
            days.append({
                "id": event.id,
                "date": day.isoformat(),
                "day_no": event.day_no,
                "revealed": revealed,
                "read": read,
                "memory_refs": self._refs(event),
                "title": payload.get("title"),
                "assertion": payload.get("assertion"),
                "question": payload.get("question"),
            })
        return {"week_start": monday.isoformat(), "days": days, "unread_count": unread}

    async def mark_read(self, db: AsyncSession, user: User, event_id: str) -> dict:
        """点击卡片进入对话 → payload.read_at（幂等；越权 40403）。"""
        event = await journey_repository.get(db, event_id)
        if event is None or event.user_id != user.id or event.event_kind != KIND:
            raise AppException(ErrorCode.FORBIDDEN, "无权访问该主题卡")
        payload = self._payload(event)
        if not payload.get("read_at"):
            payload["read_at"] = utc_now().isoformat()
            await journey_repository.set_payload(db, event.id, payload=payload)
            await db.flush()
        return {"id": event.id, "read": True}

    # ── 素材（近 30 天 Memory + Pattern，零 LLM）──

    async def _collect_materials(self, db: AsyncSession, user: User, today: date) -> list[dict]:
        start = local_day_start_utc(user.timezone, today - timedelta(days=MATERIAL_WINDOW_DAYS))
        end = local_day_start_utc(user.timezone, today + timedelta(days=1))
        memories = await memory_repository.list_between(
            db, user.id, start, end, limit=MATERIAL_MEMORY_LIMIT
        )
        records = await growth_repository.list_between(
            db, user.id, start, end, limit=MATERIAL_PATTERN_LIMIT
        )
        materials = [
            {
                "kind": "memory",
                "id": m.id,
                "excerpt": _excerpt(m.content, 80),
                "day": m.created_at.astimezone(safe_zoneinfo(user.timezone)).date().isoformat()
                if m.created_at else "",
            }
            for m in memories
        ]
        materials += [
            {"kind": "pattern", "id": r.id, "excerpt": _excerpt(r.content, 80), "day": ""}
            for r in records if r.record_type == "pattern_discovery"
        ]
        return materials[:MATERIAL_MEMORY_LIMIT + MATERIAL_PATTERN_LIMIT]

    # ── 周规划：LLM 一次调用 + Rule 兜底 ──

    async def _plan_week(
        self, db: AsyncSession, user: User, materials: list[dict], lang: str, language: str
    ) -> list[dict]:
        """LLM 周规划（失败/违规逐卡回退 Rule）。ref 只信服务端映射（硬不变量）。"""
        rule_cards = self._rule_cards(materials, language)
        lines = [
            f"[{i}] ({m['kind']} {m['day']}) {m['excerpt']}"
            for i, m in enumerate(materials)
        ]
        if lang == "zh":
            prompt = (
                "基于以下用户近 30 天的真实记录，为下周 7 天各规划一张主题卡。"
                "要求：内容全部来自这些记录（不编造）；断言具体引用用户自己的话或事件；"
                "只观察不预测不评判，不使用医疗化、占星、运势措辞。\n"
                f"{safety_service.boundary(language)}\n{safety_service.evidence_policy(language)}\n"
                "每张卡输出 JSON 对象：{\"ref\": 素材编号或null, \"title\": ≤15字标题, "
                "\"assertion\": ≤50字开场断言, \"question\": ≤30字引导提问}。\n"
                "只输出 JSON 数组（7 个对象），不要解释。\n素材：\n" + "\n".join(lines)
            )
        else:
            prompt = (
                "Based on the user's real notes from the last 30 days, plan one theme card "
                "for each of the next 7 days. Everything must come from these notes (never "
                "invent); assertions should quote the user's own words or concrete events; "
                "observe only — no predictions, no judgments, no medicalized/astrology wording.\n"
                f"{safety_service.boundary(language)}\n{safety_service.evidence_policy(language)}\n"
                "Each card as a JSON object: {\"ref\": material index or null, \"title\": "
                "<=15 chars, \"assertion\": <=50 chars opening assertion, \"question\": <=30 chars}.\n"
                "Output ONLY the JSON array of 7 objects.\nNotes:\n" + "\n".join(lines)
            )

        try:
            text = await llm.complete(prompt, language=language, lite=True, max_tokens=MAX_TOKENS)
            cards = self._parse_cards(text)
        except Exception as exc:  # noqa: BLE001 - LLM 失败安全降级 Rule 卡
            logger.warning("Curriculum 周规划 LLM 失败，回退 Rule 卡: %s", exc)
            cards = []

        planned = []
        for i in range(WEEK_DAYS):
            card = cards[i] if i < len(cards) else None
            merged = await self._validate_card(db, user, card, materials, rule_cards[i])
            planned.append(merged)
        return planned

    @staticmethod
    def _parse_cards(text: str) -> list[dict]:
        """从 LLM 输出提取 JSON 数组（容错：截取首个 [...] 块）。"""
        if not text:
            return []
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
        return [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []

    async def _validate_card(
        self, db: AsyncSession, user: User, card: Optional[dict],
        materials: list[dict], fallback: dict,
    ) -> dict:
        """单卡合规校验：过闸 + ref 映射；任一失败回退 Rule 卡。"""
        lang = _lang_key(user.language)
        if card:
            title = str(card.get("title") or "").strip()
            assertion = str(card.get("assertion") or "").strip()
            question = str(card.get("question") or "").strip()
            if title and assertion and question and not any(
                _card_banned(t, lang) for t in (title, assertion, question)
            ):
                try:
                    checked = await safety_service.check_or(
                        assertion, user.language or "en", "", scope="assertion"
                    )
                except Exception:  # noqa: BLE001 - 检查异常按不合规处理
                    checked = ""
                if checked and checked == assertion:
                    ref = card.get("ref")
                    refs = []
                    if isinstance(ref, int) and 0 <= ref < len(materials):
                        refs = [{"memory_id": materials[ref]["id"], "excerpt": materials[ref]["excerpt"]}]
                    return {"title": title, "assertion": assertion, "question": question, "memory_refs": refs}
        return fallback

    def _rule_cards(self, materials: list[dict], language: str) -> list[dict]:
        """Rule 层：由素材轮转拼 7 张卡（确定性模板，零 LLM）。模板走文案库 curriculum.rule_cards。"""
        templates = copy.t_list("curriculum", "rule_cards", language)
        cards = []
        for i in range(WEEK_DAYS):
            tpl = templates[i % len(templates)]
            m = materials[i % len(materials)]
            title, assertion, question = tpl
            cards.append({
                "title": title.format(topic=_excerpt(m["excerpt"], 12), count=len(materials)),
                "assertion": assertion.format(
                    day=m["day"] or copy.t("curriculum", "date_placeholder", language),
                    excerpt=_excerpt(m["excerpt"], 40),
                    count=len(materials),
                ),
                "question": question,
                "memory_refs": [{"memory_id": m["id"], "excerpt": m["excerpt"]}],
            })
        return cards

    # ── 工具 ──

    @staticmethod
    def _payload(event: JourneyEvent) -> dict:
        payload = parse_json(event.payload, {})
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _refs(event: JourneyEvent) -> list[dict]:
        refs = parse_json(event.memory_refs, [])
        return refs if isinstance(refs, list) else []


curriculum_service = CurriculumService()
