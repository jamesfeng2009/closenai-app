"""R2 两份「Python → Go 副本」的对面那半边闸（2026-10-16）。

被验对象（全部由 `scripts/gen_go_ai_side.py` 录制，Go 侧只读不写）：
  · `go-core/internal/aiclient/templates/copy_msg_fallback.json`
    ＝ `backend/data/copy/{locale}.json` 里 `msg.fallback` 的 15 语言副本，
    供 Go 在「Python 整个进程联系不上」时出降级文案（V3 `13:110-117` §8）。
  · `go-core/testdata/sse_event_golden.json`
    ＝ `backend/routers/chat.py:39-42` 的 `_sse_event()` 输出字节夹具，
    供 `go-core/internal/aiclient/aiclient_test.go` 逐字节回放。

为什么 Python 侧也要来一遍，而不信 Go 那侧的 `TestSSEGoldenReplay`：
  那八条测证明的是「Go 的输出 == 夹具」，**不**证明「夹具 == 今天的 Python」。
  两份副本都是生成物，漂移有两种成因——① 改了源忘了重录，② 有人手改了副本。
  ①要靠 `--check`，②要靠「拿当下的真函数重算一遍再比」。两个都在这个文件里。

另两条是跨语言的：
  · 事件名集合必须**等于**已发布客户端认识的那四种
    （`frontend/src/types/index.ts:33` 逐字 `type: 'token' | 'memory_context' | 'done' | 'error'`，
     `frontend/src/services/api.ts:329-340` 的 switch 也只有这四支）——
    Go 的 `sse.go` 把 DEGRADED/BLOCKED 落成 done 就是为这条纪律服务的。
  · 15 语言的 `msg.fallback` 全部非空且条数一致：`message_generator.py:453-458` 的
    `len(user_input) % len(templates)` 遇到空数组就是 **ZeroDivisionError**
    （`copy.py:79-90` 的 `t_list` 兜底返回 `[]`），而它发生在降级路径上；
    条数不一致会让同一段中文在两侧轮转到不同模板（Go 侧副本按同一条口径录）。

跑法：
    .venv/bin/python -m pytest backend/tests/test_r2_ai_side_copies.py -q
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.core import copy as copy_lib
from backend.routers.chat import _sse_event

ROOT = Path(__file__).resolve().parents[2]
RECORDER = ROOT / "scripts" / "gen_go_ai_side.py"
TPL_PATH = ROOT / "go-core" / "internal" / "aiclient" / "templates" / "copy_msg_fallback.json"
GOLDEN_PATH = ROOT / "go-core" / "testdata" / "sse_event_golden.json"
FRONTEND_TYPES = ROOT / "frontend" / "src" / "types" / "index.ts"
FRONTEND_API = ROOT / "frontend" / "src" / "services" / "api.ts"

# 已发布客户端认识的事件名（不是「我们希望它认识的」，是它 switch 里真写了的四支）。
CLIENT_KNOWN_EVENTS = {"token", "memory_context", "done", "error"}


@pytest.fixture(scope="module")
def tpl() -> dict:
    assert TPL_PATH.exists(), f"副本不存在：{TPL_PATH}（先跑 {RECORDER}）"
    return json.loads(TPL_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def golden() -> dict:
    assert GOLDEN_PATH.exists(), f"夹具不存在：{GOLDEN_PATH}（先跑 {RECORDER}）"
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def test_copies_do_not_drift():
    """成因①：改了 `backend/data/copy/*.json` 或 `_sse_event()` 却没重录副本 → 红。"""
    proc = subprocess.run(
        [sys.executable, str(RECORDER), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"副本漂移：\n{out}"
    assert "两份副本都与源一致" in out, f"--check 没说清自己查了什么：\n{out}"


def test_fallback_copy_is_locale_for_locale_source(tpl):
    """逐语言比对副本与 `msg.fallback` 原文（副本的键面也要一起比，改名会先在这里显形）。"""
    assert tpl["locales"] == list(copy_lib.SUPPORTED_LOCALES), "15 语言的**顺序**是契约的一部分"
    for locale in copy_lib.SUPPORTED_LOCALES:
        source = copy_lib._lookup(copy_lib._load(locale), ("msg", "fallback"))
        assert source == tpl["fallback"][locale], f"{locale} 的副本与源不同：{source!r} ≠ ..."
        assert tpl["fallback"][locale], f"{locale} 的副本是空数组"


def test_fallback_never_hits_the_modulo_zero_path(tpl):
    """把 `t_list → []` 那条 `% 0` 的**可达性**关掉，而不是顺手去修它。

    缺陷登记（2026-10-16，只记不修，理由见 tasks.md §R2）：
    `message_generator.py:453-458` 的 `_fallback_reply` 写的是
    `idx = len(user_input) % len(templates)`，而 `copy.py:79-90` 的 `t_list`
    在两级回退都缺时返回 `[]` ⇒ 那一刻就是 ZeroDivisionError，
    且发生在「本来要兜底」的那条路径上（兜底反而炸掉）。
    今天 15 语言都有非空数组，所以这条分支不可达；本函数是它的**可达性闸**——
    谁把某语言的 `msg.fallback` 删空，这里当场红。
    """
    for locale in copy_lib.SUPPORTED_LOCALES:
        templates = copy_lib.t_list("msg", "fallback", locale)
        assert isinstance(templates, list) and templates, f"{locale} 的 msg.fallback 空了：`% 0` 分支变可达"
    counts = {len(v) for v in tpl["fallback"].values()}
    assert len(counts) == 1, f"15 语言条数不一致 {counts}：轮转下标会在两侧分叉"


def test_unknown_language_still_rotates(tpl):
    """未知语言码必须经 `locale_for` 落到 en（`copy.py:33-41`），Go 侧副本同形。

    非 ASCII 的输入在这里也有观测点：Python 的 `len()` 数**码点**、Go 的 `len(string)` 数**字节**，
    Go 侧的闸是 `TestFallbackRotationMatchesPythonCodePointCount`，这里补上 Python 这一半参照值。
    """
    zh = copy_lib.t_list("msg", "fallback", "zh")
    en = copy_lib.t_list("msg", "fallback", "en")
    assert copy_lib.t_list("msg", "fallback", "sw") == en, "未知码没回退到 en"
    text = "我今天"
    assert len(text) == 3, "这条参照值的前提是 Python 按码点数（3）；变了就要同步改 Go 侧那条测"
    assert zh[len(text) % len(zh)] == zh[3], "Python 侧的轮转参照漂了（3 % 4 == 3）"


def test_sse_golden_bytes_still_come_from_today_router(golden):
    """成因②：有人手改了夹具里的期望字节 → 红（`--check` 只比「与源一致」，比不出手改的夹具）。"""
    for i, case in enumerate(golden["cases"]):
        live = _sse_event(case["event"], case["payload"])
        assert live == case["line"], (
            f"case {i}（{case['event']}）的字节与今天的 _sse_event 不同步："
            f"\n 夹具={case['line']!r}\n 现算={live!r}（改 chat.py 要重录，改副本要说明理由）"
        )


def test_sse_golden_covers_four_event_names_and_no_more(golden):
    """Go 只能把块映射成客户端认识的四种事件名；夹具就是这条纪律的取值面。"""
    seen = {c["event"] for c in golden["cases"]}
    assert seen == CLIENT_KNOWN_EVENTS, f"夹具的事件名集合漂了：{seen}"
    assert len(golden["cases"]) >= 13, f"夹具被裁到 {len(golden['cases'])} 条，转义面覆盖不足"
    assert "_sse_event" in golden["note"], "期望字节只能来自真 _sse_event，标注变了要说明"


def test_shipped_client_still_only_knows_four_events():
    """对面（已发布前端）的事件面——这条变了就不是「改一行 Go」的事，要先改客户端。"""
    types_src = FRONTEND_TYPES.read_text(encoding="utf-8")
    api_src = FRONTEND_API.read_text(encoding="utf-8")
    for event in sorted(CLIENT_KNOWN_EVENTS):
        assert f"'{event}'" in types_src, f"types/index.ts 的联合类型里没有 {event}（客户端面缩了？）"
        assert f"case '{event}'" in api_src, f"api.ts 的 switch 里没有 {event} 这一支"
    assert api_src.count("case '") >= 4, "SSE 解析的分支数变了，先回来核这条闸的判据"


def test_sse_wire_shape_is_data_prefix_only():
    """现网没有 `event:` 字段：前端是 `JSON.parse(line.slice(6))`（api.ts:325）。

    `slice(6)` 正好是 `data: ` 的长度 ⇒ 前后缀任一边改动都会把已发布客户端的解析打空。
    Go 侧 `sse.go` 的 `sseWrap` 抄的就是这一条，这里把「为什么没有 event: 字段」钉成闸。
    """
    assert _sse_event("done", {}).startswith("data: ")
    assert _sse_event("done", {}).endswith("\n\n")
    assert "event:" not in _sse_event("token", {"content": "x"})
    assert "line.slice(6)" in FRONTEND_API.read_text(encoding="utf-8"), (
        "前端不再按 `data: ` 定长切了 ⇒ Go 侧 sseWrap 的口径要重新取证"
    )
