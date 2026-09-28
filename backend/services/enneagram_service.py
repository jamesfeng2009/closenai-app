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

from backend.core.errors import AppException, ErrorCode
from backend.core.llm import llm
from backend.models.schemas import (
    AssessmentAnswerRequest,
    EnneagramAnswerResponse,
    EnneagramQuestion,
    EnneagramResultResponse,
    EnneagramStateResponse,
)
from backend.repositories import personality_repository, user_repository

logger = logging.getLogger("closenai.enneagram")

# ═══ 九型类型标签（zh/en/id；PRD：1号完美主义者 ~ 9号和平使者）═══
ENNEAGRAM_LABELS = {
    "1": {"zh": "完美主义者", "en": "Perfectionist", "id": "Perfeksionis"},
    "2": {"zh": "给予者", "en": "Helper", "id": "Penolong"},
    "3": {"zh": "成就者", "en": "Achiever", "id": "Pencapaian"},
    "4": {"zh": "浪漫主义者", "en": "Individualist", "id": "Individualis"},
    "5": {"zh": "探索者", "en": "Investigator", "id": "Penyelidik"},
    "6": {"zh": "忠诚者", "en": "Loyalist", "id": "Loyalis"},
    "7": {"zh": "享乐主义者", "en": "Enthusiast", "id": "Antusias"},
    "8": {"zh": "挑战者", "en": "Challenger", "id": "Penantang"},
    "9": {"zh": "和平使者", "en": "Peacemaker", "id": "Pendamai"},
}

# 统一选项（陈述式题目：符合 / 不符合 / 不确定）
ENNEAGRAM_OPTION_LABELS = {
    "a": {"zh": "符合我", "en": "Sounds like me", "id": "Sesuai dengan saya"},
    "b": {"zh": "不符合我", "en": "Not like me", "id": "Tidak sesuai dengan saya"},
    "neutral": {"zh": "不确定 / 看情况", "en": "Not sure / it depends", "id": "Ragu / tergantung"},
}

