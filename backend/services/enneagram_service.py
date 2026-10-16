"""
九型人格渐进式评估服务 (PRD P1-F03)。

36 题题库（9 型 × 每型 4 题，回合制交错下发）+ 断点续答状态机 + 计分定型 +
类型标签（zh/en/id）+ MBTI×九型关联表。

渐进式：一次一题，每答一题实时落库，可跨天 / 跨会话续答；
评估与 P0-F06 的 8 题 16 型评估共用 personality_assessments 表（kind='enneagram'）。
完成后类型写入 users.enneagram_type，供性格画像 / Pro 组合报告 / 分享名片引用。

措辞合规：九型为人格心理学框架，所有文案只做性格描述，不做运势预测。
"""
import json
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core import copy
from backend.core.errors import AppException, ErrorCode
from backend.ai.llm import llm
from backend.models.schemas import (
    AssessmentAnswerRequest,
    EnneagramAnswerResponse,
    EnneagramQuestion,
    EnneagramResultResponse,
    EnneagramStateResponse,
)
from backend.repositories import personality_repository, user_repository

logger = logging.getLogger("closenai.enneagram")

# ═══ 九型类型名与统一选项 ═══
# 类型名/选项的 15 语言展示走文案库（core/copy.py，enneagram.type.* / enneagram.option.*）；
# 这里保留合法类型集合与各类型题库归组依据。
VALID_TYPES = ("1", "2", "3", "4", "5", "6", "7", "8", "9")

# 选项文案键（core/copy.py enneagram.option.*：a=符合 / b=不符合 / neutral=不确定）
_OPTION_KEYS = ("a", "b", "neutral")

# ═══ 36 题题库（每型 4 题的文本走文案库 enneagram.q.{type}.{round}，15 语言）═══
ENNEAGRAM_QUESTIONS = [
    {"type": t, "key": f"q.{t}.{round_idx}"}
    for round_idx in range(4)
    for t in ("1", "2", "3", "4", "5", "6", "7", "8", "9")
]
ENNEAGRAM_TOTAL = len(ENNEAGRAM_QUESTIONS)

# ═══ MBTI×九型关联表（PRD P1-F03）═══
# 组合名的 15 语言展示走文案库 enneagram.combo.{MBTI}_{N}；此处仅保留合法组合键集。
MBTI_ENNEAGRAM_KEYS = frozenset({
    ("INTP", "5"), ("INTJ", "5"), ("INTJ", "1"), ("ENTP", "7"), ("ENTJ", "8"),
    ("ENTJ", "3"), ("INFP", "4"), ("INFP", "9"), ("INFJ", "4"), ("INFJ", "9"),
    ("ENFP", "7"), ("ENFP", "4"), ("ENFJ", "2"), ("ISTJ", "1"), ("ISTJ", "6"),
    ("ISFJ", "2"), ("ISFJ", "9"), ("ISTP", "5"), ("ISFP", "4"), ("ESTP", "8"),
    ("ESTJ", "8"), ("ESTJ", "3"), ("ESFJ", "2"), ("ESFP", "7"),
})


def enneagram_label_for(enneagram_type: str | None, language: str | None = None) -> str | None:
    """按语言返回九型类型名（15 语言文案库 enneagram.type.*；未知类型返回 None）。"""
    if (enneagram_type or "") not in VALID_TYPES:
        return None
    return copy.t("enneagram", f"type.{enneagram_type}", language)


def combo_name_for(
    mbti_type: str | None, enneagram_type: str | None, language: str | None = None
) -> str | None:
    """查 MBTI×九型关联表，返回按语言的组合名（文案库 enneagram.combo.*；未命中返回 None）。"""
    key = ((mbti_type or "").upper(), enneagram_type or "")
    if key not in MBTI_ENNEAGRAM_KEYS:
        return None
    return copy.t("enneagram", f"combo.{key[0]}_{key[1]}", language)


