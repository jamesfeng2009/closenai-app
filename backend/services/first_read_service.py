"""
性格初读卡（PRD §5.19.2 B01 · 架构方案 §二 M1）。

静态模板直出：断言 = 复述并放大用户对 3 道快问的自述，**零 LLM 调用**
（架构方案 §二 M1：零成本、确定性、零合规风险——内容只是用户自己的话）。
文案出口统一过 `safety_service.filter_template()`（§六-1；静态模板只用确定性
词表/句式闸，语义分类器留给不可控的 LLM 产出）。

生成率：PRD §5.19.2 要求 onboarding → 初读卡生成率 100%，因此稀疏作答或
选项无法解析时用 `fillers` 兜底补至 MIN_ASSERTIONS 条，永不返回空卡。

D 级标签（MBTI）在本卡里只描述用户自己，不用于预测或定性第三方（措辞规范 §1.1）。
"""
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.core.errors import AppException, ErrorCode
from backend.core.referral import referral_link
from backend.core.time_utils import local_today, utc_now
from backend.repositories import onboarding_repository, user_repository
from backend.services.journey_service import journey_service
from backend.ai.safety import safety_service

logger = logging.getLogger("closenai.first_read")

# 断言条数区间（PRD §5.19.2 B01：2–3 条）
MIN_ASSERTIONS = 2
MAX_ASSERTIONS = 3

# 模板库目录：backend/data/first_read/{locale}.json
DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "first_read"

# 模板只有 zh / en 两版（措辞规范 §六 决策点 3 的词表口径同步）；其余语言回退 en
_LOCALES = ("zh", "en")
_DEFAULT_LOCALE = "en"

_TEMPLATES: Dict[str, dict] = {}


def locale_for(language: Optional[str]) -> str:
    lang = (language or "").lower()
    return "zh" if lang.startswith("zh") else _DEFAULT_LOCALE


def load_template(language: Optional[str]) -> dict:
    """按语言取模板（带进程内缓存）；无对应 locale 文件时回退 en。"""
    locale = locale_for(language)
    cached = _TEMPLATES.get(locale)
    if cached is not None:
        return cached
    try:
        with (DATA_DIR / f"{locale}.json").open(encoding="utf-8") as f:
            template = json.load(f)
    except FileNotFoundError:
        if locale == _DEFAULT_LOCALE:
            raise AppException(
                ErrorCode.INTERNAL_ERROR, "初读卡模板缺失 / first-read template missing"
            )
        logger.warning("初读卡模板缺少 %s，回退 %s", locale, _DEFAULT_LOCALE)
        return load_template(_DEFAULT_LOCALE)
    _TEMPLATES[locale] = template
    return template


