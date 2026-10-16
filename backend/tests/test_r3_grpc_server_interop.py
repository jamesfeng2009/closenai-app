"""R3-④ · 真 Python server × 真 Go client 的跨进程互操作闸。

这一层补的是 R2 单测的结构盲区：R2 的四类失败跑在 Go 进程内的假 server 上（`stub_test.go`），
验的是 `aiclient.classify()` 那张表；而线上真正发生的是
「Python handler 的 `context.abort(code, detail)` → HTTP/2 trailers → Go 的 `status.Code(err)`」。
中间这三跳没有人替我们保证过：状态码会不会在编码时退化、detail 会不会带栈、
流式 abort 之后 `Recv()` 给的是哪一次错误。⇒ 这里两侧都用**真实现**：
server 是 `scripts/r3_interop_server.py` 起的真 `grpc.aio` 进程（真 handler、真生成物、真端口），
client 是 `go run ./tools/aiclientprobe` 里的真 `aiclient.GRPC`（真 deadline／重试／熔断／降级）。
只有最底下的 provider 调用是编排的——429 不能想调就调，超时不能说 2 秒就 2 秒。

四类失败的落点（tasks.md §R3 验收逐字「超时／429／5xx／畸形」）：
  · 超时  → test_safety_timeout_fails_closed / test_stream_before_first_token_times_out
  · 429   → test_embed_429_is_transient_and_retried / test_stream_429_midway_keeps_early_tokens
  · 5xx   → test_embed_5xx_is_transient_not_malformed / test_breaker_opens_on_real_transients
  · 畸形  → test_embed_count_mismatch_is_permanent_not_retried 等三条
外加一条跨语言才测得到的：**服务端业务拒绝**（INVALID_ARGUMENT）在 Go 侧必须落成 permanent。

跑法（本机 go 是混合安装，须点名；没有 go 时 skip 且把没跑的用例数报出来，不当成绿）：

    GO_BIN=/path/to/go1.27.1/bin/go .venv/bin/python -m pytest backend/tests/test_r3_grpc_server_interop.py -q -s

⚠️ skip≠绿：`_require_go()` 只在本机确实找不到 go 时才 skip，skip 信息里带着「这一轮少跑了 N 条」。
一旦 go 找到了但 `go run` 失败／server 起不来／端口握手没回，一律 **fail**——
那种情况下线上是真的会坏的，把它读成绿等于把这条闸拆了。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parents[2]
GO_CORE = ROOT / "go-core"
DRIVER = ROOT / "scripts" / "r3_interop_server.py"
PROBE_TIMEOUT = 300
PORT_HANDSHAKE_TIMEOUT = 60.0

# 场景 → 它属于哪一种 server 故障面。**这张表就是本文件的用例清单**：
# 最后一条测试断言「跑过的用例 == 这张表」，少跑一条就红，不靠肉眼数。
CASE_PLAN: Dict[str, str] = {
    "embed_ok": "ok",
    "embed_zero_dims_rejected": "ok",
    "safety_ok_maps": "ok",
    "safety_crisis_maps": "ok",
    "safety_blank_maps_server_side": "ok",
    "safety_unknown_scope_rejected": "ok",
    "stream_ok": "ok",
    "safety_slow_is_unavailable": "slow",
    "stream_slow_degraded": "slow",
    "embed_429_retried": "rate429",
    "safety_429_never_ok": "rate429",
    "stream_429_midway": "rate429",
    "embed_500_transient": "err500",
    "breaker_open_second_call": "err500",
    "embed_malformed_count": "malformed",
    "safety_out_of_contract_status": "malformed",
    "stream_malformed_same_chunk": "malformed",
    "stream_malformed_done": "malformed_done",
    "stream_overlong_rejected": "ok",
}

_RAN: Dict[str, Dict[str, Any]] = {}


# ── 工具链与进程编排 ─────────────────────────────────────


def _go_candidates() -> List[str]:
    raw = [
        os.environ.get("GO_BIN"),
        os.environ.get("GOROOT") and str(Path(os.environ["GOROOT"]) / "bin" / "go"),
        shutil.which("go"),
    ]
    out: List[str] = []
    for item in raw:
        if item and item not in out:
            out.append(item)
    return out


def _require_go() -> str:
    """找不到 go ⇒ skip 并把「少跑了几条」写进 skip 理由；找到了但编不动 ⇒ 也 skip（本机没装对工具链）。

    ⚠️ 这里 skip 的判据只有「go 二进制不存在 / 编译不过」一种。server 起不来、probe 跑挂、
    端口握手没回都在 `_start_server` / `_probe` 里 fail——那些是线上真会坏的面，读成绿等于拆闸。
    """
    candidates = _go_candidates()
    if not candidates:
        pytest.skip(
            f"本机没有 go 工具链（用 GO_BIN/GOROOT 指定）⇒ 本文件的 {len(CASE_PLAN)} 条"
            "跨进程互操作用例**一条都没跑**，别把它当绿"
        )
    failures = []
    for candidate in candidates:
        try:
            probe = subprocess.run(
                [candidate, "run", "./tools/aiclientprobe"],
                cwd=GO_CORE, capture_output=True, text=True,
                env=dict(os.environ), timeout=PROBE_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append(f"{candidate}: {exc}")
            continue
        # ⚠️ 判据是 stderr 里那句用法提示，不是退出码：`go run` 会把子进程的 exit(2)
        # 包成自己的 rc=1（本机实测），照 rc 判会把「编译成功」读成「工具链不可用」。
        if "必须给" in probe.stderr + probe.stdout:
            return candidate
        failures.append(f"{candidate}: rc={probe.returncode} {(probe.stderr + probe.stdout).strip()[:200]}")
    pytest.skip("没有能编译 aiclientprobe 的 go：\n" + "\n".join(failures))


def _start_server(mode: str) -> tuple[subprocess.Popen, int]:
    """起真 server，等它的端口握手行。任何异常都是 fail，不是 skip。"""
    proc = subprocess.Popen(
        [sys.executable, str(DRIVER)],
        env={**os.environ, "R3_INTEROP_MODE": mode},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=ROOT,
    )
    box: List[str] = []

    def _read() -> None:
        box.append(proc.stdout.readline())  # type: ignore[union-attr]

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    reader.join(PORT_HANDSHAKE_TIMEOUT)
    if reader.is_alive():
        proc.kill()
        raise AssertionError(f"server({mode}) 60s 内没回端口行，卡住了")
    line = box[0] if box else ""
    if not line.startswith("PORT "):
        err = proc.stderr.read() if proc.stderr else ""
        proc.kill()
        raise AssertionError(f"server({mode}) 没起来：握手行={line!r}\nstderr:\n{err}")
    return proc, int(line.split()[1])


def _probe(go_bin: str, port: int, cases: List[str], flags: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    cmd = [go_bin, "run", "./tools/aiclientprobe", "-target", f"127.0.0.1:{port}",
           "-cases", ",".join(cases)]
    for key, value in flags.items():
        if value is None:
            continue
        cmd += [f"-{key}", str(value)]
    proc = subprocess.run(
        cmd, cwd=GO_CORE, capture_output=True, text=True, env=dict(os.environ), timeout=PROBE_TIMEOUT
    )
    assert proc.returncode == 0, f"aiclientprobe 失败 rc={proc.returncode}\n{proc.stderr[:2000]}"
    got = {r["case"]: r for r in json.loads(proc.stdout)["results"]}
    assert set(got) == set(cases), f"probe 少了用例：{sorted(set(cases) - set(got))}"
    _RAN.update(got)
    return got


def _run(go_bin: str, mode: str, cases: List[str], **flags: Any) -> Dict[str, Dict[str, Any]]:
    proc, port = _start_server(mode)
    try:
        defaults: Dict[str, Any] = {"retries": -1, "breaker": -1}
        defaults.update(flags)
        return _probe(go_bin, port, cases, defaults)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()


@pytest.fixture(scope="module")
def go_bin() -> str:
    return _require_go()


# ── 成功面：字节真的对得上 ───────────────────────────────


def test_ok_plane_roundtrips(go_bin):
    got = _run(go_bin, "ok", ["embed_ok", "safety_ok_maps", "safety_crisis_maps"])
    embed = got["embed_ok"]
    assert embed["degraded"] is False and embed["err"] == ""
    assert embed["vectors"] == 2 and embed["dimensions"] == 3
    # 模型名由服务端按 provider 配置回带（proto:126 的排障用途），本仓不硬编码它的取值
    assert embed["model"], "服务端没回带 embedding 模型名"

    ok = got["safety_ok_maps"]
    assert ok["status"] == "ok" and ok["reason"] == "" and ok["err"] == ""
    assert ok["attempts"] == 1

    crisis = got["safety_crisis_maps"]
    assert crisis["status"] == "crisis"
    assert crisis["reason"] == "crisis"
    assert crisis["matched"] == ["不想活"]
    assert crisis["categories"] == ["self_harm"]
    assert crisis["adviceBlocked"] is False


def test_embed_zero_dimensions_is_permanent_reject(go_bin):
    """服务端把 dimensions=0 判成调用方编程错误 ⇒ Go 必须读成 permanent 且**一次都不重投**。

    这是跨语言才存在的一格：R2 单测里那个码是本包自己造的，这里的码来自对面 abort 的 trailers。
    """
    got = _run(go_bin, "ok", ["embed_zero_dims_rejected"])["embed_zero_dims_rejected"]
    assert got["degraded"] is True, "Embed 的失败面是 fail-soft，不许硬失败"
    assert got["kind"] == "permanent"
    assert got["failure"] == "rejected"
    assert got["attempts"] == 1, f"permanent 失败被重投了：attempts={got['attempts']}"


def test_unknown_scope_is_rejected_not_silently_chat(go_bin):
    got = _run(go_bin, "ok", ["safety_unknown_scope_rejected"])["safety_unknown_scope_rejected"]
    assert got["status"] == "unavailable", "判定失败必须读成 unavailable，不能读成 ok"
    assert got["kind"] == "permanent" and got["attempts"] == 1
    assert got["failure"] == "rejected"


def test_blank_text_still_crosses_the_wire(go_bin):
    """空文本的判定留在服务端（proto:138-139）：Go 不许本地短路。

    判据是 `attempts>=1`——真发生过一次 RPC，而不只是结果恰好是 ok。
    """
    got = _run(go_bin, "ok", ["safety_blank_maps_server_side"])["safety_blank_maps_server_side"]
    assert got["status"] == "ok"
    assert got["attempts"] >= 1, "Go 侧把空文本短路掉了 ⇒ 两处结论会分叉"


def test_stream_shape_over_real_transport(go_bin):
    got = _run(go_bin, "ok", ["stream_ok"])["stream_ok"]
    chunks = got["chunks"]
    assert [c["finish"] for c in chunks] == ["", "", "", "", "done"]
    assert chunks[0]["memories"] == 1 and chunks[0]["content"] == ""
    assert "".join(c["content"] for c in chunks[1:4]) == "今天过得怎样"
    assert chunks[-1]["content"] == ""
    # SSE 透传（R2 的映射）接上真字节之后事件名仍然是那三个，前端不用认识新事件
    joined = "".join(got["sse"])
    assert "memory_context" in joined and '"token"' in joined and "done" in joined


# ── 超时（D-8 的跨进程正身）──────────────────────────────


def test_safety_timeout_fails_closed(go_bin):
    """对面 2s 才回、预算 800ms ⇒ Go 必须给出 unavailable，而且**不能**是 ok。

    这条就是 D-8 在真 transport 上的形态：Python 侧 fail-open 的旧口径（check_text）
    跨进程后不许变成「拿不到判定就当安全」。
    """
    got = _run(go_bin, "slow", ["safety_slow_is_unavailable"])["safety_slow_is_unavailable"]
    assert got["status"] == "unavailable"
    assert got["failure"] == "deadline_exceeded"
    assert got["kind"] == "transient"
    assert got["err"], "超时必须同时给非 nil err"
    assert 500 <= got["elapsedMs"] < 2000, f"预算没按 safetyDeadline 收口：{got['elapsedMs']}ms"


def test_stream_before_first_token_times_out(go_bin):
    """首字之前断掉 ⇒ 两块降级面（模板 + DEGRADED），SSE 侧仍是 token+done。"""
    got = _run(go_bin, "slow", ["stream_slow_degraded"], streamDeadlineMs=400)["stream_slow_degraded"]
    chunks = got["chunks"]
    assert len(chunks) == 2, chunks
    assert chunks[0]["content"] and chunks[0]["finish"] == ""
    assert chunks[1]["finish"] == "degraded"
    assert chunks[1]["failure"] == "deadline_exceeded"


# ── 429 / 5xx：transient 的两条处置差异 ──────────────────


def test_embed_429_is_transient_and_retried(go_bin):
    """429 → RESOURCE_EXHAUSTED → transient → 按预算重投（默认 1+2 次）。

    这条测的是「Go 的默认重试次数没被跨进程悄悄砍掉」：`failure.go:60` 的处置差异全靠
    对面真的按 429 回这一个码。
    """
    got = _run(go_bin, "rate429", ["embed_429_retried"], retries=2)["embed_429_retried"]
    assert got["kind"] == "transient"
    assert got["failure"] == "rate_limited"
    assert got["attempts"] == 3, f"重投次数与 R2 定的预算不符：{got['attempts']}"
    assert got["degraded"] is True and got["vectors"] == 0


def test_safety_429_never_ok(go_bin):
    got = _run(go_bin, "rate429", ["safety_429_never_ok"])["safety_429_never_ok"]
    assert got["status"] == "unavailable"
    assert got["failure"] == "rate_limited"


def test_stream_429_midway_keeps_early_tokens(go_bin):
    """已经吐过内容之后断流 ⇒ 只报 ERROR 块，不追加模板句（半句话后面接文案是造新真相源）。"""
    got = _run(go_bin, "rate429", ["stream_429_midway"])["stream_429_midway"]
    chunks = got["chunks"]
    assert chunks[0]["content"] == "前半句", "半途失败前已下发的 token 被丢了"
    assert chunks[-1]["finish"] == "error"
    assert chunks[-1]["failure"] == "rate_limited"
    assert chunks[-1]["hasErrorCode"] is True and chunks[-1]["errorCode"] == 500
    assert all(c["finish"] != "degraded" for c in chunks)


def test_embed_5xx_is_transient_not_malformed(go_bin):
    """5xx → INTERNAL → transient（换一次可能就好）；畸形才是 permanent。"""
    got = _run(go_bin, "err500", ["embed_500_transient"], retries=2)["embed_500_transient"]
    assert got["kind"] == "transient"
    assert got["failure"] == "service_unavailable"
    assert got["attempts"] == 3


def test_breaker_opens_on_real_transients(go_bin):
    """连续对面故障要在**真 transport** 上打开熔断，并且打开那次根本不发 RPC。

    `Attempts==0` 是这条的正身：计数走的是对面真给的状态码，不是本包自己造的。
    """
    got = _run(go_bin, "err500", ["breaker_open_second_call"], breaker=2)["breaker_open_second_call"]
    assert got["failure"] == "breaker_open", got
    assert got["attempts"] == 0, f"熔断打开还是发出了请求：attempts={got['attempts']}"
    assert got["degraded"] is True
    assert "service_unavailable" in got["note"], f"第一次失败的码没落进 transient 面：{got['note']}"


# ── 畸形：对面发的字节过不了 Go 的 decode 校验 ───────────


def test_embed_count_mismatch_is_permanent_not_retried(go_bin):
    """条数≠请求条数 ⇒ malformed（permanent）：重试不会变好，且整批不能当有向量用。"""
    got = _run(go_bin, "malformed", ["embed_malformed_count"], retries=2)["embed_malformed_count"]
    assert got["kind"] == "permanent"
    assert got["failure"] == "malformed"
    assert got["attempts"] == 1, "畸形被重投了（V3 17 §8 的 Permanent 面）"
    assert got["degraded"] is True and got["vectors"] == 0


def test_unknown_safety_status_is_unavailable_not_ok(go_bin):
    """三态之外的枚举值不许按 proto3 的「容忍未知值」读成 OK。"""
    got = _run(go_bin, "malformed", ["safety_out_of_contract_status"])[
        "safety_out_of_contract_status"
    ]
    assert got["status"] == "unavailable"
    assert got["failure"] == "malformed"
    assert got["kind"] == "permanent"


def test_chunk_with_content_and_memories_is_malformed(go_bin):
    """一个块同时带 content 与 memories ⇒ SSE 侧无法定序 ⇒ 判畸形，且在首字前 → 降级两块。"""
    got = _run(go_bin, "malformed", ["stream_malformed_same_chunk"])["stream_malformed_same_chunk"]
    chunks = got["chunks"]
    assert len(chunks) == 2 and chunks[-1]["finish"] == "degraded"
    assert chunks[-1]["failure"] == "malformed"


def test_done_chunk_carrying_content_is_malformed(go_bin):
    """DONE 再带 content＝两个真相源（现网 done 事件不带载荷）；已经吐过内容 ⇒ 只报 ERROR。"""
    got = _run(go_bin, "malformed_done", ["stream_malformed_done"])["stream_malformed_done"]
    chunks = got["chunks"]
    assert chunks[0]["content"] == "半句"
    assert chunks[-1]["finish"] == "error"
    assert chunks[-1]["failure"] == "malformed"


def test_stream_rejects_overlong_content_server_side(go_bin):
    """4000 上限由服务端按 HTTP 同一个 Pydantic 模型判：Go 侧不重抄，所以这里只能看到拒绝。

    用户输入的原文**不许**出现在回给 Go 的 detail 里（`_redacted_validation_detail`），
    这一格在 Python 侧已钉（test_r3_rpc_server.py），这里钉跨进程后的可见面：
    Go 拿到的降级块里没有那段超长文本。
    """
    got = _run(go_bin, "ok", ["stream_overlong_rejected"])["stream_overlong_rejected"]
    chunks = got["chunks"]
    assert got["status"] == "degraded", got
    assert all("很" not in c["content"] for c in chunks), "detail 漏了用户输入"
    assert chunks[0]["failure"] == "rejected"


# ── 收尾：本文件的用例清单必须跑齐 ───────────────────────


def test_every_planned_case_actually_ran():
    """用例清单闸：`CASE_PLAN` 里每一条都必须在真进程上跑过（skip≠绿的最后一道）。

    漏跑通常发生在「加了一个 case 忘了接进某个 mode 的清单」——那种情况下 probe 的返回值
    仍然自洽，只有这张表会发现它没跑。
    """
    missing = sorted(set(CASE_PLAN) - set(_RAN))
    print(
        f"\n[r3-interop] 计划 {len(CASE_PLAN)} 条 / 实跑 {len(_RAN)} 条 / "
        f"故障面 {sorted(set(CASE_PLAN.values()))}",
        file=sys.stderr,
    )
    assert missing == [], f"这些互操作用例没跑过：{missing}"
