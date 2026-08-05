"""personality_service：_parse_llm_result 16 型校验、7 天无新数据不扣配额。"""
import pytest

from backend.repositories import personality_repository
from backend.services.personality_service import personality_service


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
