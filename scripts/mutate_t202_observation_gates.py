#!/usr/bin/env python3
"""T2-02（Python 注册 ``observation.extract`` 的 handler）的变异自证（2026-10-18）。

这批交付的是「一行用户原文 → 证据提案」这**第一段**生产者。它坏起来的形态几乎都不报错：

* 子串校验一摘，模型改写的句子变成「用户说过的话」，而 ``excerpt`` 是指回原文的唯一凭据；
* 上限／去重／超长一松，一次生成多产的行会带着**确定性的 id** 进库，inbox 认为它们本来就该有；
* 成本闸（长度、24h、配额）一摘，表现是账单而不是故障；
* 安全短路一摘，危机原文照常产候选；
* 组合根一挪到模块级，「开关关 ⇒ 零副作用」与分层闸同时失效，而且**测试照样全绿**——
  它看的是刚 new 出来的 ``Registry()``，不是进程接线（这批把它改掉了，见下面 M22/口径格）。

所以每条破坏都必须打红**它自己的那一条判据**（[[feedback-mutation-anchor-must-reach-target]]）。

靶面分五层：

AI 能力侧（``backend/ai/prompts/observation.py``＋``backend/ai/mq/handlers/observation.py``）
- M1 子串校验摘掉 → 「2 条合法＋1 条非子串 ⇒ 只出 2 条」这条验收；
- M2 上限不截；M3 去重摘掉；M4 超长不丢整条；
- M5 负样本挪到原文**之后**（那会被模型读成「用户后来写的东西」而进了证据）；
- M6 词表外的 evidence_type 放行；
- M7 长度闸 30→1；M8 危机不再短路；M9 安全判定不可用当放行（D-8）；
- M10 配额闸摘掉；M11 留痕 id 缺失照投；M12 ``item_key`` 丢掉来源段；
- M13 映射表少一格（KeyError 形态）；M14 映射到 Go 不认的 evidenceType。

管道与组合根（``pipeline.py``／``worker.py``／``backend/services/ai_observation.py``）
- M15 没有 item_key 的多条候选照发 ⇒ 第二条起被对面 inbox 静默吃掉；
- M16 跨层 import 提到 worker 的模块级 ⇒ 「import 即连库」；
- M17 组合根多注册一个 ``pattern`` ⇒ 「只交第一段」这条结案口径失守；
- M18/M19/M20 三个真 SQL 端口各摘一个过滤条件；
- M21 给只读例外加一个写形态 ⇒ canonical 那条例外的边界（不变量 7）。

闸与文档口径
- M22 新文件不登记进 ``EXPECTED_FILES``（反向登记闸的靶）；
- M23 只把 ``contracts/events.md`` 的零流量口径改回旧话 ⇒ pytest 侧的影子证人要红；
- M24 只改 ``topology.go`` 的 Document() 不重导 ⇒ Go 的 stale 清单要红（缺 go 记「未跑」）。

不该打红的证人（GR1…GR3）：闸判的是集合与形状，不判字典书写顺序、不判行号、不判提示词
里多一句话。这三条若红，说明闸门在钉排版而不是钉语义。

第一轮（2026-10-18）M2 与 M15 各打不红一次，两处都是**测写虚了**而不是破坏无效，当场补正：
- M2 摘掉 ``[:limit]`` 之后照样绿 ⇒ 那条「上限」用的是「三条＋一条复读」的输入，
  去重先把复读吃掉、剩下正好等于上限，于是它测的是去重而不是上限。改成四条**互不重复**的
  合法观测，并加一条「序号也得是三条不同的」的断言。
- M15 摘掉「缺序号就拒」之后照样绿 ⇒ 两条候选的 ``item_key`` 都是空串，
  「同一个 item_key」那一格也会拦它，``reason`` 相同，只比 reason 的测分不出这两格。
  现在一条断言加 ``detail`` 指回自己那一格，另一格（同序号）拆成独立用例、独立靶 M15b。
参见 [[feedback-mutation-anchor-must-reach-target]]：这两条如果没有跑变异，会一直绿着骗人。

纪律：串行执行、copy2 备份＋``filecmp(shallow=False)`` 逐字节写回、不用 git checkout
（[[feedback-mutation-restore-no-git-checkout]]、[[feedback-mutation-gates-no-concurrency]]）。
每条变异前先跑基线；没装 go 的机器上 Go 侧那条记「未跑」而不是绿。

用法：
    ./.venv/bin/python scripts/mutate_t202_observation_gates.py --check
    GOROOT=/Users/fengyu/sdk/go1.27.1 ./.venv/bin/python scripts/mutate_t202_observation_gates.py
    ./.venv/bin/python scripts/mutate_t202_observation_gates.py --only M1,M21
"""
from __future__ import annotations

