"""T2-02 · ``observation.extract`` 的 handler 与服务侧端口闸（不连真 LLM、不连真库）。

工作单这一批的验收五条逐字落在这里：

    fake LLM 返回 2 条合法 + 1 条非子串 ⇒ 只出 2 条   → :func:`test_two_legal_items_and_one_fabricated...`
    source 不存在 ⇒ ``None`` + 原因                   → :func:`test_reasons_are_logged_verbatim`
    crisis ⇒ 0 候选 + 安全事件                         → :func:`test_crisis_source_produces...`
    候选里 ``prompt_version``/``model_id`` 必填          → :func:`test_prompt_version_and_model_id...`
    ``registry.registered() == ("observation",)``       → :func:`test_production_registry_registers...`

为什么抽取用**注入的假端口**而不是只靠 ``conftest.mock_llm``：那样"模型答了四条只出三条"
这类判据不必猜 mock 的调用次序，而安全判定**不走端口**——它必须用 ``backend/ai/safety.py``
那份唯一事实源（在这里复制一份检测判断正是措辞规范禁止的事），所以每个用例仍要让 autouse 的
``mock_llm`` 给分类器一个读得回的裁决（``SAFE``／``UNSAFE``）。默认那句 ``MOCK_LLM_REPLY``
里既没有 SAFE 也没有 UNSAFE，按 D-8 会落成 ``unavailable``——**那是刻意的**，
假答案不许被当成安全，:func:`test_safety_unavailable_is_transient_not_a_pass` 钉的就是它。

库侧（``backend/services/ai_observation.py`` 那八个端口）用 sqlite 跑**生产那一份 SQL**：
``load_source`` 的三个过滤条件、``already_extracted`` 的那个 join、``record_safety_event``
真写的审计行，都属于"写错了不报错、只是永远不生效"的形状，假端口拦不住它们。
"""
from __future__ import annotations

import ast
import dataclasses
import json
import logging
import pathlib
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import backend.models  # noqa: F401  注册全部表到 Base.metadata
from backend.ai.mq import pipeline, topology
from backend.ai.mq.contract import (
    CANDIDATE_BODY_KEYS,
    SOURCE_OCCURRED_AT_FIELD,
    SOURCE_REF_FIELD,
    Command,
    ContractError,
    candidate_message_id,
    REASON_MULTI_CANDIDATE,
)
from backend.ai.mq.handlers.observation import (
    EXPLICITNESS_BY_TYPE,
    MAPPING_TABLES,
    METRIC_EVENT,
    METRIC_RESULTS,
    MIN_CONTENT_CHARS,
    SPECIFICITY_BY_TYPE,
    WIRE_EVIDENCE_TYPE,
    ObservationExtractHandler,
    ObservationPorts,
    SourceText,
    candidate_body,
    crisis_kind_of,
    item_key,
)
from backend.ai.mq.registry import Proposal, Registry
from backend.ai.prompts.observation import (
    MAX_ITEMS,
    MAX_OBSERVATION_CHARS,
    OBSERVATION_TYPES,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    ObservationItem,
    answered_items,
    build_prompt,
    parse_items,
)
from backend.ai.safety import safety_service
from backend.database import Base
from backend.models.canonical import Evidence, SourceRecord, UserFeedback
from backend.models.journey import SafetyEvent
from backend.models.memory import Memory
from backend.repositories.chat_repository import chat_repository
from backend.repositories.user_repository import user_repository
from backend.services import ai_observation

ROOT = pathlib.Path(__file__).resolve().parents[2]
HANDLER_FILE = ROOT / "backend" / "ai" / "mq" / "handlers" / "observation.py"
WORKER_FILE = ROOT / "backend" / "ai" / "mq" / "worker.py"
SERVICES_FILE = ROOT / "backend" / "services" / "ai_observation.py"

USER_ID = "u-obs"
MEMORY_ID = "6f1c2b3a-0000-4111-8a2b-000000000001"
SOURCE_REF = f"memories:{MEMORY_ID}"
RUN_ID = "11111111-2222-3333-4444-555555555555"
MODEL = "qwen-max-2026-01"
OCCURRED_AT = "2026-01-01T00:00:00Z"

#: 一行够长、且下面每个 excerpt 都能在里面**逐字**找到的原文。
CONTENT = "这周第三次凌晨两点还在改方案，发出去那一刻反而更空。我说我习惯了，但上次朋友生日我也忘了。"
EXCERPT_A = "凌晨两点还在改方案"
EXCERPT_B = "我说我习惯了"
EXCERPT_C = "发出去那一刻反而更空"
#: 差一个字就不是子串的那一条：模型改写／记错原话的可执行形态。
EXCERPT_FAKE = "凌晨三点还在改方案"

ITEM_A = {
    "evidence_type": "repeated_behavior",
    "role": "supporting",
    "excerpt": EXCERPT_A,
    "observation": "一周内三次深夜还在工作",
    "strength": "high",
}
ITEM_B = {
    "evidence_type": "value_statement",
    "role": "supporting",
    "excerpt": EXCERPT_B,
    "observation": "把熬夜说成习惯而不是选择",
    "strength": "medium",
}
ITEM_C = {
    "evidence_type": "stated_preference",
    "role": "supporting",
    "excerpt": EXCERPT_C,
    "observation": "交付之后并没有满足感",
    "strength": "medium",
}
ITEM_FAKE = {
    "evidence_type": "emotional_pattern",
    "role": "supporting",
    "excerpt": EXCERPT_FAKE,
    "observation": "深夜时情绪最低",
    "strength": "low",
}
#: 第四条**合法**观测（excerpt 同样是原文的子串）。上限那一格必须有它：
#: 只喂「三条＋一条复读」的话，去重先把复读吃掉，剩下的正好等于上限，
#: 于是那条测的是去重而不是上限（2026-10-18 变异 M2 打不出来时发现的）。
EXCERPT_D = "上次朋友生日我也忘了"
ITEM_D = {
    "evidence_type": "relationship_pattern",
    "role": "supporting",
    "excerpt": EXCERPT_D,
    "observation": "忘了亲近的人的生日",
    "strength": "low",
}
#: 「不想活」取自 ``safety.CRISIS_TRIGGERS["self_harm"]["zh"]``。危机判定是**确定性正则**，
#: 排在模型调用之前，所以这一格不依赖 mock_llm 给什么答案。
CRISIS_CONTENT = "这周第三次凌晨两点还在改方案，说完只觉得活着很累，有点不想活了，写在这里谁也看不见。"

LEGAL_THREE = (ITEM_A, ITEM_B, ITEM_C)


