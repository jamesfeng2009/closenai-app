"""
性格画像服务 (P0-F06)。

对话式性格发现：冷启动进度 + 16 型人格画像生成 + 刷新配额 +
8 题对话式评估 + 星座/九型维度 + Pro 组合报告。

冷启动阶段（依据数据丰富度）：
    破冰 → 观察 → 探索 → 揭示 → 运用
"""
import json
import logging
from datetime import timedelta, timezone
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.errors import AppException, ErrorCode
from backend.core.llm import llm
from backend.core.time_utils import local_day_start_utc, local_now, utc_now
from backend.models.schemas import (
    AssessmentAnswerRequest,
    AssessmentAnswerResponse,
    AssessmentQuestion,
    AssessmentStateResponse,
    ComboReportResponse,
    PersonalityProfileResponse,
    PersonalityRefreshResponse,
)
from backend.repositories import (
    chat_repository,
    growth_repository,
    memory_repository,
    mood_repository,
    personality_repository,
    user_repository,
)
from backend.config import settings
from backend.services.safety_service import safety_service

logger = logging.getLogger("closenai.personality")

# MBTI 类型名（中文表；同时作为 16 型合法集合用于 LLM 结果校验）
MBTI_LABELS = {
    "INTP": "逻辑学家", "INTJ": "建筑师", "ENTP": "辩论家", "ENTJ": "指挥官",
    "INFP": "调停者", "INFJ": "提倡者", "ENFP": "竞选者", "ENFJ": "主人公",
    "ISTP": "鉴赏家", "ISFJ": "守卫者", "ESTP": "企业家", "ESFJ": "执政官",
    "ISTJ": "物流师", "ISFP": "探险家", "ESTJ": "总经理", "ESFP": "表演者",
}

# MBTI 类型名（英文表；非中文用户返回英文 label）
MBTI_LABELS_EN = {
    "INTP": "Logician", "INTJ": "Architect", "ENTP": "Debater", "ENTJ": "Commander",
    "INFP": "Mediator", "INFJ": "Advocate", "ENFP": "Campaigner", "ENFJ": "Protagonist",
    "ISTP": "Virtuoso", "ISFJ": "Defender", "ESTP": "Entrepreneur", "ESFJ": "Consul",
    "ISTJ": "Logistician", "ISFP": "Adventurer", "ESTJ": "Executive", "ESFP": "Entertainer",
}


def mbti_label_for(mbti_type: str | None, language: str | None = None) -> str | None:
    """按语言返回 MBTI 类型名：zh*（含 zh-CN）中文，其余英文。"""
    table = MBTI_LABELS if (language or "").startswith("zh") else MBTI_LABELS_EN
    return table.get(mbti_type or "", None)


