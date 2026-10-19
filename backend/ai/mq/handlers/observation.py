"""``observation.extract`` 的 handler（T2-02）：一行用户原文 → 最多三条证据提案。

这里是 **AI 能力侧**，所以它只做四件事：读原文（经端口）、问模型、把回答校验成候选、
把不合格的答案如实咽掉。它**不写任何权威表**——候选出网后由 Go 的 ``AdmitEvidence``
核验归属、算终局分、落 ``source_records``／``evidence``（不变量 7「只有 domain 能改 canonical 态」）。

端口（``ObservationPorts``）而不是直接 import 仓储，是这一层能同时满足两条纪律的关键：
* ``test_ai_boundary.py`` 禁止 ``backend/ai/`` import ``backend.models``/``repositories``/``database``；
* 但 V3 的「有界上下文」（12:84-97）不等于「读不到自己该读的那一行」——
  ``TaskRequested.sourceRef`` 点名的就是**一行**原文：读它，而不是读这个用户的全部历史。
于是读库代码住在 ``backend/services/ai_observation.py``（组合根在 worker 的函数体里装配），
本文件只声明端口形状。假端口就能把下面每一条判据跑红，不需要 LLM、不需要库。

四条「咽掉」与它们各自的 reason（都进日志，因为「handler 返回空」与「handler 坏了」
在指标里长得一模一样）：

    source_ref_absent          命令没带体外来源 ⇒ 重试也变不出来，ACK 掉并留痕
    source_missing             sourceRef 指向的行读不到（删了／不是这个用户的）
    source_too_short           原文短于 MIN_CONTENT_CHARS ⇒ 一句话不值得一次归纳
    already_extracted          同一行 24h 内提取过 ⇒ 成本闸（工作单 §怎么做.7）
    quota_exhausted            当日配额已满 ⇒ 抛瞬时错走阶梯，最后一层进 DLQ（可重放）
    crisis_source / unsafe_source  原文里有危机或违规信号 ⇒ 不产候选，只落安全事件
    model_unavailable / model_unparseable  LLM 超时或答非所问 ⇒ 瞬时错
    generation_run_missing / model_id_missing  这次生成答不出「哪跑的」⇒ 不产候选

一条**每命令恰好一次**的计数（工作单 T2-04 §怎么做.5，口径见台账 §口径.4）住在
``_metric``：``event=evidence_extracted_total result=ok|insufficient|invalid``。
三态按「这一次抽取的产出」划分且互斥——``ok``＝产出 ≥1 条候选，``invalid``＝模型给了
东西但**全部**被校验闸丢掉（幻觉摘录的形状），``insufficient``＝其余零产出。
配额满那种瞬时失败**不计数**：它没走完这次抽取，计进去会把「今天没跑」读成「跑了但没结果」。
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Mapping

from backend.ai.mq.contract import (
    SOURCE_OCCURRED_AT_FIELD,
    SOURCE_REF_FIELD,
    Command,
    split_source_ref,
)
from backend.ai.mq.pipeline import TransientError
from backend.ai.mq.registry import Proposal
from backend.ai.prompts.observation import (
    PROMPT_VERSION,
    ObservationItem,
    answered_items,
    build_prompt,
    parse_items,
)
from backend.ai.safety import (
    SAFETY_CRISIS,
    SAFETY_OK,
    SAFETY_UNAVAILABLE,
    SAFETY_UNSAFE,
    safety_service,
)

log = logging.getLogger("backend.ai.mq.handlers.observation")

#: 原文短到多少字就不值得跑一次提取（工作单 §怎么做.7：「只对 len(content) >= 30 触发」）。
MIN_CONTENT_CHARS = 30

#: 同一行原文重复提取的成本窗口（工作单 §怎么做.7：「24h 内未提取过」）。
REEXTRACT_WINDOW = timedelta(hours=24)

#: 负样本条数（工作单 §怎么做.5：最近 20 条 correct/reject）。
NEGATIVE_SAMPLE_LIMIT = 20

#: 证据提案的可靠度来源：这一次是**AI 从用户原文里抽**，不是用户自己确认过的。
#: 固定一个常量而不是按类型挑——`source_kind` 是算分五因子之一（03 §5），
#: 让 handler 有机会「选一个更高分的档」＝提案方在给算分挑档位，而那正是 V3 不采信的。
SOURCE_KIND = "SOURCE_KIND_AI_EXTRACTION"

#: 模型面的五值 → 线上 ``evidenceType``（Go `gate.go:60-62` 的 allowedOrigins 六值）。
#: 为什么必须有这张表：`checkEvidenceCandidate`（service_evidence.go:207）对不在六值里的
#: evidence_type **永久拒绝**，模型面五值有四个今天会直接撞在那道上。
#: 映射只看「这条观测的可靠度形态」，不看分数：direct/repeated 两档给的是
#: 「用户明说」与「记录里反复出现」，temporal/behavioral 给的是「跨时间的关系」与
#: 「行为／情绪信号」——原始五值同时留在 ``metadata_json.observationType`` 里，不丢信息。
WIRE_EVIDENCE_TYPE: Mapping[str, str] = {
    "stated_preference": "direct",
    "value_statement": "direct",
    "repeated_behavior": "repeated",
    "relationship_pattern": "temporal",
    "emotional_pattern": "behavioral",
}

#: 显隐度／具体度：这两个**进算分**（explicitnessFactor/specificityFactor），
#: 所以由这张表给而不是由模型自报——模型想要「更明确一点」只需要改一个字符串，
#: 而这里改档位要改这张表，改表就过一次评审。
EXPLICITNESS_BY_TYPE: Mapping[str, str] = {
    "stated_preference": "EXPLICITNESS_EXPLICIT",
    "value_statement": "EXPLICITNESS_EXPLICIT",
    "repeated_behavior": "EXPLICITNESS_STRONGLY_IMPLIED",
    "emotional_pattern": "EXPLICITNESS_STRONGLY_IMPLIED",
    "relationship_pattern": "EXPLICITNESS_WEAKLY_IMPLIED",
}

SPECIFICITY_BY_TYPE: Mapping[str, str] = {
    "stated_preference": "SPECIFICITY_SPECIFIC",
    "value_statement": "SPECIFICITY_SPECIFIC",
    "repeated_behavior": "SPECIFICITY_CONTEXTUAL",
    "emotional_pattern": "SPECIFICITY_CONTEXTUAL",
    "relationship_pattern": "SPECIFICITY_GENERIC",
}

#: 抽取计数的名字（工单 §怎么做.5 的 ``evidence_extracted_total{result=…}``）。
#: 本仓库的指标面是可 grep 的结构化日志而不是 Prometheus（对面 Go 用 ``internal/obs``
#: 的 slog，同一形状），所以这里要保住的只有两件事：名字稳定、``result`` 只有三值。
METRIC_EVENT = "evidence_extracted_total"

#: ``result`` 的封闭取值面。``_metric`` 只许写这三个之一：多一个值＝Loki 那条
#: 按 result 分组的查询多出一个没人认领的桶，少一个值＝某一类产出在账面上消失。
METRIC_RESULTS: frozenset[str] = frozenset({"ok", "insufficient", "invalid"})


@dataclass(frozen=True)
class SourceText:
    """``sourceRef`` 指向的那一行原文。

    ``occurred_at`` 是 RFC3339 字符串（可为空）：证据的时刻**只来自来源行自己**，
    拿消费时刻兜底会让重放的老消息被「最近 30 天」这类窗口算成新近证据。
    """

    content: str
    occurred_at: str = ""
    language: str = "en"


#: 端口全是**注入的函数**而不是继承某个基类：组合根给得出、测试塞得进假件，
#: 而「必须实现三个方法」的抽象基类只会诱导有人在这里加业务分支。
SourceLoader = Callable[[str, str], Awaitable[SourceText | None]]
CorrectionLoader = Callable[[str, int], Awaitable[tuple[str, ...]]]
ExtractedChecker = Callable[[str, str, str, timedelta], Awaitable[bool]]
QuotaGate = Callable[[str], Awaitable[bool]]
#: 落安全事件：``(user_id, 判定结果, 语言, 原文)``。类别／命中词／热线都由**服务侧**从判定结果里取
#: （那边才拿得到用户时区，而热线是按地区选的），AI 层不自己拼一条 A 级文案。
SafetyRecorder = Callable[[str, Mapping[str, Any], str, str], Awaitable[None]]
Completer = Callable[[str, str], Awaitable[str]]
RunIdReader = Callable[[], str]
ModelReader = Callable[[], str]


@dataclass(frozen=True)
class ObservationPorts:
    """``observation.extract`` 需要的全部外部能力（没有一件属于 AI 层自己）。"""

    load_source: SourceLoader
    recent_corrections: CorrectionLoader
    already_extracted: ExtractedChecker
    quota_available: QuotaGate
    record_safety_event: SafetyRecorder
    complete: Completer
    generation_run_id: RunIdReader
    model_id: ModelReader


def item_key(source_ref: str, item: ObservationItem) -> str:
    """D-R5-5 留给生产者那一批的「同一次生成内的稳定序号」（T2-02 定案）。

    形状＝``来源指针 | 线上 evidenceType | sha256(观测+摘录)[:16]``。
    为什么是这个形状：
    * 带来源 ⇒ 两条命令抽了同一句话不会互相吃掉（候选 id 的尾巴本来也含命令 id）；
    * 哈希取**观测正文** ⇒ 与模型的输出顺序无关、与重试次数无关，同一条观测重跑还是同 id，
      inbox 的 ``(message_id, consumer)`` 唯一键才真的拦得住重复；
    * 不含 ``generation_run_id``／时刻 ⇒ 那两个每次重试都变，带上就等于每次重跑都产新证据行。
    """
    digest = hashlib.sha256(f"{item.observation}\n{item.excerpt}".encode("utf-8")).hexdigest()
    return f"{source_ref}|{WIRE_EVIDENCE_TYPE[item.evidence_type]}|{digest[:16]}"


def candidate_body(
    *,
    command: Command,
    source: SourceText,
    source_ref: str,
    item: ObservationItem,
    generation_run_id: str,
    model_id: str,
) -> dict[str, Any]:
    """把一条观察组装成 ``closenai.v1.EvidenceCandidate`` 的 protojson 形态。

    ⚠️ **不带 ``safety`` 这一列**：``EvidenceCandidate`` 的字段面里没有它
    （``contract.CANDIDATE_BODY_KEYS["evidence"]`` 逐键对过 registry），而对面 protojson
    默认拒未知字段——带上它＝每条证据提案永远进 DLQ。安全判定照样在前面跑完并决定
    「产不产候选」，只是不上线承载。
    """
    table, row_id = split_source_ref(source_ref)
    occurred_at = (
        command.body.get(SOURCE_OCCURRED_AT_FIELD) or ""
    ) or source.occurred_at
    source_pointer: dict[str, Any] = {"sourceTable": table, "sourceId": row_id}
    if occurred_at:
        source_pointer["occurredAt"] = occurred_at
    metadata = {
        "promptVersion": PROMPT_VERSION,
        "modelId": model_id,
        "observationType": item.evidence_type,
        "llmStrength": item.strength,
        "extractedBy": "ai.observation.requested",
    }
    return {
        "schemaVersion": command.schema_version,
        "sourceKind": SOURCE_KIND,
        "source": source_pointer,
        "evidenceType": WIRE_EVIDENCE_TYPE[item.evidence_type],
        "role": item.role,
        "observation": item.observation,
        "excerpt": item.excerpt,
        "metadataJson": _dumps_sorted(metadata),
        "explicitness": EXPLICITNESS_BY_TYPE[item.evidence_type],
        "specificity": SPECIFICITY_BY_TYPE[item.evidence_type],
        "generationRunId": generation_run_id,
    }


def _dumps_sorted(value: Mapping[str, Any]) -> str:
    """metadata 的 JSON 文本：键序固定，好让「同一次重跑」的字节也相同。

    为什么自己排而不是 ``json.dumps`` 默认顺序：dict 插入顺序今天恰好稳定，但它是**实现细节**；
    metadata 原样进 ``evidence.metadata_json``，重跑时字节不同会让人以为产出了新内容。
    """
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class ObservationExtractHandler:
    """``async (command) -> tuple[Proposal, ...]``：注册进 ``Registry`` 的那个 handler。"""

    def __init__(self, ports: ObservationPorts) -> None:
        self._ports = ports

    async def __call__(self, command: Command) -> tuple[Proposal, ...]:
        # 计数只有一个出口：把 ``_metric`` 摊到下面十几个 return 站点上，迟早会有一格漏计
        # 或双计，而这两者在曲线长得一模一样（都是「今天的抽取数不对」）。
        proposals, result = await self._run(command)
        self._metric(command, result, len(proposals))
        return proposals

    async def _run(self, command: Command) -> tuple[tuple[Proposal, ...], str]:
        """跑完一次抽取，返回 ``(候选, result)``；``result`` 见 ``METRIC_RESULTS``。

        抛 ``TransientError`` 的路径（配额、安全判定不可用、模型超时、答非所问）不返回也不计数：
        那一次抽取没有跑完，把它计成 ``insufficient`` 等于把「今天没给这个用户跑」
        写进「跑了但没结果」那一桶。
        """
        body = command.body
        source_ref = str(body.get(SOURCE_REF_FIELD) or "").strip()
        if not source_ref:
            # 命令没带体外来源：observation 这一段除了这一行原文之外没有输入，
            # 重试也变不出来源指针 ⇒ ACK 掉并留痕（发射点该带而不带，归 T2-03/T2-04）。
            self._log(command, "source_ref_absent")
            return (), "insufficient"

        source = await self._ports.load_source(command.user_id, split_source_ref(source_ref)[1])
        if source is None or not source.content.strip():
            # 读不到＝这一行被删了、从来不存在，或不属于这个用户（端口自己按 user 筛）。
            # 不抛异常：抛了会让它撞完整条重试阶梯，而「重跑一万次」也不会让删掉的行回来。
            self._log(command, "source_missing")
            return (), "insufficient"
        if len(source.content) < MIN_CONTENT_CHARS:
            self._log(command, "source_too_short")
            return (), "insufficient"
        if await self._ports.already_extracted(
            command.user_id, *split_source_ref(source_ref), REEXTRACT_WINDOW
        ):
            self._log(command, "already_extracted")
            return (), "insufficient"
        if not await self._ports.quota_available(command.user_id):
            # 配额是按天恢复的：抛瞬时错让它走 30s/2m/10m，最后一层进 DLQ。
            # 为什么不安静默 ACK：那样「今天没给这个用户跑提取」这件事在指标里是零，
            # 而 DLQ 里躺着一堆可重放的命令——T2-05 的重放工具就是为这一格准备的。
            raise TransientError("quota_exhausted：当日 AI 配额已满")

        language = source.language or "en"
        verdict = await self._judge(source.content, language)
        if verdict["status"] == SAFETY_CRISIS:
            await self._ports.record_safety_event(
                command.user_id, verdict, language, source.content
            )
            self._log(command, "crisis_source")
            return (), "insufficient"
        if verdict["status"] == SAFETY_UNSAFE:
            self._log(command, f"unsafe_source:{verdict['reason']}")
            return (), "insufficient"
        if verdict["status"] != SAFETY_OK:
            raise TransientError(f"model_unavailable：安全判定不可用（{verdict['reason']}）")

        negatives = await self._ports.recent_corrections(command.user_id, NEGATIVE_SAMPLE_LIMIT)
        prompt = build_prompt(source.content, negatives)
        try:
            raw = await self._ports.complete(prompt, language)
        except TransientError:
            raise
        except Exception as exc:  # noqa: BLE001 — provider 的超时/5xx 都是「换个时刻可能成」
            raise TransientError(f"model_unavailable：{type(exc).__name__}: {exc}") from exc

        try:
            items = parse_items(raw, source.content)
        except ValueError as exc:
            raise TransientError(f"model_unparseable：{exc}") from exc
        # 校验**前**的条数，给下面的 ``invalid`` / ``insufficient`` 分界用（§口径.4）。
        answered = answered_items(raw)

        generation_run_id = self._ports.generation_run_id()
        if not generation_run_id:
            # 留痕 id 是 `AdmitEvidence` 的必填四列之一（service_evidence.go:190-200）：
            # 空着投出去＝每条都进 DLQ，而 DLQ 里的 reason 是 Go 的字段缺失，不是「我没留痕」。
            # 在这里咽掉，日志里留下的是本进程该负责的那件事。
            self._log(command, "generation_run_missing")
            return (), "insufficient"
        model_id = self._ports.model_id()
        if not model_id:
            # 验收条款「候选里 prompt_version/model_id 必填」的另一半：模型名读不到时
            # 不写一个空串冒充——空串会让这条证据在排障时被算成「不知道谁生成的」。
            self._log(command, "model_id_missing")
            return (), "insufficient"
        proposals: list[Proposal] = []
        for item in items:
            item_verdict = await self._judge(item.observation, language)
            if item_verdict["status"] != SAFETY_OK:
                # 单条不过闸：丢这一条，不为它重跑，也不让整条命令失败——
                # 一句话说错不该把同一次生成里其它站得住的观察一起烧掉。
                self._log(command, f"observation_dropped:{item_verdict['status']}")
                continue
            proposals.append(
                Proposal(
                    kind="evidence",
                    body=candidate_body(
                        command=command,
                        source=source,
                        source_ref=source_ref,
                        item=item,
                        generation_run_id=generation_run_id,
                        model_id=model_id,
                    ),
                    item_key=item_key(source_ref, item),
                )
            )
        # 三态的分界只看**产出**：模型一条也没给（``answered=0``）是「这段原文确实没东西」，
        # 给了却全被闸掉才是「它在编」——后者要能单独查出来，因为那是 prompt 该改的信号。
        # 判据取校验前的条数而不是 ``items``：幻觉摘录那种答案在 ``items`` 里也是零，
        # 记成「确实没东西」会让这一桶在曲线上长得像好消息。
        result = "ok" if proposals else ("invalid" if answered else "insufficient")
        self._log(command, f"extracted={len(proposals)}")
        return tuple(proposals), result

    async def _judge(self, text: str, language: str) -> Mapping[str, Any]:
        """安全判定（复用 L3 唯一事实源，scope=assertion：产出是要给用户看的断言）。"""
        return await safety_service.check_text_tri(text, language, scope="assertion")

    @staticmethod
    def _log(command: Command, reason: str) -> None:
        """一条可 grep 的机读留痕：``event=observation_extract``＋命令 id＋reason。

        为什么由 handler 自己打：worker 那条每轮日志只认得到 ``pipeline`` 给的
        ``no_candidate``／``candidate_published``，分不出「没抽出来」的**哪一种**没抽出来。
        """
        log.info(
            "event=observation_extract command=%s user=%s reason=%s",
            command.message_id,
            command.user_id,
            reason,
        )

    @staticmethod
    def _metric(command: Command, result: str, proposals: int) -> None:
        """抽取计数（工单 §怎么做.5）：一条命令一行。

        ``result`` 的取值面是 ``METRIC_RESULTS``，由 ``test_ai_mq_observation_handler.py``
        的三态测钉住：那条查询按 result 分组，写进一个没登记过的值＝这条命令在曲线上彻底消失。
        """
        log.info(
            "event=%s result=%s command=%s user=%s proposals=%d",
            METRIC_EVENT,
            result,
            command.message_id,
            command.user_id,
            proposals,
        )


#: 模型面五值必须与三张映射表的键**完全一致**：少一格不是「那条被丢掉」，
#: 而是走到 ``WIRE_EVIDENCE_TYPE[...]`` 抛 KeyError，把一次正常生成变成 handler 崩溃。
#: 这条判据由 ``test_ai_mq_observation_handler.py`` 逐张表比对（三张表任一少一格都红），
#: 不在这里写 assert：`-O` 会把它整句删掉，而它要的是一条会红的测。
MAPPING_TABLES: Mapping[str, Mapping[str, str]] = {
    "WIRE_EVIDENCE_TYPE": WIRE_EVIDENCE_TYPE,
    "EXPLICITNESS_BY_TYPE": EXPLICITNESS_BY_TYPE,
    "SPECIFICITY_BY_TYPE": SPECIFICITY_BY_TYPE,
}


def crisis_kind_of(verdict: Mapping[str, Any]) -> str:
    """危机类别（``self_harm`` | ``relationship_violence``），给落安全事件的那一侧用。

    判定顺序（自伤优先）已经在 ``safety_service.crisis_intercept`` 里做完了，
    这里只是把 ``categories[0]`` 取出并给一个不至于让写入崩掉的兜底值。
    """
    categories = verdict.get("categories") or []
    return str(categories[0]) if categories else "self_harm"
