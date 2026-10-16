#!/usr/bin/env python
"""R5-③ · 跨语言互操作闸用的**真 AI worker** 驱动程序（不是生产入口）。

沿 `scripts/r3_interop_server.py` 的形态：pytest 当指挥，本脚本只把真组件接起来、如实报告
观测值，断言全在 `backend/tests/test_r5_interop_broker.py`。被验的东西一个都不换：
真 `backend.ai.mq.worker.serve`、真 `CommandConsumer` 八步、真 `pipeline`＋`encode_candidate`、
真 `AioCandidatePublisher`（confirm 模式）、真 broker。只有最底下的**产出**是编排的——
R5 的结案口径逐字是「管道通了，流量仍是零」，四个域的候选生产归 R6，所以这里注册一个
**回声 handler**：把命令体里的 `evidenceIds` 原样包成一条 pattern 提案。

为什么必须是回声而不是别的：
  · 它不查库（`test_ai_boundary.py` 禁的就是这一类），也不自己算分（03 §5「Final Evidence
    Strength is calculated by domain code」）；
  · 它的输出只由**命令的 message_id** 决定，于是 `candidate_message_id()` 那条派生规则
    （D-R5-5）在真链上必然给出同一个 id ⇒ 「重放第二次不生效」这条验收才有可判的形式；
  · 措辞、`safety.status`、`generation_run_id` 都按 Go 的 Gate 九关的**准入**形态给，
    所以这一轮跑的是「候选真进了权威表」，不是「候选真被拒了」。

跑法（由 pytest 起停；手工跑法，需先有一个真 broker 与一份 alembic upgrade head 的库）：

    CLOSEN_MQ_WORKER=1 CLOSEN_AMQP_URL="$R5_AMQP_URL" \\
    R5_INTEROP_EXPECT_CONSUMED=1 .venv/bin/python scripts/r5_interop_worker.py
    → stdout 一行 JSON（计数＋派生出的候选 id），退出码 0＝达成了目标

退出码与 `worker.main` 同一口径：2＝开关配置非法，3＝拓扑缺失，4＝连不上 broker，
1＝窗口到点仍没达成目标（**不许把没走到读成走过**）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, List

# 本文件由 pytest 用 `python scripts/r5_interop_worker.py` 直接起，不走 -m，
# 所以要自己把仓根塞进 sys.path（与 pyproject 的 pythonpath=["."] 同一口径）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aio_pika

from backend.ai.mq import worker
from backend.ai.mq.config import SwitchError, load_switch
from backend.ai.mq.consumer import WorkerStats
from backend.ai.mq.contract import candidate_message_id
from backend.ai.mq.publisher import TopologyMissing
from backend.ai.mq.registry import Proposal, Registry

#: 要消费掉几条命令才算达成目标。0＝「这一轮**不该**消费任何东西」，
#: 用于 D-R5-10 的反向不变性：回投面只有 Go 的入站 worker 订阅，Python 一个字节都不许碰。
EXPECT_CONSUMED = int(os.environ.get("R5_INTEROP_EXPECT_CONSUMED", "1"))
#: 观察窗口（秒）。expect>0 时到点仍未达成就判失败；expect==0 时它就是「空转多久」。
RUN_SECONDS = float(os.environ.get("R5_INTEROP_RUN_SECONDS", "60"))

#: 措辞刻意避开 Go 侧 Gate 的三张禁令表（绝对量词 `gate.go:68-71`、因果连接
#: `gate.go:77-80`、标签词 `policy.go:101-107`）：本批要测的是「候选进得了权威表」，
#: 被措辞关拦下就等于把这条链测成了另一件事。
PATTERN_DESCRIPTION = "过去两周的记录里，出现过两次晚间处理事情之后入睡变慢的共现"


def pattern_body(command: Any) -> dict:
    """由**命令**确定性地产出一条 pattern 提案体（protojson 的 lowerCamelCase 形态）。

    确定性是这条链的地板：同一个 message_id 重放两次必须产出**同一条**候选
    （`candidate_message_id` 不含时间也不含随机，D-R5-5），否则 Go 的 inbox 唯一键
    `(message_id, consumer)` 永远撞不上，重复投递就会重复生效。
    """
    digest = hashlib.sha256(command.message_id.encode("utf-8")).hexdigest()
    evidence_ids = list(command.body.get("evidenceIds") or [])
    return {
        "schemaVersion": command.schema_version,
        "kind": "KIND_PATTERN",
        "patternType": "temporal_rhythm",
        "scope": "sleep",
        "description": PATTERN_DESCRIPTION,
        "fingerprint": f"r5-{digest[:32]}",
        "confidence": 0.8,
        "detectorVersion": "r5-interop-1",
        "evidenceIds": evidence_ids,
        "generationRunId": f"gir-{digest[:24]}",
        "safety": {"status": "SAFETY_STATUS_OK"},
    }


async def _watch(stats: WorkerStats, stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    started = loop.time()
    while True:
        await asyncio.sleep(0.2)
        if EXPECT_CONSUMED > 0 and stats.consumed >= EXPECT_CONSUMED and stats.produced >= EXPECT_CONSUMED:
            stop.set()
            return
        if loop.time() - started >= RUN_SECONDS:
            stop.set()
            return


async def amain() -> tuple[dict, int]:
    seen: List[str] = []

    async def handler(command: Any) -> Proposal:
        seen.append(command.message_id)
        return Proposal(kind="pattern", body=pattern_body(command))

    sw = load_switch(os.environ)
    registry = Registry()
    registry.register("pattern", handler)
    stats = WorkerStats()
    stop = asyncio.Event()

    fail = ""
    rc = 0
    watcher = asyncio.create_task(_watch(stats, stop))
    try:
        await worker.serve(sw, registry, stats, stop)
    except TopologyMissing as exc:
        fail, rc = str(exc), 3
    except aio_pika.exceptions.AMQPConnectionError as exc:
        fail, rc = f"{type(exc).__name__}: {exc}", 4
    except Exception as exc:  # noqa: BLE001 — 观察面失败照实报告，不在这里发明第二种处置
        fail, rc = f"{type(exc).__name__}: {exc}", 1
    finally:
        watcher.cancel()

    snapshot = stats.snapshot()
    if EXPECT_CONSUMED > 0:
        reached = stats.consumed >= EXPECT_CONSUMED and stats.produced >= EXPECT_CONSUMED
    else:
        reached = stats.consumed == 0 and stats.produced == 0
    candidates = [
        {
            "command_message_id": mid,
            "candidate_message_id": candidate_message_id(mid),
            "fingerprint": f"r5-{hashlib.sha256(mid.encode('utf-8')).hexdigest()[:32]}",
            "generation_run_id": f"gir-{hashlib.sha256(mid.encode('utf-8')).hexdigest()[:24]}",
        }
        for mid in sorted(set(seen))
    ]
    report = {
        "ok": reached and rc == 0,
        "expect_consumed": EXPECT_CONSUMED,
        "run_seconds": RUN_SECONDS,
        "stats": snapshot,
        "candidates": candidates,
        "fail": fail,
    }
    if not reached and rc == 0:
        rc = 1  # 「窗口到点没达成目标」不是成功：读成绿就等于把这条闸拆了
    return report, rc


def main() -> int:
    # 日志一律 stderr：stdout 只许那一行 JSON，pytest 要解析它。
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    try:
        report, rc = asyncio.run(amain())
    except SwitchError as exc:
        print(json.dumps({"ok": False, "fail": f"开关配置非法：{exc}", "stats": {}}))
        return 2
    print(json.dumps(report, ensure_ascii=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