# ═══ 8 题对话式评估（PRD P0-F06 §3：覆盖 4 维度，每维度 2 题交叉验证）═══
# 题目/选项文案：zh/en/id 三语（PRD 验收三语），其余语言回退英文
ASSESSMENT_QUESTIONS = [
    {
        "dimension": "EI",
        "text": {
            "zh": "周末如果没有安排，你更倾向一个人待着还是跟朋友出去？",
            "en": "If you have no plans for the weekend, would you rather spend it alone or go out with friends?",
            "id": "Kalau akhir pekan tidak ada rencana, kamu lebih suka sendirian atau pergi dengan teman?",
        },
        "options": {
            "a": {"pole": "I", "label": {"zh": "一个人待着", "en": "Stay alone", "id": "Sendirian"}},
            "b": {"pole": "E", "label": {"zh": "跟朋友出去", "en": "Go out with friends", "id": "Pergi dengan teman"}},
        },
    },
    {
        "dimension": "TF",
        "text": {
            "zh": "当朋友跟你倾诉烦恼时，你更倾向于帮 TA 分析解决方案还是先安慰情绪？",
            "en": "When a friend shares their troubles, do you first help analyze solutions or comfort their feelings?",
            "id": "Saat teman curhat soal masalahnya, kamu lebih dulu membantu menganalisis solusi atau menenangkan perasaannya?",
        },
        "options": {
            "a": {"pole": "T", "label": {"zh": "分析解决方案", "en": "Analyze solutions", "id": "Menganalisis solusi"}},
            "b": {"pole": "F", "label": {"zh": "先安慰情绪", "en": "Comfort first", "id": "Menenangkan dulu"}},
        },
    },
    {
        "dimension": "TF",
        "text": {
            "zh": "你做决定时，更看重「合不合理」还是「感觉对不对」？",
            "en": "When making decisions, do you care more about whether it's logical or whether it feels right?",
            "id": "Saat mengambil keputusan, kamu lebih mementingkan 'apakah masuk akal' atau 'apakah terasa benar'?",
        },
        "options": {
            "a": {"pole": "T", "label": {"zh": "合不合理", "en": "Whether it's logical", "id": "Masuk akal"}},
            "b": {"pole": "F", "label": {"zh": "感觉对不对", "en": "Whether it feels right", "id": "Terasa benar"}},
        },
    },
    {
        "dimension": "JP",
        "text": {
            "zh": "你更喜欢计划好每件事，还是随性而为？",
            "en": "Do you prefer planning everything out, or going with the flow?",
            "id": "Kamu lebih suka merencanakan semuanya, atau mengikuti arus?",
        },
        "options": {
            "a": {"pole": "J", "label": {"zh": "计划好每件事", "en": "Plan everything", "id": "Merencanakan semuanya"}},
            "b": {"pole": "P", "label": {"zh": "随性而为", "en": "Go with the flow", "id": "Mengikuti arus"}},
        },
    },
    {
        "dimension": "SN",
        "text": {
            "zh": "你更关注事情的细节，还是大局方向？",
            "en": "Do you focus more on the details, or the big picture?",
            "id": "Kamu lebih fokus pada detail, atau gambaran besarnya?",
        },
        "options": {
            "a": {"pole": "S", "label": {"zh": "细节", "en": "Details", "id": "Detail"}},
            "b": {"pole": "N", "label": {"zh": "大局方向", "en": "Big picture", "id": "Gambaran besar"}},
        },
    },
    {
        "dimension": "EI",
        "text": {
            "zh": "到了新环境，你更习惯先观察还是直接融入？",
            "en": "In a new environment, do you tend to observe first or jump right in?",
            "id": "Di lingkungan baru, kamu lebih terbiasa mengamati dulu atau langsung bergabung?",
        },
        "options": {
            "a": {"pole": "I", "label": {"zh": "先观察", "en": "Observe first", "id": "Mengamati dulu"}},
            "b": {"pole": "E", "label": {"zh": "直接融入", "en": "Jump right in", "id": "Langsung bergabung"}},
        },
    },
    {
        "dimension": "SN",
        "text": {
            "zh": "学习新东西时，你更喜欢先理解理论框架，还是直接动手实践？",
            "en": "When learning something new, do you prefer understanding the theory first, or practicing hands-on right away?",
            "id": "Saat belajar hal baru, kamu lebih suka memahami kerangka teorinya dulu, atau langsung praktik?",
        },
        "options": {
            "a": {"pole": "N", "label": {"zh": "先理解理论框架", "en": "Theory first", "id": "Teori dulu"}},
            "b": {"pole": "S", "label": {"zh": "直接动手实践", "en": "Hands-on first", "id": "Langsung praktik"}},
        },
    },
    {
        "dimension": "JP",
        "text": {
            "zh": "面对压力时，你更倾向于先行动还是先想清楚？",
            "en": "Under pressure, do you tend to act first or think it through first?",
            "id": "Saat menghadapi tekanan, kamu lebih cenderung bertindak dulu atau memikirkannya dulu?",
        },
        "options": {
            "a": {"pole": "P", "label": {"zh": "先行动", "en": "Act first", "id": "Bertindak dulu"}},
            "b": {"pole": "J", "label": {"zh": "先想清楚", "en": "Think it through", "id": "Memikirkannya dulu"}},
        },
    },
]