def reply_of(*items: dict[str, Any]) -> str:
    return json.dumps({"items": list(items)}, ensure_ascii=False)


def to_item(entry: dict[str, Any]) -> ObservationItem:
    return ObservationItem(**entry)  # 键与契约五字段逐字同名


# ── 假端口：八格都给，每一格都留痕，于是"没调用"也是一件可断言的事 ────────────


class PortSpy:
    def __init__(self, **over: Any) -> None:
        self.source: SourceText | None = over.pop(
            "source", SourceText(content=CONTENT, occurred_at=OCCURRED_AT, language="zh")
        )
        self.reply: Any = over.pop("reply", reply_of(ITEM_A, ITEM_B, ITEM_FAKE))
        self.quota: Any = over.pop("quota", True)
        self.extracted: bool = over.pop("extracted", False)
        self.corrections: tuple[str, ...] = over.pop("corrections", ())
        self.run_id: str = over.pop("run_id", RUN_ID)
        self.model: str = over.pop("model", MODEL)
        self.calls: list[tuple[str, Any]] = []
        self.safety_events: list[tuple[str, dict, str, str]] = []
        if over:
            raise TypeError(f"PortSpy 不认这些键：{sorted(over)}（拼错的端口名会让假件静默失配）")
        self.ports = ObservationPorts(
            load_source=self._load,
            recent_corrections=self._negatives,
            already_extracted=self._extracted,
            quota_available=self._quota,
            record_safety_event=self._record,
            complete=self._complete,
            generation_run_id=self._run_id,
            model_id=self._model,
        )

    async def _load(self, user_id: str, memory_id: str) -> SourceText | None:
        self.calls.append(("load_source", (user_id, memory_id)))
        return self.source

    async def _negatives(self, user_id: str, limit: int) -> tuple[str, ...]:
        self.calls.append(("recent_corrections", limit))
        return self.corrections

    async def _extracted(self, user_id: str, table: str, row_id: str, window: timedelta) -> bool:
        self.calls.append(("already_extracted", (table, row_id, window)))
        return self.extracted

    async def _quota(self, user_id: str) -> bool:
        self.calls.append(("quota_available", user_id))
        return await self.quota() if callable(self.quota) else self.quota

    async def _record(self, user_id: str, verdict: dict, language: str, excerpt: str) -> None:
        self.safety_events.append((user_id, dict(verdict), language, excerpt))

    async def _complete(self, prompt: str, language: str) -> str:
        self.calls.append(("complete", prompt))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def _run_id(self) -> str:
        return self.run_id

    def _model(self) -> str:
        return self.model

    @property
    def prompts(self) -> list[str]:
        return [arg for name, arg in self.calls if name == "complete"]


def make_handler(**over: Any) -> tuple[ObservationExtractHandler, PortSpy]:
    spy = PortSpy(**over)
    return ObservationExtractHandler(spy.ports), spy


def make_command(**body_extra: Any) -> Command:
    body: dict[str, Any] = {
        "schemaVersion": "1",
        "taskType": "TASK_TYPE_OBSERVATION_EXTRACT",
        SOURCE_REF_FIELD: SOURCE_REF,
        **body_extra,
    }
    return Command(
        message_id="cmd-obs-0001",
        user_id=USER_ID,
        routing_key="ai.observation.requested",
        task_type="TASK_TYPE_OBSERVATION_EXTRACT",
        message_type="ai_observation_requested",
        schema_version="1",
        correlation_id="corr-1",
        causation_id="task-1",
        attempt=0,
        body=body,
        body_size=len(json.dumps(body)),
    )


@pytest.fixture
def safe_gate(mock_llm: SimpleNamespace) -> SimpleNamespace:
    """让 L3 语义分类器读得出"安全"（autouse 那句假答案里读不出任何裁决）。"""
    mock_llm.state["complete_text"] = "SAFE"
    return mock_llm


async def run_handler(**over: Any) -> tuple[tuple[Proposal, ...], PortSpy]:
    handler, spy = make_handler(**over)
    return await handler(make_command()), spy


def metadata_of(proposal: Proposal) -> dict[str, Any]:
    return json.loads(proposal.body["metadataJson"])


class SpyPublisher:
    """确认式发布替身：``publish`` 返回＝候选拿到了 broker 的 ``Basic.Ack``。"""

    def __init__(self, *, exchange: str = topology.EXCHANGE_AI_CANDIDATES) -> None:
        self.exchange = exchange
        self.published: list[Any] = []

    async def publish(self, message: Any) -> None:
        self.published.append(message)


# ── 验收①：2 条合法 + 1 条非子串 ⇒ 只出 2 条 ────────────────────────────────


async def test_two_legal_items_and_one_fabricated_excerpt_yield_only_two(safe_gate) -> None:
    proposals, spy = await run_handler()
    assert len(proposals) == 2, [p.body["excerpt"] for p in proposals]
    assert {p.body["excerpt"] for p in proposals} == {EXCERPT_A, EXCERPT_B}
    assert spy.safety_events == [], "非危机不该写安全事件"


async def test_every_excerpt_is_a_verbatim_substring_of_the_source(safe_gate) -> None:
    """不变量 1 的可执行形式：回链给用户看的"依据"必须是他自己写过的字。"""
    proposals, _ = await run_handler(
        reply=reply_of(ITEM_A, ITEM_B, ITEM_FAKE, {**ITEM_A, "excerpt": "改写了原话"})
    )
    for proposal in proposals:
        assert proposal.body["excerpt"] in CONTENT, proposal.body["excerpt"]


async def test_more_than_the_cap_is_cut_at_the_cap_not_loosened(safe_gate) -> None:
    """模型给四条**互不重复**的合法观测只留 ``MAX_ITEMS`` 条：上限是常量，不是 prompt 里的一句商量。

    四条必须四条都不同：拿「三条＋一条复读」当输入时去重先把复读吃掉，
    剩下的正好等于上限，那条测就变成了去重的证人而不是上限的证人（变异 M2 实测打不红）。
    """
    proposals, _ = await run_handler(reply=reply_of(ITEM_A, ITEM_B, ITEM_C, ITEM_D))
    assert len(proposals) == MAX_ITEMS, [p.body["observation"] for p in proposals]
    assert len({p.item_key for p in proposals}) == MAX_ITEMS, "序号也没了＝上限变成随便挑三条"


async def test_duplicate_observation_is_collapsed_before_the_cap(safe_gate) -> None:
    """同一条观测被说两遍只算一条——否则"三条上限"会被一次复读吃满。"""
    proposals, _ = await run_handler(reply=reply_of(ITEM_A, ITEM_A, ITEM_B, ITEM_C))
    assert [p.body["observation"] for p in proposals] == sorted(
        [ITEM_A["observation"], ITEM_B["observation"], ITEM_C["observation"]]
    )


