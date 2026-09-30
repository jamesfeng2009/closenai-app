"""enneagram_service：题库结构、计分定型、断点续答、完成落库、配额隔离、关联表。"""
import pytest

from backend.core import copy as copy_lib
from backend.core.copy import SUPPORTED_LOCALES
from backend.core.errors import AppException
from backend.models.schemas import AssessmentAnswerRequest
from backend.repositories import personality_repository, user_repository
from backend.services.enneagram_service import (
    ENNEAGRAM_QUESTIONS,
    ENNEAGRAM_TOTAL,
    VALID_TYPES,
    combo_name_for,
    enneagram_label_for,
    enneagram_service,
)
from backend.services.personality_service import personality_service


# ── 题库与静态表 ──


def test_question_bank_structure():
    """36 题 = 9 型 × 4 题；回合制交错（第 i 题归属 i%9+1 型）；题面 15 语言齐全。"""
    assert ENNEAGRAM_TOTAL == 36
    for i, q in enumerate(ENNEAGRAM_QUESTIONS):
        assert q["type"] == str(i % 9 + 1)
    # 每型 4 题、15 语言题面齐全（文案库 enneagram.q.*）
    for t in "123456789":
        typed = [q for q in ENNEAGRAM_QUESTIONS if q["type"] == t]
        assert len(typed) == 4
        for q in typed:
            for loc in SUPPORTED_LOCALES:
                text = copy_lib.t("enneagram", q["key"], loc)
                assert text and text != q["key"], f"{q['key']}/{loc} 题面缺失"


def test_labels_cover_all_types_three_languages():
    """9 型标签 zh/en/id 齐全；未知类型返回 None。"""
    assert set(VALID_TYPES) == set("123456789")
    assert enneagram_label_for("5", "zh") == "探索者"
    assert enneagram_label_for("5", "en") == "Investigator"
    assert enneagram_label_for("5", "id") == "Penyelidik"
    assert enneagram_label_for("9", "zh") == "和平使者"
    assert enneagram_label_for("0") is None
    assert enneagram_label_for(None) is None


def test_combo_table_lookup():
    """MBTI×九型关联表：命中返回本地化组合名，未命中 None。"""
    assert combo_name_for("INTP", "5", "zh") == "系统构建者"
    assert combo_name_for("INTP", "5", "en") == "System Builder"
    assert combo_name_for("ENFP", "7", "zh") == "可能创造者"
    assert combo_name_for("INTP", "9") is None
    assert combo_name_for(None, "5") is None


# ── 计分定型（纯函数）──


def _answers(option_for):
    """按规则构造 36 题答案：option_for(type, q_index) -> option。"""
    return [
        {"q": i, "option": option_for(ENNEAGRAM_QUESTIONS[i]["type"], i), "text": None}
        for i in range(ENNEAGRAM_TOTAL)
    ]


def test_scoring_dominant_type_wins():
    """某型全 a、其余全 b → 定型该型。"""
    answers = _answers(lambda t, _i: "a" if t == "5" else "b")
    assert enneagram_service._type_from_answers(answers) == "5"


def test_scoring_neutral_counts_half():
    """neutral 计 0.5 分：4 neutral(2.0) 胜 1a(1.0)，证明中性非零分。"""
    answers = _answers(lambda t, i: "neutral" if t == "9" else ("a" if t == "2" and i == 1 else "b"))
    assert enneagram_service._type_from_answers(answers) == "9"


def test_scoring_tie_prefers_more_a_votes():
    """总分平票时取 a 票多者（2a+2b=2.0 胜 4neutral=2.0）。"""
    def rule(t, i):
        if t == "3":
            return "a" if i in (2, 11) else "b"  # 2a → 2.0，a 票 2
        if t == "9":
            return "neutral"  # 4 neutral → 2.0，a 票 0
        return "b"

    assert enneagram_service._type_from_answers(_answers(rule)) == "3"


def test_scoring_full_tie_falls_back_to_lower_type():
    """总分与 a 票全平 → 取小号型（确定性）。"""
    answers = _answers(lambda t, _i: "a" if t in ("1", "9") else "b")
    assert enneagram_service._type_from_answers(answers) == "1"


def test_scoring_ignores_out_of_range_index():
    """越界/非法 q 下标不计分（防脏数据崩溃）。"""
    answers = [{"q": 999, "option": "a"}, {"q": -1, "option": "a"}, {"q": "x", "option": "a"}]
    # 全部无效 → 全部 0 分平票 → 回退 1 号
    assert enneagram_service._type_from_answers(answers) == "1"


# ── 状态机与落库（异步）──


async def test_answer_without_start_raises(db, make_user):
    """未开始直接答题 → 400。"""
    await make_user("u1", language="en")
    with pytest.raises(AppException):
        await enneagram_service.answer(db, "u1", AssessmentAnswerRequest(option="a"))