import argparse
import ast
import filecmp
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GO_CORE = REPO / "go-core"
VENV_PY = REPO / ".venv" / "bin" / "python"
GOROOT = os.environ.get("GOROOT", "/Users/fengyu/sdk/go1.27.1")
GO_BIN = os.environ.get("GO_BIN", f"{GOROOT}/bin/go")
GO_ENV = {
    **os.environ,
    "GOROOT": GOROOT,
    "PATH": f"{GOROOT}/bin:{os.environ.get('PATH', '')}",
    "GO_BIN": GO_BIN,
}

PROMPTS = REPO / "backend" / "ai" / "prompts" / "observation.py"
HANDLER = REPO / "backend" / "ai" / "mq" / "handlers" / "observation.py"
PIPELINE = REPO / "backend" / "ai" / "mq" / "pipeline.py"
WORKER = REPO / "backend" / "ai" / "mq" / "worker.py"
SERVICES = REPO / "backend" / "services" / "ai_observation.py"
BOUNDARY = REPO / "backend" / "tests" / "test_ai_boundary.py"
CONTRACT_TEST = REPO / "backend" / "tests" / "test_ai_mq_contract.py"
CANON_TEST = REPO / "backend" / "tests" / "test_canonical_schema.py"
EVENTS_MD = REPO / "contracts" / "events.md"
TOPOLOGY_GO = GO_CORE / "internal" / "messaging" / "amqp" / "topology.go"

H = "backend/tests/test_ai_mq_observation_handler.py"
ZERO = "backend/tests/test_go_task_command_body_accepted.py"

#: Go 侧那条口径闸的名字（只有 ``TestContractStatesTheUnboundEventSideInPlainWords`` 管 stale 清单）。
GO_DOC = "^TestContractStatesTheUnboundEventSideInPlainWords$"
GO_PKG = "./internal/messaging/amqp/"