async def test_overlong_observation_is_dropped_not_truncated() -> None:
    """截断会造出一句用户没说过、又指不回原文的话，所以整条丢（prompts/observation.py 的口径）。"""
    long_item = {**ITEM_A, "observation": "很" * (MAX_OBSERVATION_CHARS + 1)}
    assert parse_items(reply_of(long_item, ITEM_B), CONTENT) == (to_item(ITEM_B),)


@pytest.mark.parametrize("bad_type", ["attachment_style", "MBTI", ""])
def test_unknown_evidence_type_never_reaches_the_mapping_table(bad_type: str) -> None:
    """模型换个词表外类型：丢这一条，而不是 ``WIRE_EVIDENCE_TYPE[...]`` 抛 KeyError 崩掉整段。"""
    items = parse_items(reply_of({**ITEM_A, "evidence_type": bad_type}, ITEM_B), CONTENT)
    assert items == (to_item(ITEM_B),)


def test_parse_items_orders_by_content_not_by_model_output_order() -> None:
    """候选 id 由 ``item_key`` 派生，而 ``item_key`` 由观测正文派生：
    模型的输出顺序若影响 id，inbox 的幂等键就白设了。"""
    forward = parse_items(reply_of(*LEGAL_THREE), CONTENT)
    backward = parse_items(reply_of(*reversed(LEGAL_THREE)), CONTENT)
    assert [i.observation for i in forward] == [i.observation for i in backward]
    assert item_key(SOURCE_REF, forward[0]) == item_key(SOURCE_REF, backward[0])


# ── 验收④：prompt_version / model_id 必填，候选形状就是 proto 那一条 ─────────


async def test_prompt_version_and_model_id_are_written_into_every_candidate(safe_gate) -> None:
    proposals, _ = await run_handler()
    assert proposals, "没有候选时这条验收会空跑"
    for proposal in proposals:
        meta = metadata_of(proposal)
        assert meta["promptVersion"] == PROMPT_VERSION, meta
        assert meta["modelId"] == MODEL, meta
        # 模型自报的强度只作输入留痕：终局分由 Go 算（03 §5）
        assert meta["llmStrength"] in {"low", "medium", "high"}, meta
        assert meta["observationType"] in OBSERVATION_TYPES, meta
        assert meta == json.loads(json.dumps(meta, sort_keys=True)), "键序要稳定：重跑字节不许变"


async def test_candidate_body_keys_are_exactly_the_evidence_proto_fields(safe_gate) -> None:
    """多一个键＝Go 的 protojson 拒收（``candidate_decode.go:17-20`` 默认拒绝未知字段）。"""
    proposals, _ = await run_handler()
    for proposal in proposals:
        extra = set(proposal.body) - CANDIDATE_BODY_KEYS["evidence"]
        assert not extra, sorted(extra)
        assert "safety" not in proposal.body, "EvidenceCandidate 没有 safety 这一列"
    assert "safety" not in CANDIDATE_BODY_KEYS["evidence"], "proto 加了这列要连着改 handler"


async def test_candidates_are_evidence_kind_and_carry_the_source_pointer(safe_gate) -> None:
    proposals, _ = await run_handler()
    for proposal in proposals:
        assert proposal.kind == "evidence"
        assert proposal.body["source"] == {
            "sourceTable": "memories",
            "sourceId": MEMORY_ID,
            "occurredAt": OCCURRED_AT,
        }
        assert proposal.body["generationRunId"] == RUN_ID
        assert proposal.body["schemaVersion"] == "1"
        assert proposal.body["sourceKind"] == "SOURCE_KIND_AI_EXTRACTION"
        assert proposal.body["role"] in {"supporting", "contradicting"}
        assert proposal.item_key, "没有序号的多条候选会被 pipeline 整批拒掉"


async def test_command_occurred_at_beats_the_row_timestamp(safe_gate) -> None:
    """``sourceOccurredAt`` 带了就用带的：那是发射方登记的观测时刻。"""
    handler, _ = make_handler()
    proposals = await handler(make_command(**{SOURCE_OCCURRED_AT_FIELD: "2026-09-01T00:00:00Z"}))
    assert proposals[0].body["source"]["occurredAt"] == "2026-09-01T00:00:00Z"


async def test_score_bearing_gears_come_from_the_table_not_from_the_model(safe_gate) -> None:
    """``explicitness``/``specificity`` 是算分因子，模型想升档只需改一个字符串，
    所以这两个值只认映射表；模型自报的 strength 留在 metadata 里不参与判定。"""
    proposals, _ = await run_handler(reply=reply_of({**ITEM_A, "strength": "high"}))
    body = proposals[0].body
    assert body["explicitness"] == EXPLICITNESS_BY_TYPE[ITEM_A["evidence_type"]]
    assert body["specificity"] == SPECIFICITY_BY_TYPE[ITEM_A["evidence_type"]]
    assert body["evidenceType"] == WIRE_EVIDENCE_TYPE[ITEM_A["evidence_type"]]
    assert metadata_of(proposals[0])["llmStrength"] == "high", "自报值只留痕，不进那两个档位"


async def test_missing_run_id_or_model_id_is_swallowed_not_published(safe_gate) -> None:
    """空 ``generationRunId`` 在 Go 侧是必填列缺失（service_evidence.go:196-198）：
    与其投一条注定进 DLQ、reason 还指着 Go 的字节，不如在这里咽掉并留下自己的 reason。"""
    for over in ({"run_id": ""}, {"model": ""}):
        handler, _ = make_handler(**over)
        assert await handler(make_command()) == (), over


# ── 验收②：读不到原文 ⇒ 零候选＋原因 ────────────────────────────────────────


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"source": None}, "source_missing"),
        ({"source": SourceText(content="   ")}, "source_missing"),
        ({"source": SourceText(content="只有短短一句", language="zh")}, "source_too_short"),
        ({"extracted": True}, "already_extracted"),
    ],
)
async def test_reasons_are_logged_verbatim(safe_gate, caplog, over, reason) -> None:
    """四种"没抽出来"在指标里长得一模一样，只有 reason 能把它们分开。"""
    handler, spy = make_handler(**over)
    with caplog.at_level(logging.INFO, logger="backend.ai.mq.handlers.observation"):
        proposals = await handler(make_command())
    assert proposals == ()
    assert f"reason={reason}" in caplog.text, caplog.text
    assert not spy.prompts, "成本闸生效时不该问模型"