class FirstReadService:
    """初读卡生成与存取。"""

    def gate(self, text: str, locale: str) -> str:
        """静态文案出口闸：命中措辞规范则丢弃该条（返回 ""）。"""
        return safety_service.filter_template(text, locale)

    def _resolve_option(
        self, question: dict, option_id: str, option_text: str
    ) -> Optional[tuple]:
        """把答案项解析为 (option_id, option_text)：优先按 id，其次按选项文案反查。"""
        options = question.get("options") or []
        for opt in options:
            if option_id and opt.get("id") == option_id:
                return opt.get("id"), opt.get("text")
        if option_text:
            for opt in options:
                if opt.get("text") == option_text:
                    return opt.get("id"), opt.get("text")
        return None

    def _assertions(self, template: dict, answers: List[dict], locale: str) -> List[str]:
        """按作答顺序生成断言（复述 + 放大），无法解析的项跳过。"""
        questions = {q.get("id"): q for q in template.get("questions") or []}
        slots = template.get("assertions") or {}
        out: List[str] = []
        for item in answers or []:
            if not isinstance(item, dict):
                continue
            q_id = item.get("q") or item.get("question_id") or item.get("question")
            question = questions.get(q_id)
            if question is None:
                logger.info("初读卡跳过未知题目: %s", q_id)
                continue
            resolved = self._resolve_option(
                question,
                item.get("option") or item.get("option_id") or "",
                item.get("text") or "",
            )
            if resolved is None:
                logger.info("初读卡跳过未知选项: q=%s option=%s", q_id, item.get("option"))
                continue
            opt_id, opt_text = resolved
            pattern = (slots.get(q_id) or {}).get(opt_id)
            if not pattern:
                logger.info("初读卡缺少断言槽位: q=%s option=%s", q_id, opt_id)
                continue
            text = self.gate(pattern.format(option=opt_text), locale)
            if text and text not in out:
                out.append(text)
        return out[:MAX_ASSERTIONS]

    def _summary(self, template: dict, persona_type: Optional[str], locale: str) -> str:
        """D 级标签仅描述用户自己（措辞规范 §1.1）；未选类型则不出 summary。"""
        personas = template.get("personas") or {}
        trait = personas.get((persona_type or "").upper())
        if not trait:
            return ""
        return self.gate(
            template.get("summary_frame", "").format(persona=persona_type.upper(), trait=trait),
            locale,
        )

    def build_first_read(
        self,
        answers: List[dict],
        persona_type: Optional[str] = None,
        language: str = "en",
    ) -> dict:
        """生成初读卡内容（架构方案 §二 M1 的 `{assertions[2–3], summary, card_payload}`）。"""
        locale = locale_for(language)
        template = load_template(language)

        assertions = self._assertions(template, answers, locale)
        fillers = template.get("fillers") or []
        idx = 0
        while len(assertions) < MIN_ASSERTIONS and idx < len(fillers):
            text = self.gate(fillers[idx], locale)
            if text:
                assertions.append(text)
            idx += 1

        summary = self._summary(template, persona_type, locale)
        closer = self.gate(template.get("closer", ""), locale)
        card_payload = {
            "card_type": "first_read",
            "language": locale,
            "persona_type": (persona_type or "").upper() or None,
            "assertions": assertions,
            "summary": summary,
            "closer": closer,
            "answer_count": len(assertions),
            # 这个值会**落进 first_read 的列**（restore 按列复现、不再回查设置），
            # 所以它进了 N8 夹具的字节面：换口径必须重录，两侧同形由 Go 的 ShareLandingURL 守。
            "share_url": settings.share_landing_url,
            "generated_at": utc_now().isoformat(),
        }
        return {
            "assertions": assertions,
            "summary": summary,
            "closer": closer,
            "card_payload": card_payload,
        }

    async def submit(
        self,
        db: AsyncSession,
        user_id: str,
        persona_type: Optional[str],
        answers: List[dict],
        language: str,
    ) -> dict:
        """生成并落库（每用户一行，重复提交以最后一次为准；repository 内 flush）。"""
        card = self.build_first_read(answers, persona_type, language)
        user = await user_repository.get_user(db, user_id)
        if user is not None:
            # 分享落地链接带本人推荐码（M4 归因）：点这张卡的人装完 App 能回溯到分享者。
            # 必须在 upsert 之前写死——restore 按落库的 card_payload 复现，事后改不落盘。
            card["card_payload"]["share_url"] = referral_link(
                settings.site_url, await user_repository.ensure_referral_code(db, user)
            )
        profile = await onboarding_repository.upsert(
            db, user_id, persona_type, answers, card, language
        )
        # 架构方案 §二 M2：第一周剧本在 **onboarding 完成事务内**预写 pending 行，
        # 不由 cron 现算。幂等：重放/重新做引导只更新计划时刻，不会产生第二条同类事件。
        if user is not None:
            await journey_service.prefill(db, user, base_day=local_today(user.timezone))
        data = dict(card["card_payload"])
        data["id"] = profile.id
        return data

    async def restore(self, db: AsyncSession, user_id: str) -> Optional[dict]:
        """重进 App 恢复初读卡：按生成当时的语言复现，不随偏好切换漂移（models/journey.py 注释）。"""
        profile = await onboarding_repository.get(db, user_id)
        if profile is None:
            return None
        try:
            card = json.loads(profile.first_read or "{}")
        except json.JSONDecodeError:
            logger.exception("初读卡存储解析失败 user=%s", user_id)
            return None
        payload = dict(card.get("card_payload") or {})
        payload.update(
            {
                "id": profile.id,
                "assertions": card.get("assertions") or [],
                "summary": card.get("summary") or "",
                "closer": card.get("closer") or "",
                "language": profile.language,
                "created_at": profile.created_at,
            }
        )
        return payload


first_read_service = FirstReadService()