# 中性选项文案（PRD：「两者都有/看情况」标记为边界型，由另一题交叉验证）
NEUTRAL_OPTION_LABEL = {
    "zh": "两者都有 / 看情况",
    "en": "Both / it depends",
    "id": "Keduanya / tergantung",
}

ASSESSMENT_TOTAL = len(ASSESSMENT_QUESTIONS)

# 太阳星座区间表（按起始月日升序，3/21 起；1-2 月在 zodiac_sign_for 中单独处理）
_ZODIAC_PERIODS = [
    (3, 21, "aries"), (4, 20, "taurus"), (5, 21, "gemini"), (6, 21, "cancer"),
    (7, 23, "leo"), (8, 23, "virgo"), (9, 23, "libra"), (10, 23, "scorpio"),
    (11, 22, "sagittarius"), (12, 22, "capricorn"),
]


def zodiac_sign_for(birthday) -> str | None:
    """由生日（月/日）推导太阳星座 key（aries 等）；无生日返回 None。"""
    if not birthday:
        return None
    md = (birthday.month, birthday.day)
    for month, day, sign in reversed(_ZODIAC_PERIODS):
        if md >= (month, day):
            return sign
    if md >= (2, 19):
        return "pisces"
    if md >= (1, 20):
        return "aquarius"
    return "capricorn"