# ═══ 36 题题库（每型 4 题；zh/en/id，其余语言回退英文）═══
_QUESTIONS_BY_TYPE = {
    "1": [
        {"zh": "我很难忍受事情做得不够正确或不够妥当。",
         "en": "I find it hard to tolerate things not being done correctly or properly.",
         "id": "Saya sulit mentolerir hal yang tidak dikerjakan dengan benar atau semestinya."},
        {"zh": "我经常第一个注意到环境里需要改进或纠正的地方。",
         "en": "I'm often the first to notice things around me that need improving or fixing.",
         "id": "Saya sering menjadi orang pertama yang menyadari hal-hal di sekitar yang perlu diperbaiki."},
        {"zh": "即使很累，我也很难在任务完成前允许自己休息。",
         "en": "Even when tired, I find it hard to allow myself rest before the task is done.",
         "id": "Meski lelah, saya sulit mengizinkan diri beristirahat sebelum tugas selesai."},
        {"zh": "我内心常有一个声音，指出自己哪里做得不够好。",
         "en": "I often have an inner voice pointing out where I fell short.",
         "id": "Sering ada suara dalam hati yang menunjukkan kekurangan saya."},
    ],
    "2": [
        {"zh": "别人遇到困难时，我常常是第一个主动伸出援手的人。",
         "en": "When others are in trouble, I'm often the first to offer help.",
         "id": "Saat orang lain kesulitan, saya sering menjadi orang pertama yang menawarkan bantuan."},
        {"zh": "我很容易察觉到别人的情绪和需求，哪怕他们没说出口。",
         "en": "I easily sense others' feelings and needs, even when unspoken.",
         "id": "Saya mudah merasakan perasaan dan kebutuhan orang lain, meski tidak diucapkan."},
        {"zh": "被别人需要的感觉对我来说很重要。",
         "en": "Feeling needed by others matters a lot to me.",
         "id": "Merasa dibutuhkan orang lain sangat penting bagi saya."},
        {"zh": "我常常把别人的事情排在自己的事情前面。",
         "en": "I often put others' matters ahead of my own.",
         "id": "Saya sering mendahulukan urusan orang lain daripada urusan sendiri."},
    ],
    "3": [
        {"zh": "我喜欢设定目标，并高效地一个一个完成它们。",
         "en": "I like setting goals and efficiently checking them off one by one.",
         "id": "Saya suka menetapkan tujuan dan menyelesaikannya satu per satu dengan efisien."},
        {"zh": "在别人眼中保持「成功」的形象对我很重要。",
         "en": "Maintaining a \"successful\" image in others' eyes matters to me.",
         "id": "Menjaga citra 'sukses' di mata orang lain penting bagi saya."},
        {"zh": "我很难闲下来什么都不做，总觉得要有所产出。",
         "en": "I find it hard to idle; I always feel I should be producing something.",
         "id": "Saya sulit berdiam diri; rasanya saya harus selalu menghasilkan sesuatu."},
        {"zh": "我擅长根据不同场合调整自己展现的一面。",
         "en": "I'm good at adjusting the side of myself I show to fit the occasion.",
         "id": "Saya pandai menyesuaikan sisi diri yang saya tampilkan sesuai situasi."},
    ],
    "4": [
        {"zh": "我常觉得自己的情感体验比周围的人更深刻。",
         "en": "I often feel my emotional experiences run deeper than those around me.",
         "id": "Saya sering merasa pengalaman emosional saya lebih dalam dibanding orang di sekitar."},
        {"zh": "做真实的自己，比融入群体对我更重要。",
         "en": "Being my authentic self matters more to me than fitting in.",
         "id": "Menjadi diri sendiri yang autentik lebih penting bagi saya daripada sekadar diterima kelompok."},
        {"zh": "我有时会觉得，别人很难真正理解我。",
         "en": "I sometimes feel others can hardly truly understand me.",
         "id": "Saya kadang merasa orang lain sulit benar-benar memahami saya."},
        {"zh": "我容易被有意义、有美感、或带一点忧郁的事物打动。",
         "en": "I'm easily moved by things that are meaningful, beautiful, or a little melancholic.",
         "id": "Saya mudah tersentuh oleh hal-hal yang bermakna, indah, atau sedikit melankolis."},
    ],
    "5": [
        {"zh": "参与一件事之前，我更喜欢先把它研究透彻。",
         "en": "Before engaging in something, I prefer to research it thoroughly first.",
         "id": "Sebelum terlibat dalam sesuatu, saya lebih suka mempelajarinya secara mendalam dulu."},
        {"zh": "我需要大量独处的时间来恢复精力。",
         "en": "I need plenty of alone time to recharge.",
         "id": "Saya butuh banyak waktu sendirian untuk mengisi ulang energi."},
        {"zh": "与人相处太久，会让我感到精力被消耗。",
         "en": "Being with people for too long drains my energy.",
         "id": "Berada bersama orang terlalu lama membuat energi saya terkuras."},
        {"zh": "相比表达感受，我更习惯用思考和分析来处理问题。",
         "en": "Rather than expressing feelings, I'm more used to processing problems through thinking and analysis.",
         "id": "Dibanding mengungkapkan perasaan, saya lebih terbiasa memproses masalah dengan berpikir dan menganalisis."},
    ],
    "6": [
        {"zh": "做决定前，我习惯先考虑各种可能出错的情况。",
         "en": "Before deciding, I habitually consider all the ways things could go wrong.",
         "id": "Sebelum memutuskan, saya terbiasa mempertimbangkan berbagai kemungkinan yang bisa salah."},
        {"zh": "我重视承诺，对自己认可的人或团队非常忠诚。",
         "en": "I value commitment and am deeply loyal to people or teams I believe in.",
         "id": "Saya menghargai komitmen dan sangat setia pada orang atau tim yang saya percaya."},
        {"zh": "面对不确定的事情，我容易反复权衡、难以放下。",
         "en": "Facing uncertainty, I tend to weigh things over and over and struggle to let go.",
         "id": "Menghadapi ketidakpastian, saya mudah terus menimbang-nimbang dan sulit melepaskannya."},
        {"zh": "我经常会提前为潜在的风险做好准备。",
         "en": "I often prepare in advance for potential risks.",
         "id": "Saya sering bersiap lebih awal untuk risiko yang mungkin terjadi."},
    ],
    "7": [
        {"zh": "我总是对新的计划和可能性感到兴奋。",
         "en": "I'm always excited by new plans and possibilities.",
         "id": "Saya selalu bersemangat dengan rencana dan kemungkinan baru."},
        {"zh": "我喜欢同时保留很多选择，不喜欢被限制住。",
         "en": "I like keeping many options open and dislike feeling boxed in.",
         "id": "Saya suka membuka banyak pilihan dan tidak suka merasa terkekang."},
        {"zh": "当一件事变得枯燥或沉重时，我会想转去做更有趣的事。",
         "en": "When something gets boring or heavy, I want to switch to something more fun.",
         "id": "Ketika sesuatu menjadi membosankan atau berat, saya ingin beralih ke hal yang lebih seru."},
        {"zh": "我擅长在不如意的事情里找到积极的一面。",
         "en": "I'm good at finding the bright side when things don't go well.",
         "id": "Saya pandai menemukan sisi positif ketika keadaan tidak berjalan baik."},
    ],
    "8": [
        {"zh": "我说话直接，不喜欢拐弯抹角。",
         "en": "I speak directly and dislike beating around the bush.",
         "id": "Saya berbicara langsung dan tidak suka bertele-tele."},
        {"zh": "面对不公平的事，我很难忍住不站出来。",
         "en": "Faced with injustice, I can hardly stop myself from standing up.",
         "id": "Menghadapi ketidakadilan, saya sulit menahan diri untuk tidak maju."},
        {"zh": "我习惯掌控局面，而不是把主动权交给别人。",
         "en": "I'm used to taking charge rather than handing control to others.",
         "id": "Saya terbiasa memegang kendali daripada menyerahkannya kepada orang lain."},
        {"zh": "别人常说我气场强，即使我并无恶意。",
         "en": "People often say I have an intense presence, even when I mean no harm.",
         "id": "Orang sering bilang aura saya kuat, meski saya tidak bermaksud buruk."},
    ],
    "9": [
        {"zh": "为了维持和谐，我常常愿意先让步。",
         "en": "To keep harmony, I'm often willing to yield first.",
         "id": "Demi menjaga keharmonisan, saya sering bersedia mengalah lebih dulu."},
        {"zh": "发生分歧时，我更倾向做调和双方的那个人。",
         "en": "When disagreements arise, I tend to be the one who mediates both sides.",
         "id": "Saat terjadi perbedaan pendapat, saya cenderung menjadi penengah kedua pihak."},
        {"zh": "我能很容易理解同一件事上不同人的立场。",
         "en": "I can easily understand different people's positions on the same issue.",
         "id": "Saya mudah memahami posisi orang yang berbeda-beda dalam satu hal."},
        {"zh": "我有时会为了避免冲突，把自己的想法放在一边。",
         "en": "I sometimes set aside my own opinions to avoid conflict.",
         "id": "Saya kadang mengesampingkan pendapat sendiri demi menghindari konflik."},
    ],
}