class EnneagramService:
    """九型人格渐进式评估服务。"""

    async def get_state(self, db: AsyncSession, user_id: str) -> EnneagramStateResponse:
        """查询评估状态：进行中 → 返回下一题；已完成（含手动输入过类型）→ 返回结果。"""
        user = await user_repository.require_user(db, user_id)
        language = user.language if user else "en"
        assessment = await personality_repository.get_in_progress_assessment(
            db, user_id, kind="enneagram"
        )
        if assessment:
            return self._state(assessment, language)
        if user.enneagram_type:
            return EnneagramStateResponse(
                status="completed",
                current_index=ENNEAGRAM_TOTAL,
                total=ENNEAGRAM_TOTAL,
                question=None,
                result=await self._result_for(db, user_id, user.enneagram_type, language),
            )
        return EnneagramStateResponse(
            status="not_started", current_index=0, total=ENNEAGRAM_TOTAL, question=None, result=None
        )

    async def start(self, db: AsyncSession, user_id: str) -> EnneagramStateResponse:
        """开始评估：有进行中评估则续答，否则新建一轮（重测亦走新建），返回当前题。"""
        user = await user_repository.require_user(db, user_id)
        assessment = await personality_repository.get_in_progress_assessment(
            db, user_id, kind="enneagram"
        )
        if not assessment:
            assessment = await personality_repository.create_assessment(
                db, user_id, kind="enneagram"
            )
        return self._state(assessment, user.language if user else "en")

    async def answer(
        self, db: AsyncSession, user_id: str, req: AssessmentAnswerRequest
    ) -> EnneagramAnswerResponse:
        """提交当前题答案：快捷选项直接计分；自由输入由 LLM lite 归类（失败回退中性）。

        答满 36 题后按 9 型计分定型，结果写入 users.enneagram_type
        （行锁串行化，防并发双击重复答题）。
        """
        user = await user_repository.require_user(db, user_id, for_update=True)
        assessment = await personality_repository.get_in_progress_assessment(
            db, user_id, kind="enneagram"
        )
        if not assessment:
            raise AppException(ErrorCode.BAD_REQUEST, "请先开始九型人格评估")
        answers = self._parse_answers(assessment.answers)
        index = len(answers)
        if index >= ENNEAGRAM_TOTAL:
            raise AppException(ErrorCode.BAD_REQUEST, "本轮评估已完成")
        language = user.language if user else "en"

        question = ENNEAGRAM_QUESTIONS[index]
        option = req.option
        if option not in ("a", "b", "neutral"):
            option = await self._classify_free_answer(question, req.answer or "", language)
        answers.append({"q": index, "option": option, "text": req.answer})

        completed = len(answers) >= ENNEAGRAM_TOTAL
        await personality_repository.save_assessment(
            db, assessment, json.dumps(answers, ensure_ascii=False),
            "completed" if completed else "in_progress",
        )
        if not completed:
            return EnneagramAnswerResponse(
                completed=False,
                next_question=self._question_payload(index + 1, language),
            )
        enneagram_type = self._type_from_answers(answers)
        await user_repository.update_settings(db, user_id, enneagram_type=enneagram_type)
        result = await self._result_for(db, user_id, enneagram_type, language)
        return EnneagramAnswerResponse(
            completed=True,
            next_question=None,
            result=result,
            message=copy.t("enneagram", "completed_message", language).format(
                type=enneagram_type, label=result.type_label
            ),
        )

    # ── 内部 helpers ──

    async def _result_for(
        self, db: AsyncSession, user_id: str, enneagram_type: str, language: str | None
    ) -> EnneagramResultResponse:
        """构建结果：类型 + 标签；有 16 型画像且命中关联表时附组合名。"""
        profile = await personality_repository.get_latest(db, user_id)
        mbti = profile.mbti_type if profile else None
        combo = combo_name_for(mbti, enneagram_type, language)
        return EnneagramResultResponse(
            enneagram_type=enneagram_type,
            type_label=enneagram_label_for(enneagram_type, language) or "",
            combo_name=combo,
            combo_mbti=mbti if combo else None,
        )

    def _state(self, assessment, language: str | None) -> EnneagramStateResponse:
        """由评估记录构建状态响应（current_index = 已答题数，question 为下一题）。"""
        answers = self._parse_answers(assessment.answers)
        index = len(answers)
        completed = assessment.status == "completed" or index >= ENNEAGRAM_TOTAL
        return EnneagramStateResponse(
            status="completed" if completed else "in_progress",
            current_index=index,
            total=ENNEAGRAM_TOTAL,
            question=None if completed else self._question_payload(index, language),
            result=None,
        )

    def _question_payload(self, index: int, language: str | None) -> EnneagramQuestion:
        """按用户语言下发题目（文案库 enneagram.q.*；不下发归属类型，防刷分）。"""
        q = ENNEAGRAM_QUESTIONS[index]
        return EnneagramQuestion(
            index=index,
            total=ENNEAGRAM_TOTAL,
            text=copy.t("enneagram", q["key"], language),
            options={k: copy.t("enneagram", f"option.{k}", language) for k in _OPTION_KEYS},
        )

    def _type_from_answers(self, answers: list) -> str:
        """9 型计分定型：a +1 / neutral +0.5 / b +0；平票取 a 票多者，再平取小号型。"""
        scores = {t: 0.0 for t in "123456789"}
        a_counts = {t: 0 for t in "123456789"}
        for a in answers:
            q_index = a.get("q")
            if not isinstance(q_index, int) or not 0 <= q_index < ENNEAGRAM_TOTAL:
                continue
            t = ENNEAGRAM_QUESTIONS[q_index]["type"]
            if a.get("option") == "a":
                scores[t] += 1.0
                a_counts[t] += 1
            elif a.get("option") == "neutral":
                scores[t] += 0.5
        return max(scores, key=lambda t: (scores[t], a_counts[t], -int(t)))

    @staticmethod
    def _parse_answers(raw: Optional[str]) -> list:
        if not raw:
            return []
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except Exception:  # noqa: BLE001
            return []

    async def _classify_free_answer(self, question: dict, text: str, language: str | None) -> str:
        """自由输入归类为 a/b/neutral（LLM lite，失败安全回退 neutral——中性只加半分，不偏置结果）。"""
        # 注入英文题面作为归类基准（prompt 本体为 LLM 指令，不强翻）
        prompt = (
            f"性格描述：「{copy.t('enneagram', question['key'], 'en')}」。\n"
            f"用户自由回答：「{text[:300]}」。\n"
            "判断该描述是否符合用户：符合输出 A，不符合输出 B，不确定/两者皆有输出 NEUTRAL。只输出 A、B 或 NEUTRAL。"
        )
        try:
            result = await llm.complete(prompt, language=language or "en", lite=True, max_tokens=10)
        except Exception as exc:  # noqa: BLE001
            logger.warning("九型评估自由输入归类失败，回退中性: %s", exc)
            return "neutral"
        verdict = result.strip().upper()
        if verdict.startswith("A"):
            return "a"
        if verdict.startswith("B"):
            return "b"
        return "neutral"


enneagram_service = EnneagramService()
