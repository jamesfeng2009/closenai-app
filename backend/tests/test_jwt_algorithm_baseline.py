"""批 3 · D-G13 的跨语言登记闸：`JWT_ALGORITHM` 在 Go 侧是校验位、在 Python 侧是真旋钮。

实测形状（2026-10-17 逐处读源，四条都指得回）：
  · `go-core/internal/config/config.go` 读 env 后**只比对**字面量 "HS256"，不相等就
    `return cfg, errors.New(...)` ⇒ 改成 RS256 的后果是「Go 拒绝启动」，不是「换了算法」；
  · `go-core/internal/auth/token.go:88/:100` 硬编码 HS256（签发 + `t.Method != jwt.SigningMethodHS256`），
    从不读那个字段；
  · `backend/config.py:38` 默认 HS256，而 `backend/core/auth.py` 的
    `create_access_token` / `create_refresh_token` / `decode_token` / `get_current_user`
    **四处**都读 `settings.jwt_algorithm` ⇒ 同一个 env 在对面那一侧确实能改动签发。

⇒ 这个键的语义是「只能是 HS256」，不是「选一个算法」。工作单 D-G13 因此**不改签发处**
（跨语言基线就是 HS256，插一根没人用的分支只会多出一个假旋钮，与 D-G12 那格同类），
改成把这个口径钉成可执行判据。本文件四向：三处默认值同字、Go 侧零读取点、
Python 侧确有读取点（不对称本身是登记的一部分）、以及两侧都 **fail-closed**。

为什么最后两向要跑真 `jwt` 而不是扫源码：这条闸真正防的事故是
「运维把 JWT_ALGORITHM 改成 HS384，py-api 照常签、go-api 拒启或把该批端点 401」。
只有拿同一个密钥真签一遍、再让另一侧真验一遍，才证明最坏情况是拒绝而不是误接受
（后者是经典的 HMAC 跨算法密钥复用）。Go 那一侧的对称判据在
`go-core/internal/auth/token_alg_gate_test.go`。
"""
from __future__ import annotations

import base64
import json
import pathlib
import re

import jwt
import pytest
import yaml

from backend.config import settings
from backend.core import auth as auth_module

ROOT = pathlib.Path(__file__).resolve().parents[2]
GO_CORE = ROOT / "go-core"
CONFIG_GO = (GO_CORE / "internal" / "config" / "config.go").read_text(encoding="utf-8")
TOKEN_GO = (GO_CORE / "internal" / "auth" / "token.go").read_text(encoding="utf-8")
PY_AUTH = (ROOT / "backend" / "core" / "auth.py").read_text(encoding="utf-8")
CONFIG_MAP = yaml.safe_load(
    (ROOT / "deploy" / "k8s" / "10-config.yaml").read_text(encoding="utf-8")
)

BASELINE = "HS256"
GATE_SECRET = "d-g13-cross-language-gate-secret-0123456789abcdefghij"  # ≥48B：HS384 不报短密钥
# 同族更长摘要：同一个 HMAC 密钥、不同算法——这正是误接受就会构成密钥复用的那一族。
NON_BASELINE = "HS384"


def test_three_defaults_are_the_same_literal():
    """Python 默认、Go 默认、部署清单三处必须逐字同值——这个键没有「各自默认」的余地。"""
    assert settings.jwt_algorithm == BASELINE
    assert f'envString("JWT_ALGORITHM", "{BASELINE}")' in CONFIG_GO, (
        "Go 侧的默认值不再是 HS256：两侧默认一旦不同，同一个 ConfigMap 就只有一侧是对的"
    )
    assert CONFIG_MAP["data"]["JWT_ALGORITHM"] == BASELINE, (
        "部署清单没把这个键钉在基线上"
    )


def test_go_validator_refuses_every_other_value():
    """Go 那一侧的判据形态：只比对字面量，没有第二档。"""
    assert f'if cfg.JWTAlgorithm != "{BASELINE}"' in CONFIG_GO, (
        "config.go 里的校验不再是单值比对 ⇒ D-G13「这个键只能是 HS256」的登记需要重写"
    )
    accepted = set(re.findall(r'cfg\.JWTAlgorithm\s*[!=]=\s*"([A-Z0-9]+)"', CONFIG_GO))
    assert accepted == {BASELINE}, f"Go 侧认得的算法集合不再是单值：{accepted}"


def test_go_has_no_reader_of_the_field_outside_config():
    """「校验位」的硬证据：除 internal/config 之外，Go 全模块不许读 `JWTAlgorithm`。

    与 `gate_map_coverage_test.go`/`jwt_algorithm_gate_test.go` 同一判据的 pytest 复刻：
    `test_go_auth_interop.py` 在 `GO_BIN` 缺失时整个文件 skip，这条不依赖工具链，
    所以 CI 少装 Go 时口径仍然有人守（本仓有过一次「整包 skip 被当成绿」的教训）。
    """
    readers = []
    for path in GO_CORE.rglob("*.go"):
        rel = path.relative_to(GO_CORE).as_posix()
        if rel.startswith(("gen/", "testdata/")):
            continue
        if rel.startswith("internal/config/"):
            continue
        if "JWTAlgorithm" in path.read_text(encoding="utf-8"):
            readers.append(rel)
    assert not readers, (
        f"这些 Go 文件读了 cfg.JWTAlgorithm：{readers} ⇒ 键已从校验位变成旋钮。"
        "按 D-G13：换算法必须 Go/Python 两侧同批改签发与校验，"
        "并重录 backend/tests/test_go_auth_interop.py 的基线夹具"
    )


def test_python_side_readers_are_still_there_and_counted():
    """登记的另一半：Python 确实读这个键，所以「改 env 只影响一边」这件事是真实的。

    这条**不是**要求它一直读下去——把它改成硬编码同样能收口 D-G13（那时两侧都是常量）。
    它要防的是「登记说这里不对称，而代码早就变了」：数一旦不等于实测，注释就开始骗人。
    """
    sites = PY_AUTH.count("settings.jwt_algorithm")
    assert sites == 4, (
        f"`backend/core/auth.py` 读 settings.jwt_algorithm 的处数 = {sites}，登记值是 4"
        "（create_access_token / create_refresh_token / decode_token / get_current_user）。"
        "要么是本文件的登记过期，要么是有人动了签发面——两种都要重读 D-G13 的口径"
    )


def test_python_rejects_a_token_signed_with_a_non_baseline_algorithm():
    """fail-closed 的 Python 向：同一密钥换成 HS384 签，按基线解码必须拒。"""
    token = jwt.encode(
        {"sub": "gate-user", "type": "access"}, GATE_SECRET, algorithm=NON_BASELINE
    )
    with pytest.raises(jwt.InvalidAlgorithmError):
        jwt.decode(token, GATE_SECRET, algorithms=[BASELINE])


def test_python_knob_really_moves_the_header(monkeypatch):
    """证伪「这只是注释」：把 settings.jwt_algorithm 改掉，签发出来的 header 真的变。

    这条为的是让上面那向不是空话——正因为 Python 这边会真的换算法，
    才需要两侧各自的 fail-closed 闸（本文件上一条 + token_alg_gate_test.go）。
    """
    monkeypatch.setattr(settings, "jwt_algorithm", NON_BASELINE)
    token = auth_module.create_access_token("gate-user")
    header = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "=="))
    assert header["alg"] == NON_BASELINE, (
        f"改了键却没换算法（header={header}）⇒ Python 侧也变成校验位了，"
        "本文件的不对称登记与 D-G13 的口径要一起改写"
    )