MUTATIONS: list[tuple] = [
    # ── 1. 模型面契约：不合格的答案不许变成候选 ────────────────────────────
    (
        "M1", "摘掉 excerpt 的逐字子串校验：模型改写的句子变成「用户说过的话」，"
        "而 excerpt 是指回原文的唯一凭据",
        [(PROMPTS,
          '        if not excerpt.strip() or excerpt not in content:\n            continue\n',
          '        if not excerpt.strip():  # 变异证人：摘掉子串校验\n            continue\n')],
        "pytest", f"{H}::test_two_legal_items_and_one_fabricated_excerpt_yield_only_two", "", False,
    ),
    (
        "M2", "超过上限不截：一次生成产五条，五条都带「确定性 id」进库，"
        "而工作单的上限是三条",
        [(PROMPTS,
          "    return tuple(sorted(kept, key=lambda i: (i.observation, i.excerpt))[:limit])",
          "    return tuple(sorted(kept, key=lambda i: (i.observation, i.excerpt)))")],
        "pytest", f"{H}::test_more_than_the_cap_is_cut_at_the_cap_not_loosened", "", False,
    ),
    (
        "M3", "摘掉同一条观测的去重：模型把一句话说两遍就是两行证据，"
        "而 item_key 的内容派生会让它们拿到同一个 id ⇒ inbox 之后无处可判",
        [(PROMPTS,
          "        if identity in seen:  # 同一条观测被说了两遍：只留第一条\n            continue\n",
          "        if identity in set():  # 变异证人：摘掉去重\n            continue\n")],
        "pytest", f"{H}::test_duplicate_observation_is_collapsed_before_the_cap", "", False,
    ),
    (
        "M4", "超长观测改成截断（放行）：截出来的一句既不是用户说的、又指不回原文",
        [(PROMPTS,
          "        if not observation or len(observation) > MAX_OBSERVATION_CHARS:\n            continue\n",
          "        if not observation:  # 变异证人：超长不再整条丢\n            continue\n")],
        "pytest", f"{H}::test_overlong_observation_is_dropped_not_truncated", "", False,
    ),
    (
        "M5", "负样本挪到原文**之后**：那一段会被读成「用户看完原文又写的话」，"
        "于是被否掉的归纳有机会变成一条新证据",
        [(PROMPTS,
          '    lines = [_INSTRUCTION]\n'
          '    if negatives:\n'
          '        lines.append(\n'
          '            "The user has corrected or rejected these summaries before. "\n'
          '            "Do NOT generalise them again:\\n"\n'
          '            + "\\n".join(f"- {item}" for item in negatives)\n'
          '            + "\\n"\n'
          '        )\n'
          '    lines.append(f"Record:\\n<<<\\n{content}\\n>>>\\n")\n'
          '    return "\\n".join(lines)\n',
          '    lines = [_INSTRUCTION]\n'
          '    lines.append(f"Record:\\n<<<\\n{content}\\n>>>\\n")\n'
          '    if negatives:  # 变异证人：负样本排到原文之后\n'
          '        lines.append(\n'
          '            "The user has corrected or rejected these summaries before. "\n'
          '            "Do NOT generalise them again:\\n"\n'
          '            + "\\n".join(f"- {item}" for item in negatives)\n'
          '            + "\\n"\n'
          '        )\n'
          '    return "\\n".join(lines)\n')],
        "pytest", f"{H}::test_negative_samples_go_into_the_prompt_and_never_into_a_candidate", "", False,
    ),
    (
        "M6", "词表外的 evidence_type 放行：对面按 allowedOrigins 永久拒绝，"
        "而这条链上没人知道模型换了个词",
        [(PROMPTS,
          "        if evidence_type not in OBSERVATION_TYPES:\n            continue\n",
          "        if evidence_type not in ():  # 变异证人：词表不再拦\n            continue\n")],
        "pytest",
        f"{H}::test_unknown_evidence_type_never_reaches_the_mapping_table[MBTI]", "", False,
    ),
    # ── 2. handler 的短路、成本闸与留痕 ────────────────────────────────────
    (
        "M7", "长度闸 30→1：一句话也去问一次模型，而这一批唯一的全局成本闸就是它"
        "（配额计数器不数提取自己的调用）",
        [(HANDLER, "MIN_CONTENT_CHARS = 30", "MIN_CONTENT_CHARS = 1  # 变异证人")],
        "pytest", f"{H}::test_short_source_gate_has_two_witnesses_at_the_threshold", "", False,
    ),
    (
        "M8", "危机原文不再短路：照样问模型、照样产候选，而那条用户正该被接住的记录"
        "变成了一行证据",
        [(HANDLER,
          '        if verdict["status"] == SAFETY_CRISIS:\n',
          '        if verdict["status"] == SAFETY_CRISIS and False:  # 变异证人：危机不短路\n')],
        "pytest", f"{H}::test_crisis_source_produces_no_candidates_and_one_safety_event", "", False,
    ),
    (
        "M9", "安全判定不可用（超时／答非所问）当放行：D-8 的「不可用不是安全」"
        "在跨进程面上失效，一条没人判过的内容变成候选",
        [(HANDLER,
          '        if verdict["status"] != SAFETY_OK:\n'
          '            raise TransientError(f"model_unavailable：安全判定不可用（{verdict[\'reason\']}）")\n',
          '        if verdict["status"] not in (SAFETY_UNAVAILABLE,) and verdict["status"] != SAFETY_OK:\n'
          '            raise TransientError(f"model_unavailable：安全判定不可用（{verdict[\'reason\']}）")\n')],
        "pytest", f"{H}::test_safety_unavailable_is_transient_not_a_pass", "", False,
    ),
    (
        "M10", "配额闸摘掉：当日预算用尽还继续调模型。配额是按天恢复的，"
        "本该抛瞬时错让它走 30s/2m/10m 阶梯、最后一层进 DLQ 可重放",
        [(HANDLER,
          "        if not await self._ports.quota_available(command.user_id):\n",
          "        if False and not await self._ports.quota_available(command.user_id):\n")],
        "pytest", f"{H}::test_quota_exhausted_retries_rather_than_disappears", "", False,
    ),
    (
        "M11", "留痕 id 缺失照投：``generationRunId`` 是对面的必填列，"
        "空着投出去＝每条进 DLQ，而 DLQ 里的 reason 指着 Go 的字段缺失而不是「我没留痕」",
        [(HANDLER,
          "        if not generation_run_id:\n",
          "        if not generation_run_id and False:  # 变异证人：留痕缺失照投\n")],
        "pytest", f"{H}::test_missing_run_id_or_model_id_is_swallowed_not_published", "", False,
    ),
    (
        "M12", "``item_key`` 丢掉来源段：两条命令抽同一句话拿到同一个 id，"
        "第二条起被 inbox 判成重复而静默丢掉",
        [(HANDLER,
          '    return f"{source_ref}|{WIRE_EVIDENCE_TYPE[item.evidence_type]}|{digest[:16]}"',
          '    return f"{WIRE_EVIDENCE_TYPE[item.evidence_type]}|{digest[:16]}"  # 变异证人')],
        "pytest", f"{H}::test_item_key_shape_carries_source_and_wire_type_and_content", "", False,
    ),
    (
        "M13", "映射表少一格：不是「那条被丢掉」，而是 ``WIRE_EVIDENCE_TYPE[...]`` 抛 KeyError，"
        "一次正常生成变成 handler 崩溃＋整条命令上重试阶梯",
        [(HANDLER,
          '    "relationship_pattern": "EXPLICITNESS_WEAKLY_IMPLIED",\n',
          '')],
        "pytest", f"{H}::test_mapping_tables_cover_every_observation_type", "", False,
    ),
    (
        "M14", "映射到对面 allowedOrigins 之外的 evidenceType：每条候选都永久拒绝、"
        "直入 DLQ，而两侧单测各自全绿",
        [(HANDLER,
          '    "relationship_pattern": "temporal",\n',
          '    "relationship_pattern": "time_based",  # 变异证人：Go 不认这个值\n')],
        "pytest", f"{H}::test_wire_evidence_types_are_in_go_allowed_origins", "", False,
    ),
    # ── 3. 管道与组合根 ────────────────────────────────────────────────────
    (
        "M15", "摘掉「缺序号就拒」那一格：两条没有 item_key 的候选拿到同一个 message_id，"
        "第二条被对面 inbox 判成重复，而这边记的是「已发布」",
        [(PIPELINE,
          "        missing = [i for i, p in enumerate(proposals) if not p.item_key.strip()]\n"
          "        if missing:\n",
          "        missing = [i for i, p in enumerate(proposals) if not p.item_key.strip()]\n"
          "        if missing and False:  # 变异证人：缺序号也照发\n")],
        "pytest", f"{H}::test_pipeline_rejects_candidates_without_an_item_key", "", False,
    ),
    (
        "M15b", "摘掉「同一序号重复申报」那一格：两条**不同**的观测报同一个序号，"
        "等于第二条永久进不了库（这一格与上一格的靶不同，必须各打一次）",
        [(PIPELINE,
          "        duplicated = _duplicated([p.item_key for p in proposals])\n"
          "        if duplicated:\n",
          "        duplicated = _duplicated([p.item_key for p in proposals])\n"
          "        if duplicated and False:  # 变异证人：同序号也照发\n")],
        "pytest", f"{H}::test_pipeline_rejects_two_candidates_claiming_the_same_item_key", "", False,
    ),
    (
        "M16", "跨层 import 提到 worker 的模块级：「import 这个模块」连带把 SQLAlchemy "
        "拖进 AI 层的依赖图，开关关 ⇒ 零副作用同时失效",
        [(WORKER,
          "from backend.ai.mq.registry import Registry\n",
          "from backend.ai.mq.registry import Registry\nfrom backend.services.ai_observation import build_registry  # 变异证人：模块级跨层\n"),
         (WORKER,
          "    from backend.services.ai_observation import build_registry  # 唯一一处跨层 import\n\n",
          "")],
        "pytest", f"{H}::test_ai_layer_still_never_imports_the_service_layer", "", False,
    ),
    (
        "M17", "组合根多注册一个 ``pattern``：本批只交第一段，"
        "注册没做完的那类＝把半成品挂上现网入口，而「零流量」的口径也跟着失真",
        [(SERVICES,
          '    registry.register("observation", ObservationExtractHandler(ports or build_ports()))\n',
          '    registry.register("observation", ObservationExtractHandler(ports or build_ports()))\n'
          '    registry.register("pattern", ObservationExtractHandler(ports or build_ports()))  # 变异证人\n')],
        "pytest", f"{H}::test_production_registry_registers_only_observation", "", False,
    ),
    # ── 4. 服务侧的三个真 SQL 端口 ─────────────────────────────────────────
    (
        "M18", "读原文少一个 ``status='current'`` 过滤：把用户已经改写掉的旧版本"
        "再抽一遍当证据端回去",
        [(SERVICES, '                Memory.status == "current",\n', "")],
        "pytest", f"{H}::test_load_source_reads_only_current_undeleted_rows_of_that_owner", "", False,
    ),
    (
        "M19", "24h 成本闸少一个 ``source_id`` 过滤：同一张表的**别的行**"
        "产过证据，这一行就被判成「提取过了」，于是永远不抽",
        [(SERVICES, "                SourceRecord.source_id == row_id,\n", "")],
        "pytest", f"{H}::test_already_extracted_looks_at_evidence_of_the_same_pointer", "", False,
    ),
    (
        "M20", "负样本不再要求「用户自己写了那句话」：空文本进 prompt 变成一行行 ``- ``，"
        "而那些只点过按钮的 reject 会把「没有任何内容的噪声」当成指令",
        [(SERVICES,
          '    return tuple(\n'
          '        text for text in ((row.correction_text or "").strip() for row in rows) if text\n'
          '    )',
          '    return tuple(\n'
          '        (row.correction_text or "").strip() for row in rows  # 变异证人：空文本也收\n'
          '    )')],
        "pytest", f"{H}::test_recent_corrections_keeps_only_what_the_user_actually_wrote", "", False,
    ),
    (
        "M21", "给「只读例外」那一格加一个写形态：例外②只扩了读，"
        "多一个写就是 Python 改权威态（不变量 7），这条边界必须有人守",
        [(SERVICES,
          "__all__: list[str] = [\n",
          "def _mutation_witness(session) -> None:  # 变异证人：只读例外里出现写形态\n"
          "    session.add(Evidence())\n\n\n"
          "__all__: list[str] = [\n")],
        "pytest",
        "backend/tests/test_canonical_schema.py::"
        "test_python_reaches_canonical_models_only_through_deletion", "", False,
    ),
    # ── 5. 闸的登记面与文档口径 ────────────────────────────────────────────
    (
        "M22", "新文件不登记进边界闸的 ``EXPECTED_FILES``：那条闸的判据是子集，"
        "不登记就静默少扫一个文件（反向登记闸就是为这一格设的）",
        [(BOUNDARY, '    "prompts/observation.py",\n', "")],
        "pytest",
        "backend/tests/test_ai_mq_contract.py::"
        "test_mq_files_are_registered_in_the_boundary_gate", "", False,
    ),
    (
        "M23", "只把 ``contracts/events.md`` 的零流量口径改回旧话（不动生成器）："
        "pytest 侧的影子证人要拦住「文档与代码各说一套」",
        [(EVENTS_MD, "但**流量仍是零**，三个原因各自独立：", "但**流量仍是零**，两个原因各自独立：")],
        "pytest", f"{ZERO}::test_the_producer_side_is_still_zero_traffic_for_two_measurable_reasons",
        "", False,
    ),
    (
        "M24", "只改 ``topology.go`` 的 Document() 不重导：Go 的 stale 清单抓旧口径"
        "（缺 go 工具链时这一条记「未跑」，不算绿）",
        [(TOPOLOGY_GO, '"但**流量仍是零**，三个原因各自独立：\\n"',
          '"但**流量仍是零**，两个原因各自独立：\\n"')],
        "go", GO_PKG, GO_DOC, False,
    ),
]

