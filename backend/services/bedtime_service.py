"""
睡前复盘服务（PRD §5.16 S1 / tasks C1）。

三步流程（22:00–23:30 本地窗口由 cron 推送提醒，App 内卡片兜底）：
- step1「今天的一句话」：用户一句话（可跳过），返回当日摘要参考（纯规则拼装，零 LLM）
- step2「你今天值得记下的」：AI 从当日内容（记忆/签到/对话）提取，memory_refs 硬不变量
- step3「留给明天」：轻量观察点——只观察、不预测、不承诺（PRD 红线）

存储：growth_records record_type="bedtime_review"，三步内容存 metadata_json，
step2 引用的记忆落 related_memory_ids；沉淀出口走既有成长记录时间线。

合规：
- step1 用户输入挂危机转介（SafetyEvent.source="bedtime_review"，PRD §5.16 ④）
- LLM 一律 lite + try/except 模板回退，输出过 check_or(scope="assertion")
- 复盘非对话轮：不写对话流、不计对话配额，每日一次复盘本身即频控
"""
import json
import logging
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import copy
from backend.core.errors import AppException, ErrorCode
from backend.core.llm import llm
from backend.core.time_utils import local_day_start_utc, local_today
from backend.models.growth import GrowthRecord
from backend.models.user import User
from backend.repositories import (
    chat_repository,
    growth_repository,
    memory_repository,
    mood_repository,
    safety_repository,
    user_repository,
)
from backend.services.safety_service import safety_service

logger = logging.getLogger("closenai.bedtime")

RECORD_TYPE = "bedtime_review"
VALID_STEPS = (1, 2, 3)

# 当日素材上限（成本控制）
DIGEST_MEMORY_LIMIT = 5
DIGEST_CHAT_LIMIT = 10
MAX_TOKENS = 120

# LLM 失败/违规回退模板已迁文案库 bedtime.step2/step3（均需过 filter_template 确定性闸，测试钉死；
# step3 红线：只观察、不预测、不承诺）


def _is_zh(language: str | None) -> bool:
    return (language or "").startswith("zh")


