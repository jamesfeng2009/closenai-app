"""T7-03：MQ 体内身份键的跨语言契约闸（Python 这一半）。

归属只认受信 header（``x-user-id``）。体内出现任何读起来像 user id 的顶层键都必须
**在任何 handler／领域写入之前**拒绝，而不是让 protojson／未知字段各给一个码。
2026-10-25 之前 Python 只剥下划线，``user-id`` 这种连字符形态因此从拒绝面漏掉；
本文件把「原规则漏掉的那一格」与整张变体表一起钉住。

两侧读同一份用例：``contracts/mq_identity_cases.json``。
零写入证人在 ``test_ai_mq_consumer.py``（那里有假 publisher／handler 计数）；
本文件只判纯解析面与 Go 源码侧的归一规则同形。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.ai.mq import contract

ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = ROOT / "contracts" / "mq_identity_cases.json"
IDENTITY_GO = (
    ROOT / "go-core" / "internal" / "messaging" / "inbound" / "identity_keys.go"
)
DECODE_GO = (
    ROOT / "go-core" / "internal" / "messaging" / "inbound" / "candidate_decode.go"
)


def _cases() -> dict:
    doc = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    assert doc["variants"], "共享变体表被清空 ⇒ 下面几条会扫空集永远绿"
    assert doc["non_identity"], "共享非身份键表被清空 ⇒ 误伤面没有证人"
    return doc


def test_normaliser_matches_shared_variants():
    doc = _cases()
    for key in doc["variants"]:
        assert contract.is_identity_key(key), f"{key!r} 没被归一成身份键"
    for key in doc["non_identity"]:
        assert not contract.is_identity_key(key), (
            f"{key!r} 被误判成身份键（归一={contract.normalise_identity_key(key)!r}）"
        )
    # 最小覆盖三张拼写：表被删到只剩一条时，上面两个循环仍可能绿。
    for required in ("user_id", "userId", "user-id"):
        assert required in doc["variants"], f"共享变体表缺了 {required!r}"
    rule = doc["identity_rule"]
    assert rule["canonical"] == contract.IDENTITY_KEY_CANONICAL
    assert set(rule["strip"]) == {"_", "-"}
    assert doc["reject_reason"] == contract.REASON_FOREIGN_USER_ID, (
        "共享用例的拒绝码与 Python 词表不同值：同一件事会出现两个码"
    )


def test_shared_fixture_bodies_are_legal_on_their_own():
    """正例的体里不许自带身份键：否则「合法消息保持通过」证的是另一件事。"""
    doc = _cases()
    for surface in ("command", "candidate"):
        keys = contract.foreign_identity_keys(doc[surface]["base_body"])
        assert keys == [], f"{surface} 的 base_body 自带身份键 {keys}"
    assert doc["source_deleted"]["base_body"][
        doc["source_deleted"]["allowed_identity_key"]
    ] == doc["header_user"], "事件面正例的头体必须同值"


@pytest.mark.parametrize("identity_key", _cases()["variants"])
def test_command_identity_variant_is_rejected_with_the_shared_reason(identity_key: str):
    doc = _cases()
    surface = doc["command"]
    body = dict(surface["base_body"])
    body[identity_key] = doc["body_user"]
    with pytest.raises(contract.ContractError) as exc:
        contract.parse_command(
            body=json.dumps(body).encode(),
            content_type=contract.CONTENT_TYPE_JSON,
            routing_key=surface["routing_key"],
            message_id="cmd-t703",
            message_type=surface["message_type"],
            headers=surface["headers"],
            max_body_bytes=65536,
        )
    assert exc.value.reason == doc["reject_reason"], exc.value.reason


def test_legal_command_without_body_identity_key_passes():
    doc = _cases()
    surface = doc["command"]
    parsed = contract.parse_command(
        body=json.dumps(surface["base_body"]).encode(),
        content_type=contract.CONTENT_TYPE_JSON,
        routing_key=surface["routing_key"],
        message_id="cmd-t703-ok",
        message_type=surface["message_type"],
        headers=surface["headers"],
        max_body_bytes=65536,
    )
    assert parsed.user_id == doc["header_user"]
    assert contract.foreign_identity_keys(parsed.body) == []


def test_hyphen_variant_is_the_regression_witness():
    """旧规则 ``lower().replace("_","")`` 对 ``user-id`` 归一后仍是 ``user-id``。

    这一条把「曾经漏掉的那一格」单独写成证人：把归一改回旧写法时，
    上面的参数化会红在 user-id 上，而这里给出一句可直接引用的原因。
    """
    old_rule = "user-id".lower().replace("_", "")
    assert old_rule != "userid", "旧规则的归一结果变了？先修这条断言再谈回归"
    assert contract.normalise_identity_key("user-id") == "userid"


def test_go_normaliser_uses_the_same_rule():
    """不跑 Go 也能在 CI 里发现「Go 侧只剥了下划线」这类漂移。"""
    src = IDENTITY_GO.read_text(encoding="utf-8")
    assert f'identityKeyCanonical = "{contract.IDENTITY_KEY_CANONICAL}"' in src
    assert 'strings.ReplaceAll(key, "_", "")' in src, "Go 归一没剥下划线"
    assert 'strings.ReplaceAll(key, "-", "")' in src, "Go 归一没剥连字符"
    decode = DECODE_GO.read_text(encoding="utf-8")
    pattern = rf'ReasonForeignUserID\s*=\s*"{re.escape(contract.REASON_FOREIGN_USER_ID)}"'
    assert re.search(pattern, decode), (
        "Go 的 foreign_user_id 常量与 Python 不同值：同一件事会出现两个码"
    )