#: 应当**保持绿**的破坏：证明闸判的是语义而不是排版／字典顺序／提示词的字数。
GREEN: list[tuple] = [
    (
        "GR1", "把 WIRE_EVIDENCE_TYPE 的五条换个书写顺序：判据看的是集合与映射结果，"
        "不该钉字典顺序",
        [(HANDLER,
          '    "stated_preference": "direct",\n'
          '    "value_statement": "direct",\n'
          '    "repeated_behavior": "repeated",\n'
          '    "relationship_pattern": "temporal",\n'
          '    "emotional_pattern": "behavioral",\n',
          '    "emotional_pattern": "behavioral",\n'
          '    "stated_preference": "direct",\n'
          '    "value_statement": "direct",\n'
          '    "repeated_behavior": "repeated",\n'
          '    "relationship_pattern": "temporal",\n')],
        "pytest", f"{H}::test_mapping_tables_cover_every_observation_type", "",
    ),
    (
        "GR2", "在 handler 文件顶部插 12 行注释：注册面的判据取 AST 的**语句形状**，"
        "不钉行号（钉行号的闸改一次排版就红，然后被人改成 skip）",
        [(HANDLER, "from __future__ import annotations\n",
          "from __future__ import annotations\n"
          + "# 变异证人：排版噪声\n" * 12)],
        "pytest", f"{H}::test_registration_happens_inside_a_function_body_not_at_import_time", "",
    ),
    (
        "GR3", "给 SYSTEM_PROMPT 末尾加一句：闸只判「它要求严格 JSON 且非空」，"
        "不判提示词的字节数",
        [(PROMPTS,
          '    "an excerpt you cannot copy verbatim from the record is an item you do not output."\n',
          '    "an excerpt you cannot copy verbatim from the record is an item you do not output. "\n'
          '    "Be concise."\n')],
        "pytest", f"{H}::test_prompt_carries_the_verbatim_rule_and_the_json_shape", "",
    ),
]