async def test_command_without_source_ref_is_acked_with_a_reason(safe_gate, caplog) -> None:
    handler, spy = make_handler()
    with caplog.at_level(logging.INFO, logger="backend.ai.mq.handlers.observation"):
        proposals = await handler(make_command(**{SOURCE_REF_FIELD: ""}))
    assert proposals == ()
    assert "reason=source_ref_absent" in caplog.text
    assert spy.calls == [], "没有来源就读库、读配额，是给一条空命令记账"


async def test_short_source_gate_has_two_witnesses_at_the_threshold(safe_gate, caplog) -> None:
    """``MIN_CONTENT_CHARS`` 就是工作单 §怎么做.7 那个 30：30 字过、29 字不过。

    两侧都要证人：只测「短的被拦」这条闸，把常量改成 0 也不会红——那一格拦的是
    「一句话不值得一次归纳」，也是这条链唯一的全局成本闸（配额计数器不数它自己的调用）。
    """
    item = {
        "evidence_type": "stated_preference",
        "role": "supporting",
        "excerpt": "一",
        "observation": "连续写同一个字",
        "strength": "high",
    }
    for size, passes in ((MIN_CONTENT_CHARS, True), (MIN_CONTENT_CHARS - 1, False)):
        caplog.clear()
        handler, spy = make_handler(
            source=SourceText(content="一" * size, language="zh"), reply=reply_of(item)
        )
        with caplog.at_level(logging.INFO, logger="backend.ai.mq.handlers.observation"):
            proposals = await handler(make_command())
        assert (proposals != ()) is passes, f"{size} 字应当{'过' if passes else '不过'}长度闸"
        assert (bool(spy.prompts)) is passes, f"{size} 字{'该' if passes else '不该'}问模型"
        assert ("reason=source_too_short" in caplog.text) is (not passes), size


# ── 验收③：crisis ⇒ 0 候选 + 安全事件 ───────────────────────────────────────


async def test_crisis_source_produces_no_candidates_and_one_safety_event(safe_gate) -> None:
    handler, spy = make_handler(
        source=SourceText(content=CRISIS_CONTENT, occurred_at=OCCURRED_AT, language="zh")
    )
    proposals = await handler(make_command())
    assert proposals == ()
    assert spy.prompts == [], "危机走的是关怀链路，不是再抽一次证据"
    assert len(spy.safety_events) == 1, spy.safety_events
    user_id, verdict, language, excerpt = spy.safety_events[0]
    assert (user_id, language) == (USER_ID, "zh")
    assert verdict["status"] == "crisis" and verdict["categories"] == ["self_harm"], verdict
    assert verdict["matched"], "命中词要带回去，否则审计行只剩一个类别没有依据"
    assert excerpt == CRISIS_CONTENT


async def test_unsafe_source_is_dropped_without_a_safety_event(mock_llm) -> None:
    """黑名单／模型判 unsafe 的**非危机**内容不许冒充危机（D-G17 的四态就是为这一格补的）。"""
    mock_llm.state["complete_text"] = "UNSAFE"
    handler, spy = make_handler()
    assert await handler(make_command()) == ()
    assert spy.safety_events == []
    assert spy.prompts == []


async def test_safety_unavailable_is_transient_not_a_pass(mock_llm) -> None:
    """D-8：拿不到判定＝不放行。安静 ACK 会让"分类器挂了就不测了"变成常态。"""
    mock_llm.state["complete_text"] = "MOCK_LLM_REPLY"  # 读不出 SAFE 也读不出 UNSAFE
    handler, _ = make_handler()
    with pytest.raises(pipeline.TransientError):
        await handler(make_command())


async def test_llm_failure_and_garbage_are_transient(safe_gate) -> None:
    with pytest.raises(pipeline.TransientError, match="model_unavailable"):
        await run_handler(reply=TimeoutError("provider 5s 没回"))
    with pytest.raises(pipeline.TransientError, match="model_unparseable"):
        await run_handler(reply="我觉得这条记录说明他有焦虑症。")
    with pytest.raises(pipeline.TransientError, match="model_unparseable"):
        await run_handler(reply='{"items": "不是数组"}')


async def test_quota_exhausted_retries_rather_than_disappears(safe_gate) -> None:
    with pytest.raises(pipeline.TransientError, match="quota_exhausted"):
        await run_handler(quota=False)


async def test_one_bad_observation_does_not_burn_the_whole_batch(safe_gate, caplog, mock_llm) -> None:
    """单条过不了闸只丢那一条：一句话说错不该把同一次生成里其它站得住的观察一起烧掉。"""

    def classify(prompt: str, **_: Any) -> str:
        return "UNSAFE" if "把熬夜说成习惯" in prompt else "SAFE"

    mock_llm.state["complete_fn"] = classify
    handler, spy = make_handler()
    with caplog.at_level(logging.INFO, logger="backend.ai.mq.handlers.observation"):
        proposals = await handler(make_command())
    assert [p.body["observation"] for p in proposals] == [ITEM_A["observation"]]
    assert "reason=observation_dropped:unsafe" in caplog.text
    assert spy.safety_events == [], "单条被闸掉不是危机"


# ── T2-04 §怎么做.5：``evidence_extracted_total`` 的三态与「每命令恰好一次」───

METRIC_LOGGER = "backend.ai.mq.handlers.observation"


def metric_lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, str]]:
    """把这一轮打出的抽取计数行读成字段表。

    只认 ``event=<METRIC_EVENT> `` 开头那一行：同一次运行里还有 ``event=observation_extract``
    的留痕行，把它们一起算进「每命令一次」这条判据就成了数错东西。
    """
    lines = []
    for record in caplog.records:
        if record.name != METRIC_LOGGER:
            continue
        text = record.getMessage()
        if not text.startswith(f"event={METRIC_EVENT} "):
            continue
        lines.append(dict(re.findall(r"(\w+)=(\S+)", text)))
    return lines


@pytest.mark.parametrize(
    ("over", "want_result", "want_proposals"),
    [
        ({}, "ok", 2),
        ({"source": None}, "insufficient", 0),
        ({"extracted": True}, "insufficient", 0),
        ({"reply": '{"items": []}'}, "insufficient", 0),
        ({"reply": reply_of(ITEM_FAKE, {**ITEM_A, "excerpt": "改写过的原话"})}, "invalid", 0),
    ],
)
async def test_extracted_metric_reports_each_outcome_exactly_once(
    safe_gate, caplog, over, want_result, want_proposals
) -> None:
    """三态互斥且**每条命令一行**：多一行＝计数虚高，少一行＝某一类产出在曲线上消失。

    ``invalid`` 那一格是给「模型全在编」用的：它给的条目全被校验闸掉，产出同样是零，
    但零产出有两种含义，只有把这两种分开，运维才知道该改 prompt 还是该收成本闸。
    """
    handler, _ = make_handler(**over)
    with caplog.at_level(logging.INFO, logger=METRIC_LOGGER):
        proposals = await handler(make_command())
    lines = metric_lines(caplog)
    assert len(lines) == 1, f"计数行数={len(lines)}：{lines}"
    assert lines[0]["result"] == want_result, lines[0]
    assert lines[0]["proposals"] == str(want_proposals), lines[0]
    assert lines[0]["command"] == "cmd-obs-0001", lines[0]
    assert lines[0]["user"] == USER_ID, lines[0]
    assert len(proposals) == want_proposals, [p.body["excerpt"] for p in proposals]


