"""批 4 · D-G17 的两侧同批闸：`SafetyStatus` 的第四态 UNSAFE。

为什么单独一份文件（而不是并进 `test_r3_safety_tri.py`）：那条测判的是**一个函数**的返回值，
而 D-G17 的风险面是「三处各写一份枚举」——契约源（proto）、生成物（`backend/gen`）、
Python 常量、Go 常量。任何一处漏改，函数级测仍然是绿的：
· 只改 proto 不重导 ⇒ `rpc/server.py:173` 里的 `common_pb2.SAFETY_STATUS_UNSAFE` 直接 AttributeError；
· 改了 Python 不改 Go ⇒ Go 的 default 分支 fail-closed 拦下（`domain/gate.go` 第 6 关），
  拦住是对的、身份说成「判定缺失」是错的，指标面就按这个错身份计数；
· 改了 Go 不改 Python ⇒ 三面违规内容仍落 crisis，Go 侧新加的 unsafe 分支是死代码。

判据一律指向**源**而不是被测物：枚举的期望值来自 `contracts/proto/closenai_common.proto:84-101`
（本文件把它当唯一真相源读），Python 常量来自 `backend/ai/safety.py:525-528`，
Go 常量来自 `go-core/internal/domain/candidate.go:24-34`。四态的语义口径来自
`tasks.md §R3` 登记的缺口②（「非危机的违规只能落 crisis」）与 `V3 15 §8`（additive 枚举、号位永不复用）。

⚠️ 计数与扫描一律点名具体文件，不用 `rglob`：`backend/gen/`、`go-core/gen/` 是生成物，
protoc 会把枚举名烤进去，数进去只会得到「UNSAFE 出现 N 次取决于 protoc 版本」这种假闸
（工作单 §八.2 点名的正是这一格）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.ai.rpc.server import SCHEMA_VERSION, ClosenAIService
from backend.ai.safety import (
    SAFETY_CRISIS,
    SAFETY_OK,
    SAFETY_UNAVAILABLE,
    SAFETY_UNSAFE,
)
from backend.gen import closenai_ai_rpc_pb2 as ai_pb2
from backend.gen import closenai_common_pb2 as common_pb2

ROOT = Path(__file__).resolve().parents[2]
PROTO = ROOT / "contracts" / "proto" / "closenai_common.proto"
SAFETY_PY = ROOT / "backend" / "ai" / "safety.py"
CANDIDATE_GO = ROOT / "go-core" / "internal" / "domain" / "candidate.go"

# 失真句归零的面：这四态改造后仍是假话的写法。历史口径（「三态时只能落 crisis」）不在这里，
# 因为那是 D-G17 的根因说明，删掉反而丢掉理由。
STALE_PHRASES = ("三态之外", "不是三态之一", "proto 的三态", "没有 UNSAFE")
SURFACE_FILES = (
    "backend/ai/safety.py",
    "backend/ai/rpc/server.py",
    "backend/ai/mq/contract.py",
    "backend/tests/test_r3_safety_tri.py",
    "backend/tests/test_r3_rpc_server.py",
    "backend/tests/test_r3_grpc_server_interop.py",
    "scripts/r3_interop_server.py",
    "contracts/proto/closenai_common.proto",
    "contracts/proto/closenai_ai_rpc.proto",
    "go-core/internal/domain/candidate.go",
    "go-core/internal/domain/gate.go",
    "go-core/internal/domain/proactive_gate.go",
    "go-core/internal/aiclient/grpc.go",
    "go-core/internal/messaging/inbound/candidate_decode.go",
)

# Python 状态串 → 生成物里的枚举值名（`rpc/server.py:167-175` 那张表的另一半）。
# 号位不在这里写死，一律由 `_proto_safety_enum()` 现读契约源，避免「两个真相源」。
PY_STATE_TO_ENUM_NAME = {
    SAFETY_OK: "SAFETY_STATUS_OK",
    SAFETY_CRISIS: "SAFETY_STATUS_CRISIS",
    SAFETY_UNSAFE: "SAFETY_STATUS_UNSAFE",
    SAFETY_UNAVAILABLE: "SAFETY_STATUS_UNAVAILABLE",
}


def _proto_safety_enum() -> dict[str, int]:
    """从契约源解析 `SafetyStatus` 的值名→号位。

    解析失败必须红（不是 skip）：这条闸的全部意义是「读源对账」，读不到就等于没闸。
    """
    text = PROTO.read_text(encoding="utf-8")
    body = re.search(r"enum SafetyStatus \{(.*?)\n\}", text, re.S)
    assert body, "没在契约源里定位到 `enum SafetyStatus`，本闸失效"
    pairs = re.findall(r"^\s*(SAFETY_STATUS_[A-Z_]+)\s*=\s*(\d+);", body.group(1), re.M)
    assert pairs, "SafetyStatus 的枚举项一条没解析到（正则失效即红）"
    return {name: int(num) for name, num in pairs}


# ── 值集三方对账 ─────────────────────────────────────────


def test_proto_declares_the_four_states_plus_unspecified():
    """锚点：号位与命名以契约源为准。additive 的底线是「旧号位不许变」。"""
    got = _proto_safety_enum()
    assert got == {
        "SAFETY_STATUS_UNSPECIFIED": 0,
        "SAFETY_STATUS_OK": 1,
        "SAFETY_STATUS_CRISIS": 2,
        "SAFETY_STATUS_UNAVAILABLE": 3,
        "SAFETY_STATUS_UNSAFE": 4,
    }, "SafetyStatus 的值集变了：三处（Python 常量、Go 常量、生成物）必须同批改"


def test_python_constants_cover_proto_names_verbatim():
    """Python 侧的四个串必须逐字等于 proto 值名去掉 `SAFETY_STATUS_` 前缀后的小写。

    少一个 ⇒ 有一个面在跨进程后无法表达；多一个 ⇒ Go 的 default 分支会把它读成判定缺失。
    """
    declared = {
        name[len("SAFETY_STATUS_"):].lower()
        for name in _proto_safety_enum()
        if name != "SAFETY_STATUS_UNSPECIFIED"
    }
    assert {SAFETY_OK, SAFETY_CRISIS, SAFETY_UNSAFE, SAFETY_UNAVAILABLE} == declared
    # UNSPECIFIED 不该有对应的 Python 常量：服务端永远不该主动发它（proto:88-92 的理由）。
    src = SAFETY_PY.read_text(encoding="utf-8")
    assert "SAFETY_UNSPECIFIED =" not in src


def test_go_constants_match_python_constants():
    """Go 的 `domain` 值集与 Python 同形（对面是 `candidate.go:24-34`）。"""
    src = CANDIDATE_GO.read_text(encoding="utf-8")
    pairs = dict(re.findall(r'Safety(\w+)\s+SafetyStatus\s*=\s*"(\w+)"', src))
    assert pairs, "没在 candidate.go 解析到 SafetyStatus 常量，本闸失效"
    assert set(pairs.values()) == {
        SAFETY_OK, SAFETY_CRISIS, SAFETY_UNSAFE, SAFETY_UNAVAILABLE
    }
    assert pairs["Unsafe"] == SAFETY_UNSAFE


def test_generated_python_enum_is_in_sync_with_source():
    """重导真的落进了 `rpc/server.py` 引用的那份生成物，而不只是 .proto 改了字。"""
    declared = _proto_safety_enum()
    for name, number in declared.items():
        assert getattr(common_pb2, name) == number, f"{name} 号位漂移"
    assert common_pb2.SafetyStatus.Name(declared["SAFETY_STATUS_UNSAFE"]) == "SAFETY_STATUS_UNSAFE"


# ── rpc 映射面 ───────────────────────────────────────────


class _NoAbort:
    """合法输入不该走到 `context.abort`；走到就抛，让测红而不是静默返回默认消息。

    `invocation_metadata` 是批 T-E 在 ClassifySafety 入口新增的读点（真 context 必有），
    夹具不实现它就会 AttributeError——红会伪装成「映射面坏了」，先补上「没带头」这一格。
    """

    def invocation_metadata(self):
        return ()

    async def abort(self, code, detail):
        raise AssertionError(f"合法判定被 abort：{code} {detail}")


async def _safety_status_from_rpc(py_status: str) -> int:
    async def _fixed(text, language, *, scope="chat"):
        return {
            "status": py_status, "reason": "", "matched": [], "categories": [],
            "advice_blocked": False,
        }

    service = ClosenAIService(classify=_fixed)
    out = await service.ClassifySafety(
        ai_pb2.TextInput(schema_version=SCHEMA_VERSION, text="x"), _NoAbort()
    )
    return out.safety.status


@pytest.mark.parametrize("py_status", sorted(PY_STATE_TO_ENUM_NAME))
async def test_rpc_emits_each_declared_state(py_status):
    """四个 Python 状态串各回自己的号位，且谁都不许退成 UNSPECIFIED。"""
    declared = _proto_safety_enum()
    got = await _safety_status_from_rpc(py_status)
    assert got == declared[PY_STATE_TO_ENUM_NAME[py_status]]
    assert got != declared["SAFETY_STATUS_UNSPECIFIED"]


async def test_rpc_unsafe_and_crisis_are_distinct_on_the_wire():
    """D-G17 的正题：这两格在线上必须是两个值。相等就等于第四态没加。"""
    declared = _proto_safety_enum()
    assert declared["SAFETY_STATUS_UNSAFE"] != declared["SAFETY_STATUS_CRISIS"]
    got_unsafe = await _safety_status_from_rpc(SAFETY_UNSAFE)
    got_crisis = await _safety_status_from_rpc(SAFETY_CRISIS)
    assert got_unsafe != got_crisis, "unsafe 与 crisis 在线上同形 ⇒ Go 无法区分拦下与危机"


async def test_rpc_unknown_state_is_unavailable_not_one_of_the_four():
    async def _weird(text, language, *, scope="chat"):
        return {
            "status": "maybe", "reason": "", "matched": [], "categories": [],
            "advice_blocked": False,
        }

    declared = _proto_safety_enum()
    service = ClosenAIService(classify=_weird)
    out = await service.ClassifySafety(
        ai_pb2.TextInput(schema_version=SCHEMA_VERSION, text="x"), _NoAbort()
    )
    assert out.safety.status == declared["SAFETY_STATUS_UNAVAILABLE"]


# ── 源码形状反向闸：三面不许再报 crisis ───────────────────


def _tri_calls() -> list[tuple[str, str]]:
    """解析 `check_text_tri` 的每个返回点，取出 (状态常量名, reason)。"""
    src = SAFETY_PY.read_text(encoding="utf-8")
    start = src.index("async def check_text_tri(")
    end = src.index("\n# 四态取值", start)
    body = re.sub(r"\s+", " ", src[start:end])
    return re.findall(r'_tri\(\s*SAFETY_([A-Z_]+),\s*"(\w*)"', body)


def test_violation_faces_return_unsafe_not_crisis():
    """形状闸：banned/overreach/model 三面各落 unsafe，crisis 只剩危机那一格。

    为什么用源码形状而不是只靠行为测：行为测（`test_r3_safety_tri.py`）判的是「这一次调用
    返回什么」，而这一条判的是「还有没有哪个违规面在写 SAFETY_CRISIS」。把 crisis 挪回
    banned 那一行时，只要同时改掉对应行为测的断言，行为面就重新自证了——形状闸不听这个。
    """
    assert _tri_calls() == [
        ("OK", ""),
        ("CRISIS", "crisis"),
        ("UNSAFE", "banned"),
        ("UNSAFE", "overreach"),
        ("UNAVAILABLE", "model"),
        ("UNSAFE", "model"),
        ("OK", ""),
        ("UNAVAILABLE", "model"),
    ]


def test_crisis_name_appears_once_in_tri_body():
    body = re.sub(r"\s+", " ", SAFETY_PY.read_text(encoding="utf-8").split(
        "async def check_text_tri(", 1
    )[1].split("\n# 四态取值", 1)[0])
    assert body.count("SAFETY_CRISIS") == 1, "crisis 在四态面里被用了不止一次 ⇒ 违规面又挤回来了"


# ── 失真措辞归零 ─────────────────────────────────────────


def test_stale_three_state_wording_is_gone():
    """改完语义必须把「只有三态」这类句子一起清掉：注释敏感这条在批 3 已被验证两次。"""
    offenders = []
    for rel in SURFACE_FILES:
        path = ROOT / rel
        assert path.exists(), f"闸门点名面不存在：{rel}"
        text = path.read_text(encoding="utf-8")
        offenders += [f"{rel}: {phrase}" for phrase in STALE_PHRASES if phrase in text]
    assert offenders == []


def test_four_state_wording_really_landed():
    """反向证人：上一条归零可能是扫了个空集，这里要求「四态」确实在面上出现过。"""
    hits = sum(
        (ROOT / rel).read_text(encoding="utf-8").count("四态") for rel in SURFACE_FILES
    )
    assert hits >= 6, f"「四态」在点名面里只出现 {hits} 次 ⇒ 改判没落到文档面上"