#: 恢复后必须复跑的东西：本批改过的闸＋契约文件，一处残渣就当作失败。
RESIDUE = [
    H,
    "backend/tests/test_ai_mq_contract.py",
    "backend/tests/test_ai_boundary.py",
    "backend/tests/test_ai_mq_consumer.py",
    "backend/tests/test_go_task_command_body_accepted.py",
    CANON_TEST.relative_to(REPO).as_posix(),
]


def _apply(edits: list) -> None:
    for path, old, new in edits:
        src = path.read_text(encoding="utf-8")
        n = src.count(old)
        assert n == 1, f"{path.name}：锚点命中 {n} 次（要求恰好 1 次）\n{old[:90]!r}"
        path.write_text(src.replace(old, new, 1), encoding="utf-8")


def _verify_anchor(edits: list) -> str | None:
    for path, old, _new in edits:
        if not path.exists():
            return "文件不存在"
        n = path.read_text(encoding="utf-8").count(old)
        if n != 1:
            return f"命中 {n} 次（要 1）"
    return None


def _sanity(edits: list) -> str | None:
    """变异体语法不通时红的是解释器而不是判据——先当场拦下。"""
    for path, _old, _new in edits:
        if path.suffix != ".py":
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            return f"{path.name}：变异体不是合法 Python（{exc}）"
    return None