async def test_transient_outcomes_emit_no_metric_line(safe_gate, caplog) -> None:
    """配额满／答非所问**不计数**（§口径.4）：那一次抽取没跑完。

    把它们计进 ``insufficient`` 会把「今天没给这个用户跑提取」写成「跑了但没结果」，
    而前者的正确归宿是 DLQ＋重放工具（T2-05），后者的归宿是调 prompt。
    """
    for over in ({"quota": False}, {"reply": "我觉得这条记录说明他有焦虑症。"}):
        caplog.clear()
        handler, _ = make_handler(**over)
        with caplog.at_level(logging.INFO, logger=METRIC_LOGGER), pytest.raises(
            pipeline.TransientError
        ):
            await handler(make_command())
        assert metric_lines(caplog) == [], over


def test_metric_result_vocabulary_is_exactly_the_three_named_buckets() -> None:
    """``METRIC_RESULTS`` 是封闭面：多一个值没人查询它，少一个值那一类产出会静默消失。"""
    assert METRIC_RESULTS == frozenset({"ok", "insufficient", "invalid"})


def test_answered_items_counts_before_the_validation_gate() -> None:
    """``invalid`` 的分界**必须**取校验前的条数：``parse_items`` 之后这两种零产出同形。"""
    assert answered_items('{"items": []}') == 0
    assert answered_items(reply_of(ITEM_FAKE)) == 1
    assert answered_items(reply_of(ITEM_A, ITEM_FAKE)) == 2
    # 正向证人：同一条回答里，校验后只剩 1 条——两个数不同才说明上面那格没退化成 len(items)
    assert len(parse_items(reply_of(ITEM_A, ITEM_FAKE), CONTENT)) == 1


# ── D-R5-5 的序号：内容派生 ⇒ 重跑同 id、换顺序也同 id ──────────────────────


async def test_item_key_is_stable_across_retries_and_shuffled_output(safe_gate) -> None:
    first, _ = await run_handler()
    second, _ = await run_handler(reply=reply_of(ITEM_B, ITEM_FAKE, ITEM_A))
    assert {p.item_key for p in first} == {p.item_key for p in second}


def test_item_key_shape_carries_source_and_wire_type_and_content() -> None:
    """三段各有用处：带来源＝两条命令抽同一句话不会互相吃掉；带线上类型＝
    同一观测换个可靠度档不会被当成同一条；带正文哈希＝与输出顺序、与重试次数都无关。"""
    key = item_key(SOURCE_REF, to_item(ITEM_A))
    source, wire, digest = key.split("|")
    assert (source, wire) == (SOURCE_REF, WIRE_EVIDENCE_TYPE[ITEM_A["evidence_type"]])
    assert len(digest) == 16 and re.fullmatch(r"[0-9a-f]{16}", digest), digest
    assert item_key(f"memories:{'0' * 36}", to_item(ITEM_A)) != key
    # 只有观测正文变了才会换号：strength／模型自报措辞都不参与
    assert item_key(SOURCE_REF, to_item({**ITEM_A, "strength": "low"})) == key


async def test_pipeline_publishes_every_candidate_with_a_distinct_id(safe_gate) -> None:
    """正向证人：多条候选**能**发出去。只判"多条被拒"的闸会把"支持多条"扫成空气。"""
    registry = Registry()
    handler, _ = make_handler(reply=reply_of(ITEM_A, ITEM_B, ITEM_FAKE))
    registry.register("observation", handler)
    # 期望的序号取自 handler 自己（候选 id 的尾巴由它派生）；两次调用之间排序是确定的，
    # 这条断言才不是在拿"实现恰好同序"当证据。
    expected = [p.item_key for p in await handler(make_command())]
    assert len(expected) == 2 and all(expected), expected
    pub = SpyPublisher()
    decision = await pipeline.run(
        make_command(), registry=registry, publisher=pub, max_body_bytes=64 * 1024
    )
    assert decision.action == pipeline.ACK and decision.reason == pipeline.REASON_PUBLISHED
    assert len(pub.published) == 2, [m.routing_key for m in pub.published]
    assert len(decision.candidates) == 2
    assert len({m.message_id for m in pub.published}) == 2
    for message, key in zip(pub.published, expected):
        assert message.routing_key == "candidate.evidence"
        assert message.message_id == candidate_message_id("cmd-obs-0001", key)
    # 与「没有序号」那条老 id 不同：尾巴确实带上了内容，否则 inbox 会判成同一条
    assert pub.published[0].message_id != candidate_message_id("cmd-obs-0001")


async def test_pipeline_rejects_candidates_without_an_item_key(safe_gate) -> None:
    """没有序号的多条候选会拿到同一个 message_id ⇒ 第二条起被 Go 的 inbox 静默判成重复：
    出网前就拦（``pipeline.py`` 第 6 步的**缺序号**那一格）。

    ⚠️ 断言里必须带上 ``detail``：两条都缺序号时，「同一个 item_key」那一格也会拦它
    （两个空串当然是"同一条观测"），只比 ``reason`` 的测无法区分这两格——
    2026-10-18 变异 M15 摘掉前者、后者照样放行断言，就是踩的这一点。
    """
    registry = Registry()
    body = candidate_body(
        command=make_command(),
        source=SourceText(content=CONTENT, language="zh"),
        source_ref=SOURCE_REF,
        item=to_item(ITEM_A),
        generation_run_id=RUN_ID,
        model_id=MODEL,
    )

    async def handler(command: Command) -> tuple[Proposal, ...]:
        return (Proposal(kind="evidence", body=body), Proposal(kind="evidence", body=body))

    registry.register("observation", handler)
    pub = SpyPublisher()
    decision = await pipeline.run(
        make_command(), registry=registry, publisher=pub, max_body_bytes=64 * 1024
    )
    assert decision.action == pipeline.REJECT
    assert decision.reason == REASON_MULTI_CANDIDATE
    assert "没有 item_key" in decision.detail, decision.detail
    assert pub.published == []


