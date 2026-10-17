"""批 2 · D-G8（签到附一句话）的后端语义闸。

被验对象：`MoodCheckinRequest.note` → 解读 prompt / 记忆沉淀，以及 1000 这个长度上限。

这条缺陷长在**前端**：`api.ts:409` 的 `moodCheckin(emoji, note?)` 早就能带备注、后端
`mood_service.checkin` 也早就吃这个参数（`_interpret` 把它拼进 prompt、`mood_service.py:139-140` 把它沉淀成记忆），
唯独两个调用点只传 emoji ⇒ 用户没有输入框，这条线一次也没被喂过。
所以本批代码改在前端，本闸钉的是**前端将要依赖的那几条后端语义**，防止接线接到一个猜出来的口径上：

- 「空 ⇒ 不写库」：前端 trim 后传 undefined，后端 `if note and note.strip()` 也判空——两侧同判才算闭环。
- 「1000」：前端 `maxLength` 取的是这里，不是拍的一个数（schemas.py:87 的 max_length）。
- 「响应里没有 note」：这是现状而不是缺陷，但它决定了前端本地行为什么只能写用户刚打的那句
  （`MoodCheckinResponse` 无 note 字段 ⇒ 回读不到 ⇒ 不能用响应刷新）。改判这条需要后端加列，不在本批。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.models.schemas import MoodCheckinRequest, MoodCheckinResponse
from backend.repositories import memory_repository
from backend.services.mood_service import mood_service

NOTE = "今天把压了很久的那句话说出来了"


async def test_note_is_sedimented_as_a_memory(db, make_user):
    """带备注签到 ⇒ 沉淀一条 preference 记忆，情绪上下文跟着 emoji。"""
    await make_user("u-note", timezone="UTC")
    await mood_service.checkin(db, "u-note", "😊", NOTE, "zh")

    memories = await memory_repository.list(db, "u-note")
    assert [m.content for m in memories] == [NOTE]
    assert memories[0].category == "preference"
    assert memories[0].emotion_context == "😊"


@pytest.mark.parametrize("blank", [None, "", "   ", "\n"])
async def test_blank_note_sediments_nothing(db, make_user, blank):
    """空/纯空白备注不沉淀（`mood_service.py:139` 的 `note and note.strip()`）。

    这一条是前端「trim 后为空就传 undefined」的配对判据：两侧都判空，
    才不会在库里留一排 content="" 的记忆。
    """
    await make_user("u-blank", timezone="UTC")
    await mood_service.checkin(db, "u-blank", "😌", blank, "zh")
    assert await memory_repository.list(db, "u-blank") == []


async def test_note_reaches_the_interpretation_prompt(db, make_user, mock_llm):
    """备注真的进了 LLM 解读的 prompt（已同意数据共享时），不是只存在请求里。"""
    await make_user("u-prompt", ai_consent=True, timezone="UTC")
    await mood_service.checkin(db, "u-prompt", "😢", NOTE, "zh")

    prompts = [c["prompt"] for c in mock_llm.calls["complete"]]
    assert any(NOTE in p for p in prompts), (
        "备注没进解读 prompt：用户写的那句被收下就没被读过，"
        "界面上『AI 看到了你写的话』这句话是假的"
    )


async def test_without_consent_the_note_does_not_leave_the_server(db, make_user, mock_llm):
    """未同意 AI 数据共享：只用规则模板，备注不上送 LLM（合规 5.1.2(i)）。"""
    await make_user("u-noconsent", ai_consent=False, timezone="UTC")
    resp = await mood_service.checkin(db, "u-noconsent", "😰", NOTE, "zh")
    assert NOTE not in "".join(c["prompt"] for c in mock_llm.calls["complete"])
    # 签到本身仍然保留
    assert resp.mood_emoji == "😰"


def test_length_limit_is_the_one_the_frontend_copies():
    """1000 是后端真源（schemas.py:87）；前端 ReflectScreen 的 maxLength 必须与之同值。

    1001 收、1000 放——把这条钉住，前端那个常量才有出处，而不是第二真源。
    """
    MoodCheckinRequest(emoji="😊", note="字" * 1000)
    with pytest.raises(ValidationError):
        MoodCheckinRequest(emoji="😊", note="字" * 1001)


def test_response_carries_no_note_so_the_local_row_uses_what_was_typed():
    """现状登记：`MoodCheckinResponse` 没有 note 字段 ⇒ 签到后回读不到备注。

    前端因此只能用「用户刚打的那句」写本地 mood_checkins.note（服务端行里根本没有这一列）。
    若哪天后端补了这列，这条会红——那时该改成用响应刷新，而不是继续用手上的值。
    """
    assert "note" not in MoodCheckinResponse.model_fields