class BedtimeService:
    """睡前复盘服务。"""

    # ── 查询 ──

    async def get_today(self, db: AsyncSession, user_id: str) -> dict:
        """今日三步状态 + step1 摘要参考（零 LLM）+ 昨日归档。"""
        user = await user_repository.require_user(db, user_id)
        return await self._today_view(db, user, local_today(user.timezone), user.language or "en")

    async def _today_view(self, db: AsyncSession, user: User, day: date, language: str) -> dict:
        start = local_day_start_utc(user.timezone, day)
        end = local_day_start_utc(user.timezone, day + timedelta(days=1))
        record = await self._find_record(db, user.id, start, end)
        yesterday_snapshot = self._snapshot(
            await self._find_record(
                db, user.id,
                local_day_start_utc(user.timezone, day - timedelta(days=1)),
                start,
            )
        )
        return {
            "date": day.isoformat(),
            "completed": self._is_completed(record),
            "record": self._snapshot(record),
            "digest": await self._day_digest(db, user, day, language),
            "yesterday": yesterday_snapshot,
        }

    async def has_completed_today(self, db: AsyncSession, user: User) -> bool:
        """当日复盘是否已完成（cron 去重依据；未建记录/未到 step3 均视为未完成）。"""
        record = await self._find_record(
            db, user.id, local_day_start_utc(user.timezone),
            local_day_start_utc(user.timezone, local_today(user.timezone) + timedelta(days=1)),
        )
        return self._is_completed(record)

    # ── 三步提交 ──

    async def submit_step(
        self, db: AsyncSession, user: User, step: int, text: Optional[str]
    ) -> dict:
        """逐步提交复盘（step 1|2|3）。危机文本在 step1 被拦截，不落复盘素材。

        模板/LLM 语言以 users.language 为事实源（AI 用用户语言回复）。
        """
        language = user.language or "en"
        if step not in VALID_STEPS:
            raise AppException(ErrorCode.INVALID_PARAMS, f"无效的复盘步骤: {step}")
        if step == 1:
            return await self._submit_step1(db, user, text, language)
        if step == 2:
            return await self._submit_step2(db, user, language)
        return await self._submit_step3(db, user, language)

    async def _submit_step1(self, db: AsyncSession, user: User, text: Optional[str], language: str) -> dict:
        note = (text or "").strip() or None

        # 危机转介（PRD §5.16 ④）：命中即挂转介载荷，不落复盘记录、不走 LLM
        if note:
            crisis = safety_service.crisis_intercept(note, language, user.timezone)
            if crisis:
                await safety_repository.record(
                    db, user.id, kind=crisis["kind"], source="bedtime_review",
                    matched_terms=crisis["matched"], hotline=crisis["hotline"],
                    language=language, excerpt=note,
                    advice_blocked=crisis["advice_blocked"],
                )
                return {
                    "intercepted": True,
                    "crisis": {
                        "kind": crisis["kind"],
                        "reply": crisis["reply"],
                        "hotline": crisis["hotline"],
                        "advice_blocked": crisis["advice_blocked"],
                    },
                }

        day = local_today(user.timezone)
        start = local_day_start_utc(user.timezone, day)
        end = local_day_start_utc(user.timezone, day + timedelta(days=1))
        record = await self._find_record(db, user.id, start, end) or await self._create_record(
            db, user.id, content=note or ""
        )
        steps = self._steps(record)
        steps["step1"] = {"text": note, "skipped": note is None}
        if note:
            record.content = note
        await self._save_steps(db, record, steps)

        return {
            "step": 1,
            "skipped": note is None,
            "digest": await self._day_digest(db, user, day, language),
            "record": self._snapshot(record),
        }

    async def _submit_step2(self, db: AsyncSession, user: User, language: str) -> dict:
        day = local_today(user.timezone)
        start = local_day_start_utc(user.timezone, day)
        end = local_day_start_utc(user.timezone, day + timedelta(days=1))
        record = await self._find_record(db, user.id, start, end)

        # 幂等：已生成直接返回（不重复消耗 LLM）
        existing = self._steps(record).get("step2") if record else None
        if existing:
            return {
                "step": 2,
                "content": existing["content"],
                "memory_refs": existing["memory_refs"],
                "record": self._snapshot(record),
            }

        refs = await self._day_memory_refs(db, user.id, start, end)
        content, refs = await self._worth_noting(db, user, start, end, refs, language)

        record = record or await self._create_record(db, user.id, content=content)
        steps = self._steps(record)
        steps["step2"] = {"content": content, "memory_refs": refs}
        record.content = content
        record.related_memory_ids = ",".join(r["memory_id"] for r in refs) or None
        await self._save_steps(db, record, steps)

        return {
            "step": 2,
            "content": content,
            "memory_refs": refs,
            "record": self._snapshot(record),
        }

    async def _submit_step3(self, db: AsyncSession, user: User, language: str) -> dict:
        day = local_today(user.timezone)
        start = local_day_start_utc(user.timezone, day)
        end = local_day_start_utc(user.timezone, day + timedelta(days=1))
        record = await self._find_record(db, user.id, start, end)

        existing = self._steps(record).get("step3") if record else None
        if existing:
            return {
                "step": 3,
                "content": existing["content"],
                "completed": True,
                "record": self._snapshot(record),
            }

        content = await self._observation(db, user, start, end, language)

        record = record or await self._create_record(db, user.id, content=content)
        steps = self._steps(record)
        steps["step3"] = {"content": content}
        await self._save_steps(db, record, steps)

        return {"step": 3, "content": content, "completed": True, "record": self._snapshot(record)}

    # ── step1 摘要（零 LLM，get_day_summary 同口径拼装）──

    async def _day_digest(self, db: AsyncSession, user: User, day: date, language: str) -> dict:
        start = local_day_start_utc(user.timezone, day)
        end = local_day_start_utc(user.timezone, day + timedelta(days=1))
        checkin = await mood_repository.get_by_date(db, user.id, day)
        insight = await growth_repository.get_latest(db, user.id, "daily_insight")
        memories = await memory_repository.list_between(db, user.id, start, end, limit=DIGEST_MEMORY_LIMIT)
        return {
            "has_data": bool(checkin or memories),
            "mood": (
                {"emoji": checkin.mood_emoji, "label": checkin.mood_label}
                if checkin else None
            ),
            "chat_count": await chat_repository.count_between(db, user.id, start, end),
            "insight": insight.content if insight else None,
            "memory_count": len(memories),
        }

    # ── step2「值得记下的」──

    async def _day_memory_refs(
        self, db: AsyncSession, user_id: str, start, end
    ) -> list[dict]:
        """当日新建记忆 → 引用依据（硬不变量的唯一来源：refs 全部来自归属该用户的真实行）。"""
        memories = await memory_repository.list_between(
            db, user_id, start, end, limit=DIGEST_MEMORY_LIMIT
        )
        return [{"memory_id": m.id, "excerpt": m.content[:80]} for m in memories]

    async def _worth_noting(
        self, db: AsyncSession, user: User, start, end, refs: list[dict], language: str
    ) -> tuple[str, list[dict]]:
        """AI 从当日内容提取「你今天值得记下的」。LLM lite + 模板回退 + 断言过闸。"""
        checkin = await mood_repository.get_by_date(db, user.id, local_today(user.timezone))
        chats = await chat_repository.list_user_contents(db, user.id, start, end, limit=DIGEST_CHAT_LIMIT)

        if not (refs or checkin or chats):
            return copy.t("bedtime", "step2", language), []

        lang = "zh" if _is_zh(language) else "en"
        lines = []
        if checkin:
            lines.append(f"- 情绪签到：{checkin.mood_emoji} {checkin.mood_label or ''}")
        lines += [f"- 对话：{c[:80]}" for c in reversed(chats)]
        lines += [f"- 记忆：{r['excerpt']}" for r in refs]

        if lang == "zh":
            prompt = (
                "基于以下今天的真实数据，写 1-3 句「你今天值得记下的」：具体引用用户自己的"
                "表达或当天事件，不要空泛，不评价人格，不使用医疗化措辞：\n"
                f"{safety_service.boundary(language)}\n{safety_service.evidence_policy(language)}\n"
                "今日数据：\n" + "\n".join(lines)
            )
        else:
            prompt = (
                "Based on today's real data below, write 1-3 sentences of \"worth noting "
                "today\": quote the user's own words or concrete events, be specific, "
                "no personality labels, no medicalized wording:\n"
                f"{safety_service.boundary(language)}\n{safety_service.evidence_policy(language)}\n"
                "Today's data:\n" + "\n".join(lines)
            )

        content = await self._llm_or_fallback(prompt, copy.t("bedtime", "step2", language), language)
        return content, refs

    # ── step3「留给明天」：只观察、不预测、不承诺 ──

    async def _observation(self, db: AsyncSession, user: User, start, end, language: str) -> str:
        lang = "zh" if _is_zh(language) else "en"
        chats = await chat_repository.list_user_contents(db, user.id, start, end, limit=DIGEST_CHAT_LIMIT)
        if not chats:
            return copy.t("bedtime", "step3", language)

        if lang == "zh":
            prompt = (
                "基于今天的对话内容，写一句「留给明天」的轻量观察点：只描述值得留意的小事或"
                "感受方向，只观察、不预测、不承诺、不布置任务，不使用医疗化措辞：\n"
                f"{safety_service.boundary(language)}\n{safety_service.evidence_policy(language)}\n"
                "今日对话：\n" + "\n".join(f"- {c[:80]}" for c in reversed(chats))
            )
        else:
            prompt = (
                "Based on today's conversation, write one light \"note for tomorrow\": describe "
                "something small worth noticing, observe only — no predictions, no promises, "
                "no assigned tasks, no medicalized wording:\n"
                f"{safety_service.boundary(language)}\n{safety_service.evidence_policy(language)}\n"
                "Today's conversation:\n" + "\n".join(f"- {c[:80]}" for c in reversed(chats))
            )
        return await self._llm_or_fallback(prompt, copy.t("bedtime", "step3", language), language)

    # ── 内部工具 ──

    async def _llm_or_fallback(self, prompt: str, fallback: str, language: str) -> str:
        try:
            text = await llm.complete(prompt, language=language, lite=True, max_tokens=MAX_TOKENS)
            text = (text or "").strip()
            if not text:
                return fallback
            return await safety_service.check_or(text, language, fallback, scope="assertion")
        except Exception as exc:  # noqa: BLE001 - LLM 失败安全降级模板
            logger.warning("睡前复盘 LLM 失败，回退模板: %s", exc)
            return fallback

    async def _find_record(
        self, db: AsyncSession, user_id: str, start, end
    ) -> Optional[GrowthRecord]:
        """[start, end) 窗口内最新一条复盘记录。"""
        records = await growth_repository.list_by_type_between(
            db, user_id, RECORD_TYPE, start, end, limit=1
        )
        return records[0] if records else None

    async def _create_record(self, db: AsyncSession, user_id: str, content: str) -> GrowthRecord:
        return await growth_repository.create(db, user_id, RECORD_TYPE, content)

    @staticmethod
    def _steps(record: Optional[GrowthRecord]) -> dict:
        if not record or not record.metadata_json:
            return {}
        try:
            return json.loads(record.metadata_json)
        except (json.JSONDecodeError, TypeError):
            return {}

    @staticmethod
    async def _save_steps(db: AsyncSession, record: GrowthRecord, steps: dict) -> None:
        record.metadata_json = json.dumps(steps, ensure_ascii=False)
        await db.flush()

    @staticmethod
    def _is_completed(record: Optional[GrowthRecord]) -> bool:
        return bool(record and BedtimeService._steps(record).get("step3"))

    @staticmethod
    def _snapshot(record: Optional[GrowthRecord]) -> Optional[dict]:
        if not record:
            return None
        return {
            "id": record.id,
            "completed": BedtimeService._is_completed(record),
            "content": record.content,
            "steps": BedtimeService._steps(record),
            "created_at": record.created_at.isoformat() if record.created_at else None,
        }


bedtime_service = BedtimeService()