async def test_pipeline_rejects_two_candidates_claiming_the_same_item_key(safe_gate) -> None:
    """两条候选各自有序号、但**报了同一个序号**：同 id＝第二条起被 inbox 判成重复。

    这一格拦的是「序号没带上区分它们的内容」——两条不同的观测共用一个序号，
    比两条都没有序号更早发生（有 ``item_key`` 就不会走缺序号那一格），
    所以它必须有自己的一条断言与自己的一个靶。
    """
    registry = Registry()
    source = SourceText(content=CONTENT, language="zh")

    def proposal(item: dict[str, Any]) -> Proposal:
        return Proposal(
            kind="evidence",
            body=candidate_body(
                command=make_command(),
                source=source,
                source_ref=SOURCE_REF,
                item=to_item(item),
                generation_run_id=RUN_ID,
                model_id=MODEL,
            ),
            item_key="同一个序号",
        )

    async def handler(command: Command) -> tuple[Proposal, ...]:
        return (proposal(ITEM_A), proposal(ITEM_B))

    registry.register("observation", handler)
    pub = SpyPublisher()
    decision = await pipeline.run(
        make_command(), registry=registry, publisher=pub, max_body_bytes=64 * 1024
    )
    assert decision.action == pipeline.REJECT
    assert decision.reason == REASON_MULTI_CANDIDATE
    assert "同一个 item_key" in decision.detail, decision.detail
    assert pub.published == []


# ── 跨语言/跨层一致性：映射表必须同时齐于模型面取值、Go 准入面和 proto 枚举 ────


def test_mapping_tables_cover_every_observation_type() -> None:
    """少一格不是"那条被丢掉"，而是 ``WIRE_EVIDENCE_TYPE[...]`` 抛 KeyError：
    一次正常生成变成 handler 崩溃＋整条命令上重试阶梯。"""
    for name, table in MAPPING_TABLES.items():
        assert set(table) == set(OBSERVATION_TYPES), name


def test_wire_evidence_types_are_in_go_allowed_origins() -> None:
    """Go 的 ``checkEvidenceCandidate`` 对不在这六值里的 evidence_type **永久拒绝**
    （service_evidence.go:207）；映射结果落在外面就是每条候选都进 DLQ。"""
    src = (ROOT / "go-core" / "internal" / "domain" / "gate.go").read_text(encoding="utf-8")
    block = src[src.index("var allowedOrigins") :]
    allowed = set(re.findall(r'"([a-z_]+)"', block[: block.index("}\n")]))
    assert allowed == {
        "direct", "repeated", "temporal", "behavioral", "user_confirmed", "user_corrected"
    }, allowed
    assert set(WIRE_EVIDENCE_TYPE.values()) <= allowed


def test_explicitness_and_specificity_are_proto_enum_names() -> None:
    """枚举名写错＝protojson 解不进这两个字段，而那在对面的报错是"解码失败"而不是"档位不对"。

    判据是**子集**而不是相等，因为 proto 里有四档显隐度、本表只产三档：
    ``EXPLICITNESS_SPECULATIVE``（03:132 的 0.10 因子）对应「没有原文支撑的推断」，
    而这一条链的 ``excerpt`` 必须是用户原文的逐字子串（``prompts/observation.py`` 的
    VERBATIM 规则＋:func:`parse_items` 的子串校验），产不出这一档。
    它留给框架推断那一路；这里改成相等断言会把那次改动误报成 bug。
    """
    proto = (ROOT / "contracts" / "proto" / "closenai_ai.proto").read_text(encoding="utf-8")
    emitted: dict[str, set[str]] = {}
    for enum_name, prefix, table in (
        ("Explicitness", "EXPLICITNESS_", EXPLICITNESS_BY_TYPE),
        ("Specificity", "SPECIFICITY_", SPECIFICITY_BY_TYPE),
    ):
        block = proto[proto.index(f"enum {enum_name}") :]
        block = block[: block.index("\n}\n")]
        values = set(re.findall(rf"\b({prefix}[A-Z_]+) = \d+;", block))
        real = values - {f"{prefix}UNSPECIFIED"}
        assert real, f"{enum_name} 没解析出任何枚举名（解析式写坏了）"
        unknown = set(table.values()) - values
        assert not unknown, f"{enum_name}：本表发出对面枚举面里没有的名字 {sorted(unknown)}"
        emitted[enum_name] = real - set(table.values())
    # 反向证人：确实有档位不产，且它们正是上面那段说明指名的那些档。
    assert emitted == {"Explicitness": {"EXPLICITNESS_SPECULATIVE"}, "Specificity": set()}, (
        "不产的档位变了：同步改这段说明与它指名的那条 VERBATIM 规则（03:132 的 0.10 因子）"
    )


async def test_negative_samples_go_into_the_prompt_and_never_into_a_candidate(safe_gate) -> None:
    """负样本是"别再这样归纳"的指令，不是用户写过的原话：进了候选就成了编造的证据。"""
    corrections = ("我不是完美主义者", "不要把加班说成热爱")
    handler, spy = make_handler(corrections=corrections)
    proposals = await handler(make_command())
    prompt = spy.prompts[0]
    for text in corrections:
        assert text in prompt
        assert prompt.index(text) < prompt.index(CONTENT), text
    for proposal in proposals:
        assert "完美主义" not in json.dumps(proposal.body, ensure_ascii=False)


async def test_without_negative_samples_the_prompt_still_asks(safe_gate) -> None:
    handler, spy = make_handler(corrections=())
    assert await handler(make_command()) != ()
    assert "corrected or rejected" not in spy.prompts[0]


def test_prompt_carries_the_verbatim_rule_and_the_json_shape() -> None:
    prompt = build_prompt(CONTENT, negatives=())
    assert "VERBATIM substring" in prompt
    assert '"items":[' in prompt
    assert f"at most {MAX_ITEMS}" in prompt
    for value in OBSERVATION_TYPES:
        assert value in prompt, value
    assert SYSTEM_PROMPT.strip() and "JSON" in SYSTEM_PROMPT


# ── 组合根（验收⑤）：注册面恰好是 observation，而且不在 import 时发生 ────────


def test_production_registry_registers_only_observation() -> None:
    registry = ai_observation.build_registry(Registry())
    assert registry.registered() == ("observation",), registry.registered()


def test_the_other_three_kinds_still_have_no_handler() -> None:
    """本批**只**交一段：pattern／insight／proactive 仍按 ``handler_unregistered`` 进 DLQ。
    这条是"零流量"新口径的第二证人（第一证人在 ``test_go_task_command_body_accepted.py``）。"""
    registry = ai_observation.build_registry(Registry())
    for kind in ("pattern", "insight", "proactive"):
        with pytest.raises(ContractError) as exc:
            registry.handler_for(kind)
        assert exc.value.reason == "handler_unregistered", kind


