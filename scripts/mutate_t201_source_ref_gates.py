#!/usr/bin/env python3
"""T2-01（命令体扩来源指针 `sourceRef`／`sourceOccurredAt`）的变异自证（2026-10-18）。

这批交付的是「记忆 → 证据」这条链的**入口**在契约上写不写得出来：现有体只有
`taskType/sourceKind/sourceMessageId`，那是「候选入库后请下一段」的形状，第一段永远没有来源。
新加的两键一旦判据漂了，表现不是报错而是**静默**——命令照投、对面照收，
handler（T2-02）拿不到来源行就产零候选，用户等不到那条洞察，两侧单测还各自全绿。
所以每条破坏都必须打红**它自己的那一条判据**。

判据分四层，靶也按这四层排：

Python 解码侧（`backend/ai/mq/contract.py`，真解码器）——
- P1 摘掉 `parse_command` 里的 `validate_source_ref` 调用 ⇒ 非法 ref 一路放行，
  handler 会把 `reflections:<uuid>` 当 memories 去查库（跨表串语义的候选安静入库）；
- P2 放宽形状正则（`^.*$`）⇒ `orders:xxx`／26 位 uuid 全成「合法指针」；
- P3 开关表全开 ⇒ ADR-003「本期只启用 memories」这句不再成立；
- P4 摘掉「带了发生时刻却没带主体」⇒ 下游只能拿消费时刻兜底，重放的老消息
  会被「最近 30 天」的窗口算成新近证据；
- P5 摘掉 RFC3339 判定 ⇒ `2026年10月18日` 这种串一路进库；
- P6 Python 的键名漂成 `source_ref` ⇒ 对面读成缺省（来源指针静默丢失），
  靶是号位/键名对账闸而不是行为闸——那种错行为面看不出，只有对照生成物能看见。

Go 产出侧（`go-core/internal/domain/task_chain.go`，唯一的命令体出生地）——
- G1 开关表全开 ⇒ Go 会**产**出 `reflections:<uuid>` 的体，而对面判成未启用 ⇒
  一条注定进 DLQ 的命令，且两侧单测各自全绿；
- G2 正则里删一张表名 ⇒ Go 侧把 `chat_messages:` 判成形状错（reason 变成
  `invalid_source_ref` 而不是 `source_type_not_enabled`），值班照着开关表找半天；
- G3 reason 常量漂字母（`source_type_not_enabled`→`source_not_enabled`）⇒
  只有跨语言对账闸抓得住：Go 用例比的是自家常量，永远绿；
- G4 体里少写 `sourceRef` 键 ⇒ 五键形状塌成四键；
- G5 体里键名写成 `source_ref` ⇒ 对面读成缺省，非法 ref 不再被拒；
- G6 摘掉 `TaskRequestBody` 的正门判据 ⇒ 非法 ref 被**写进体里**投出去；
- G7 摘掉 `validateAITaskRequest` 里的载荷判据（侧门）⇒ 取证驱动器／测试自己拼的
  非法体一路进 outbox，而这条链上没人知道是谁写的体。

契约与生成物侧——
- C1 把 `source_ref` 的号位从 13 挪到 15 ⇒ additive 演进闸红（15 §6：旧字节里的 13
  会被读成别的字段，删号位必须留 reserved）；
- C2 同一条改动而不重导 ⇒ 生成物逐字节同步闸红（「改了 .proto 不重导」这件事本身要有证人）；
- C3 从 .proto 删掉这两个字段并重导 ⇒ 号位/键名对账闸**断言红**：registry 里 13/14 没了；
- C4 只删跨语言夹具里的那两行（契约与代码都不动）⇒ 「夹具必须带这两个键」那条断言红。
  这一条是当场补的取证：第一轮拿跨语言闸当靶时，红的是 marshal 那一步的 `ERROR`
  （`unknown field "sourceRef"`），pytest 根本没跑到断言——「红的是工具链而不是判据」
  不算证人（[[feedback-mutation-anchor-must-reach-target]]）。所以给对账闸加了「夹具必须带
  这两个键」这一格，让「夹具那两行不是装饰」变成一条**能被打红的断言**。
  ⚠️ C3 会真的写生成物（`go-core/gen`、`backend/gen`、`contracts/proto/registry.json`），
  所以这四个文件都进备份表，恢复后逐字节 `filecmp` 验回（本轮实测：跑完
  `git status --porcelain` 里 .proto 仍是 +11 行、生成物与夹具零残渣）。

  第一轮（2026-10-18）C1/C2 挂过一次、当场改对了取证点：原计划把号位改成**已占用的
  reserved 9**，实测红的是 `protoc` 自己（`Field "source_ref" uses reserved number 9`），
  生成物根本没机会比对——那是「破坏没到达自己的靶」（[[feedback-mutation-anchor-must-reach-target]]）。
  改成挪到空号 15 之后，protoc 认、自家两条闸才第一次真的判它。protoc 那一层仍然是有效证人，
  只是它不在本批闸门的靶面上，所以记在这里而不是拿它充数。

不该打红的证人（GR1…GR4）：闸判的是**语义与字面量**，不判行号、不判 map 的书写顺序、
不判 const 块里的行序。往函数上方插 15 行注释、把 `enabledSourceTables` 四条换序、
把三条 reason 常量调换顺序，都应当照旧全绿——否则这几条闸就是在钉排版。

纪律：串行执行、copy2 备份＋`filecmp(shallow=False)` 逐字节写回、不用 git checkout
（[[feedback-mutation-restore-no-git-checkout]]、[[feedback-mutation-gates-no-concurrency]]）。
每条变异前先跑基线（基线按 (runner,target) 缓存，理由是每条都由 filecmp 逐字节确认写回，
源状态可证与基线一致）。没装 go 的机器上，跨语言夹具那两条记「未跑」而不是绿
（[[feedback-definition-of-done-tests]]、[[feedback-mutation-anchor-must-reach-target]]）。

用法：
    ./.venv/bin/python scripts/mutate_t201_source_ref_gates.py --check
    GOROOT=/Users/fengyu/sdk/go1.27.1 ./.venv/bin/python scripts/mutate_t201_source_ref_gates.py
    ./.venv/bin/python scripts/mutate_t201_source_ref_gates.py --only P1,G3
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

PROTO = REPO / "contracts" / "proto" / "closenai_ai.proto"
REGISTRY = REPO / "contracts" / "proto" / "registry.json"
CASES = GO_CORE / "testdata" / "proto_contract_cases.json"
PY_GEN = REPO / "backend" / "gen" / "closenai_ai_pb2.py"
GO_GEN = GO_CORE / "gen" / "closenai" / "v1" / "closenai_ai.pb.go"
PY_CONTRACT = REPO / "backend" / "ai" / "mq" / "contract.py"
GO_CHAIN = GO_CORE / "internal" / "domain" / "task_chain.go"
GO_COMMANDS = GO_CORE / "internal" / "domain" / "commands.go"

BODY = "backend/tests/test_go_task_command_body_accepted.py"
MQ = "backend/tests/test_ai_mq_contract.py"
XLANG = "backend/tests/test_proto_cross_language.py"
GO_PKG = "./internal/domain/"

REJECT_NODE = f"{BODY}::test_illegal_source_ref_is_permanently_rejected"
VOCAB_NODE = f"{MQ}::test_source_ref_vocabulary_is_the_same_on_both_sides"
REGNODE_NODE = f"{MQ}::test_source_ref_json_names_and_field_numbers_match_the_registry"
ECHO_NODE = f"{BODY}::test_go_body_with_a_memory_source_ref_is_decoded_and_echoed"

#: Go 侧三个靶：这条判开关表＋正则＋reason，下面两条判五键形状与侧门载荷。
GO_DOMAIN = "^TestTaskRequestBodyRejectsIllegalSourceRef$"
GO_PAIR = "^TestTaskRequestBodyWritesTheSourceRefPair$"
GO_SIDE = "^TestValidateAITaskRequestJudgesRefInsidePayload$"

PY_CALL_OLD = (
    "    validate_source_ref(\n"
    "        _as_str(parsed.get(SOURCE_REF_FIELD)).strip(),\n"
    "        _as_str(parsed.get(SOURCE_OCCURRED_AT_FIELD)).strip(),\n"
    "    )\n"
)
PY_REGEX_OLD = (
    'SOURCE_REF_PATTERN = re.compile(r"^(memories|reflections|mood_checkins|'
    'chat_messages):[0-9a-f-]{36}$")'
)
PY_TABLE_OLD = (
    "SOURCE_REF_TABLES: Mapping[str, bool] = {\n"
    '    "memories": True,\n'
    '    "reflections": False,\n'
    '    "mood_checkins": False,\n'
    '    "chat_messages": False,\n'
    "}"
)
PY_TABLE_ALL_ON = (
    "SOURCE_REF_TABLES: Mapping[str, bool] = {\n"
    '    "memories": True,\n'
    '    "reflections": True,\n'
    '    "mood_checkins": True,\n'
    '    "chat_messages": True,\n'
    "}"
)
PY_ORPHAN_OLD = "    if not source_ref:\n        if source_occurred_at:\n"
PY_ORPHAN_NEW = "    if not source_ref:\n        if False:  # 变异证人：摘掉「没有主体」这一格\n"
PY_RFC_OLD = (
    "    if source_occurred_at:\n"
    "        try:\n"
    "            datetime.fromisoformat(source_occurred_at)\n"
)
PY_RFC_NEW = (
    "    if False:  # 变异证人：摘掉 RFC3339 判定\n"
    "        try:\n"
    "            datetime.fromisoformat(source_occurred_at)\n"
)
GO_TABLE_OLD = 'var enabledSourceTables = map[string]bool{"memories": true}'
GO_TABLE_ALL_ON = (
    'var enabledSourceTables = map[string]bool{"memories": true, "reflections": true, '
    '"mood_checkins": true, "chat_messages": true}'
)
GO_REGEX_OLD = "\t`^(memories|reflections|mood_checkins|chat_messages):[0-9a-f-]{36}$`,\n"
GO_REGEX_NARROW = "\t`^(memories|reflections|mood_checkins):[0-9a-f-]{36}$`,\n"
GO_REASON_OLD = '\tReasonSourceTypeNotEnabled    = "source_type_not_enabled"\n'
GO_REASON_NEW = '\tReasonSourceTypeNotEnabled    = "source_not_enabled"\n'
GO_BODY_REF_OLD = '\t\t"sourceRef":        sourceRef,\n'
GO_BODY_REF_RENAMED = '\t\t"source_ref":     sourceRef,\n'
GO_GATE_OLD = (
    "\tif err := ValidateSourceRef(sourceRef, sourceOccurredAt); err != nil {\n"
    "\t\treturn \"\", err\n"
    "\t}\n"
)
GO_SIDE_OLD = (
    "\tif ref, occurredAt, ok := sourceRefFromPayload(req.Payload); ok {\n"
    "\t\tif err := ValidateSourceRef(ref, occurredAt); err != nil {\n"
    '\t\t\treturn fmt.Errorf("%s → %s：%w", req.Kind.MessageType(), req.Kind.RoutingKey(), err)\n'
    "\t\t}\n"
    "\t}\n"
)
PROTO_REF_OLD = "  string source_ref = 13;\n"
#: 13→15：protoc 编译期不拦（15 是空闲号位），所以这条**只能**由 --compat／--check 判红。
#: 取证 2026-10-18：改成占 reserved 的 9 时红的是 protoc 本身
#: （`Field "source_ref" uses reserved number 9.`），两侧闸根本读不到 descriptor——
#: 那条更靠前的证人记在这里，不作为闸的靶。
PROTO_REF_MOVED = "  string source_ref = 15;\n"
PROTO_BOTH_OLD = (
    "  string source_ref = 13;\n"
    "  // 来源那一行**发生**的时刻（RFC3339，可选）。空串＝没带；带了就必须可解析，\n"
    "  // 因为「最近 30 天」这类窗口若拿消费时刻兜底，会把重放的老消息算成新近证据。\n"
    "  string source_occurred_at = 14;\n"
)
#: 夹具里那两行（C4 只删它、不动契约与代码 ⇒ 靶落在「夹具必须带这两个键」那条断言上）。
FIXTURE_BOTH_OLD = (
    '        "promptVersion": "observe-v3",\n'
    '        "sourceRef": "memories:0f1e2d3c-4b5a-4968-8a7b-9c0d1e2f3a4b",\n'
    '        "sourceOccurredAt": "2026-10-18T09:00:00Z"'
)
FIXTURE_BOTH_NEW = '        "promptVersion": "observe-v3"'

MUTATIONS: list[tuple] = [
    # ── 1. Python 真解码侧（四格判据各一个靶）──
    (
        "P1", "摘掉 parse_command 里的来源指针判定：非法 ref 一路放行，"
        "handler 会把未启用的表当 memories 查库",
        [(PY_CONTRACT, PY_CALL_OLD, "")],
        "pytest", f"{REJECT_NODE}[unknown_table]", "", False,
    ),
    (
        "P2", "形状正则放宽成 `^.*$`：拼错表名、uuid 短一位、大写全成合法指针",
        [(PY_CONTRACT, PY_REGEX_OLD, 'SOURCE_REF_PATTERN = re.compile(r"^.*$")')],
        "pytest", f"{REJECT_NODE}[uuid_short]", "", False,
    ),
    (
        "P3", "白名单全开：ADR-003「本期只启用 memories」这句不再成立",
        [(PY_CONTRACT, PY_TABLE_OLD, PY_TABLE_ALL_ON)],
        "pytest", f"{REJECT_NODE}[reflections_off]", "", False,
    ),
    (
        "P4", "摘掉「带了发生时刻却没带主体」：下游只能拿消费时刻兜底，"
        "重放的老消息被算成新近证据",
        [(PY_CONTRACT, PY_ORPHAN_OLD, PY_ORPHAN_NEW)],
        "pytest", f"{REJECT_NODE}[occurred_without_ref]", "", False,
    ),
    (
        "P5", "摘掉 RFC3339 判定：`2026年10月18日` 这种串一路进库",
        [(PY_CONTRACT, PY_RFC_OLD, PY_RFC_NEW)],
        "pytest", f"{REJECT_NODE}[occurred_not_rfc3339]", "", False,
    ),
    (
        "P6", "Python 的键名漂成 source_ref：对面读成缺省，来源指针静默丢失"
        "（行为面看不出，靶是号位/键名对账闸）",
        [(PY_CONTRACT, 'SOURCE_REF_FIELD = "sourceRef"', 'SOURCE_REF_FIELD = "source_ref"')],
        "pytest", REGNODE_NODE, "", False,
    ),
    # ── 2. Go 产出侧 ──
    (
        "G1", "Go 开关表全开：Go 产得出 reflections 的体，对面判成未启用 ⇒ "
        "一条注定进 DLQ 的命令，两侧单测各自全绿",
        [(GO_CHAIN, GO_TABLE_OLD, GO_TABLE_ALL_ON)],
        "go", GO_PKG, GO_DOMAIN, False,
    ),
    (
        "G2", "Go 正则里删一张表名：chat_messages 被自家判成形状错而不是未启用，"
        "值班照着开关表找半天",
        [(GO_CHAIN, GO_REGEX_OLD, GO_REGEX_NARROW)],
        "go", GO_PKG, GO_DOMAIN, False,
    ),
    (
        "G3", "Go 的 reason 漂一个字母：只有跨语言对账闸抓得住——"
        "Go 用例比的是自家常量，永远绿",
        [(GO_CHAIN, GO_REASON_OLD, GO_REASON_NEW)],
        "pytest", VOCAB_NODE, "", False,
    ),
    (
        "G4", "体里少写 sourceRef 键：五键形状塌成四键，对面读成「没有体外来源」",
        [(GO_CHAIN, GO_BODY_REF_OLD, "")],
        "go", GO_PKG, GO_PAIR, False,
    ),
    (
        "G5", "体里键名写成 source_ref：对面按 json_name 取值，取不到就当缺省放行",
        [(GO_CHAIN, GO_BODY_REF_OLD, GO_BODY_REF_RENAMED)],
        "pytest", ECHO_NODE, "", False,
    ),
    (
        "G6", "摘掉 TaskRequestBody 的正门判据：非法 ref 被**写进体里**投出去",
        [(GO_CHAIN, GO_GATE_OLD, "")],
        "go", GO_PKG, GO_DOMAIN, False,
    ),
    (
        "G7", "摘掉 validateAITaskRequest 里的载荷判据（侧门）：自己拼的非法体"
        "一路进 outbox，这条链上没人知道是谁写的体",
        [(GO_COMMANDS, GO_SIDE_OLD, "")],
        "go", GO_PKG, GO_SIDE, False,
    ),
    # ── 3. 契约与生成物 ──
    (
        "C1", "source_ref 的号位从 13 挪走（15 §6 号位只增不复用）：旧字节里的 13 会被读成别的字段",
        [(PROTO, PROTO_REF_OLD, PROTO_REF_MOVED)],
        "gencompat", "", "被删却没 reserved", False,
    ),
    (
        "C2", "改了 .proto 不重导：生成物逐字节同步闸要有自己的证人",
        [(PROTO, PROTO_REF_OLD, PROTO_REF_MOVED)],
        "gencheck", "", "生成物漂移", False,
    ),
    (
        "C3", "从 .proto 删掉这两个字段并重导：号位/键名对账闸要真的看见号位没了"
        "（重导之后 registry 里没有 13/14，夹具那份字节则两侧都解不进）",
        [(PROTO, PROTO_BOTH_OLD, "")],
        "pytest", REGNODE_NODE, "", True,
    ),
    (
        "C4", "只删跨语言夹具里的那两行（契约与代码都不动）：来源指针退化成「契约上有、"
        "真字节从没解过」，那种字段要到 T2-02 的 handler 才第一次被解码",
        [(CASES, FIXTURE_BOTH_OLD, FIXTURE_BOTH_NEW)],
        "pytest", REGNODE_NODE, "", False,
    ),
]

GREEN: list[tuple] = [
    (
        "GR1", "往 validate_source_ref 上方插 15 行注释：闸判的是四格语义，不钉行号",
        [(PY_CONTRACT, "def validate_source_ref(",
          "".join("# 变异证人 %d\n" % i for i in range(15)) + "def validate_source_ref(")],
        "pytest", REJECT_NODE, "", False,
    ),
    (
        "GR2", "往 ValidateSourceRef 上方插 15 行注释：Go 用例同样不该因为文件变长而红",
        [(GO_CHAIN, "func ValidateSourceRef(",
          "".join("// 变异证人 %d\n" % i for i in range(15)) + "func ValidateSourceRef(")],
        "go", GO_PKG, GO_DOMAIN, False,
    ),
    (
        "GR3", "把 Go 开关表改成多行、四条换序：对账闸比的是集合不是书写顺序",
        [(GO_CHAIN, GO_TABLE_OLD,
          "var enabledSourceTables = map[string]bool{\n"
          '\t"chat_messages": false,\n'
          '\t"mood_checkins": false,\n'
          '\t"reflections": false,\n'
          '\t"memories": true,\n'
          "} ")],
        "pytest", VOCAB_NODE, "", False,
    ),
    (
        "GR4", "把三条 reason 常量在 const 块里调换顺序：词表对账只看值不看位置",
        [(GO_CHAIN,
          '\tReasonSourceRefShape          = "invalid_source_ref"\n'
          '\tReasonSourceTypeNotEnabled    = "source_type_not_enabled"\n'
          '\tReasonSourceOccurredAtInvalid = "invalid_source_occurred_at"\n',
          '\tReasonSourceTypeNotEnabled    = "source_type_not_enabled"\n'
          '\tReasonSourceOccurredAtInvalid = "invalid_source_occurred_at"\n'
          '\tReasonSourceRefShape          = "invalid_source_ref"\n')],
        "pytest", VOCAB_NODE, "", False,
    ),
]

#: C3 会真的重导生成物，这四个文件必须一起进备份表（恢复靠 filecmp 逐字节验回）。
REGEN_PATHS = [PROTO, REGISTRY, PY_GEN, GO_GEN]

RESIDUE = [BODY, MQ, XLANG, "backend/tests/test_ai_mq_worker_logging.py",
           "backend/tests/test_ai_mq_consumer.py"]


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
    """Python 靶被改成语法不通时，红的是解释器而不是判据——先当场拦下。"""
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
        cmd = [GO_BIN, "test", target, "-count=1", "-timeout", "120s", "-run", marker]
        cwd = GO_CORE
    elif runner == "pytest":
        if target.startswith(XLANG) and not go_available():
            return -2, f"{GO_BIN} 不存在：跨语言夹具闸整文件 skip，不算绿"
        cmd = [str(VENV_PY), "-m", "pytest", target, "-q", "--no-header", "-x"]
        cwd = REPO
    elif runner in ("gencheck", "gencompat"):
        flag = "--check" if runner == "gencheck" else "--compat"
        cmd = [str(VENV_PY), str(REPO / "scripts" / "gen_proto.py"), flag]
        cwd = REPO
    else:
        raise AssertionError(f"未知 runner：{runner}")
    proc = subprocess.run(cmd, cwd=cwd, env=GO_ENV, capture_output=True, text=True, timeout=1800)
    return proc.returncode, proc.stdout + proc.stderr


def regenerate() -> tuple[int, str]:
    proc = subprocess.run(
        [str(VENV_PY), str(REPO / "scripts" / "gen_proto.py")],
        cwd=REPO, env=GO_ENV, capture_output=True, text=True, timeout=1800,
    )
    return proc.returncode, proc.stdout + proc.stderr


def is_red(code: int, out: str, runner: str, target: str, marker: str) -> bool:
    """「红」＝**那条判据自己**失败，工具链坏／没匹配到用例／skip 都不算。"""
    if code == 0:
        return False
    if runner == "go":
        if "[build failed]" in out or "no tests to run" in out or "no test files" in out:
            return False
        return f"--- FAIL: {marker.strip('^$')}" in out
    if runner == "pytest":
        if "no tests ran" in out or "skipped" in out:
            return False
        return f"FAILED {target}" in out
    return marker in out


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
        print(f"提示：没找到 {GO_BIN}，Go 侧与跨语言夹具的用例会记「未跑」，不算绿")

    paths = sorted({e[0] for case in cases for e in case[2]} |
                   ({p for p in REGEN_PATHS if p.is_file()} if any(c[6] for c in cases) else set()))
    with tempfile.TemporaryDirectory(prefix="mutate-t201-") as tmp:
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
            for cid, desc, edits, runner, target, marker, regen in MUTATIONS:
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
                    if broken:
                        code, out = -1, broken
                    else:
                        if regen:
                            rcode, rout = regenerate()
                            if rcode != 0:
                                code, out = -1, f"重导失败：{rout}"
                            else:
                                code, out = run(runner, target, marker)
                        else:
                            code, out = run(runner, target, marker)
                finally:
                    restore()
                if code == -1:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（变异体自身不合法）：{out}")
                    print("  " + "\n  ".join(str(out).splitlines()[-12:]))
                    continue
                if is_red(code, out, runner, target, marker):
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓（{runner}）  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（{runner} exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))

            for cid, desc, edits, runner, target, marker, _regen in GREEN:
                if wanted and cid.upper() not in wanted:
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
            for pkg in (GO_PKG, "./internal/messaging/inbound/"):
                proc = subprocess.run(
                    [GO_BIN, "test", pkg, "-count=1"], cwd=GO_CORE, env=GO_ENV,
                    capture_output=True, text=True, timeout=1800,
                )
                if proc.returncode != 0:
                    residue += 1
                    print(f"残留（红！）：go {pkg}\n  "
                          + "\n  ".join((proc.stdout + proc.stderr).splitlines()[-12:]))
        else:
            print("复核 Go 整包：未跑（缺 go 工具链）")
        print("复核 两侧契约／夹具／Go 整包：" + ("绿" if residue == 0 else f"{residue} 条残留"))

        print(f"\nT2-01 变异自证：打红 {red_ok}/{len(MUTATIONS)}，保持绿 {green_ok}/{len(GREEN)}，"
              f"失败 {red_fail + green_fail}，未跑 {len(unrun)}，残留 {residue}")
        if unrun:
            print(f"未跑清单（缺工具链，不算绿）：{', '.join(unrun)}")
        if failures:
            print(f"失败清单：{failures}")
        return 1 if (red_fail or green_fail or residue or unrun) else 0


if __name__ == "__main__":
    sys.exit(main())
