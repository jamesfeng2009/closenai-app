"""邮件那批测（2026-10-20 批 3 重定）。

重定的理由＝code review 抓到的 P1-5：`email_service.py` 原先写
`settings.environment == "production"`，而 D-T604-3 定的口径是「`staging` 与
`production` 同档（受保护）」。字面量那个写法既漏 `prod` 别名也漏 `staging`，
后果不是报错而是**静默**：staging 上 `resend_api_key` 没配时走 DEV 告警降级、
`send_email_code` 照返回 `{"status": "ok"}`，而邮件根本没发出去——验证码是登录凭证，
用户被永久挡在登录外，运维在日志里只看得到一条 `[DEV]` 告警。

三条纪律摆在这里，因为它们决定了本文件的形状：
· **档籍判据只有一份**：`core/env_isolation.PROTECTED_ENVIRONMENTS`。本文件不写第二张表，
  而是**遍历那张表**（表里将来加一档，这几条测自动要求邮件也认它——否则就是「判据改了、
  某个入口没跟上」那个形状本身）。
· **反向归零闸要配正向证人**（[[feedback-gate-needs-positive-witness]]）：`development`
  必须**继续**能降级，否则这条闸会在第一天被人绕过去；本文件两半各钉一次。
· **静默成功是这一格唯一的坏形状**：所有「不许放过」的断言都判「抛了什么」，
  而不是判「有没有日志」——`[DEV]` 告警在两种情形下长得一模一样。

`_mask_email` 与「验证码明文不落日志」是既有口径（本批不动语义，只把它们一起钉住）。
"""
from __future__ import annotations

import ast
import inspect
import logging

import pytest

from backend.config import settings
from backend.core import env_isolation
from backend.core.errors import AppException, ErrorCode
from backend.services import email_service

DEV_CODE = "123456"
PROD_SENDER = "no-reply@closenai.test"


def _use_tier(monkeypatch, environment: str, api_key: str = "") -> None:
    """把档籍与 key 摆成某一格。key 走 monkeypatch，绝不留到 .env 里。"""
    monkeypatch.setattr(settings, "environment", environment)
    monkeypatch.setattr(settings, "resend_api_key", api_key)
    monkeypatch.setattr(settings, "email_from", PROD_SENDER)


# ── 受保护档：缺 key 必须抛，不许静默成功 ──────────────────────────

def test_every_protected_tier_raises_503_without_an_api_key(monkeypatch):
    """遍历**那张表**而不是抄一份名单：新增一档而邮件不认＝本条红。

    断言的另一侧是 `env_isolation.PROTECTED_ENVIRONMENTS` 这个模块对象，
    不是本文件里的字面量（[[feedback-orm-identity-map-gate]]：自己比自己恒真）。
    """
    seen = []
    for tier in env_isolation.PROTECTED_ENVIRONMENTS:
        _use_tier(monkeypatch, tier)
        with pytest.raises(AppException) as caught:
            email_service.send_verification_code("t@x.com", DEV_CODE, "login", "zh")
        assert caught.value.code == ErrorCode.SERVICE_UNAVAILABLE, (tier, caught.value)
        seen.append(tier)
    assert seen == list(env_isolation.PROTECTED_ENVIRONMENTS), (
        f"受保护档只跑到了一半：{seen}／表是 {env_isolation.PROTECTED_ENVIRONMENTS}")


def test_staging_specifically_raises_503_without_an_api_key(monkeypatch):
    """P1-5 那一格的本名靶：staging 从前走 DEV 降级并返回成功。

    单独成一条（不并入上一条的参数化）＝变异脚本要拿它当 marker，
    而 pytest 会把中文参数 id 转义成 `\\uXXXX`，节点串对不上就变成「未跑」。
    """
    _use_tier(monkeypatch, "staging")
    with pytest.raises(AppException) as caught:
        email_service.send_verification_code("u@x.com", DEV_CODE, "register", "en")
    assert caught.value.code == ErrorCode.SERVICE_UNAVAILABLE
    assert "unavailable" in str(caught.value.message).lower(), caught.value.message


def test_prod_alias_is_the_same_tier_as_production(monkeypatch):
    """`prod` 是 `production` 的别名（D-T604-2 词表）——字面量比较漏的就是它。"""
    _use_tier(monkeypatch, "prod")
    with pytest.raises(AppException) as caught:
        email_service.send_verification_code("u@x.com", DEV_CODE, "reset", "zh")
    assert caught.value.code == ErrorCode.SERVICE_UNAVAILABLE