def test_registration_happens_inside_a_function_body_not_at_import_time() -> None:
    """工作单逐字：「**不在模块 import 时注册**」。判据取 AST 顶层**会执行**的语句：
    把 ``registry.register(...)`` 挪到模块级就红，留在函数体里（``build_registry``／
    ``build_production_registry``）就绿——注册表是显式传入的对象，不是导入副作用。"""
    offenders: list[str] = []
    for path in (HANDLER_FILE, WORKER_FILE, SERVICES_FILE):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        hits = [node.lineno for node in _import_time_nodes(tree) if _has_register_call(node)]
        if hits:
            offenders.append(f"{path.name}:{hits}")
    assert not offenders, f"模块级注册了 handler：{offenders}"
    # 正向证人：整条链一次注册都没有＝上面那个判据在扫空集。
    total = 0
    for path in (WORKER_FILE, SERVICES_FILE):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        total += sum(1 for sub in ast.walk(tree) if _has_register_call(sub))
    assert total >= 2, f"组合根里没有 register 调用（注册接线被删了？）：{total}"


def test_ai_layer_still_never_imports_the_service_layer() -> None:
    """方向闸：跨层 import 只许出现在 ``worker.build_production_registry()`` 的函数体里。

    三种情形分开判，因为它们坏的是不同的东西：
    ① ``mq/handlers/`` 与 ``prompts/`` 里**任何一级**出现 ``backend.services`` ⇒ 红——
       这一层只能经 ``ObservationPorts`` 说话，一旦 import 到读库代码，
       「用假端口把每条判据跑红」和「AI 能力面可分进程部署」同时失效；
    ② ``worker.py`` 的模块顶层出现它 ⇒ 红——那会让「开关关 ⇒ 零副作用」变成
       「import 了这个模块就连上了 SQLAlchemy」；
    ③ ``rpc/`` 不在本闸管辖内：R3 结案口径里它按分层闸（只禁
       ``routers/repositories/models/database``）走 ``backend.services``，那是既有接线，
       本批不顺手改。
    """
    ai_dir = ROOT / "backend" / "ai"
    pure = [ai_dir / "mq" / "handlers" / "observation.py", ai_dir / "prompts" / "observation.py"]
    for path in pure:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        hits = [
            f"{path.name}:{lineno}"
            for lineno, name in _imported_names(list(ast.walk(tree)))
            if name == "backend.services" or name.startswith("backend.services.")
        ]
        assert not hits, f"能力层直接 import 了服务层（端口是唯一合法出口）：{hits}"

    worker_tree = ast.parse(WORKER_FILE.read_text(encoding="utf-8"))
    top_hits = [
        f"worker.py:{lineno}"
        for lineno, name in _imported_names(_import_time_nodes(worker_tree))
        if name == "backend.services" or name.startswith("backend.services.")
    ]
    assert not top_hits, f"worker 在模块级 import 了服务层：{top_hits}"
    # 正向证人：函数体里那**一处**必须还在——删了它，worker 就又变成零 handler 的进程，
    # 而上面两条判据会照样全绿。
    nested_hits = [
        f"worker.py:{lineno}"
        for lineno, name in _imported_names(list(ast.walk(worker_tree)))
        if name == "backend.services" or name.startswith("backend.services.")
    ]
    assert len(nested_hits) == 1, (
        f"跨层 import 应当恰好一处、且在组合根的函数体里，实际 {nested_hits}"
    )


def test_build_ports_fills_every_declared_port() -> None:
    """端口表少一格＝``ObservationPorts`` 构造期就抛；这条判的是生产真填满了 AI 层声明的每一格。"""
    ports = ai_observation.build_ports()
    for field in dataclasses.fields(ObservationPorts):
        assert callable(getattr(ports, field.name)), field.name


# ── 服务侧端口：真 SQL（sqlite 内存库，语句与生产同一份）────────────────────


