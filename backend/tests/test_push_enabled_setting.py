"""批 2 · D-G6（推送总开关）的后端行为闸。

被验对象：`UserProfileResponse.push_enabled` 与 `user_service.get_profile` 的回显。

缺陷形状和 U3-a 的 push_frequency 一模一样，是**读路径缺失**，不是「后端没实现」：
`users.push_enabled` 这列早就是推送分流的入口——cron_service.py:445/534/583/677/849/897
与 journey_service.py:339 七处都在读它，`PATCH /user/settings` 也写得进去
（user_service.py:250 → user_repository.py:174-175），唯独 `UserProfileResponse` 没有这个字段
⇒ 界面无从显示开关的真实位置，用户只能盲设、且看不出自己关掉过。

所以本闸一律判**响应体与再读一次的值**：
- 只测「PATCH 能写进去」是空转——写侧从立项之前就是好的，撤掉本批改动它照样绿。
- 默认值那条会跟着一起绿（get_profile 不传值时 pydantic 也给 False），所以它只是兜底，
  真正的证人是「先写 True → 写 False → 换一次调用再读」和「改频率不许把开关顶掉」两条。
"""
from __future__ import annotations

from typing import get_args

import pytest

from backend.models.schemas import UserProfileResponse, UserSettingsUpdate
from backend.services.user_service import user_service


async def _raw_user(db, user_id: str):
    from backend.repositories.user_repository import user_repository

    return await user_repository.get_user(db, user_id)


async def test_new_user_profile_reports_off(db, make_user):
    """没设过开关的用户，响应给 False（不是 None、也不是缺键）。"""
    await make_user("u-pushoff-default")
    profile = await user_service.get_profile(db, "u-pushoff-default")
    assert profile.push_enabled is False


async def test_update_settings_echoes_the_flag_that_was_written(db, make_user):
    """写后**回读**：PATCH 的响应体里必须就是刚写进去的那个位置。

    `update_settings` 末尾走 `get_profile`（读库，不回显请求体），所以这一条打的是
    「响应模型有没有这个字段」+「get_profile 有没有真的传值」两处。
    """
    await make_user("u-pushoff-echo")
    updated = await user_service.update_settings(
        db, "u-pushoff-echo", UserSettingsUpdate(push_enabled=True)
    )
    assert updated.push_enabled is True, (
        "PATCH 回了 False：开关在界面上永远是关的，"
        "用户拨上去那一瞬间就被弹回——这正是本批要修的「写得进读不出」"
    )


async def test_flag_survives_a_fresh_profile_read(db, make_user):
    """开 → 关 → 各换一次调用再读：值必须来自库里，不是请求体的回声或某处缓存。

    写成「True 之后再读一次 True」是不够的：若 get_profile 从请求体拿值，
    两条都会绿。必须先落一个 False，再证明读回来的是 False 而不是先前的 True。
    """
    await make_user("u-pushoff-flip")
    await user_service.update_settings(
        db, "u-pushoff-flip", UserSettingsUpdate(push_enabled=True)
    )
    assert (await user_service.get_profile(db, "u-pushoff-flip")).push_enabled is True

    await user_service.update_settings(
        db, "u-pushoff-flip", UserSettingsUpdate(push_enabled=False)
    )
    assert (await user_service.get_profile(db, "u-pushoff-flip")).push_enabled is False
    assert (await user_service.get_profile(db, "u-pushoff-flip")).push_enabled is False


async def test_touching_frequency_does_not_clobber_the_switch(db, make_user):
    """改频率不许把总开关顶成关。

    写侧是 `Optional[bool]`，仓储按「None 就不动」处理（user_repository.py:174-175）。
    若哪天改成无条件赋值，用户每拨一次频率档位就悄悄关掉了全部推送——而界面上
    开关仍显示关（因为读路径当时还没修），这条会红，且红的正是那个真实风险。
    """
    await make_user("u-pushoff-partial")
    await user_service.update_settings(
        db, "u-pushoff-partial", UserSettingsUpdate(push_enabled=True)
    )
    after_freq = await user_service.update_settings(
        db, "u-pushoff-partial", UserSettingsUpdate(push_frequency="important")
    )
    assert after_freq.push_enabled is True
    assert after_freq.push_frequency == "important"
    assert (await _raw_user(db, "u-pushoff-partial")).push_enabled is True


async def test_empty_settings_update_changes_neither(db, make_user):
    """空 PATCH（只用来刷新响应）既不该开也不该关。"""
    await make_user("u-pushoff-noop", push_enabled=True)
    profile = await user_service.update_settings(db, "u-pushoff-noop", UserSettingsUpdate())
    assert profile.push_enabled is True


def test_read_side_is_a_plain_bool_not_optional():
    """读侧退化成了 `Optional[bool]` 就等于回到本批之前那条缺陷：响应给 null 时
    界面只能猜默认值，而猜错的那一侧正好是「用户以为关了、实际还在推」。"""
    annotation = UserProfileResponse.model_fields["push_enabled"].annotation
    assert annotation is bool, f"读侧类型变了：{annotation}"
    assert type(None) not in get_args(annotation)


def test_write_side_stays_optional_tri_state():
    """写侧必须是三态（None / True / False），否则「我只想改名字」的 PATCH
    会被迫带一个开关值。"""
    annotation = UserSettingsUpdate.model_fields["push_enabled"].annotation
    assert type(None) in get_args(annotation), f"写侧丢了 None 支：{annotation}"
    assert bool in get_args(annotation)


def test_response_does_not_accept_an_arbitrary_string():
    """响应模型不许退化成自由字符串：JS 侧非空串一律是真值，
    开关会显示成「开」，而这在 Python 里不报错。"""
    with pytest.raises(Exception):
        UserProfileResponse(**{**_profile_payload(), "push_enabled": "push_is_on"})
    # 反面对照：pydantic 认 "false" 这类字面量，说明上一条红的是类型而不是宽松转换
    assert UserProfileResponse(**{**_profile_payload(), "push_enabled": "false"}).push_enabled is False


def _profile_payload() -> dict:
    return {
        "id": "u1", "name": None, "mbti_type": None, "streak_count": 0,
        "relationship_level": "stranger", "chat_count": 0, "tier": "free",
        "daily_quota_used": 0, "daily_quota_total": 5,
    }