def go_available() -> bool:
    return Path(GO_BIN).is_file()


def run(runner: str, target: str, marker: str) -> tuple[int, str]:
    if runner == "go":
        if not go_available():
            return -2, f"{GO_BIN} 不存在：Go 侧用例没跑，不算绿"
        cmd = [GO_BIN, "test", target, "-count=1", "-timeout", "180s", "-run", marker]
        cwd = GO_CORE
    elif runner == "pytest":
        cmd = [str(VENV_PY), "-m", "pytest", target, "-q", "--no-header", "-x"]
        cwd = REPO
    else:
        raise AssertionError(f"未知 runner：{runner}")
    proc = subprocess.run(cmd, cwd=cwd, env=GO_ENV, capture_output=True, text=True, timeout=1800)
    return proc.returncode, proc.stdout + proc.stderr


def is_red(code: int, out: str, runner: str, target: str, marker: str) -> bool:
    """「红」＝**那条判据自己**失败；工具链坏／没匹配到用例／skip 都不算。"""
    if code == 0:
        return False
    if runner == "go":
        if "[build failed]" in out or "no tests to run" in out or "no test files" in out:
            return False
        return f"--- FAIL: {marker.strip('^$')}" in out
    if "no tests ran" in out or "1 skipped" in out:
        return False
    return f"FAILED {target}" in out


BASELINE: dict[tuple[str, str, str], tuple[int, str]] = {}