@pytest.fixture
async def session_factory(monkeypatch):
    """把 ``ai_observation`` 的会话工厂换成内存库；被测 SQL 与 ORM 条件一行没换。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    monkeypatch.setattr(ai_observation, "SessionLocal", factory)
    yield factory
    await engine.dispose()


async def _seed_user(session_factory, **over: Any) -> None:
    async with session_factory() as session:
        user = await user_repository.get_or_create_user(session, USER_ID)
        for key, value in over.items():
            setattr(user, key, value)
        await session.commit()


def _memory(content: str = CONTENT, **over: Any) -> Memory:
    row = Memory(id=MEMORY_ID, user_id=USER_ID, content=content, category="factual", source="self")
    for key, value in over.items():
        setattr(row, key, value)
    return row


async def test_load_source_reads_only_current_undeleted_rows_of_that_owner(session_factory) -> None:
    await _seed_user(session_factory, language="zh", timezone="Asia/Shanghai")
    async with session_factory() as session:
        session.add(_memory())
        await session.commit()

    source = await ai_observation.load_source(USER_ID, MEMORY_ID)
    assert source is not None and source.content == CONTENT
    assert source.language == "zh", "语言取 users.language，不是硬编码"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", source.occurred_at), source
    assert await ai_observation.load_source("u-other", MEMORY_ID) is None, "跨用户读不到才是对的"

    async with session_factory() as session:
        row = await session.get(Memory, MEMORY_ID)
        row.status = "superseded"
        await session.commit()
    assert await ai_observation.load_source(USER_ID, MEMORY_ID) is None, "被改写掉的旧版本不是原文"

    async with session_factory() as session:
        row = await session.get(Memory, MEMORY_ID)
        row.status = "current"
        row.deleted = True
        await session.commit()
    assert await ai_observation.load_source(USER_ID, MEMORY_ID) is None, "软删的行不许再被抽成证据"


async def test_load_source_of_missing_user_falls_back_to_default_language(session_factory) -> None:
    async with session_factory() as session:
        session.add(_memory())
        await session.commit()
    source = await ai_observation.load_source(USER_ID, MEMORY_ID)
    assert source is not None and source.language == "en", "没有用户行时用契约默认值而不是崩"


async def test_recent_corrections_keeps_only_what_the_user_actually_wrote(session_factory) -> None:
    rows = [
        ("0", "correct", "我不是完美主义者"),
        ("1", "reject", None),  # 只点了否决、没写字 ⇒ 没东西可注入
        ("2", "confirm", "对的"),  # 正向表态不是负样本
        ("3", "correct", "   "),
        ("4", "reject", "不要把加班说成热爱"),
    ]
    async with session_factory() as session:
        for index, kind, text in rows:
            session.add(
                UserFeedback(
                    id=f"uf-{index}",
                    user_id=USER_ID,
                    feedback_type=kind,
                    target_type="pattern",
                    target_id=f"pat-{index}",
                    correction_text=text,
                    metadata_json="{}",
                )
            )
        await session.commit()

    got = await ai_observation.recent_corrections(USER_ID, 20)
    assert set(got) == {"我不是完美主义者", "不要把加班说成热爱"}, got
    assert len(await ai_observation.recent_corrections(USER_ID, 1)) == 1
    assert await ai_observation.recent_corrections("u-other", 20) == ()


async def _add_admitted_evidence(session_factory, evidence_id: str, age: timedelta) -> None:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        session.add(
            SourceRecord(
                id=f"sr-{evidence_id}",
                user_id=USER_ID,
                source_type="memory",
                source_table="memories",
                source_id=MEMORY_ID,
                occurred_at=now,
            )
        )
        session.add(
            Evidence(
                id=evidence_id,
                user_id=USER_ID,
                source_record_id=f"sr-{evidence_id}",
                evidence_type="repeated",
                metadata_json="{}",
                created_at=now - age,
            )
        )
        await session.commit()


async def test_already_extracted_looks_at_evidence_of_the_same_pointer(session_factory) -> None:
    await _add_admitted_evidence(session_factory, "ev-fresh", timedelta(hours=1))
    window = timedelta(hours=24)
    assert await ai_observation.already_extracted(USER_ID, "memories", MEMORY_ID, window)
    assert not await ai_observation.already_extracted("u-other", "memories", MEMORY_ID, window)
    assert not await ai_observation.already_extracted(USER_ID, "memories", "0" * 36, window)
    assert not await ai_observation.already_extracted(
        USER_ID, "memories", MEMORY_ID, timedelta(minutes=1)
    ), "窗口判据落在 created_at 上，不是「有过就算」"


async def test_already_extracted_is_false_until_someone_admits_the_candidate(
    session_factory,
) -> None:
    """只投过候选、没被 ``AdmitEvidence`` 接纳＝没提取过：被门拒掉之后下一次值得再抽。"""
    async with session_factory() as session:
        session.add(_memory())
        await session.commit()
    assert not await ai_observation.already_extracted(
        USER_ID, "memories", MEMORY_ID, timedelta(hours=24)
    )


async def test_quota_available_uses_the_unified_pool(session_factory) -> None:
    await _seed_user(session_factory, tier="free", timezone="UTC")
    assert await ai_observation.quota_available(USER_ID)

    async with session_factory() as session:
        for index in range(5):  # settings.free_daily_messages 的默认值
            await chat_repository.create(
                session, USER_ID, "assistant", f"主动消息 {index}", message_type="proactive"
            )
        await session.commit()
    assert not await ai_observation.quota_available(USER_ID)
    assert not await ai_observation.quota_available("u-没有这个人"), "查不到用户就是不给跑"


async def test_record_safety_event_writes_an_auditable_row(session_factory) -> None:
    await _seed_user(session_factory, language="zh", timezone="Asia/Shanghai")
    verdict = {
        "status": "crisis",
        "reason": "crisis",
        "matched": ["不想活"],
        "categories": ["self_harm"],
        "advice_blocked": False,
    }
    await ai_observation.record_safety_event(USER_ID, verdict, "zh", CRISIS_CONTENT)

    async with session_factory() as session:
        rows = list((await session.scalars(select(SafetyEvent))).all())
    assert len(rows) == 1, rows
    row = rows[0]
    assert row.user_id == USER_ID
    assert row.kind == crisis_kind_of(verdict) == "self_harm"
    assert row.source == "observation_extract"
    assert json.loads(row.matched_terms) == ["不想活"]
    assert row.advice_blocked is False
    assert row.hotline == safety_service.HOTLINES["cn"], "热线按 users.timezone 推断"
    assert len(row.excerpt) <= 280, "隐私最小化：只留摘录，不留整段会话"


def test_crisis_kind_of_falls_back_instead_of_crashing() -> None:
    """安全事件那一格不能因为 categories 空就抛——抛了等于危机没留痕。"""
    assert crisis_kind_of({"categories": []}) == "self_harm"
    assert crisis_kind_of({"categories": ["relationship_violence"]}) == "relationship_violence"


async def test_extract_purpose_and_system_prompt_are_what_the_port_passes(mock_llm) -> None:
    """``model_id()`` 必须与 :func:`ai_observation.complete` 那次调用同档，
    否则留痕里的模型名指着一条根本没跑过的模型。"""
    await ai_observation.complete("一行提示词", "zh")
    call = mock_llm.calls["complete"][-1]
    assert call["purpose"] == ai_observation.EXTRACT_PURPOSE
    assert call["system_prompt"] == SYSTEM_PROMPT
    assert call["temperature"] == 0.0
    assert "lite" not in call, "不传 lite＝主模型档，与 model_id() 的默认读取同档"
    assert ai_observation.model_id() == ai_observation.llm.get_model()


def _has_register_call(node: ast.stmt) -> bool:
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "register"
        ):
            return True
    return False


#: 函数／类的身体在 import 时**不执行**，所以里面的调用不算「导入副作用」。
DEF_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _import_time_nodes(tree: ast.Module) -> list[ast.AST]:
    """模块顶层、import 那一刻就会跑的节点。

    少了这一层区分，判据会把本批的**设计**（组合根 ``build_registry`` 函数体里那句
    ``registry.register``）当成违规——那是一条永远绿不了、也永远不代表正确性的测。
    装饰器与默认参数值确实是在 import 时求值的，所以它们留在扫描面里。
    """
    out: list[ast.AST] = []
    for stmt in tree.body:
        if isinstance(stmt, DEF_NODES):
            out.extend(stmt.decorator_list)
        else:
            out.append(stmt)
    return out


def _imported_names(nodes: list[ast.AST]) -> list[tuple[int, str]]:
    """这些节点里真实发生的 import（绝对名；``(行号, 名字)`` 去重，因为一条语句
    可能同时被它的父节点与自身 ``ast.walk`` 到）。"""
    out: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for node in nodes:
        for sub in ast.walk(node):
            found: list[str] = []
            if isinstance(sub, ast.Import):
                found = [alias.name for alias in sub.names]
            elif isinstance(sub, ast.ImportFrom):
                found = [sub.module or ""]
            for name in found:
                if (sub.lineno, name) not in seen:
                    seen.add((sub.lineno, name))
                    out.append((sub.lineno, name))
    return out