# 回合制交错：第 0-8 题为 1-9 型的第 1 题，第 9-17 题为各型第 2 题……
# 部分作答时各型覆盖仍均匀（渐进式中途信号不失偏），且连续题目型别不同、体验更自然
ENNEAGRAM_QUESTIONS = [
    {"type": t, "text": _QUESTIONS_BY_TYPE[t][round_idx]}
    for round_idx in range(4)
    for t in ("1", "2", "3", "4", "5", "6", "7", "8", "9")
]
ENNEAGRAM_TOTAL = len(ENNEAGRAM_QUESTIONS)

# ═══ MBTI×九型关联表（PRD P1-F03：内置常见组合解读；zh/en，id 回退英文）═══
MBTI_ENNEAGRAM_COMBOS = {
    ("INTP", "5"): {"zh": "系统构建者", "en": "System Builder"},
    ("INTJ", "5"): {"zh": "深度谋略者", "en": "Deep Strategist"},
    ("INTJ", "1"): {"zh": "原则架构师", "en": "Principled Architect"},
    ("ENTP", "7"): {"zh": "机会探索者", "en": "Opportunity Explorer"},
    ("ENTJ", "8"): {"zh": "天生领导者", "en": "Born Leader"},
    ("ENTJ", "3"): {"zh": "巅峰成就者", "en": "Peak Achiever"},
    ("INFP", "4"): {"zh": "灵魂梦想家", "en": "Soulful Dreamer"},
    ("INFP", "9"): {"zh": "平和调停者", "en": "Peaceful Mediator"},
    ("INFJ", "4"): {"zh": "洞察治愈者", "en": "Insightful Healer"},
    ("INFJ", "9"): {"zh": "宁静引路人", "en": "Gentle Guide"},
    ("ENFP", "7"): {"zh": "可能创造者", "en": "Possibility Creator"},
    ("ENFP", "4"): {"zh": "热忱理想家", "en": "Passionate Idealist"},
    ("ENFJ", "2"): {"zh": "赋能导师", "en": "Empowering Mentor"},
    ("ISTJ", "1"): {"zh": "秩序守护者", "en": "Order Guardian"},
    ("ISTJ", "6"): {"zh": "可靠基石", "en": "Dependable Rock"},
    ("ISFJ", "2"): {"zh": "温暖守护者", "en": "Warm Guardian"},
    ("ISFJ", "9"): {"zh": "安宁支持者", "en": "Serene Supporter"},
    ("ISTP", "5"): {"zh": "冷静分析者", "en": "Cool Analyst"},
    ("ISFP", "4"): {"zh": "敏感艺术家", "en": "Sensitive Artist"},
    ("ESTP", "8"): {"zh": "果敢行动派", "en": "Bold Go-Getter"},
    ("ESTJ", "8"): {"zh": "坚定执行者", "en": "Determined Executor"},
    ("ESTJ", "3"): {"zh": "高效管理者", "en": "Efficient Executive"},
    ("ESFJ", "2"): {"zh": "热心联结者", "en": "Heartfelt Connector"},
    ("ESFP", "7"): {"zh": "快乐点燃者", "en": "Joy Igniter"},
}


