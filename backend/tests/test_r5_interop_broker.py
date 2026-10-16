"""R5-③ · 真 outbox × 真 broker × 真 Python 消费者 × 真 Go 入站 的跨进程互操作闸。

这一层补的是 R4/R5 各自单测的结构盲区：R4-② 证的是「真 broker 收得下我们这套拓扑参数」，
R5-② 证的是「Go 消费循环的次序与计数」（跑在 fake Domain 上），R5-① 证的是「Python 八步与
ACK/REJECT/REQUEUE 口径」（跑在假消息对象上）。三侧都绿，**不代表**它们接起来能跑：
这条链上有四处只有跨进程才会暴露的字节面——
header 名、`message_type` 的命名法（点↔下划线）、`schema_version` 的整数/字符串两形态、
候选 id 的派生规则（D-R5-5）。任何一处写错的后果都不是「报错」，而是**静默**：
对面读不懂就重投，或者更坏——inbox 唯一键永远撞不上，重复投递每次都生效。

两侧都用真实现，只有最底下的「AI 产出」是编排的（R5 的结案口径逐字是「管道通了，流量仍是零」，
四个域的候选生产归 R6）：
  · Go 侧 = `go-core/tools/r5interop`：真 `dispatch.Runner`＋真 `inbound.Runner`＋真
    `amqp.Session/Publisher`＋真 `store.Canonical`/`domain.Service`，外面套一层调用计数；
  · Python 侧 = `scripts/r5_interop_worker.py`：真 `backend.ai.mq.worker.serve` 起停，
    注册一个把命令体回声成 pattern 提案的 handler。

编排形态（生产取证定案）：`deploy/k8s` 里 `CLOSEN_MQ_WORKER`／`CLOSEN_OUTBOX_WORKER`／
`CLOSEN_CANDIDATE_WORKER` 实测 0 命中 ⇒ 生产没有「谁 spawn 谁」，三个 worker 各是独立进程。
所以这里**串行四步**（Go 发命令 → Python 消费并回投候选 → Go 入站收并 Admit → 重放同
message_id 证明幂等），不做并发 spawn 编排——并发编排要造的正是生产里不存在的那个东西。

跑法（本机 go 是混合安装，须点名；缺任一前置就 skip，并把少跑的条数写进 skip 理由）：

    export GOROOT=/Users/fengyu/sdk/go1.27.1 PATH=$GOROOT/bin:$PATH
    TEST_DATABASE_URL=postgresql://… CLOSEN_AMQP_URL=amqp://… \\
      .venv/bin/pytest backend/tests/test_r5_interop_broker.py -q

⚠️ skip≠绿：三个前置（go 工具链／真库／真 broker）任一缺失，本文件 `len(CASE_PLAN)` 条
一条都不跑。反过来，前置都在但某一跳没走通，一律 **fail**——那种形态线上是真的会坏。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Set

import pytest

from backend.ai.mq import contract as mq_contract
from backend.ai.mq import topology
from backend.ai.mq.contract import candidate_message_id
from backend.tests._pg_env import pg_test_url

ROOT = Path(__file__).resolve().parents[2]
GO_CORE = ROOT / "go-core"
WORKER = ROOT / "scripts" / "r5_interop_worker.py"
GO_PKG = "./tools/r5interop"
CALL_TIMEOUT = 300

#: 这张表就是本文件的用例清单；最后一条用例断言「跑过的 == 这张表」，少跑一条就红。
CASE_PLAN: Dict[str, str] = {
    "chain_outbox_to_canonical": "整条链的每一跳都真：outbox 行 published → Python 消费 → Go Admit 落表",
    "candidate_id_derivation": "候选 id 由**对面**的派生规则算出，两侧同值（D-R5-5）",
    "metric_surfaces_share_vocabulary": "两侧指标面的键名精确可比，且共享那一小撮同名词",
    "replay_is_absorbed_by_inbox": "同一条命令重放 ⇒ 第二次 duplicate 且领域入口一次都没被调",
    "bad_candidate_rejects_with_shared_reason": "永久拒绝的 reason 码在两侧词表里同名同值",
    "python_never_consumes_candidate_plane": "D-R5-10：候选面过一遍 broker，Python 零消费、零权威写入",
}

_RAN: Set[str] = set()

#: 指标面的键名清单（逐字对着 dispatch.Stats／inbound.Stats／WorkerStats.snapshot()）。
DISPATCH_KEYS = {"batches", "claimed", "accepted", "rejected", "dead_lettered", "lost",
                 "unrecorded", "dials", "redials", "claim_errors"}
INBOUND_KEYS = {"received", "processed", "duplicates", "rejected", "transient", "acks",
                "nacks", "settle_errors", "dials", "redials", "rejects_by_reason"}
PYTHON_KEYS = {"consumed", "produced", "acked", "requeued", "rejected", "parked", "redials",
               "rejects_by_reason"}

GO_DECODER = GO_CORE / "internal" / "messaging" / "inbound" / "candidate_decode.go"


# ── 前置与工具链 ─────────────────────────────────────────


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


def _amqp_url() -> str:
    raw = os.environ.get("CLOSEN_AMQP_URL", "").strip()
    if not raw:
        pytest.skip(
            f"CLOSEN_AMQP_URL 未设置 ⇒ 本文件的 {len(CASE_PLAN)} 条跨进程互操作用例"
            "**一条都没跑**，别把它当绿（真 broker 是这条链的地板，不是可选项）"
        )
    return raw


@pytest.fixture(scope="module")
def amqp_url() -> str:
    return _amqp_url()


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy import create_engine

    url = pg_test_url(purpose="R5 三方互操作，需 alembic upgrade head 后的隔离库")
    eng = create_engine(url)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def go_tool() -> str:
    """编译一次 `tools/r5interop` 备用；找不到/编不动 go ⇒ skip（理由里带少跑了几条）。

    ⚠️ skip 的判据只有「没有能编译的 go 工具链」这一种。编译过了但某一跳没走通，
    在 `_go()` 里一律 fail——那是线上真会坏的面。
    """
    out = Path(tempfile.mkdtemp(prefix="r5interop-")) / "r5interop"
    failures: List[str] = []
    for candidate in _go_candidates():
        try:
            proc = subprocess.run(
                [candidate, "build", "-o", str(out), GO_PKG],
                cwd=GO_CORE, capture_output=True, text=True,
                env=dict(os.environ), timeout=CALL_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append(f"{candidate}: {exc}")
            continue
        if proc.returncode == 0 and out.exists():
            return str(out)
        failures.append(f"{candidate}: rc={proc.returncode} {(proc.stderr or proc.stdout).strip()[:300]}")
    pytest.skip(
        f"没有能编译 {GO_PKG} 的 go 工具链 ⇒ 本文件 {len(CASE_PLAN)} 条一条都没跑，别把它当绿。\n"
        + "\n".join(failures)
    )


def _rows(eng, sql: str, params: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    from sqlalchemy import text

    with eng.connect() as conn:
        return [dict(row) for row in conn.execute(text(sql), params or {}).mappings()]


def _count(eng, table: str, where: str = "", **params: Any) -> int:
    sql = f"SELECT count(*) AS count FROM {table}" + (f" WHERE {where}" if where else "")
    return int(_rows(eng, sql, params)[0]["count"])


# ── broker 面：每条用例前把队列清空 ──────────────────────


def _purge(url: str) -> None:
    """清掉上一轮残留的投递，让「consumed==0」这类判据真的等于「什么都没发生」。

    为什么必须清而不只是「不断言聚合计数」：outbox 行是持久的，但四个命令队列与
    `go.candidate.accept` 也是持久的（durable）——上一轮没被消费干净的候选会漂到这一轮，
    于是「第二条投递是重复」可能其实是「第一条还没消费」。

    为什么连 `retry.30s.*` 也一起清：purpose 队列的 DLX 键写死成第一层（`topology.go:192-193`），
    那条消息在 30s 层到期后会被 broker **自己**投回 purpose 队列。只清 purpose 队列等于留了一
    颗定时炸弹：`test_python_never_consumes_candidate_plane` 那 8 秒空转窗口里，只要上一轮的
    瞬态失败消息到点，`consumed == 0` 就会因为一条不属于本轮的消息而红。
    `dlq.*` 不清：本文件没有任何判据读终态面，清了反而会把排查用的现场抹掉。
    """
    import aio_pika

    names: List[str] = list(topology.CONSUMED_QUEUES) + ["go.candidate.accept"]
    for queue in list(topology.CONSUMED_QUEUES) + ["go.candidate.accept"]:
        names += [topology.tier_routing_key(queue, tier)
                  for tier in range(len(topology.RETRY_SUFFIXES))]

    async def go() -> None:
        conn = await aio_pika.connect_robust(url)
        try:
            channel = await conn.channel()
            for name in names:
                try:
                    queue = await channel.declare_queue(name, passive=True)
                except aio_pika.exceptions.ChannelNotFoundEntity:
                    continue  # 首轮：拓扑还没被 Go 声明过，没有可清的东西
                await queue.purge()
        finally:
            await conn.close()

    asyncio.run(go())


# ── 进程面：Go 工具与 Python worker ──────────────────────


def _go(tool: str, mode: str, *, user: str = "", operation: str = "",
        candidate_id: str = "", payload: str = "", deadline: int = 60) -> Dict[str, Any]:
    cmd = [tool, "-mode", mode, "-deadline-seconds", str(deadline)]
    if user:
        cmd += ["-user", user]
    if operation:
        cmd += ["-operation", operation]
    if candidate_id:
        cmd += ["-candidate-id", candidate_id]
    if payload:
        cmd += ["-payload", payload]
    proc = subprocess.run(cmd, cwd=GO_CORE, capture_output=True, text=True,
                          env=dict(os.environ), timeout=CALL_TIMEOUT)
    line = next((ln for ln in reversed(proc.stdout.splitlines()) if ln.startswith("{")), "")
    assert proc.returncode == 0, (
        f"r5interop -mode {mode} rc={proc.returncode}\nstdout={proc.stdout[:800]}\n"
        f"stderr={proc.stderr[-3000:]}"
    )
    assert line, f"r5interop -mode {mode} 没输出 JSON 报告行：{proc.stdout[:400]}"
    report = json.loads(line)
    assert report.get("ok") is True, f"r5interop -mode {mode} 观测失败：{report.get('fail')}"
    return report


def _python_worker(url: str, expect: int, run_seconds: int = 20) -> Dict[str, Any]:
    env = {**os.environ, "CLOSEN_MQ_WORKER": "1", "CLOSEN_AMQP_URL": url,
           "R5_INTEROP_EXPECT_CONSUMED": str(expect), "R5_INTEROP_RUN_SECONDS": str(run_seconds)}
    proc = subprocess.run([sys.executable, str(WORKER)], cwd=ROOT, capture_output=True,
                          text=True, env=env, timeout=CALL_TIMEOUT)
    line = next((ln for ln in reversed(proc.stdout.splitlines()) if ln.startswith("{")), "")
    assert line, (
        f"r5_interop_worker 没输出 JSON 报告行 rc={proc.returncode}\n"
        f"stderr={proc.stderr[-3000:]}"
    )
    report = json.loads(line)
    assert report.get("ok") is True, (
        f"Python worker 没达成目标（expect={expect}）：{report}\nstderr={proc.stderr[-3000:]}"
    )
    return report


def _ids() -> tuple[str, str]:
    stamp = format(time.time_ns(), "x")
    return f"r5u-{stamp}"[:36], f"r5op-{stamp}"


def _build_chain(tool: str, url: str, eng, user: str, operation: str) -> Dict[str, Any]:
    """串行四步的前三步：真 outbox 行 → 真 broker → 真 Python 消费 → 真 Go 入站 Admit。"""
    _purge(url)
    pub = _go(tool, "publish", user=user, operation=operation)
    worker_report = _python_worker(url, expect=1)
    cands = worker_report["candidates"]
    assert len(cands) == 1, f"这一轮只投了一条命令，候选面却有 {len(cands)} 条：{cands}"
    cid = cands[0]["candidate_message_id"]
    con = _go(tool, "consume", user=user, candidate_id=cid)
    return {"publish": pub, "worker": worker_report, "consume": con,
            "candidate_id": cid, "user": user, "operation": operation}


def _settled_for(report: Dict[str, Any], message_id: str) -> List[Dict[str, Any]]:
    return [s for s in report["settled"] if s["message_id"] == message_id]


@pytest.fixture(scope="module")
def chain(amqp_url: str, engine, go_tool: str):
    user, operation = _ids()
    got: Dict[str, Any] = {"candidate_id": ""}
    try:
        got = _build_chain(go_tool, amqp_url, engine, user, operation)
        yield got
    finally:
        # cleanup 放进 finally：本文件任一用例在中途红掉时，真库里的行不会漂到下一轮
        # （`outbox_messages.message_id` 是 UNIQUE 的，残留的 op 行会让下一次 publish 直接撞键）。
        _go(go_tool, "cleanup", user=user, operation=operation,
            candidate_id=got.get("candidate_id", ""))


# ── ① 整条链：每一跳都是真的 ─────────────────────────────


def test_chain_outbox_to_canonical(chain, engine):
    """命令从真 outbox 行出发，落到真 canonical 表——中间每一跳都由 SQL/对面进程回答。"""
    _RAN.add("chain_outbox_to_canonical")
    pub, worker_report, con = chain["publish"], chain["worker"], chain["consume"]
    cid, user = chain["candidate_id"], chain["user"]

    # 第 1 跳：outbox 模式的核心判据——**broker 确认之后**行才 published（18 §6）。
    assert pub["outbox_status"] == "published"
    assert pub["dispatch_stats"]["accepted"] >= 1
    assert len(pub["evidence_ids"]) == 2

    # 同一行的**独立**取证：下面四行都是本文件直接读库，不经过那个 Go 工具。
    # 为什么需要：`pub["outbox_status"]` 是工具读库再报回来的自述，只信它等于让
    # 「工具说 published」给自己作证。⚠️ 但别把上一行读成「它能打掉工具的假话」：
    # 健康链上库里本来就是 published，工具把那格写成常量时两侧仍同值 ⇒ 打不红。
    # 这一族真正独立咬得住的是 routing_key／message_type／taskType 那三行
    # （命令面漂了而工具不报），以及本文件后面那几条；变异自证的实测计数见
    # `scripts/mutate_r5_gates.py` 的 M14／M15（那两条只有这一族跨进程闸看得见）。
    command_row = _rows(
        engine,
        "SELECT status, routing_key, message_type, payload FROM outbox_messages "
        "WHERE message_id = :op",
        {"op": chain["operation"],},
    )
    assert len(command_row) == 1, command_row
    assert command_row[0]["status"] == pub["outbox_status"], command_row
    assert command_row[0]["routing_key"] == "ai.pattern.requested", command_row
    assert command_row[0]["message_type"] == "ai_pattern_requested", command_row
    assert json.loads(command_row[0]["payload"])["taskType"] == "TASK_TYPE_PATTERN_DETECT"

    # 第 2 跳：Python 真消费到、真产出了候选（acked 在候选确认之后）。
    stats = worker_report["stats"]
    assert stats["consumed"] >= 1 and stats["produced"] >= 1, stats
    assert stats["acked"] >= 1 and stats["rejected"] == 0, stats

    # 第 3 跳：Go 入站真收、真走了领域入口。
    settled = _settled_for(con, cid)
    assert len(settled) == 1, f"这条候选在 Go 侧结算了 {len(settled)} 次：{con['settled']}"
    assert settled[0]["status"] == "processed", settled[0]
    assert settled[0]["ack"] is True and settled[0]["admits"] == 1, settled[0]
    assert con["admissions"] == {"Admit": 1}, f"进的不是 Admit 那扇门：{con['admissions']}"

    # 第 4 跳：权威表真的落了行，且落的是**对面**写来的内容（不是本地夹具）。
    inbox = _rows(
        engine,
        "SELECT status, message_type, consumer FROM inbox_messages "
        "WHERE message_id = :cid",
        {"cid": cid},
    )
    assert len(inbox) == 1 and inbox[0]["status"] == "processed"
    assert inbox[0]["message_type"] == "candidate_pattern"
    assert inbox[0]["consumer"] == "go.core.candidate"

    patterns = _rows(
        engine,
        "SELECT pattern_type, scope, description, fingerprint, generation_run_id, "
        "status, reject_reason FROM patterns WHERE user_id = :u",
        {"u": user},
    )
    assert len(patterns) == 1, f"候选没进权威表或进了多条：{patterns}"
    row = patterns[0]
    assert row["status"] == "candidate" and row["reject_reason"] is None, row
    assert row["pattern_type"] == "temporal_rhythm" and row["scope"] == "sleep", row
    # 这两格是「对面写的字真的进了我们的库」的唯一凭据：值来自 Python 进程，不是本文件的夹具。
    assert row["fingerprint"] == worker_report["candidates"][0]["fingerprint"], row
    assert row["generation_run_id"] == worker_report["candidates"][0]["generation_run_id"], row
    assert row["description"]  # Gate 第 1 关：pattern 的 description 必填

    assert _count(engine, "pattern_evidence", "user_id = :u", u=user) == 2, "证据回链没落全"
    assert _count(engine, "personal_model_events", "user_id = :u", u=user) >= 1

    # 因果链跨了三个进程还连得上：这条候选引起的投递行，causation 指回候选自己（N7-6）。
    events = _rows(
        engine,
        "SELECT message_type, causation_id FROM outbox_messages "
        "WHERE user_id = :u AND message_id <> :op",
        {"u": user, "op": chain["operation"]},
    )
    assert events, "领域入口写了事件行却没写出投递行"
    assert {e["causation_id"] for e in events} == {cid}, events


# ── ② 候选 id 的派生规则两侧同值 ─────────────────────────


def test_candidate_id_derivation(chain):
    """D-R5-5：候选 id 由**命令的** message_id 确定性派生，且这条规则只有一份实现。

    为什么单独立一条：Go 的入站工具**不许**自己再算一遍（`tools/r5interop` 的 `-candidate-id`
    由对面传进来），否则本文件测的就变成「两套实现恰好一致」，而生产里漂了也照样绿。
    """
    _RAN.add("candidate_id_derivation")
    operation = chain["operation"]
    digest = hashlib.sha256(operation.encode("utf-8")).hexdigest()
    assert chain["candidate_id"] == f"cand-{digest[:40]}"
    assert chain["candidate_id"] == candidate_message_id(operation)
    # 对面算出来的那一条，Go 真的按这个 id 结算了（不是「传进去但没对上」）。
    assert _settled_for(chain["consume"], chain["candidate_id"]), chain["consume"]["settled"]
    # Python 报告的 id 与 Go 收到并落账的 id 是同一个串。
    assert chain["worker"]["candidates"][0]["candidate_message_id"] == chain["candidate_id"]


# ── ③ 指标面同形 ─────────────────────────────────────────


def test_metric_surfaces_share_vocabulary(chain):
    """两侧指标面的键名精确可比：各自一套命名可以，但**同名的那几格必须是同一件事**。

    这是 R5-④（每轮一条日志＋计数面）在跨进程下的地板。判据分三层：
    键集精确（改名会被抓到）→ 共享词表非空（不许靠「都没出现过」蒙绿）→
    值面在这一轮自洽（Go 收了 1 条就处理 1 条，Python 消费 1 条就产出 1 条）。
    """
    _RAN.add("metric_surfaces_share_vocabulary")
    dispatch_keys = set(chain["publish"]["dispatch_stats"])
    inbound_keys = set(chain["consume"]["inbound_stats"])
    python_keys = set(chain["worker"]["stats"])

    assert dispatch_keys == DISPATCH_KEYS, dispatch_keys ^ DISPATCH_KEYS
    assert inbound_keys == INBOUND_KEYS, inbound_keys ^ INBOUND_KEYS
    assert python_keys == PYTHON_KEYS, python_keys ^ PYTHON_KEYS

    shared = dispatch_keys & inbound_keys & python_keys
    # 三对面各自相等会太松（改名成同一批词也能过），各留一行精确判据：
    assert dispatch_keys & inbound_keys == {"rejected", "dials", "redials"}, (
        dispatch_keys & inbound_keys)
    assert inbound_keys & python_keys == {"rejected", "redials", "rejects_by_reason"}, (
        inbound_keys & python_keys)
    assert shared == {"rejected", "redials"}, shared
    # 同名词必须在两侧都真的存在过、类型也一致，否则「同形」是空话。
    for key in sorted(shared):
        for view in (chain["publish"]["dispatch_stats"], chain["consume"]["inbound_stats"],
                     chain["worker"]["stats"]):
            assert isinstance(view[key], int), (key, view[key])
    for view in (chain["consume"]["inbound_stats"], chain["worker"]["stats"]):
        assert isinstance(view["rejects_by_reason"], dict), view

    assert chain["publish"]["dispatch_stats"]["claimed"] >= 1
    assert chain["consume"]["inbound_stats"]["processed"] >= 1
    assert chain["worker"]["stats"]["consumed"] == chain["worker"]["stats"]["produced"]


# ── ④ 重放不第二次生效 ───────────────────────────────────


def test_replay_is_absorbed_by_inbox(amqp_url, engine, go_tool):
    """同一条命令再走一遍全链：候选 id 不变 ⇒ Go 判 duplicate ⇒ 领域入口一次都不被调用。

    这条是 R5 验收里唯一「fake 证不了」的一格：R5-② 用 fake Domain 数过调用次数，但真
    `ConsumeIdempotent` 的 inbox 认领位是不是真的拦住了第二次 `Admit`，只能在真库＋真
    broker＋真对面身上判。`admits` 挂在 message_id 上（不是聚合计数）就是为这一条留的。
    """
    _RAN.add("replay_is_absorbed_by_inbox")
    user, operation = _ids()
    # 先绑定再进 try：中途红掉时 finally 里的 cleanup 不能因为变量没赋值而抛第二个错，
    # 把真正那条失败信息盖掉。
    cid = ""
    try:
        first = _build_chain(go_tool, amqp_url, engine, user, operation)
        cid = first["candidate_id"]
        patterns_after_first = _count(engine, "patterns", "user_id = :u", u=user)
        events_after_first = _count(engine, "personal_model_events", "user_id = :u", u=user)
        assert patterns_after_first == 1, first

        _purge(amqp_url)
        replayed = _go(go_tool, "replay", operation=operation)
        assert replayed["outbox_status"] == "replayed" and replayed["body_bytes"] > 0

        again = _python_worker(amqp_url, expect=1)
        assert again["candidates"][0]["candidate_message_id"] == cid, (
            "重放同一条命令却换了候选 id ⇒ inbox 唯一键永远撞不上（D-R5-5 被打破）"
        )

        second = _go(go_tool, "consume", user=user, candidate_id=cid)
        settled = _settled_for(second, cid)
        assert len(settled) == 1, second["settled"]
        assert settled[0]["status"] == "duplicate", settled[0]
        assert settled[0]["ack"] is True, "重复也必须 ACK：不 ACK 就是让毒消息上阶梯"
        assert settled[0]["admits"] == 0, f"第二次投递又调用了领域入口：{settled[0]}"
        assert second["admissions"] == {}, f"整轮都没该有领域调用：{second['admissions']}"
        assert second["inbound_stats"]["duplicates"] >= 1
        assert second["inbound_stats"]["processed"] == 0

        assert _count(engine, "inbox_messages", "message_id = :c", c=cid) == 1
        assert _count(engine, "patterns", "user_id = :u", u=user) == patterns_after_first
        assert _count(engine, "personal_model_events", "user_id = :u",
                      u=user) == events_after_first
    finally:
        _go(go_tool, "cleanup", user=user, operation=operation, candidate_id=cid)


# ── ⑤ 永久拒绝的 reason 码两侧同值 ───────────────────────


def test_bad_candidate_rejects_with_shared_reason(amqp_url, engine, go_tool):
    """契约级非法的候选在 Go 侧落成 permanent_rejected，且 reason 串在两侧词表里同名同值。

    为什么不是「看它空不空」：`rejects_by_reason` 在成功链上必然为空，那条闸会蒙绿。
    这里真的把一条坏候选投过 broker，让那个码**出现在两边都能读到的那一格**里。
    """
    _RAN.add("bad_candidate_rejects_with_shared_reason")
    user, operation = _ids()
    bad_id = "r5-bad-cand-" + operation
    body = json.dumps({
        "schemaVersion": "1",
        "kind": "KIND_FACT",  # 键是 candidate.pattern ⇒ 体内自报的 kind 与键不同义
        "patternType": "temporal_rhythm",
        "description": "r5 interop 故意写坏的候选：kind 与 routing key 不同义",
        "fingerprint": "r5-bad-kind",
        "evidenceIds": [],
        "safety": {"status": "SAFETY_STATUS_OK"},
    })
    _purge(amqp_url)
    try:
        sent = _go(go_tool, "candidate", user=user, candidate_id=bad_id, payload=body)
        assert sent["candidate_message_id"] == bad_id
        got = _go(go_tool, "consume", user=user, candidate_id=bad_id)
        settled = _settled_for(got, bad_id)
        assert len(settled) == 1, got["settled"]
        assert settled[0]["status"] == "permanent_rejected", settled[0]
        assert settled[0]["admits"] == 0, "被拒的候选不该进任何领域入口"
        assert got["inbound_stats"]["rejected"] >= 1
        # 码面从指标格上读：detail 的写法不作为判据（两侧日志格式本就不必相同），
        # 但 x-reject-reason 那一格必须**只有**这一个码——多一个就说明拒收走了别的道。
        assert list(got["inbound_stats"]["rejects_by_reason"]) == ["candidate_kind_mismatch"], (
            got["inbound_stats"]
        )

        # 同一个串必须也在 Python 的词表里：两侧对着一条 x-reject-reason 排查是同一条纪律。
        python_vocab = {v for k, v in vars(mq_contract).items() if k.startswith("REASON_")}
        assert "candidate_kind_mismatch" in python_vocab

        go_src = GO_DECODER.read_text(encoding="utf-8")
        go_vocab = set(re.findall(r'Reason\w+\s*=\s*"([a-z_]+)"', go_src))
        assert "candidate_kind_mismatch" in go_vocab

        # 永久拒绝要留痕（14 §7 第 3 项），而且不许写进权威表。
        inbox = _rows(engine, "SELECT status FROM inbox_messages WHERE message_id = :c",
                      {"c": bad_id})
        assert len(inbox) == 1 and inbox[0]["status"] == "failed", inbox
        assert _count(engine, "patterns", "user_id = :u", u=user) == 0
    finally:
        _go(go_tool, "cleanup", user=user, operation=operation, candidate_id=bad_id)


# ── ⑥ 反向不变性（D-R5-10）───────────────────────────────


def test_python_never_consumes_candidate_plane(amqp_url, engine, go_tool):
    """候选面在 broker 上过了一遍：Python 零消费、零产出，权威表零写入。

    这条测的是「谁**不该**碰这条面」。`CONSUMED_QUEUES` 只有四个命令队列，Python 也不许
    写 canonical（`test_ai_boundary.py` 那条禁令）；今天的不变性靠代码形状，
    这一条把它变成运行时证据——真消息真从 `closenai.ai.candidates` 上过了一遍。
    """
    _RAN.add("python_never_consumes_candidate_plane")
    user, operation = _ids()
    body = json.dumps({
        "schemaVersion": "1", "kind": "KIND_PATTERN",
        "patternType": "temporal_rhythm", "scope": "sleep",
        "description": "过去两周的记录里，出现过两次晚间处理事情之后入睡变慢的共现",
        "fingerprint": "r5-reverse-" + operation, "confidence": 0.8,
        "detectorVersion": "r5-interop-1", "evidenceIds": [],
        "generationRunId": "gir-" + operation,
        "safety": {"status": "SAFETY_STATUS_OK"},
    })
    cid = "r5-reverse-cand-" + operation
    _purge(amqp_url)
    proc = subprocess.Popen(
        [sys.executable, str(WORKER)],
        env={**os.environ, "CLOSEN_MQ_WORKER": "1", "CLOSEN_AMQP_URL": amqp_url,
             "R5_INTEROP_EXPECT_CONSUMED": "0", "R5_INTEROP_RUN_SECONDS": "8"},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=ROOT,
    )
    out, err = "", ""
    try:
        sent = _go(go_tool, "candidate", user=user, candidate_id=cid, payload=body)
        assert sent["candidate_message_id"] == cid and sent["body_bytes"] > 0
        # 等满整个空转窗口（不是「看到第一条就停」）：反向不变性的证据是**这段时间里
        # 一个字节都没动过**，提前停就等于只证明了「暂时还没消费到」。
        out, err = proc.communicate(timeout=CALL_TIMEOUT)
    finally:
        if proc.poll() is None:
            proc.kill()
        # 收尾必须把这一条清掉：本用例**故意**让候选停在 `go.candidate.accept` 里
        # （「没人消费这条面」就是现场本身），但 R4-② 那条真 broker 测要按队列空不空来判
        # 「无绑定的事件不该出现在任何队列」。留一条不属于它的消息，就是把别人的现场当证据。
        _purge(amqp_url)
        _go(go_tool, "cleanup", user=user, operation=operation, candidate_id=cid)

    line = next((ln for ln in reversed(out.splitlines()) if ln.startswith("{")), "")
    assert line, f"Python worker 没回报告行：{err[-2000:]}"
    report = json.loads(line)
    stats = report["stats"]
    assert report["ok"] is True, f"{report} stderr={err[-2000:]}"
    assert stats["consumed"] == 0 and stats["produced"] == 0, stats
    assert stats["acked"] == 0 and stats["rejected"] == 0, stats

    # 零写入按**这张面**的三个落点逐条数：候选面只有 Go 的入站 worker 订阅。
    assert _count(engine, "inbox_messages", "message_id = :c", c=cid) == 0
    assert _count(engine, "patterns", "user_id = :u", u=user) == 0
    assert _count(engine, "outbox_messages", "user_id = :u", u=user) == 0


# ── 清单闸：少跑一条就红 ─────────────────────────────────


def test_case_plan_fully_ran():
    missing = sorted(set(CASE_PLAN) - _RAN)
    extra = sorted(_RAN - set(CASE_PLAN))
    assert not missing, f"这些跨进程用例一条都没跑到：{missing}（别把它当绿）"
    assert not extra, f"跑了清单外的用例，请把 CASE_PLAN 一起补上：{extra}"
