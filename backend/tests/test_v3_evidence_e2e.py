"""T2-04 · 证据链跨进程 E2E（真 broker ＋ 真库 ＋ 真 Go 入站）。

工单 §3 那条验收逐字是：
`create memory` → publish command → Python handler（fake LLM）→ candidate → Go inbound
→ 断言 `source_records=1, evidence=1, evidence.excerpt ⊆ memories.content`。

**实测口径是 `evidence=2` 而不是 1**（台账结案时同步）：一条命令只产一条候选时，
`source_records=1` 既可能来自「两条候选复用同一指针」也可能来自「压根只有一条」，
两种形状在库里长得一模一样，那条幂等指针判据就成了空话。所以夹具让假模型答**三条**：
两条摘录逐字取自原文、第三条原文里根本没有——它必须被 `parse_items` 的子串闸咽在
Python 进程内，出网的是 2 条，而这 2 条复用同一个来源指针 ⇒ `source_records=1`
这才是一个真的证人（闸 G-04 的主闸与 D-2 的薄指针同一轮各证一次）。

与 R5 那条闸（`test_r5_interop_broker.py`）的分工：R5 证「管道通了但流量是零」，
注册的是回声 handler；本文件注册的是**生产组合根**
`backend.services.ai_observation.build_registry()`——真读一行 `memories`、真跑四态安全
判定、真按子串闸丢条、真写 `ai_generation_runs` 留痕。只有最底下的模型回答是编排的
（换 `backend/ai/llm` 的 client 缓存，不换 `llm.complete`，否则留痕链就断了）。

跑法（本机 go 是混合安装，须点名；缺任一前置就 skip，并把少跑的条数写进 skip 理由）：

    export GOROOT=/Users/fengyu/sdk/go1.27.1 PATH=$GOROOT/bin:$PATH
    TEST_DATABASE_URL=postgresql://… CLOSEN_AMQP_URL=amqp://… DATABASE_URL=postgresql+psycopg://… \\
      .venv/bin/pytest backend/tests/test_v3_evidence_e2e.py -q

⚠️ skip≠绿：三个前置（go 工具链／真库／真 broker）任一缺失，本文件 `len(CASE_PLAN)` 条
一条都不跑。反过来，前置都在但某一跳没走通，一律 **fail**——那种形态线上真的会坏。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Set

import pytest

from backend.ai.mq import contract, topology
from backend.tests._pg_env import pg_test_url

ROOT = Path(__file__).resolve().parents[2]
GO_CORE = ROOT / "go-core"
DRIVER = ROOT / "scripts" / "t204_interop_worker.py"
GO_PKG = "./tools/r5interop"
CALL_TIMEOUT = 300

#: 本文件的用例清单；最后一条断言「跑过的 == 这张表」，少跑一条就红。
CASE_PLAN: Dict[str, str] = {
    "chain_memory_to_evidence": "原文行→命令→候选→Go 入站→权威表，每一跳都由库或对面进程回答",
    "fabricated_excerpt_never_leaves_process": "闸 G-04 主闸：编造的摘录出不了 Python 进程，更进不了 evidence",
    "generation_lineage_crosses_processes": "留痕 id 与 promptVersion/modelId 从候选一路进权威表",
    "metric_line_crosses_process_boundary": "evidence_extracted_total{result=ok} 这条命令只打一次",
    "replay_hit_cost_gate": "同一行 24h 内重投⇒already_extracted，零候选、零新证据、零新模型调用",
    "source_deleted_propagates_cross_process": "删除事件出 Python 进程→真 broker→真 Go 入站→翻登记行并撤掉仅依赖它的结论",
}

_RAN: Set[str] = set()

#: 假模型答几条、出网几条、落库几条——三个数各管一件事，不许混成一个「2」。
ANSWERED_ITEMS = 3
PRODUCED_CANDIDATES = 2
ADMITTED_EVIDENCE = 2

#: 假模型里那条**原文根本没有**的摘录（与驱动脚本逐字同串，见其 FABRICATED_EXCERPT）。
FABRICATED_EXCERPT = "我每天清晨五点起床跑步"

#: 提示词版本（对面 `observation.py:PROMPT_VERSION`）。这里写**字面量**而不是导入常量：
#: 判据要能抓到「Python 自己改了版本号却没人复核」，导入自己等于让这一格永远真。
EXPECTED_PROMPT_VERSION = "obs-v2"
EXTRACTION_PURPOSE = "services.ai_observation.extract"


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
            f"CLOSEN_AMQP_URL 未设置 ⇒ 本文件的 {len(CASE_PLAN)} 条跨进程用例"
            "**一条都没跑**，别把它当绿（真 broker 是这条链的地板，不是可选项）"
        )
    return raw


@pytest.fixture(scope="module")
def amqp_url() -> str:
    return _amqp_url()


def _backend_database_url() -> str:
    """Python 驱动那一侧的库串（`backend/config.py:DATABASE_URL`）。

    为什么单独立一条而不是让它自己去报连接错：缺这一格时驱动**照样能跑完**——
    `load_source` 读不到就 `source_missing`、`already_extracted` 查不到就当没提取过，
    于是 produced=0、rc=1，测试红得像「链断了」。真相是本机少导一个 env。
    """
    raw = os.environ.get("DATABASE_URL", "").strip()
    if not raw:
        pytest.skip(
            f"DATABASE_URL 未设置 ⇒ 本文件的 {len(CASE_PLAN)} 条跨进程用例**一条都没跑**，"
            "别把它当绿（TEST_DATABASE_URL 只喂 pytest 与 Go，驱动进程读的是 DATABASE_URL）"
        )
    return raw


@pytest.fixture(scope="module")
def backend_db() -> str:
    return _backend_database_url()


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy import create_engine

    eng = create_engine(pg_test_url(purpose="T2-04 证据链跨进程 E2E，需 alembic upgrade head 后的隔离库"))
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def go_tool() -> str:
    """编译一次 `tools/r5interop` 备用；找不到/编不动 go ⇒ skip（理由里带少跑了几条）。"""
    out = Path(tempfile.mkdtemp(prefix="t204interop-")) / "r5interop"
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
        failures.append(
            f"{candidate}: rc={proc.returncode} {(proc.stderr or proc.stdout).strip()[:300]}"
        )
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


def _exec(eng, sql: str, params: Dict[str, Any] | None = None) -> int:
    """写一条并**当场提交**：`_rows` 那个连接从不 commit，用它做 UPDATE 会静默回滚。

    只有 `memories` 这一张表走这里——它是 legacy 表，写面在 Python
    （`backend/services/memory_service.py`），Go 侧没有对应的 Store 方法。
    canonical 那几张表的夹具一律由 `-mode seedfact` 走 `domain.Store` 种（见那边注释）。
    """
    from sqlalchemy import text

    with eng.connect() as conn:
        result = conn.execute(text(sql), params or {})
        conn.commit()
        return int(result.rowcount or 0)


# ── broker 面：每一轮之前把队列清空 ──────────────────────


def _purge(url: str) -> None:
    """清掉上一轮残留的投递，让「produced==0」这类判据真的等于「这一轮什么都没发生」。

    与 R5 同一条理由，而且本文件更吃这一格：手工冒烟时实测到队列里漂着两条
    `source_missing` 的旧命令，驱动进程一轮 `consumed=3`——不清的话
    `replay_hit_cost_gate` 那条「produced==0」会被上一轮的残留读成「真的没产出」。
    `dlq.*` 不清：本文件没有判据读终态面，清了反而抹掉排查现场。

    ⚠️ `go.source.deleted` 也在清单里（T2-06 那条事件面）：它是**另一条**队列，
    上一轮漂着的一条删除事件会让这一轮的 `settled` 计数与 `admissions` 都对不上，
    而那条用例的判据形状（「这一条投递引起了一次传播」）读的正是单条投递的计数。
    """
    import aio_pika

    purposes = list(topology.CONSUMED_QUEUES) + [
        "go.candidate.accept",
        contract.SOURCE_DELETED_QUEUE,
    ]
    names: List[str] = list(purposes)
    for queue in purposes:
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
                    continue
                await queue.purge()
        finally:
            await conn.close()

    asyncio.run(go())


# ── 进程面：Go 工具与 Python 驱动 ────────────────────────


def _go(tool: str, mode: str, *, user: str = "", operation: str = "",
        candidate_id: str = "", payload: str = "", expect_settled: int = 1,
        source_event_id: str = "", deadline: int = 60) -> Dict[str, Any]:
    cmd = [tool, "-mode", mode, "-deadline-seconds", str(deadline)]
    if user:
        cmd += ["-user", user]
    if operation:
        cmd += ["-operation", operation]
    if candidate_id:
        cmd += ["-candidate-id", candidate_id]
    if source_event_id:
        cmd += ["-source-event-id", source_event_id]
    if payload:
        cmd += ["-payload", payload]
    if expect_settled != 1:
        cmd += ["-expect-settled", str(expect_settled)]
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


def _driver(url: str, *, expect_consumed: int, expect_produced: int,
            run_seconds: int = 45) -> Dict[str, Any]:
    """起真 worker 进程（生产组合根＋假 client），等它自己到点收工。

    驱动脚本的退出码就是判据：`rc!=0` 意味着观察窗口到点仍没达成目标，
    这里必须 fail 而不是 skip——「没等到」和「等到了但形状不对」是同一件红事。
    """
    env = {**os.environ, "CLOSEN_MQ_WORKER": "1", "CLOSEN_AMQP_URL": url,
           "DATABASE_URL": _backend_database_url(),
           "T204_EXPECT_CONSUMED": str(expect_consumed),
           "T204_EXPECT_PRODUCED": str(expect_produced),
           "T204_RUN_SECONDS": str(run_seconds)}
    proc = subprocess.run([sys.executable, str(DRIVER)], cwd=ROOT, capture_output=True,
                          text=True, env=env, timeout=CALL_TIMEOUT)
    line = next((ln for ln in reversed(proc.stdout.splitlines()) if ln.startswith("{")), "")
    assert line, f"t204_interop_worker 没输出 JSON 报告行 rc={proc.returncode}\nstderr={proc.stderr[-3000:]}"
    report = json.loads(line)
    assert report.get("ok") is True, (
        f"Python 驱动没达成目标（expect={expect_consumed}/{expect_produced}）："
        f"{report}\nstderr={proc.stderr[-3000:]}"
    )
    return report


def _ids() -> tuple[str, str]:
    stamp = format(time.time_ns(), "x")
    return f"t204u-{stamp}"[:36], f"t204op-{stamp}"


def _build_chain(tool: str, url: str, user: str, operation: str) -> Dict[str, Any]:
    """串行四步：Go 种一行 memories 并发命令 → 真 Python 抽取 → 真 Go 入站落表。"""
    _purge(url)
    pub = _go(tool, "observe", user=user, operation=operation)
    driver = _driver(url, expect_consumed=1, expect_produced=PRODUCED_CANDIDATES)
    candidates = driver["candidates"]
    assert len(candidates) == PRODUCED_CANDIDATES, (
        f"这一轮只发了一条命令，候选面却有 {len(candidates)} 条：{candidates}"
    )
    ids = [c["candidate_message_id"] for c in candidates]
    con = _go(tool, "consume", user=user, expect_settled=ADMITTED_EVIDENCE)
    return {"publish": pub, "driver": driver, "consume": con,
            "candidate_ids": ids, "user": user, "operation": operation}


@pytest.fixture(scope="module")
def chain(amqp_url: str, backend_db: str, engine, go_tool: str):
    user, operation = _ids()
    got: Dict[str, Any] = {"candidate_ids": [], "source_event_id": ""}
    try:
        got = _build_chain(go_tool, amqp_url, user, operation)
        yield got
    finally:
        # cleanup 放进 finally：中途红掉时真库里的行不会漂到下一轮
        # （`outbox_messages.message_id` 与 `inbox_messages` 的唯一键都会让下一轮直接撞键）。
        # `source_event_id` 由第⑥条用例写进这个 dict（同一条派生规则，跑过才有）：
        # 没跑过时它是空的，那一格本来也没写进 inbox，删不删都一样。
        _go(go_tool, "cleanup", user=user, operation=operation,
            candidate_id=",".join(got["candidate_ids"]),
            source_event_id=got.get("source_event_id", ""))


def _settled_for(report: Dict[str, Any], message_id: str) -> List[Dict[str, Any]]:
    return [s for s in report["settled"] if s["message_id"] == message_id]


# ── ① 整条链：从一行原文到两张权威表 ─────────────────────


def test_chain_memory_to_evidence(chain, engine):
    """`memories` 一行原文出发，命令→候选→Go 入站→`source_records`/`evidence` 落表。"""
    _RAN.add("chain_memory_to_evidence")
    pub, driver, con = chain["publish"], chain["driver"], chain["consume"]
    user, operation = chain["user"], chain["operation"]

    # 第 1 跳：outbox 模式的核心判据——**broker 确认之后**行才 published（18 §6）。
    assert pub["outbox_status"] == "published"
    assert pub["dispatch_stats"]["accepted"] >= 1

    # 同一行的独立取证：本文件直接读库，不经过那个 Go 工具（工具自述不许给自己作证）。
    command_row = _rows(
        engine,
        "SELECT status, routing_key, message_type, payload FROM outbox_messages "
        "WHERE message_id = :op",
        {"op": operation},
    )
    assert len(command_row) == 1, command_row
    assert command_row[0]["status"] == pub["outbox_status"], command_row
    assert command_row[0]["routing_key"] == "ai.observation.requested", command_row
    assert command_row[0]["message_type"] == "ai_observation_requested", command_row
    body = json.loads(command_row[0]["payload"])
    assert body["taskType"] == "TASK_TYPE_OBSERVATION_EXTRACT", body
    assert body["sourceKind"] == "fact", body
    # 命令体的来源指针必须逐字指向本轮种下的那一行；`sourceMessageId` 与体外的
    # `sourceRef` 同值＝T2-01 那条「体里两根指针不许各说各话」的判据在真链上的形状。
    assert body["sourceRef"] == f"memories:{pub['memory_id']}", body
    assert body["sourceMessageId"] == pub["memory_id"], body

    # 第 1.5 跳：输入面本身成立——那一行原文真的在库里，且假模型引用的就是它。
    memory = _rows(engine, "SELECT content, user_id, deleted FROM memories WHERE id = :m",
                   {"m": pub["memory_id"]})
    assert len(memory) == 1 and memory[0]["user_id"] == user, memory
    assert memory[0]["deleted"] is False, memory
    content = memory[0]["content"]
    # 夹具的自证：原文确实不含那条编造摘录，否则「它没出进程」这一格什么也没证。
    assert FABRICATED_EXCERPT not in content, content

    # 第 2 跳：真 Python 消费到、真按子串闸咽掉一条、真只出网 2 条。
    stats = driver["stats"]
    assert stats["consumed"] >= 1 and stats["produced"] == PRODUCED_CANDIDATES, stats
    assert stats["acked"] >= 1 and stats["rejected"] == 0, stats
    assert driver["answered_items"] == [ANSWERED_ITEMS], (
        f"假模型这一轮没答够 {ANSWERED_ITEMS} 条 ⇒ 子串闸根本没有靶：{driver['answered_items']}"
    )

    # 第 3 跳：Go 入站真收、真走 `AdmitEvidence`（不是 pattern 那条 `Admit`）。
    assert con["admissions"] == {"AdmitEvidence": ADMITTED_EVIDENCE}, con["admissions"]
    for cid in chain["candidate_ids"]:
        settled = _settled_for(con, cid)
        assert len(settled) == 1, f"这条候选在 Go 侧结算了 {len(settled)} 次：{con['settled']}"
        assert settled[0]["status"] == "processed" and settled[0]["ack"] is True, settled[0]
        assert settled[0]["admits"] == 1, settled[0]

    # 第 4 跳：权威表落了行，且幂等指针只有一条（工单 §3 的 `source_records=1`）。
    inbox = _rows(
        engine,
        "SELECT status, message_type, consumer FROM inbox_messages WHERE message_id = :c",
        {"c": chain["candidate_ids"][0]},
    )
    assert len(inbox) == 1 and inbox[0]["status"] == "processed", inbox
    assert inbox[0]["message_type"] == "candidate_evidence", inbox
    assert inbox[0]["consumer"] == "go.core.candidate", inbox

    assert _count(engine, "source_records", "user_id = :u", u=user) == 1, (
        "两条候选复用同一来源指针 ⇒ 必须只登记一次（D-2 薄指针 ＋ 08 §3 幂等）"
    )
    rows = _rows(
        engine,
        "SELECT id, source_record_id, evidence_type, role, observation, excerpt, "
        "strength, generation_run_id, metadata_json FROM evidence WHERE user_id = :u "
        "ORDER BY excerpt",
        {"u": user},
    )
    assert len(rows) == ADMITTED_EVIDENCE, f"候选没全进权威表或多进了：{rows}"
    src = _rows(engine, "SELECT source_table, source_id, source_type FROM source_records "
                        "WHERE user_id = :u", {"u": user})
    assert src[0]["source_table"] == "memories" and src[0]["source_id"] == pub["memory_id"], src

    # ⚠️ 工单原文写 `evidence=1`，这里交付的是 2——理由见文件头：**口径改判**，
    # 只有一条候选时 `source_records=1` 分不清「复用」和「只有一条」，幂等指针就是空判。
    # 两条候选同指针 ⇒ 这一格第一次有了能证的内容。
    for row in rows:
        # 「`evidence.excerpt ⊆ memories.content`」——摘录是**库里那一行**的子串。
        # 这是 V3 不变量 1「没有可追溯证据的 Pattern 不得展示为事实」在跨进程链上的最终形状：
        # 摊给用户看的那句「依据」必须是他自己写过的字。
        assert row["excerpt"] in content, row
        assert row["role"] == "supporting", row
        assert row["observation"], row
        # 终局分由 Go 算（03:108），定义域闸（T2-04 §怎么做.2）在真链上的形状。
        assert 0.0 <= float(row["strength"]) <= 1.0, row
    # 两条候选是两条不同的观察：来源指针相同、摘录不同 ⇒ item_key 的哈希尾巴也必须不同。
    assert len({r["excerpt"] for r in rows}) == ADMITTED_EVIDENCE, rows
    assert len({r["id"] for r in rows}) == ADMITTED_EVIDENCE, rows


# ── ② 编造的摘录出不了 Python 进程 ───────────────────────


def test_fabricated_excerpt_never_leaves_process(chain, engine):
    """闸 G-04 的主闸在跨进程链上的形状：三条答案里那条编造的，两处都读不到。

    为什么分两处判：只判库里没有，可能是「候选投出去了但 Go 拒了」——那 DLQ 里会躺着
    一条非法候选，而这条闸要的恰恰是**生产者出网前就丢条**（让 Go 拒等于把永远非法的
    东西投上网络、撞完阶梯再进死信）。所以先判出网面，再判入库面。
    """
    _RAN.add("fabricated_excerpt_never_leaves_process")
    driver = chain["driver"]

    # 正向证人：这一轮真出了网 2 条、真答了 3 条（反向归零闸没有正证就等于扫空集）。
    assert driver["answered_items"] == [ANSWERED_ITEMS]
    assert len(driver["candidates"]) == PRODUCED_CANDIDATES
    excerpts = [c["excerpt"] for c in driver["candidates"]]
    assert FABRICATED_EXCERPT not in excerpts, excerpts
    assert all(e in driver["source_contents"][-1] for e in excerpts), excerpts

    # 观测正文也不许带着那句话出去：子串闸挡的是摘录，但用户看到的「依据」是整条观察。
    assert not any(FABRICATED_EXCERPT in c["observation"] for c in driver["candidates"])

    # 入库面：库自己回答「有没有这条」，不靠上面那个进程的自述。
    n = _count(engine, "evidence", "user_id = :u AND excerpt = :e",
               u=chain["user"], e=FABRICATED_EXCERPT)
    assert n == 0, f"编造的摘录进了权威表 {n} 条 ⇒ 子串闸在跨进程链上没咬住"
    # 假模型给的那条 `stated_preference` 观察也一起消失（它只由那条编造摘录支撑）。
    assert "清晨长跑" not in json.dumps(driver["candidates"], ensure_ascii=False)


# ── ③ 留痕一路进权威表 ───────────────────────────────────


def test_generation_lineage_crosses_processes(chain, engine):
    """`evidence.generation_run_id` 指向真存在的 `ai_generation_runs` 行，且 prompt 版本随行入库。

    为什么这一格必须跨进程判：Python 侧单测里 `generation_run_id` 是端口函数返回的字符串，
    「候选里带了它」证不了「留痕行真的写了」——`llm.complete` 里那条
    `record_run` → `current_run_id()` 只有真跑一遍才知道接没接上。
    """
    _RAN.add("generation_lineage_crosses_processes")
    rows = _rows(
        engine,
        "SELECT generation_run_id, metadata_json FROM evidence WHERE user_id = :u",
        {"u": chain["user"]},
    )
    assert len(rows) == ADMITTED_EVIDENCE, rows
    run_ids = {r["generation_run_id"] for r in rows}
    assert len(run_ids) == 1, f"一次生成产两条证据，留痕号必须是同一个：{run_ids}"
    run_id = run_ids.pop()
    assert run_id, "留痕列为空＝这条证据查不出是谁在什么时候生成的（不变量 8）"

    # 对面（Python 进程）报的候选体里的留痕号，与库里那一格同值：两侧不许同源。
    assert {c["generation_run_id"] for c in chain["driver"]["candidates"]} == {run_id}, (
        chain["driver"]["candidates"]
    )

    runs = _rows(engine, "SELECT purpose, status, model FROM ai_generation_runs WHERE id = :r",
                 {"r": run_id})
    assert len(runs) == 1, runs
    assert runs[0]["purpose"] == EXTRACTION_PURPOSE, runs[0]
    assert runs[0]["status"] == "ok", runs[0]

    for row in rows:
        meta = json.loads(row["metadata_json"])
        caller = meta["caller"]
        # promptVersion 比字面量：改版本号要过一次评审，不能被导入的常量悄悄带着走。
        assert caller["promptVersion"] == EXPECTED_PROMPT_VERSION, caller
        assert caller["modelId"] == runs[0]["model"], (caller, runs[0])
        # 模型自报强度只作输入留痕，终局分是 `evidence.strength` 那一格（Go 算的）。
        assert caller["llmStrength"] in ("low", "medium", "high"), caller
        assert caller["extractedBy"] == "ai.observation.requested", caller
    # 算分过程与证据同行（02 §37「Why do we know it」要能指回五个因子）。
    assert "components" in json.loads(rows[0]["metadata_json"]), rows[0]["metadata_json"]


# ── ④ 指标行跨过进程边界 ─────────────────────────────────


def test_metric_line_crosses_process_boundary(chain):
    """`evidence_extracted_total{result=ok}` 在这条命令上**只打一次**，且分态与产出一致。

    为什么在 E2E 判：`result` 的三态分界用的数「模型答了几条」而不是「闸后剩几条」
    （§口径.4——幻觉摘录那种答案在闸后也是零，记成「确实没东西」会让这一桶在曲线上
    长得像好消息）。这条分界只有真跑一次假模型才看得境，而计数只有一个出口（handler 的
    `_metric`），所以跨进程这一轮还要证明它没被 pipeline/worker 再打一遍。
    """
    _RAN.add("metric_line_crosses_process_boundary")
    op = chain["operation"]
    mine = [ln for ln in chain["driver"]["metric_lines"] if f"command={op}" in ln]
    assert len(mine) == 1, f"这条命令的抽取计数打了 {len(mine)} 行：{mine}"
    line = mine[0]
    assert "event=evidence_extracted_total" in line, line
    assert "result=ok" in line, line
    assert f"proposals={PRODUCED_CANDIDATES}" in line, line

    reasons = [ln for ln in chain["driver"]["reason_lines"] if f"command={op}" in ln]
    assert any(f"reason=extracted={PRODUCED_CANDIDATES}" in ln for ln in reasons), reasons
    # 一条也没被安全闸丢掉：`invalid`/`insufficient` 之外的那条路径才是本批要证的形状。
    assert not any("observation_dropped" in ln for ln in reasons), reasons


# ── ⑤ 重放同一行撞上 24h 成本闸 ──────────────────────────


def test_replay_hit_cost_gate(chain, amqp_url, engine, go_tool):
    """同一条命令再投一次：`already_extracted` 在**模型之前**拦住，零候选、零新钱。

    工单 §怎么做.7 那条成本闸的判据不是「库里行数没变」——那 Go 的 inbox 幂等也拦得住，
    两个闸会在同一条用例里互相顶包。这里判的是三件只有生产者闸才做得到的事：
    候选面零产出（还没到 Go）、模型调用零新增（钱没花）、理由码是 `already_extracted`。

    ⚠️ 用例顺序是判据的一部分：本条依赖 `chain` 那一轮**已经落库**的两条证据
    （成本闸读的就是 `evidence` 里那条来源指针），所以它必须排在落表之后。
    """
    _RAN.add("replay_hit_cost_gate")
    user, operation = chain["user"], chain["operation"]
    runs_before = _count(engine, "ai_generation_runs", "purpose = :p", p=EXTRACTION_PURPOSE)

    _purge(amqp_url)
    replayed = _go(go_tool, "replay", operation=operation)
    assert replayed["outbox_status"] == "replayed" and replayed["body_bytes"] > 0

    driver = _driver(amqp_url, expect_consumed=1, expect_produced=0)
    assert driver["candidates"] == [], driver["candidates"]
    assert driver["answered_items"] == [], (
        f"零候选却不是成本闸拦的：模型被叫了 {driver['answered_items']} 次 ⇒ 钱白花了一次"
    )
    lines = [ln for ln in driver["reason_lines"] if f"command={operation}" in ln]
    assert any("reason=already_extracted" in ln for ln in lines), lines
    assert driver["stats"]["consumed"] >= 1 and driver["stats"]["produced"] == 0, driver["stats"]

    # 成本闸排在模型之前 ⇒ 这一次重投没留下新的抽取留痕（安全判定那一趟另有 purpose）。
    assert _count(engine, "ai_generation_runs", "purpose = :p", p=EXTRACTION_PURPOSE) \
        == runs_before
    # 权威表也不许变：两条证据还是两条，来源指针还是一个。
    assert _count(engine, "evidence", "user_id = :u", u=user) == ADMITTED_EVIDENCE
    assert _count(engine, "source_records", "user_id = :u", u=user) == 1


# ── ⑥ 删除事件跨过进程边界，把「只依赖它」的结论撤掉 ──────


def test_source_deleted_propagates_cross_process(chain, amqp_url, engine, go_tool, monkeypatch):
    """Python 投 `source.deleted` → 真 broker → 真 Go 入站 → 库里翻登记行并撤掉派生结论。

    这条用例是 T2-06 的交付面，也是这一批**唯一**能证「两条面各有各的幂等位」的写法：
    候选面那两条 id 已经在 `inbox_messages` 里以 `go.core.candidate` 留了痕，而删除事件的
    id 是**另一串**、consumer 是 `go.core.source`。并成一个串的话，这条投递会被判成
    duplicate 而永远不传播——那种坏法在单测里看不见，只有对面那个真消费者才会承认它。

    三段各判一件事：
    ① **出网**用生产函数 `publish_source_deleted`（不是在这里手搓一条消息）：
       入口那一跳由 `test_source_deleted_producer.py` 在真路由上证，这里证的是它之后；
    ② **对面认了**看 Go 报的 `admissions`——只数 `InvalidateSource`，且候选那三个入口
       一次都不许被叫（事件面产不出候选，叫了就是走错门）；
    ③ **账面变了**由库自己回答：登记行翻 `deleted` 但**行还在**（B-08 第①格）、
       只依赖它的 fact 变 `invalidated`、`evidence_withdrawn` 留下事件行，
       而 evidence 行一条都不硬删（O-6/O-7）。

    ⚠️ 排在 `chain` 之后是判据的一部分：传播的分母是**真落库的那两条 evidence**，
    自己种靶只能证编排，证不了「T2-04 那条链产出的账会被这次删除撤掉」。
    """
    _RAN.add("source_deleted_propagates_cross_process")
    from backend.services.source_events import close_producer, publish_source_deleted

    user = chain["user"]
    memory_id = chain["publish"]["memory_id"]

    _purge(amqp_url)
    # 靶：一条 active 的 fact，支撑它的正是上面那两条 evidence（同一条来源指针）。
    planted = _go(go_tool, "seedfact", user=user)
    fact_id = planted["fact_id"]
    assert fact_id, planted
    assert _rows(engine, "SELECT status FROM personal_facts WHERE id = :f", {"f": fact_id})[0][
        "status"
    ] == "active", "靶没接上：传播面读不到一条 active 的派生行，后面全在扫空集"

    # 真删除：legacy 表的写面在 Python 这一侧，所以这一格由本文件直接落（见 _exec 注释）。
    assert _exec(engine, "UPDATE memories SET deleted = true, updated_at = now() "
                         "WHERE id = :m AND deleted = false", {"m": memory_id}) == 1

    monkeypatch.setenv("CLOSEN_SOURCE_EVENTS", "1")

    # 两次投递与收连接都在**同一个事件循环**里：那个单例连接（`connect_robust`）是
    # 环绑定的，跨 `asyncio.run()` 复用会在第二次投递时报 `publish_failed`
    # （实测形态：`RobustChannel closed`）。生产里 BFF 只有一个 loop，所以这是**测试自己**
    # 的寿命形状——照生产的形状写，而不是把两次调用拆成两个环。
    async def _publish_twice() -> tuple[Dict[str, Any], Dict[str, Any]]:
        try:
            a = await publish_source_deleted(user_id=user, source_id=memory_id)
            b = await publish_source_deleted(user_id=user, source_id=memory_id)
            return a, b
        finally:
            await close_producer()

    first, second = asyncio.run(_publish_twice())

    assert first["published"] is True and first["reason"] == "published", first
    assert second["message_id"] == first["message_id"], (
        "同一行删两次的 id 不一样 ⇒ 对面那条幂等位形同虚设"
    )
    mid = first["message_id"]
    # 派生规则这一侧也钉一下：id 只由指针决定，别哪天顺手加了时间戳。
    assert mid == contract.source_deleted_message_id(user, "memories", memory_id)
    # 让 `chain` 的 finally 能把这条事件在 `inbox_messages` 里的认领位一起删掉：
    # 下一轮如果复用同一个 memory_id，这一格会让那条删除直接判成 duplicate。
    chain["source_event_id"] = mid

    con = _go(go_tool, "source", source_event_id=mid, expect_settled=2)
    settled = sorted(_settled_for(con, mid), key=lambda s: s["status"])
    assert len(settled) == 2, f"两条投递都得以这条 id 结算，一条都不许丢：{con['settled']}"
    assert [s["status"] for s in settled] == ["duplicate", "processed"], settled
    assert all(s["ack"] is True for s in settled), settled
    # 进门一次、第二次被 inbox 挡在领域入口之前。
    assert sum(s["invalidates"] for s in settled) == 1, settled
    assert {s["invalidates"] for s in settled} == {0, 1}, settled
    assert con["admissions"] == {"InvalidateSource": 1}, (
        f"事件面只许叫 InvalidateSource 一次：{con['admissions']}"
    )
    assert con["inbound_stats"]["processed"] >= 1, con["inbound_stats"]

    # ── 账面：只有真库能回答的四格 ──
    src = _rows(engine, "SELECT status, source_table, source_id FROM source_records "
                        "WHERE user_id = :u", {"u": user})
    assert len(src) == 1, src
    # B-08 第①格：翻状态、**不删行**。删了行，`live_support` 就没有可读的那一列了。
    assert src[0]["status"] == "deleted", src
    assert src[0]["source_table"] == "memories" and src[0]["source_id"] == memory_id, src

    fact = _rows(engine, "SELECT status, invalidated_at IS NULL AS no_at, reject_reason, "
                         "coalesce(model_version, 0) AS version FROM personal_facts WHERE id = :f",
                 {"f": fact_id})
    assert fact[0]["status"] == "invalidated", fact
    assert fact[0]["no_at"] is False, "状态翻了但没留时刻 ⇒ 前端读不出「这条结论什么时候撤的」"
    assert fact[0]["reject_reason"] == "evidence_withdrawn", fact
    assert int(fact[0]["version"]) >= 1, fact

    # 退场必须留痕（不变量 9：撤回要有账，而且 ++ 过版本）。
    assert _count(engine, "personal_model_events",
                  "user_id = :u AND event_type = 'evidence_withdrawn'", u=user) >= 1
    assert _count(engine, "personal_model_versions",
                  "user_id = :u AND reason = 'evidence_withdrawn'", u=user) >= 1

    # O-6/O-7：失效标记只落在 source 层，evidence 行本身**不硬删**。
    assert _count(engine, "evidence", "user_id = :u", u=user) == ADMITTED_EVIDENCE

    # 两条面的幂等位各占一格：候选那两条与这条事件，consumer 名不同、互不顶包。
    faces = _rows(engine, "SELECT DISTINCT consumer FROM inbox_messages WHERE message_id = :m",
                  {"m": mid})
    assert [f["consumer"] for f in faces] == [contract.SOURCE_DELETED_CONSUMER], faces
    assert contract.SOURCE_DELETED_CONSUMER != "go.core.candidate", (
        "两个 consumer 并成一个串＝让候选面的「吃过了」替事件面宣告吃过了"
    )
    for cid in chain["candidate_ids"]:
        got = _rows(engine, "SELECT consumer FROM inbox_messages WHERE message_id = :c",
                    {"c": cid})
        assert [g["consumer"] for g in got] == ["go.core.candidate"], (cid, got)


# ── 清单闸：少跑一条就红 ─────────────────────────────────


def test_case_plan_fully_ran(amqp_url: str, backend_db: str, engine, go_tool: str):
    """四个前置都要在，这一条才有资格判红：前置缺失时它跟着 skip，而不是报一堆假红。"""
    missing = sorted(set(CASE_PLAN) - _RAN)
    extra = sorted(_RAN - set(CASE_PLAN))
    assert not missing, f"这些跨进程用例一条都没跑到：{missing}（别把它当绿）"
    assert not extra, f"跑了清单外的用例，请把 CASE_PLAN 一起补上：{extra}"