class PersonalityService:
    """性格画像服务。"""

    async def get_profile(self, db: AsyncSession, user_id: str) -> PersonalityProfileResponse:
        """获取性格画像（含冷启动进度，无画像时返回冷启动占位；mbti_label 按用户语言）。"""
        user = await user_repository.require_user(db, user_id)
        profile = await personality_repository.get_latest(db, user_id)
        quota_remaining = await self._refresh_quota_remaining(db, user_id)
        progress, stage = await self._cold_start(db, user_id)
        zodiac = zodiac_sign_for(user.birthday if user else None)
        enneagram = user.enneagram_type if user else None

        if profile:
            observations = self._parse_observations(profile.observations)
            return PersonalityProfileResponse(
                id=profile.id,
                mbti_type=profile.mbti_type,
                mbti_label=mbti_label_for(profile.mbti_type, user.language if user else None),
                observations=observations,
                unique_traits=profile.unique_traits,
                version=profile.version,
                refresh_quota_remaining=quota_remaining,
                cold_start_progress=progress,
                cold_start_stage=stage,
                zodiac=zodiac,
                enneagram_type=enneagram,
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
            zodiac=zodiac,
            enneagram_type=enneagram,
            created_at=utc_now(),
        )

    async def refresh(self, db: AsyncSession, user_id: str) -> PersonalityRefreshResponse:
        """刷新性格画像（配额校验 + 无新数据免扣次数 + LLM 生成）。"""
        # 行锁串行化：配额校验与画像版本写入在同一事务内完成，防并发超配额
        await user_repository.require_user(db, user_id, for_update=True)
        quota_remaining = await self._refresh_quota_remaining(db, user_id)
        if quota_remaining <= 0:
            raise AppException(ErrorCode.QUOTA_EXCEEDED, "性格画像刷新次数已用完")

        user = await user_repository.get_user(db, user_id)
        zh = (user.language if user else "").startswith("zh")

        # PRD P0-F06 §6：距上次画像生成后无新记忆/新签到/新对话（7 天窗口）时不扣次数
        latest = await personality_repository.get_latest(db, user_id)
        if latest:
            threshold = self._aware(latest.created_at)
            window_start = utc_now() - timedelta(days=7)
            if threshold < window_start:
                threshold = window_start
            new_data = (
                await memory_repository.count_since(db, user_id, threshold)
                + await mood_repository.count_since(db, user_id, threshold)
                + await chat_repository.count_since(db, user_id, threshold, role="user")
            )
            if new_data == 0:
                return PersonalityRefreshResponse(
                    success=False,
                    profile=await self.get_profile(db, user_id),
                    message=(
                        "暂无新数据，多聊聊再来看看吧" if zh
                        else "No new data yet — chat a bit more and come back."
                    ),
                )

        memories = await memory_repository.list(db, user_id, limit=30)
        checkins = await mood_repository.get_recent(db, user_id, limit=30)

        data_desc = "\n".join(f"- {m.content}" for m in memories) or "暂无记忆。"
        mood_desc = " ".join(f"{c.mood_emoji}{c.mood_label or ''}" for c in checkins) or "暂无情绪记录。"
        prompt = (
            f"根据以下用户数据，推断其 16 型人格类型（如 INTP；返回格式：类型|观察1；观察2；观察3|独特之处1-2句）。\n"
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

    # ── 8 题对话式评估（PRD P0-F06 §3）──

    async def get_assessment(self, db: AsyncSession, user_id: str) -> AssessmentStateResponse:
        """查询评估状态（无进行中评估返回 not_started，question 为 None）。"""
        user = await user_repository.require_user(db, user_id)
        assessment = await personality_repository.get_in_progress_assessment(db, user_id)
        if not assessment:
            return AssessmentStateResponse(
                status="not_started", current_index=0, total=ASSESSMENT_TOTAL, question=None
            )
        return self._assessment_state(assessment, user.language if user else "en")

    async def start_assessment(self, db: AsyncSession, user_id: str) -> AssessmentStateResponse:
        """开始评估：有进行中评估则续答，否则新建一轮，返回第 1 题。"""
        user = await user_repository.require_user(db, user_id)
        assessment = await personality_repository.get_in_progress_assessment(db, user_id)
        if not assessment:
            assessment = await personality_repository.create_assessment(db, user_id)
        return self._assessment_state(assessment, user.language if user else "en")

    async def answer_assessment(
        self, db: AsyncSession, user_id: str, req: AssessmentAnswerRequest
    ) -> AssessmentAnswerResponse:
        """提交当前题答案：快捷选项直接计分；自由输入由 LLM lite 归类（失败回退中性）。

        答满 8 题后按 4 维度计分推断类型，并结合近期记忆/情绪生成画像（首次画像免费，不扣刷新配额）。
        """
        user = await user_repository.require_user(db, user_id, for_update=True)
        assessment = await personality_repository.get_in_progress_assessment(db, user_id)
        if not assessment:
            raise AppException(ErrorCode.BAD_REQUEST, "请先开始性格评估")
        answers = self._parse_answers(assessment.answers)
        index = len(answers)
        if index >= ASSESSMENT_TOTAL:
            raise AppException(ErrorCode.BAD_REQUEST, "本轮评估已完成")
        language = user.language if user else "en"
        zh = (language or "").startswith("zh")

        question = ASSESSMENT_QUESTIONS[index]
        option = req.option
        if option not in ("a", "b", "neutral"):
            option = await self._classify_free_answer(question, req.answer or "", language)
        pole = question["options"][option]["pole"] if option in ("a", "b") else None
        answers.append({"q": index, "option": option, "pole": pole, "text": req.answer})

        completed = len(answers) >= ASSESSMENT_TOTAL
        await personality_repository.save_assessment(
            db, assessment, json.dumps(answers, ensure_ascii=False),
            "completed" if completed else "in_progress",
        )
        if not completed:
            return AssessmentAnswerResponse(
                completed=False,
                next_question=self._question_payload(index + 1, language),
            )
        profile = await self._generate_profile_from_answers(db, user, answers)
        return AssessmentAnswerResponse(
            completed=True,
            next_question=None,
            profile=profile,
            message=(
                "已根据你的回答生成性格画像" if zh
                else "Your personality profile has been generated from your answers"
            ),
        )

    def _assessment_state(self, assessment, language: str | None) -> AssessmentStateResponse:
        """由评估记录构建状态响应（current_index = 已答题数，question 为下一题）。"""
        answers = self._parse_answers(assessment.answers)
        index = len(answers)
        completed = assessment.status == "completed" or index >= ASSESSMENT_TOTAL
        return AssessmentStateResponse(
            status="completed" if completed else "in_progress",
            current_index=index,
            total=ASSESSMENT_TOTAL,
            question=None if completed else self._question_payload(index, language),
        )

    def _question_payload(self, index: int, language: str | None) -> AssessmentQuestion:
        """按用户语言下发题目（zh/en/id，其余回退英文）。"""
        q = ASSESSMENT_QUESTIONS[index]
        return AssessmentQuestion(
            index=index,
            total=ASSESSMENT_TOTAL,
            dimension=q["dimension"],
            text=self._pick(q["text"], language),
            options={
                "a": self._pick(q["options"]["a"]["label"], language),
                "b": self._pick(q["options"]["b"]["label"], language),
                "neutral": self._pick(NEUTRAL_OPTION_LABEL, language),
            },
        )

    @staticmethod
    def _pick(texts: dict, language: str | None) -> str:
        """三语文案选择：zh* → zh，id → id，其余 → en。"""
        lang = language or "en"
        if lang.startswith("zh"):
            return texts["zh"]
        if lang.startswith("id"):
            return texts.get("id") or texts["en"]
        return texts["en"]

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
        """自由输入归类为 a/b/neutral（LLM lite，失败安全回退 neutral——PRD：中性由另一题交叉验证）。"""
        prompt = (
            f"性格评估问题：{question['text']['en']}\n"
            f"选项A：{question['options']['a']['label']['en']}；选项B：{question['options']['b']['label']['en']}。\n"
            f"用户自由回答：「{text[:300]}」。\n"
            "判断用户更倾向哪个选项；若两者皆有或无法判断则为中性。只输出 A、B 或 NEUTRAL。"
        )
        try:
            result = await llm.complete(prompt, language=language or "en", lite=True, max_tokens=10)
        except Exception as exc:  # noqa: BLE001
            logger.warning("评估自由输入归类失败，回退中性: %s", exc)
            return "neutral"
        verdict = result.strip().upper()
        if verdict.startswith("A"):
            return "a"
        if verdict.startswith("B"):
            return "b"
        return "neutral"

    def _type_from_answers(self, answers: list) -> Optional[str]:
        """按 4 维度计分推断类型（平票取该维度首字母 E/S/T/J；全部为中性时仍给出类型）。"""
        scores = {pole: 0 for pole in "EISNTFJP"}
        for a in answers:
            pole = a.get("pole")
            if pole in scores:
                scores[pole] += 1
        letters = [
            "E" if scores["E"] >= scores["I"] else "I",
            "S" if scores["S"] >= scores["N"] else "N",
            "T" if scores["T"] >= scores["F"] else "F",
            "J" if scores["J"] >= scores["P"] else "P",
        ]
        return "".join(letters)

    async def _generate_profile_from_answers(
        self, db: AsyncSession, user, answers: list
    ) -> PersonalityProfileResponse:
        """答题数据 + 近期记忆/情绪喂 LLM 生成画像（失败回退：答题统计类型 + 模板观察）。"""
        language = user.language if user else "en"
        zh = (language or "").startswith("zh")
        stats_type = self._type_from_answers(answers)
        qa_lines = []
        for a in answers:
            q = ASSESSMENT_QUESTIONS[a["q"]]
            if a["option"] in ("a", "b"):
                ans_text = q["options"][a["option"]]["label"]["en"]
            else:
                ans_text = a.get("text") or "Both / neutral"
            qa_lines.append(f"Q: {q['text']['en']} A: {ans_text}")
        memories = await memory_repository.list(db, user.id, limit=30)
        checkins = await mood_repository.get_recent(db, user.id, limit=30)
        data_desc = "\n".join(f"- {m.content}" for m in memories) or "暂无记忆。"
        mood_desc = " ".join(f"{c.mood_emoji}{c.mood_label or ''}" for c in checkins) or "暂无情绪记录。"
        prompt = (
            "根据以下性格问答与用户数据，推断其 16 型人格类型"
            f"（返回格式：类型|观察1；观察2；观察3|独特之处1-2句）。\n"
            f"答题统计推断：{stats_type or '未知'}（可参考或修正）\n"
            f"用户语言：{language}\n性格问答：\n" + "\n".join(qa_lines) +
            f"\n用户记忆：\n{data_desc}\n最近情绪：{mood_desc}"
        )
        mbti_type, observations, unique_traits = None, [], None
        try:
            text = await llm.complete(prompt, language=language or "en", lite=True, max_tokens=200)
            mbti_type, observations, unique_traits = self._parse_llm_result(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("评估画像 LLM 失败，回退答题统计类型: %s", exc)
        if not mbti_type:
            mbti_type = stats_type
        if not observations:
            observations = [
                "你完成了 8 题对话式评估，AI 会随对话继续校准。" if zh
                else "You completed the 8-question conversational assessment; the AI will keep calibrating as you chat."
            ]
        versions = await personality_repository.list(db, user.id)
        new_version = (versions[-1].version + 1) if versions else 1
        await personality_repository.create(
            db, user.id, mbti_type, json.dumps(observations, ensure_ascii=False),
            unique_traits, new_version,
        )
        return await self.get_profile(db, user.id)

    # ── Pro 组合报告（PRD P0-F06 §8：16 型人格 × 星座 × 九型交叉解读）──

    async def generate_combo_report(self, db: AsyncSession, user_id: str) -> ComboReportResponse:
        """生成 Pro 组合报告（每月 1 份，按用户本地月缓存控成本；LLM lite，失败回退模板）。"""
        user = await user_repository.require_user(db, user_id, for_update=True)
        if (user.tier or "free") not in ("pro", "pro_plus"):
            raise AppException(ErrorCode.PRO_REQUIRED, "组合报告为 Pro 专属功能，升级后可用")
        profile = await personality_repository.get_latest(db, user_id)
        if not profile or not profile.mbti_type:
            raise AppException(ErrorCode.BAD_REQUEST, "请先完成性格评估生成画像")

        zodiac = zodiac_sign_for(user.birthday)
        enneagram = user.enneagram_type

        # 月度缓存：本月已生成则直接返回
        now_local = local_now(user.timezone)
        month_start = local_day_start_utc(user.timezone, now_local.date().replace(day=1))
        cached = await growth_repository.get_latest(db, user_id, "combo_report")
        if cached and self._aware(cached.created_at) >= month_start:
            return ComboReportResponse(
                content=cached.content,
                cached=True,
                generated_at=cached.created_at,
                personality_type=profile.mbti_type,
                zodiac=zodiac,
                enneagram_type=enneagram,
            )

        language = user.language or "en"
        zh = language.startswith("zh")
        mbti_label = mbti_label_for(profile.mbti_type, language)
        observations = self._parse_observations(profile.observations)
        zodiac_desc = zodiac or ("未知（未填生日）" if zh else "unknown (birthday not set)")
        enneagram_desc = (
            f"{enneagram} 号" if zh and enneagram
            else f"Type {enneagram}" if enneagram
            else "未知" if zh else "unknown"
        )
        prompt = (
            "你是一位温暖、有洞察力的成长教练。请把用户的三个性格框架做交叉解读（3-5 句话），"
            "指出它们共同指向的特质与一条成长建议。只做性格框架解读，不做运势预测，不使用算命/占卜措辞：\n"
            f"16 型人格：{profile.mbti_type}（{mbti_label or ''}）\n"
            f"太阳星座：{zodiac_desc}\n"
            f"九型人格：{enneagram_desc}\n"
            f"AI 观察：{'；'.join(observations) or '暂无'}\n"
            f"独特之处：{profile.unique_traits or '暂无'}"
        )
        content = ""
        try:
            content = (await llm.complete(prompt, language=language, lite=True, max_tokens=300)).strip()
            # L3 输出过滤：命中回退模板
            content = await safety_service.check_or(content, language, "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("组合报告 LLM 失败，回退模板: %s", exc)
        if not content:
            content = self._fallback_combo(profile.mbti_type, mbti_label, zodiac_desc, enneagram_desc, zh)
        record = await growth_repository.create(db, user_id, "combo_report", content)
        return ComboReportResponse(
            content=content,
            cached=False,
            generated_at=record.created_at,
            personality_type=profile.mbti_type,
            zodiac=zodiac,
            enneagram_type=enneagram,
        )

    def _fallback_combo(self, mbti_type, mbti_label, zodiac_desc, enneagram_desc, zh: bool) -> str:
        """组合报告回退模板（LLM 不可用时；措辞合规：只讲性格框架，不谈运势）。"""
        if zh:
            return (
                f"你的性格类型是 {mbti_label or mbti_type}，九型 {enneagram_desc}，太阳星座 {zodiac_desc}。"
                "三个框架从不同角度描述你的思考与感受方式——继续记录和对话，画像会越来越准。"
            )
        return (
            f"Your personality type is {mbti_label or mbti_type}, Enneagram {enneagram_desc}, "
            f"Sun sign {zodiac_desc}. Each framework describes your way of thinking and feeling "
            "from a different angle — keep recording and chatting, and your profile will keep sharpening."
        )

    @staticmethod
    def _aware(dt):
        """naive datetime 按 UTC 补齐时区（SQLite 读出为 naive，PG 为 aware）。"""
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    async def _refresh_quota_remaining(self, db: AsyncSession, user_id: str) -> int:
        """计算剩余刷新配额（免费 3 次总计，Pro 每月 5 次）。

        评估完成生成的画像版本"免费"（见 answer_assessment），按已完成评估数剔除，
        剩余版本才计入手动刷新消耗。
        """
        user = await user_repository.get_user(db, user_id)
        tier = user.tier if user else "free"
        versions = await personality_repository.list(db, user_id)
        assessments = await personality_repository.list_completed_assessments(db, user_id)
        if tier in ("pro", "pro_plus"):
            # 本月已刷新次数（按用户本地月份，created_at 统一换算到用户时区）
            now = local_now(user.timezone if user else None)

            def _in_local_month(dt) -> bool:
                aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                local_dt = aware.astimezone(now.tzinfo)
                return local_dt.year == now.year and local_dt.month == now.month

            month_versions = sum(1 for v in versions if _in_local_month(v.created_at))
            month_assessments = sum(1 for a in assessments if _in_local_month(a.updated_at))
            quota = settings.pro_personality_refreshes if tier == "pro" else 999
            return max(0, quota - max(0, month_versions - month_assessments))
        # free：总计 3 次
        used = max(0, len(versions) - len(assessments))
        return max(0, settings.free_personality_refreshes - used)

    async def _cold_start(self, db: AsyncSession, user_id: str) -> tuple:
        """计算冷启动进度 (0.0-1.0) 与阶段。"""
        memories = await memory_repository.list(db, user_id, limit=100)
        checkins = await mood_repository.get_recent(db, user_id, limit=100)
        data_points = len(memories) + len(checkins)
        # 数据量映射到进度：0~30 条 → 0.0~1.0
        progress = min(1.0, data_points / 30.0)
        # 返回阶段下标（0-4）而非中文文案，前端按下标高亮，多语言安全
        idx = min(int(progress * 5), 4)
        return round(progress, 2), idx

    def _parse_observations(self, raw: Optional[str]) -> List[str]:
        if not raw:
            return []
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except Exception:  # noqa: BLE001
            return []

    def _parse_llm_result(self, text: str) -> tuple:
        """解析 LLM 输出：类型|观察1；观察2|独特之处。非法类型回退 None（未知）。"""
        parts = [p.strip() for p in text.split("|")]
        mbti = None
        # 提取 4 位 MBTI 字母
        candidate = "".join(ch for ch in (parts[0] if parts else "") if ch.upper() in "EISNTFJP")
        if len(candidate) >= 4:
            candidate = candidate[:4].upper()
            # 仅接受 16 型合法集合内的类型，非法值回退 None（前端兼容 None=未知）
            if candidate in MBTI_LABELS:
                mbti = candidate
        observations = parts[1].split("；") if len(parts) > 1 else []
        observations = [o.strip() for o in observations if o.strip()]
        unique_traits = parts[2] if len(parts) > 2 else None
        return mbti, observations, unique_traits


personality_service = PersonalityService()