def _pick(texts: dict, language: str | None) -> str:
    """三语文案选择：zh* → zh，id → id（缺省回退 en），其余 → en。"""
    lang = language or "en"
    if lang.startswith("zh"):
        return texts["zh"]
    if lang.startswith("id"):
        return texts.get("id") or texts["en"]
    return texts["en"]


def enneagram_label_for(enneagram_type: str | None, language: str | None = None) -> str | None:
    """按语言返回九型类型名（未知类型返回 None）。"""
    labels = ENNEAGRAM_LABELS.get(enneagram_type or "")
    return _pick(labels, language) if labels else None


def combo_name_for(
    mbti_type: str | None, enneagram_type: str | None, language: str | None = None
) -> str | None:
    """查 MBTI×九型关联表，返回按语言的组合名（未命中返回 None）。"""
    combo = MBTI_ENNEAGRAM_COMBOS.get(((mbti_type or "").upper(), enneagram_type or ""))
    return _pick(combo, language) if combo else None


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
        zh = (language or "").startswith("zh")

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
            message=(
                f"你的九型人格类型是 {enneagram_type} 号 · {result.type_label}" if zh
                else f"Your Enneagram type is Type {enneagram_type} · {result.type_label}"
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
        """按用户语言下发题目（zh/en/id，其余回退英文；不下发归属类型，防刷分）。"""
        q = ENNEAGRAM_QUESTIONS[index]
        return EnneagramQuestion(
            index=index,
            total=ENNEAGRAM_TOTAL,
            text=_pick(q["text"], language),
            options={k: _pick(v, language) for k, v in ENNEAGRAM_OPTION_LABELS.items()},
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
        prompt = (
            f"性格描述：「{question['text']['en']}」。\n"
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
