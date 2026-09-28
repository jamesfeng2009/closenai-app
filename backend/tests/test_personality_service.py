"""personality_service：_parse_llm_result 16 型校验、7 天无新数据不扣配额。"""
from datetime import datetime

import pytest

from backend.repositories import personality_repository
from backend.services.personality_service import personality_service


async def _create_profile(
    db, user_id: str, mbti: str, version: int, created_at: datetime
):
    """造一版画像并把 created_at 钉到指定时刻。

    PersonalityProfile.created_at 由 DB server_default 落库（真实时钟），
    time_travel 只补丁应用层 utc_now/local_now——按「当月」统计配额的用例
    若不钉日期，跨月回归时会落到冻结月之外。
    """
    profile = await personality_repository.create(
        db, user_id, mbti, '["观察"]', "独特", version
    )
    profile.created_at = created_at
    await db.flush()
    return profile


def test_parse_llm_result_valid_type():
    """合法 16 型：解析出类型、观察列表、独特之处。"""
    mbti, observations, unique_traits = personality_service._parse_llm_result(
        "INTP|逻辑强；喜欢独处|善于把问题系统化"
    )
    assert mbti == "INTP"
    assert observations == ["逻辑强", "喜欢独处"]
    assert unique_traits == "善于把问题系统化"


@pytest.mark.parametrize(
    "raw",
    [
        "XXXX|观察1；观察2|独特",  # 无有效字母
        "EEEE|观察1；观察2|独特",  # 4 字母但不在 16 型集合
        "类型未知",  # 完全无类型段
        "",  # 空输出
    ],
)
def test_parse_llm_result_invalid_type_falls_back_none(raw):
    """非法类型回退 None（前端兼容 None=未知），不抛异常。"""
    mbti, _observations, _traits = personality_service._parse_llm_result(raw)
    assert mbti is None


async def test_refresh_no_new_data_within_7_days_does_not_consume_quota(
    db, make_user, mock_llm
):
    """距上次画像 7 天窗口内无新记忆/签到/对话 → 不扣次数、不调 LLM、不生成新版。"""
    await make_user("u1", language="en")
    await personality_repository.create(db, "u1", "INTP", '["观察"]', "独特", 1)

    resp = await personality_service.refresh(db, "u1")

    assert resp.success is False
    assert "No new data" in resp.message
    # 未调 LLM、未生成新版本（免费共 3 次，未消耗）
    assert mock_llm.calls["complete"] == []
    versions = await personality_repository.list(db, "u1")
    assert len(versions) == 1


async def test_assessment_first_profile_does_not_consume_refresh_quota(db, make_user):
    """B6 回归：评估完成生成的首画像免费，免费用户剩余配额仍为 3。"""
    await make_user("u1", language="en", tier="free")
    # 模拟一次已完成评估（完成时生成一版画像）
    assessment = await personality_repository.create_assessment(db, "u1")
    await personality_repository.save_assessment(db, assessment, "[]", "completed")
    await personality_repository.create(db, "u1", "INTP", '["观察"]', "独特", 1)

    remaining = await personality_service._refresh_quota_remaining(db, "u1")
    assert remaining == 3


async def test_manual_refresh_consumes_quota_after_assessment(db, make_user):
    """B6 回归：评估之外的版本才计刷新消耗（1 评估版 + 1 刷新版 → 剩 2）。"""
    await make_user("u1", language="en", tier="free")
    assessment = await personality_repository.create_assessment(db, "u1")
    await personality_repository.save_assessment(db, assessment, "[]", "completed")
    await personality_repository.create(db, "u1", "INTP", '["观察"]', "独特", 1)
    await personality_repository.create(db, "u1", "INTJ", '["观察2"]', "独特2", 2)

    remaining = await personality_service._refresh_quota_remaining(db, "u1")
    assert remaining == 2


async def test_pro_monthly_quota_excludes_assessment_version(db, make_user, time_travel):
    """B6 回归：Pro 月度配额同样剔除当月评估版（1 评估版 + 1 刷新版 → 剩 4）。"""
    from datetime import datetime, timezone

    frozen = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
    time_travel.set(frozen)
    await make_user("u1", language="en", tier="pro", timezone="UTC")
    assessment = await personality_repository.create_assessment(db, "u1")
    await personality_repository.save_assessment(db, assessment, "[]", "completed")
    await _create_profile(db, "u1", "INTP", 1, frozen)
    await _create_profile(db, "u1", "INTJ", 2, frozen)

    remaining = await personality_service._refresh_quota_remaining(db, "u1")
    assert remaining == 4
