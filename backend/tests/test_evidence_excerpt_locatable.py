"""闸 G-04（工作单 T2-04 §怎么做.4）：**非子串的摘录必须被拒**。

要拦的事故只有一件，而它是 Baseline 不变量 1 的可执行形式：
「没有可追溯证据的 Pattern 不得展示为事实」。模型把用户的原话改写一个字——翻译、
补标点、顺手换个近义说法——那条证据的「依据」栏就会摊出一句**用户从没写过的话**，
而回链恰恰是用户用来核对「系统是不是又在替我编故事」的那一格。

判据住在 ``backend/ai/prompts/observation.py:parse_items`` 的 ``excerpt not in content``
那一格，这里是它的证人。三层口径都在测里钉住：

1. **主闸在 Python**：Go 不读 legacy 原文表（§怎么做.1），判不了子串，所以这一格删掉
   之后**没有任何一道闸会补上**——候选会一路进 ``evidence.excerpt``。
   （这就是验收里那句「把子串校验删掉 ⇒ 红」要打的那一靶。）
2. **不 strip 再判**：模型在引文前后补的空格也是它加的，strip 之后再判会把
   「原文里根本没有这段」读成「有」。
3. **长度是另一格**：``MAX_EXCERPT_CHARS`` 判的不是「指指得回吗」而是「这一栏会不会
   变成一整页」。两侧同值由 ``test_ai_mq_contract.py`` 钉，这里只判行为。

全部走纯函数，不连 LLM、不连库。
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from backend.ai.prompts.observation import (
    MAX_EXCERPT_CHARS,
    OBSERVATION_TYPES,
    build_prompt,
    parse_items,
)

CONTENT = "这周第三次凌晨两点还在改方案，发出去那一刻反而更空。我说我习惯了，但上次朋友生日我也忘了。"

#: 一条除了 excerpt 之外全都合格的答案。换掉 excerpt 就是换掉被测的那一格。
BASE: dict[str, Any] = {
    "evidence_type": "repeated_behavior",
    "role": "supporting",
    "excerpt": "凌晨两点还在改方案",
    "observation": "一周内三次深夜还在工作",
    "strength": "high",
}


def reply_of(*items: dict[str, Any]) -> str:
    return json.dumps({"items": list(items)}, ensure_ascii=False)


# ── 判据 1：改写一个字就不是证据 ─────────────────────────────────────────────

#: 四种「模型顺手改了一下」的形状。每一型都只动 excerpt，其它四键保持合格——
#: 否则红的是别的闸，绿的也可能是别的闸。
REWRITES: tuple[tuple[str, str], ...] = (
    ("换一个近义字", "凌晨三点还在改方案"),
    ("补了原文没有的标点", "凌晨两点，还在改方案"),
    ("翻成英文", "still rewriting the plan at 2am"),
    ("截了半句再拼接", "凌晨两点还在开会"),
)


@pytest.mark.parametrize(("shape", "excerpt"), REWRITES)
def test_rewritten_excerpt_is_dropped(shape: str, excerpt: str) -> None:
    """「差一个字」与「完全不同」必须是同一种下场：这条证据不是用户写的。"""
    assert excerpt not in CONTENT, f"夹具自己写错了：{shape} 那一行其实还是子串"
    assert parse_items(reply_of({**BASE, "excerpt": excerpt}), CONTENT) == (), shape


def test_a_fabricated_item_never_reaches_the_candidate_list() -> None:
    """三条里混一条编造 ⇒ 只出两条，且出去的那两条逐字指得回原文。

    这是 G-04 的正向证人：只断言「编的那条没了」时，把子串校验整段删掉也能靠
    「其它闸碰巧把它拦了」蒙过去；这里要求**留下的每条**都是 ``content`` 的子串。
    """
    items = parse_items(
        reply_of(BASE, {**BASE, "excerpt": "我说我习惯了", "observation": "把熬夜说成习惯"},
                 {**BASE, "excerpt": "它不存在", "observation": "另一句观察"}),
        CONTENT,
    )
    assert len(items) == 2, [i.excerpt for i in items]
    for item in items:
        assert item.excerpt in CONTENT, item.excerpt
    assert "它不存在" not in json.dumps([i.excerpt for i in items], ensure_ascii=False)


# ── 判据 2：拿 strip 之后的excerpt 判＝把「没有」读成「有」 ──────────────────


def test_padding_spaces_are_part_of_the_excerpt_being_checked() -> None:
    """``" 凌晨两点还在改方案 "`` 不是原文的子串，而 strip 之后就成了子串。

    两侧都要证人：``BASE["excerpt"] in CONTENT`` 是真的，所以这条红不会来自
    「原文夹具写错了」，只会来自 strip 那一格被改回去。
    """
    padded = f"  {BASE['excerpt']} "
    assert BASE["excerpt"] in CONTENT
    assert padded not in CONTENT
    assert parse_items(reply_of({**BASE, "excerpt": padded}), CONTENT) == ()


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_blank_excerpt_is_dropped_not_carried_as_empty_evidence(blank: str) -> None:
    """空摘录会产出一条「依据＝空白」的证据：它在 UI 上就是一句没有出处的断言。"""
    assert parse_items(reply_of({**BASE, "excerpt": blank}), CONTENT) == ()


# ── 判据 3：长度上限（Python 侧丢条；同值闸在 test_ai_mq_contract.py）────────


def test_excerpt_cap_drops_the_overlong_item_and_keeps_the_boundary_one() -> None:
    """``MAX_EXCERPT_CHARS`` 的两侧各有证人：500 字符收、501 字符丢。

    只测「超了被丢」的话，把常量改成 ``sys.maxsize`` 也能绿——那一格就不再是上限了。
    内容用同一个字重复填满，好让「摘录是原文子串」这一格先稳定成立，
    红只可能来自长度闸。
    """
    content = "甲" * (MAX_EXCERPT_CHARS + 40)
    kept = content[:MAX_EXCERPT_CHARS]
    dropped = content[: MAX_EXCERPT_CHARS + 1]
    assert parse_items(reply_of({**BASE, "excerpt": kept}), content) != ()
    assert len(parse_items(reply_of({**BASE, "excerpt": kept}), content)[0].excerpt) == (
        MAX_EXCERPT_CHARS
    )
    assert parse_items(reply_of({**BASE, "excerpt": dropped}), content) == ()


def test_overlong_excerpt_is_dropped_not_truncated() -> None:
    """截断会造出半句话：它还是子串，但「依据」栏里的省略是系统加的，不是用户说的。"""
    content = "乙" * (MAX_EXCERPT_CHARS + 10)
    items = parse_items(
        reply_of(
            {**BASE, "excerpt": content},
            {**BASE, "excerpt": "乙乙乙", "observation": "另一句站得住的观察"},
        ),
        content,
    )
    assert [i.excerpt for i in items] == ["乙乙乙"], items


def test_the_prompt_tells_the_model_the_excerpt_is_verbatim_and_bounded() -> None:
    """校验闸只是第二道：先告诉模型规矩，才轮得到「丢了它的答案」这件事有意义。

    这两句是**产品口径**而不许只是实现细节：改 prompt 时删掉它们，模型会开始交超长摘录
    或改写原话，而那时日志里看到的是 ``invalid``，没有人会想到是提示词少了两句。
    """
    prompt = build_prompt(CONTENT)
    assert "VERBATIM substring" in prompt
    assert f"under {MAX_EXCERPT_CHARS} characters" in prompt


def test_the_model_side_vocabulary_is_untouched_by_the_cap() -> None:
    """正向证人：长度闸加在子串闸旁边，不许顺手把词表闸改窄。

    另一侧取**字面量**而不是 ``OBSERVATION_TYPES``：拿常量比常量是恒真的断言，
    把词表删到只剩一格它照样绿。这里要求这五个名字**逐个**都还能过闸。
    """
    vocabulary = (
        "stated_preference",
        "repeated_behavior",
        "emotional_pattern",
        "value_statement",
        "relationship_pattern",
    )
    entries = [
        {**BASE, "evidence_type": kind, "excerpt": f"凌晨两点还在改方案{i}",
         "observation": f"第{i}条深夜工作的观察"}
        for i, kind in enumerate(vocabulary)
    ]
    content = CONTENT + "".join(f"凌晨两点还在改方案{i}" for i in range(len(vocabulary)))
    items = parse_items(reply_of(*entries), content, limit=len(vocabulary))
    assert {i.evidence_type for i in items} == set(vocabulary)
    assert set(vocabulary) == set(OBSERVATION_TYPES), (
        "提示词与映射表认的词表和 parse_items 的词表漂了：这条测的字面量该跟着改，"
        "而先红的应该是 test_ai_mq_observation_handler.py 里的三张映射表闸"
    )
