"""U3-a（推送频率 UI）的后端行为闸。

被验对象：`UserProfileResponse.push_frequency` 与 `user_service.get_profile` 的回显。

缺陷本来长这样（实测过才写）：PATCH `/user/settings` 早就能把 `push_frequency` 写进库
（user_service.py:241 → user_repository.py:172），cron_service.py:445-447 也早已按三档分流，
但 `UserProfileResponse` 里**没有这个字段** ⇒ 界面读不到当前档，用户选完重进页面永远是默认值。
所以本批真正的交付不是「加个设置项」，而是**把读路径补上**——闸门必须钉住读路径：

- 只测「PATCH 能写进去」是空转：写侧从这条缺陷立项之前就一直是好的，
  撤掉本批的改动它照样绿。⇒ 三条行为测一律判**响应体与再读一次的值**。
- 默认档单独测一条：`get_profile` 若不传值，pydantic 的字段默认值同样给出 "daily"，
  这一条会跟着一起绿——所以它只是兜底，真正的证人是最下面那两条写后回读。
"""
from __future__ import annotations

import pytest
from typing import Literal, get_args, get_origin

from pydantic import ValidationError

from backend.models.schemas import UserProfileResponse, UserSettingsUpdate
from backend.services.user_service import user_service

TIERS = ("daily", "important", "off")


async def test_new_user_profile_reports_default_tier(db, make_user):
    """没设过频率的用户，响应给 daily（不是 None、也不是缺键）。"""
    await make_user("u-freq-default")
    profile = await user_service.get_profile(db, "u-freq-default")
    assert profile.push_frequency == "daily"


@pytest.mark.parametrize("tier", TIERS)
async def test_update_settings_echoes_the_tier_that_was_written(db, make_user, tier):
    """写后**回读**：PATCH 的响应体里必须就是刚写进去的那一档。

    `update_settings` 末尾走的是 `get_profile`（读库，不回显请求体），所以这一条
    打的是「响应模型有没有这个字段 + get_profile 有没有真的传值」两处。
    """
    await make_user("u-freq-echo")
    updated = await user_service.update_settings(
        db, "u-freq-echo", UserSettingsUpdate(push_frequency=tier)
    )
    assert updated.push_frequency == tier, (
        f"PATCH 回了 {updated.push_frequency}，写的是 {tier}：界面勾不上，"
        "用户只能靠「重进页面看默认值」猜自己设过没有"
    )


async def test_tier_survives_a_fresh_profile_read(db, make_user):
    """换一次调用再读：值必须来自库里，不是某处缓存或请求体的回声。"""
    await make_user("u-freq-off")
    await user_service.update_settings(
        db, "u-freq-off", UserSettingsUpdate(push_frequency="off")
    )
    assert (await user_service.get_profile(db, "u-freq-off")).push_frequency == "off"
    assert (await user_service.get_profile(db, "u-freq-off")).push_frequency == "off"


async def test_empty_column_normalises_to_daily(db, make_user):
    """迁移前的老行若留空串，响应给 daily 而不是让 Literal 校验把个人页打挂。"""
    await make_user("u-freq-legacy")
    await user_service.update_settings(
        db, "u-freq-legacy", UserSettingsUpdate(push_frequency="important")
    )
    user = await _raw_user(db, "u-freq-legacy")
    user.push_frequency = ""
    await db.flush()
    assert (await user_service.get_profile(db, "u-freq-legacy")).push_frequency == "daily"


async def _raw_user(db, user_id: str):
    from backend.repositories.user_repository import user_repository

    return await user_repository.get_user(db, user_id)


def _literal_members(annotation) -> tuple[str, ...]:
    """取出 Literal 的成员；写侧裹在 Optional 里，先把 NoneType 那一支剥掉再看。"""
    if get_origin(annotation) is Literal:
        return tuple(get_args(annotation))
    rest = tuple(a for a in get_args(annotation) if a is not type(None))
    return _literal_members(rest[0]) if len(rest) == 1 else ()


def test_write_and_read_enums_cannot_drift():
    """写侧与读侧的档位集合必须逐字相同。

    两处分别写 Literal 是这次的真实风险：写侧收 `off` 而读侧收成 `Optional[str]`，
    界面就再也判不出「当前是哪一档」；反过来读侧收三档、写侧少一档，则是选了存不进。
    """
    for model, side in ((UserProfileResponse, "读侧"), (UserSettingsUpdate, "写侧")):
        members = _literal_members(model.model_fields["push_frequency"].annotation)
        assert members == TIERS, f"{side}档位集合变了：{members}"

    # 读侧必须**非** Optional：响应给 null 时界面只能猜默认值，等于回到本批之前那条缺陷。
    assert type(None) not in get_args(
        UserProfileResponse.model_fields["push_frequency"].annotation
    ), "读侧退化成了 Optional：当前档位又读不回来了"


def test_response_rejects_an_unknown_tier():
    """响应模型不许退化成一枚自由字符串：那样前端 `t('profile.pushFreqOption.' + x)`
    会在界面上打出键名原文，而这在类型上看不出来。"""
    with pytest.raises(ValidationError):
        UserProfileResponse(**{**_profile_payload(), "push_frequency": "weekly"})


def _profile_payload() -> dict:
    return {
        "id": "u1", "name": None, "mbti_type": None, "streak_count": 0,
        "relationship_level": "stranger", "chat_count": 0, "tier": "free",
        "daily_quota_used": 0, "daily_quota_total": 5,
    }