# ── 正向证人：development 仍然能降级（过紧的闸会被整条绕开）────────

def test_development_still_downgrades_without_raising(monkeypatch):
    """dev 缺 key 只告警、不抛：本机的登录联调第一天就要能跑（同 D-T604-6 的方向选择）。"""
    _use_tier(monkeypatch, "development")
    sent: list[dict] = []
    monkeypatch.setattr(email_service.resend.Emails, "send", lambda payload: sent.append(payload))
    email_service.send_verification_code("u@x.com", DEV_CODE, "login", "zh")
    assert sent == [], "dev 缺 key 却真的调了发送 SDK：key 是空串，这一步本该被跳过"


def test_development_warning_never_carries_the_plain_code(monkeypatch, caplog):
    """日志红线：`[DEV]` 告警只带脱敏邮箱，验证码明文（5 分钟有效登录凭证）不落日志。"""
    _use_tier(monkeypatch, "development")
    with caplog.at_level(logging.WARNING, logger="closenai.email"):
        email_service.send_verification_code("secretuser@x.com", DEV_CODE, "login", "zh")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "[DEV]" in text, f"dev 降级路径没有那条告警（静默返回与告警返回要能区分）：{text!r}"
    assert DEV_CODE not in text, "验证码明文进了日志"
    assert "secretuser" not in text, f"邮箱没脱敏：{text!r}"
    assert "se***@x.com" in text, text


# ── 配了 key 就真发：这一格管的是「别把 503 当成万能挡箭牌」 ────────

def test_configured_key_sends_with_the_shared_copy_and_masked_recipient(monkeypatch):
    _use_tier(monkeypatch, "production", api_key="rg_test_FAKE_not_real")
    sent: list[dict] = []
    monkeypatch.setattr(email_service.resend.Emails, "send", lambda payload: sent.append(payload))
    email_service.send_verification_code("u@x.com", DEV_CODE, "login", "zh")
    assert len(sent) == 1, sent
    assert sent[0]["to"] == ["u@x.com"] and sent[0]["from"] == PROD_SENDER
    assert DEV_CODE in sent[0]["html"], "正文里没有验证码（文案回退把 {code} 吞了）"


def test_send_failure_is_not_silently_swallowed(monkeypatch):
    """SDK 抛错 ⇒ AppException(INTERNAL_ERROR)：返回 ok 而邮件没发出去是同一格的另一半。"""
    _use_tier(monkeypatch, "production", api_key="rg_test_FAKE_not_real")

    def boom(payload):
        raise RuntimeError("resend 502")

    monkeypatch.setattr(email_service.resend.Emails, "send", boom)
    with pytest.raises(AppException) as caught:
        email_service.send_verification_code("u@x.com", DEV_CODE, "login", "zh")
    assert caught.value.code == ErrorCode.INTERNAL_ERROR


# ── 装配证人：这一格读的是共享判据，不是自己的字面量 ────────────────

def test_email_service_reads_the_shared_tier_predicate():
    """`email_service.py` 里不许再出现字面量档籍比较（判据只有一份）。

    只判 **AST 里的代码节点**，不判源文本：本文件与 `email_service` 的 docstring 都要
    写出那句旧写法才能解释为什么禁止它，纯文本 grep 会把注释也算成违规
    （＝T6-03 缺陷①／T6-04 那笔「扫描代码与注释同池」的同一形状）。
    两侧不许同源：一侧是 `email_service` 的语法树，另一侧是 `env_isolation` 的函数名。
    """
    tree = ast.parse(inspect.getsource(email_service))
    calls = {
        f"{node.func.value.id}.{node.func.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }
    assert "env_isolation.is_protected" in calls, (
        f"邮件这一格没调共享判据（它换来的仍是「staging 不是生产」那个旧口径，P1-5）：{sorted(calls)}")

    literal_tier_compare = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and ast.unparse(node.left) == "settings.environment"
        and all(isinstance(c, ast.Constant) for c in node.comparators)
    ]
    assert not literal_tier_compare, (
        f"字面量档籍比较回来了（漏 prod 别名也漏 staging，后果是静默成功而不是报错）："
        f"{[ast.unparse(n) for n in literal_tier_compare]}")