async def test_start_then_resume_same_round(db, make_user):
    """开始 → 答 3 题 → 再次 start 续答同一轮（不新建行），题号正确。"""
    await make_user("u1", language="zh")
    state = await enneagram_service.start(db, "u1")
    assert state.status == "in_progress"
    assert state.current_index == 0
    assert state.total == ENNEAGRAM_TOTAL
    assert state.question is not None and state.question.index == 0
    # 中文下发 + 选项本地化
    assert state.question.options["a"] == "符合我"

    for _ in range(3):
        await enneagram_service.answer(db, "u1", AssessmentAnswerRequest(option="a"))

    resumed = await enneagram_service.start(db, "u1")
    assert resumed.current_index == 3
    assert resumed.question is not None and resumed.question.index == 3
    # 未新建第二轮（整个用户仅 1 条九型评估行）
    from sqlalchemy import func, select

    from backend.models.personality import PersonalityAssessment

    count = (
        await db.execute(
            select(func.count())
            .select_from(PersonalityAssessment)
            .where(
                PersonalityAssessment.user_id == "u1",
                PersonalityAssessment.kind == "enneagram",
            )
        )
    ).scalar()
    assert count == 1


async def test_mbti_and_enneagram_assessments_coexist(db, make_user):
    """16 型与九型评估独立推进，互不干扰（kind 隔离）。"""
    await make_user("u1", language="en")
    mbti_state = await personality_service.start_assessment(db, "u1")
    enn_state = await enneagram_service.start(db, "u1")

    await enneagram_service.answer(db, "u1", AssessmentAnswerRequest(option="b"))

    mbti_after = await personality_service.get_assessment(db, "u1")
    enn_after = await enneagram_service.get_state(db, "u1")
    assert mbti_after.current_index == 0  # 未被九型答题推进
    assert mbti_after.total == 8
    assert enn_after.current_index == 1
    assert enn_after.total == ENNEAGRAM_TOTAL
    assert mbti_state.question is not None and enn_state.question is not None


async def test_complete_writes_enneagram_type_and_combo(db, make_user):
    """答满 36 题 → 定型写入 users.enneagram_type；有 INTP 画像时命中组合名。"""
    await make_user("u1", language="zh")
    await personality_repository.create(db, "u1", "INTP", '["观察"]', "独特", 1)
    await enneagram_service.start(db, "u1")

    resp = None
    for i in range(ENNEAGRAM_TOTAL):
        option = "a" if ENNEAGRAM_QUESTIONS[i]["type"] == "5" else "b"
        resp = await enneagram_service.answer(db, "u1", AssessmentAnswerRequest(option=option))

    assert resp is not None and resp.completed is True
    assert resp.result is not None
    assert resp.result.enneagram_type == "5"
    assert resp.result.type_label == "探索者"
    assert resp.result.combo_name == "系统构建者"
    assert resp.result.combo_mbti == "INTP"
    assert "5 号" in resp.message

    # 落库 users.enneagram_type
    user = await user_repository.get_user(db, "u1")
    assert user.enneagram_type == "5"

    # 状态查询 → completed + 结果
    state = await enneagram_service.get_state(db, "u1")
    assert state.status == "completed"
    assert state.result is not None and state.result.enneagram_type == "5"

    # 完成后继续答 → 400
    with pytest.raises(AppException):
        await enneagram_service.answer(db, "u1", AssessmentAnswerRequest(option="a"))


async def test_free_text_llm_failure_falls_back_neutral(db, make_user, mock_llm):
    """自由输入 + LLM 故障 → 安全回退 neutral（半分），答题不中断。"""
    await make_user("u1", language="en")

    def _boom(prompt, **kwargs):
        raise RuntimeError("llm down")

    mock_llm.state["complete_fn"] = _boom

    await enneagram_service.start(db, "u1")
    resp = await enneagram_service.answer(db, "u1", AssessmentAnswerRequest(answer="不好说，看情况"))
    assert resp.completed is False

    assessment = await personality_repository.get_in_progress_assessment(db, "u1", kind="enneagram")
    answers = enneagram_service._parse_answers(assessment.answers)
    assert len(answers) == 1
    assert answers[0]["option"] == "neutral"
    assert answers[0]["text"] == "不好说，看情况"


async def test_completed_state_from_manual_type_without_assessment(db, make_user):
    """用户仅手动输入过类型（无评估记录）→ 状态 completed 并带结果。"""
    await make_user("u1", language="en", enneagram_type="2")
    state = await enneagram_service.get_state(db, "u1")
    assert state.status == "completed"
    assert state.result is not None
    assert state.result.enneagram_type == "2"
    assert state.result.type_label == "Helper"
    assert state.result.combo_name is None  # 无 16 型画像 → 无组合


async def test_enneagram_assessment_does_not_offset_mbti_refresh_quota(db, make_user):
    """配额隔离：已完成九型评估不计入 16 型画像的"免费评估版"剔除。"""
    await make_user("u1", language="en", tier="free")
    # 1 版画像（视为手动刷新消耗）+ 1 轮已完成九型评估
    await personality_repository.create(db, "u1", "INTP", '["观察"]', "独特", 1)
    assessment = await personality_repository.create_assessment(db, "u1", kind="enneagram")
    await personality_repository.save_assessment(db, assessment, "[]", "completed")

    # 九型评估不参与剔除：1 版本 - 0 mbti 评估版 = 已用 1 次 → 剩 2
    remaining = await personality_service._refresh_quota_remaining(db, "u1")
    assert remaining == 2