def baseline(runner: str, target: str, marker: str) -> tuple[int, str]:
    key = (runner, target, marker)
    if key not in BASELINE:
        BASELINE[key] = run(runner, target, marker)
    return BASELINE[key]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数，不改任何文件")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    args = ap.parse_args()
    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    cases = [*MUTATIONS, *GREEN]
    if args.check:
        bad = 0
        for case in cases:
            problem = _verify_anchor(case[2])
            if problem:
                print(f"坏锚点 {case[0]}：{problem}")
                bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}（共 {len(cases)} 条）")
        return 1 if bad else 0

    if not VENV_PY.is_file():
        print(f"解释器不存在：{VENV_PY}（变异自证要求先备好 .venv）")
        return 2
    if not go_available():
        print(f"提示：没找到 {GO_BIN}，Go 侧那条会记「未跑」，不算绿")

    paths = sorted({e[0] for case in cases for e in case[2]})
    with tempfile.TemporaryDirectory(prefix="mutate-t202-") as tmp:
        originals: dict[Path, Path] = {}
        for idx, path in enumerate(paths):
            assert path.is_file(), f"靶文件不存在：{path}"
            rel = path.relative_to(REPO).as_posix().replace("/", "+")
            bak = Path(tmp) / f"{idx:02d}-{rel}"
            assert bak not in originals.values(), f"备份名碰撞：{bak}"
            shutil.copy2(path, bak)
            originals[path] = bak

        def restore() -> None:
            for path, bak in originals.items():
                shutil.copy2(bak, path)
                assert filecmp.cmp(bak, path, shallow=False), f"{path} 恢复后字节不一致"

        red_ok = red_fail = green_ok = green_fail = 0
        unrun: list[str] = []
        failures: list[str] = []
        try:
            for cid, desc, edits, runner, target, marker, _regen in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = baseline(runner, target, marker)
                if base_code == -2:
                    unrun.append(cid)
                    print(f"[{cid}] 未跑（{base_out}）：缺工具链不是绿，也不把它的红记在闸头上")
                    continue
                if base_code != 0:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 未跑（基线本来就不绿，exit={base_code}）："
                          "不能把环境的红记成闸的红\n  "
                          + "\n  ".join(base_out.splitlines()[-8:]))
                    continue
                _apply(edits)
                try:
                    broken = _sanity(edits)
                    code, out = (-1, broken) if broken else run(runner, target, marker)
                finally:
                    restore()
                if code == -1:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（变异体自身不合法）：{out}")
                    continue
                if is_red(code, out, runner, target, marker):
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓（{runner}）  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（{runner} exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))

            for cid, desc, edits, runner, target, marker in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = baseline(runner, target, marker)
                if base_code != 0:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 未跑（基线本来就不绿，exit={base_code}）：{desc}")
                    continue
                _apply(edits)
                try:
                    code, out = run(runner, target, marker)
                finally:
                    restore()
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 保持绿 ✓（{runner}）  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 保持绿 ✗（{runner} exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))
        except Exception as exc:  # noqa: BLE001
            restore()
            print(f"异常中止，已恢复：{exc!r}")
            return 2

        residue = 0
        for node in RESIDUE:
            code, out = run("pytest", node, "")
            if code != 0:
                residue += 1
                print(f"残留（红！）：{node}\n  " + "\n  ".join(out.splitlines()[-12:]))
        if go_available():
            proc = subprocess.run([GO_BIN, "test", GO_PKG, "-count=1"], cwd=GO_CORE, env=GO_ENV,
                                  capture_output=True, text=True, timeout=1800)
            if proc.returncode != 0:
                residue += 1
                print(f"残留（红！）：go {GO_PKG}\n"
                      + "\n  ".join((proc.stdout + proc.stderr).splitlines()[-12:]))
        else:
            print("复核 Go 整包：未跑（缺 go 工具链）")
        print("复核 两侧闸与契约文件：" + ("绿" if residue == 0 else f"{residue} 条残留"))

        print(f"\nT2-02 变异自证：打红 {red_ok}/{len(MUTATIONS)}，保持绿 {green_ok}/{len(GREEN)}，"
              f"失败 {red_fail + green_fail}，未跑 {len(unrun)}，残留 {residue}")
        if unrun:
            print(f"未跑清单（缺工具链，不算绿）：{', '.join(unrun)}")
        if failures:
            print(f"失败清单：{failures}")
        return 1 if (red_fail or green_fail or residue or unrun) else 0


if __name__ == "__main__":
    sys.exit(main())
