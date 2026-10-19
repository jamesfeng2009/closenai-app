#!/usr/bin/env python
"""T2-04 · 证据链跨进程 E2E 用的 **Python 那一半驱动程序**（不是生产入口）。

沿 `scripts/r5_interop_worker.py` 的形态：pytest 当指挥，本脚本只把真组件接起来、如实报告
观测值，断言全在 `backend/tests/test_v3_evidence_e2e.py`。

与 R5 那条闸的区别正是本批要证的这件事：R5 注册的是**回声 handler**（把命令体原样包成
pattern 提案），因为 R5 的结案口径逐字是「管道通了，流量仍是零」，四个域的候选生产归 R6。
T2-04 交付的就是**生产那一条抽取链**，所以这里注册的是生产组合根本身：
`backend.services.ai_observation.build_registry()`——真读一行 `memories`、真跑四态安全判定、
真按子串闸把不合格的观察丢掉、真写 `ai_generation_runs` 留痕。

只有一个字节面是编排的：**模型的answer**。做法是把 `backend/ai/llm` 的 client 缓存
（模块级 `_client`）换成一个假对象，而不是 monkeypatch `llm.complete`——差别在于
`complete` 里面那条留痕链（`generation_run.record_run` → `current_run_id()`）必须照跑：
`evidence.generation_run_id` 是 Go 侧 `AdmitEvidence` 的必填列，也是本条 E2E 的判据之一，
patch 掉 `complete` 就等于把「没留痕 ⇒ 零候选」那一格测成了另一件事。

为什么不把原文用 env 传进来：摘录由**库里那一行**决定，而这一行的唯一事实源是 Go 种的
`memories.content`。所以假模型读的是真端口 `load_source` 返回的正文（下面 `_load_and_record`
只是给那次真读加了一次旁路记录，不改任何值）。

答形固定三条：两条**逐字取自原文**的摘录 ＋ 一条原文里根本没有的摘录。第三条就是
`parse_items` 子串闸（T2-04 闸 G-04 的主闸）在这条链上的靶——它必须出不了本进程。

跑法（由 pytest 起停；手工跑法，需先有一个跑过 `-mode observe` 的 broker 拓扑与一份
alembic upgrade head 的库）：

    CLOSEN_MQ_WORKER=1 CLOSEN_AMQP_URL="$URL" DATABASE_URL="postgresql+psycopg://…" \\
    T204_EXPECT_CONSUMED=1 T204_EXPECT_PRODUCED=2 .venv/bin/python scripts/t204_interop_worker.py
    → stdout 一行 JSON（计数＋候选＋指标行），退出码 0＝达成了目标

退出码与 `worker.main`／r5 驱动同一口径：2＝开关配置非法，3＝拓扑缺失，4＝连不上 broker，
1＝窗口到点仍没达成目标（**不许把没走到读成走过**）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

# 本文件由 pytest 用 `python scripts/t204_interop_worker.py` 直接起，不走 -m，
# 所以要自己把仓根塞进 sys.path（与 pyproject 的 pythonpath=["."] 同一口径）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aio_pika

from backend.ai import llm as llm_module
from backend.ai.mq import worker
from backend.ai.mq.config import SwitchError, load_switch
from backend.ai.mq.consumer import WorkerStats
from backend.ai.mq.contract import candidate_message_id
from backend.ai.mq.publisher import TopologyMissing
from backend.ai.mq.registry import Proposal, Registry
from backend.ai.safety import safety_service
from backend.services import ai_observation

#: 要消费掉几条命令才算达成目标（0 不是本脚本的用法：E2E 的每一轮都真发了一条命令）。
EXPECT_CONSUMED = int(os.environ.get("T204_EXPECT_CONSUMED", "1"))
#: 要产出几条候选才算达成目标。正向链＝2（三条答案里那条编造的必须被咽掉）；
#: 重放那一轮＝0（24h 同源成本闸）。两个值都是**判据**而不是猜测：脚本到点没等到就回非零。
EXPECT_PRODUCED = int(os.environ.get("T204_EXPECT_PRODUCED", "2"))
#: 观察窗口（秒）。到点仍没达成目标 ⇒ rc=1。
RUN_SECONDS = float(os.environ.get("T204_RUN_SECONDS", "60"))

#: 那条**原文里根本没有**的摘录。它其余每一格都合法（类型在词表内、观测不超长、role 合法、
#: strength 在三值里），这样「它没出本进程」的唯一解释就只剩子串闸——那正是变异要摘掉的一格。
FABRICATED_EXCERPT = "我每天清晨五点起床跑步"

#: 原文按中文标点切成小句；取前两条小句当逐字摘录（`excerpt in content` 天然成立）。
_CLAUSE_SPLIT = re.compile(r"[，。；;！!]")


# ── 假模型：只换 client，不换 complete ────────────────────


def _completion(text: str) -> Any:
    """摆出 OpenAI 兼容 client 的取法（`resp.choices[0].message.content`，`resp.usage`）。

    `usage` 给 None：`llm._usage_fields` 对「provider 不回传用量」的形状就是留空，
    这一格与 E2E 的判据无关，不许在这里发明第三种形态。
    """
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text), index=0)],
        usage=None,
    )


class _FakeCompletions:
    def __init__(self, answer) -> None:
        self._answer = answer

    async def create(self, **kwargs: Any) -> Any:
        messages = kwargs.get("messages") or [{}]
        return _completion(self._answer(str(messages[-1].get("content") or "")))


class _FakeClient:
    def __init__(self, answer) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(answer))


def install_fake_llm(answer) -> None:
    """把 `backend/ai/llm` 的懒初始化缓存填上 ⇒ `_get_client()` 直接返回假件。

    只此一处替换。`llm.complete`、`generation_run.record_run`、`current_run_id`、
    `get_model` 全部是原码——本条链要的「留痕 id 进了候选，候选进了权威表」只有这样才能真跑。
    """
    llm_module._client = _FakeClient(answer)


# ── 编排出来的模型答案 ───────────────────────────────────

#: 假模型这一共答了几条（`invalid`/`ok` 分界的旁证，随报告一起回给 pytest）。
answered_counts: List[int] = []
#: 真端口 `load_source` 读回来的正文（旁路记录，不改值）。
seen_contents: List[str] = []


def _clauses(content: str) -> List[str]:
    """原文里够长的前两条小句——假模型要引用的就是这两串**逐字**。"""
    parts = [p.strip() for p in _CLAUSE_SPLIT.split(content) if len(p.strip()) >= 6]
    if len(parts) >= 2:
        return parts[:2]
    # 兜底只在该夹具被改坏时才可能走到：按定长切出来的两串仍然是子串，
    # 于是这一轮测的仍是「三条里有一条是编的」而不是「夹具自己造不出合法摘录」。
    return [content[:10], content[10:20]]


def _extraction_answer(content: str) -> str:
    first, second = _clauses(content)
    items = [
        {
            "evidence_type": "repeated_behavior",
            "role": "supporting",
            "excerpt": first,
            "observation": f"记录里写了「{first}」这样的晚间安排",
            "strength": "medium",
        },
        {
            "evidence_type": "emotional_pattern",
            "role": "supporting",
            "excerpt": second,
            "observation": f"记录把「{second}」和第二天早上的状态写在同一行",
            "strength": "medium",
        },
        {
            "evidence_type": "stated_preference",
            "role": "supporting",
            "excerpt": FABRICATED_EXCERPT,
            "observation": "用户在记录里说自己有清晨长跑的习惯",
            "strength": "high",
        },
    ]
    answered_counts.append(len(items))
    return json.dumps({"items": items}, ensure_ascii=False)


def model_answer(prompt: str) -> str:
    """两岔：安全分类器那一路回 SAFE，抽取那一路回上面那个 JSON。

    分派按**前缀**而不是「非抽取即安全」：`check_text_tri` 拼的是
    `f"{self._CHECK_PROMPT}{text}"`（`backend/ai/safety.py:496`），而 `safety_service` 的
    `_CHECK_PROMPT` 就是那份常量的唯一事实源——这里不抄第二份文案。
    """
    if prompt.startswith(safety_service._CHECK_PROMPT):
        return "SAFE"
    if not seen_contents:
        # 真端口还没读到原文就有人来问抽取答案：那是接线错（抽取的 prompt 由原文拼出来），
        # 回一个空 items 让它变成 insufficient 而不是把一次崩溃冒充成一次判定。
        answered_counts.append(0)
        return json.dumps({"items": []}, ensure_ascii=False)
    return _extraction_answer(seen_contents[-1])


# ── 端口与注册：生产组合根，只给 load_source 加一次旁路记录 ──


async def _load_and_record(user_id: str, memory_id: str):
    source = await ai_observation.load_source(user_id, memory_id)
    if source is not None:
        seen_contents.append(source.content)
    return source


def build_registry() -> tuple[Registry, List[Dict[str, Any]]]:
    """真组合根＋一层**只读**包装，返回 ``(注册表, 旁路抄下的候选)``。

    `build_registry` 注册的必须是生产 handler（本批交付的就是它），所以计数与候选正文是在
    它返回**之后**旁路抄一份，不改任何一条 Proposal。为什么不在 pipeline 那侧抄：
    pipeline 的 `Decision.candidates` 要等 publish 之后才有，而本脚本要在到点时就判
    `produced`——沿 R5 的 `recorder` 同一条取舍：观测面套在真件外面，一条都不拦。
    """
    ports = replace(ai_observation.build_ports(), load_source=_load_and_record)
    registry = ai_observation.build_registry(Registry(), ports)
    production = registry.handler_for("observation")
    seen: List[Dict[str, Any]] = []

    async def handler(command: Any) -> tuple[Proposal, ...]:
        proposals = await production(command)
        for proposal in proposals:
            seen.append(
                {
                    "command_message_id": command.message_id,
                    "candidate_message_id": candidate_message_id(
                        command.message_id, proposal.item_key
                    ),
                    "kind": proposal.kind,
                    "item_key": proposal.item_key,
                    "body": proposal.body,
                }
            )
        return proposals

    observed = Registry()
    observed.register("observation", handler)
    return observed, seen


# ── 日志旁路：指标行与 reason 行要能跨过进程边界被读到 ────


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(record.getMessage())
        except Exception:  # noqa: BLE001 — 观测面不许把被观测的进程带崩
            pass


async def _watch(stats: WorkerStats, stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    started = loop.time()
    while True:
        await asyncio.sleep(0.2)
        if stats.consumed >= EXPECT_CONSUMED and stats.produced == EXPECT_PRODUCED:
            stop.set()
            return
        if loop.time() - started >= RUN_SECONDS:
            stop.set()
            return


async def amain() -> tuple[dict, int]:
    registry, captured = build_registry()
    handler_logger = logging.getLogger("backend.ai.mq.handlers.observation")
    probe = _Capture()
    handler_logger.addHandler(probe)

    sw = load_switch(os.environ)
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
        handler_logger.removeHandler(probe)

    snapshot = stats.snapshot()
    reached = stats.consumed >= EXPECT_CONSUMED and stats.produced == EXPECT_PRODUCED
    metric_lines = [ln for ln in probe.lines if "event=evidence_extracted_total" in ln]
    reason_lines = [ln for ln in probe.lines if "event=observation_extract" in ln]
    report = {
        "ok": reached and rc == 0,
        "expect_consumed": EXPECT_CONSUMED,
        "expect_produced": EXPECT_PRODUCED,
        "run_seconds": RUN_SECONDS,
        "stats": snapshot,
        "candidates": [
            {
                "command_message_id": item["command_message_id"],
                "candidate_message_id": item["candidate_message_id"],
                "item_key": item["item_key"],
                "excerpt": item["body"]["excerpt"],
                "observation": item["body"]["observation"],
                "evidence_type": item["body"]["evidenceType"],
                "role": item["body"]["role"],
                "source": item["body"]["source"],
                "generation_run_id": item["body"]["generationRunId"],
                "metadata_json": item["body"]["metadataJson"],
            }
            for item in captured
        ],
        "metric_lines": metric_lines,
        "reason_lines": reason_lines,
        "answered_items": answered_counts,
        "fabricated_excerpt": FABRICATED_EXCERPT,
        "source_contents": seen_contents,
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
    install_fake_llm(model_answer)
    try:
        report, rc = asyncio.run(amain())
    except SwitchError as exc:
        print(json.dumps({"ok": False, "fail": f"开关配置非法：{exc}", "stats": {}}))
        return 2
    print(json.dumps(report, ensure_ascii=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
