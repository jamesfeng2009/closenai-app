#!/usr/bin/env python3
"""T2-03 切片 A（来源指针的准入面与归属校验）的变异自证（2026-10-18）。

这批交付的是候选面的**最后一格准入**：提案体里的 `source_table`/`source_id` 从此不再
只是「非空且不超长」，而是要回答「这张表我们认不认」「那一行是不是这个用户的」。
判据漂了以后不会报错，只会静默地往错的方向走：

* 白名单摘掉 ⇒ 一条把 `source_id` 写成别人记忆板的候选，安静登记成一行
  `source_records(user_id=攻击者, …)` 并产出一条 evidence，账面违反 01 §22 不变量 9；
* 归属比较反向 ⇒ 同上，而且是**每次**都放行；
* 「查不到」与「查库失败」混成一种 ⇒ 要么毒消息在 30s/2m/10m 阶梯上撞三次库
  最后带着空白 `error_code` 进 DLQ，要么一次抖动被记成永久拒绝、重投机会都没有；
* 三张表（Python 的开关、Go 的准入面、Go 的回查面）漂开 ⇒ **两侧的各自单测都还是绿的**，
  线上表现却是每条正常候选都被永久拒绝，而 reason 读起来像「数据不存在」。

所以每条破坏都必须打红**它自己的那一条判据**（[[feedback-mutation-anchor-must-reach-target]]）。

靶面按四层排：

Go 领域层（`internal/domain/`，纯判定，不开库的那一半＋事务里的那一半）——
- D1 摘掉 `checkEvidenceCandidate` 里的表名白名单 ⇒ 不认的表一路走到回查；
- D2 归属比较反向（`owner != c.UserID` ⇒ `==`）⇒ 别人的行被放行、自己的行被拒；
- D3 摘掉 `found=false` 那一格 ⇒ 「那一行不存在」被读成「那一行是别人的」，
  排障的人会去查越权而不是查时序，而 `inbox.error_code` 已经写错；
- D4 把回查报错包成 `ErrUnknownSourceRow` ⇒ 一次 DB 抖动变成永久拒绝，连阶梯都不走；
- D5 把归属核验挪到 `UpsertSourceRecord` **之后** ⇒ 越权提案先留下了一行指向别人
  业务行的登记（事务回滚掩盖不了次序本身违反不变量 9，也让半状态在假件里现形）。

Go 存储层（`internal/store/source_owner.go`）——
- S1 回查面的键漂成裸字符串 `"journal_entries"` ⇒ 自家集合闸红；
  同一条编辑走 pytest 是 K1 ⇒ **另一条作业**里的三方闸也红（CI 的 Go 作业没库时
  整包 Skip，所以这格漂移必须两边各有一个证人）；
- S2 把 `pgx.ErrNoRows` 分支写成 error ⇒ 真库那条「没有这一行」用例红。
  ⚠️ 这条需要 `TEST_DATABASE_URL`；缺库时记「未跑」不算绿。

Go 入站层（`internal/messaging/inbound/`，永久／瞬态的分道只有这一处）——
- I1 从 `sourcePointerRejections` 删一行 ⇒ 计数证人（`len(...)==3`）先红：
  少一行的后果是那条永久非法被读成瞬态，而账面只写着「重试过」；
- I2 `classify` 只遍历表的第一行 ⇒ 后两个哨兵原样上抛变成 NACK 重投；
- I3 `ReasonForeignSource` 的串漂成 `foreign` ⇒ 只有跨语言词表闸抓得住
  （Go 自己的用例比的是自家常量，永远绿；而运维在 Loki 里搜 `foreign_source` 搜不到 Python 侧）。

契约与写入口成对（`go-core/db/queries/canonical.sql`、alembic 的 canonical 迁移）——
- P1 Python 开关表把 `reflections` 点开（ADR-003 本期只开 memories）⇒ 三方对账闸红：
  命令面放行、候选面拒绝，每条正常候选永久落在 `source_type_not_enabled`；
- P2 `InsertSourceRecord` 的 `ON CONFLICT` 少一列 ⇒ 与 DDL 的 `UNIQUE` 不成对：
  同指针第二条候选不再是复用既有行，而是 23505 ⇒ 瞬时错误 ⇒ 阶梯 ⇒ DLQ，
  而真库那条幂等测在没有库的 CI 作业里 Skip，所以这格必须由文本闸作证；
- P3 反过来动 DDL 那侧（`UNIQUE` 少一列）⇒ 同一格成对闸从另一边红。

不该打红的证人（GR1…GR4）：闸判的是**语义与集合**，不判行号、不判表的行序、
不判 map 的书写顺序。往函数上方插 15 行注释、把回查表的三行换序、把开关表四格换序，
都应当照旧全绿——否则这几条闸就是在钉排版。

纪律：串行执行、copy2 备份＋`filecmp(shallow=False)` 逐字节写回、不用 git checkout
（[[feedback-mutation-restore-no-git-checkout]]、[[feedback-mutation-gates-no-concurrency]]）。
每条变异前先跑基线（按 (runner,target) 缓存）；基线本来就不绿的记「未跑」，
不把环境的红记成闸的红。

已知既有红（**不是本批造成的**，登记在台账的 T5-03×N8 桶）：带库跑 `go test ./...` 时
`TestN8*ContractBytes` 7 条＋`TestCurriculumCardsWindowIsHalfOpen`/`…OrderAndLimit`
共 9 条撞 `uq_journey_events_user_kind_day`；本脚本的靶与残留复核都不走那两条用例。

用法：
    ./.venv/bin/python scripts/mutate_t203_source_admission_gates.py --check
    GOROOT=/Users/fengyu/sdk/go1.27.1 TEST_DATABASE_URL=postgresql://… ./.venv/bin/python \
        scripts/mutate_t203_source_admission_gates.py
    ./.venv/bin/python scripts/mutate_t203_source_admission_gates.py --only D2,I1
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

GO_POINTER = GO_CORE / "internal" / "domain" / "source_pointer.go"
GO_EVIDENCE = GO_CORE / "internal" / "domain" / "service_evidence.go"
GO_OWNER = GO_CORE / "internal" / "store" / "source_owner.go"
GO_DECODE = GO_CORE / "internal" / "messaging" / "inbound" / "candidate_decode.go"
GO_SQL = GO_CORE / "db" / "queries" / "canonical.sql"
PY_CONTRACT = REPO / "backend" / "ai" / "mq" / "contract.py"
PY_MIG = (
    REPO / "backend" / "alembic" / "versions" / "c8a2e4b6d0f2_add_canonical_runtime_tables.py"
)

MQ = "backend/tests/test_ai_mq_contract.py"
SCH = "backend/tests/test_canonical_schema.py"
GO_DOMAIN = "./internal/domain/"
GO_STORE = "./internal/store/"
GO_INBOUND = "./internal/messaging/inbound/"

THREE_WAY = f"{MQ}::test_source_admission_is_one_table_across_three_places"
VOCAB = f"{MQ}::test_go_inbound_reason_codes_use_the_shared_vocabulary"
PAIR = f"{SCH}::test_source_record_upsert_conflicts_on_the_declared_unique_key"

#: 领域层那条用例的四个子格，一格一个靶——不许用父用例的名字糊过去。
SP = "TestService_AdmitEvidence_SourcePointerIsChecked"
SP_TABLE = f"{SP}/表不在准入面"
SP_FOREIGN = f"{SP}/来源行是别人的"
SP_MISSING = f"{SP}/来源行查不到"
SP_TRANSIENT = f"{SP}/回查本身报错"
OWNER_COVER = "^TestSourceOwnerQueriesCoverExactlyTheAdmissibleTables$"
SENTINELS = "^TestSourcePointerSentinelsBecomePermanent$"
DB_MISSING = "TestAdmitEvidenceChecksSourceRowOwnership/没有这一行"

# ── 锚点（逐字从文件里取，--check 会先当场数命中次数）────────────────────────
EV_WHITELIST_OLD = (
    "\tif err := verifySourceTable(c); err != nil {\n"
    "\t\treturn err\n"
    "\t}\n"
)
POINTER_OWN_OLD = "\tif owner != c.UserID {\n"
POINTER_OWN_INVERTED = "\tif owner == c.UserID {\n"
POINTER_NOTFOUND_OLD = (
    "\tif !found {\n"
    "\t\treturn fmt.Errorf(\"%w: source_table=%q source_id=%s 那一行读不到\",\n"
    "\t\t\tErrUnknownSourceRow, c.SourceTable, c.SourceID)\n"
    "\t}\n"
)
#: D3 的变异体：分支条件与文案都不动，只把**哨兵**换掉——这正是「两种失败混成一种」
#: 的最小写法。（写成摘掉整个分支会让 `found` 变成 declared-and-not-used，
#: 红的是编译而不是判据。）
POINTER_NOTFOUND_AS_FOREIGN = (
    "\tif !found {\n"
    "\t\treturn fmt.Errorf(\"%w: source_table=%q source_id=%s 那一行读不到\",\n"
    "\t\t\tErrForeignSource, c.SourceTable, c.SourceID)\n"
    "\t}\n"
)
POINTER_ERR_OLD = (
    "\tif err != nil {\n"
    "\t\treturn fmt.Errorf(\"domain: 回查 source=(%s,%s) 的归属: %w\", "
    "c.SourceTable, c.SourceID, err)\n"
    "\t}\n"
)
POINTER_ERR_BECOMES_PERMANENT = (
    "\tif err != nil {\n"
    "\t\treturn fmt.Errorf(\"%w: 回查 source=(%s,%s) 的归属: %v\", ErrUnknownSourceRow, "
    "c.SourceTable, c.SourceID, err)\n"
    "\t}\n"
)
EV_OWN_CHECK_OLD = (
    "\t\tif err := s.verifySourceOwnership(ctx, st, c); err != nil {\n"
    "\t\t\treturn err\n"
    "\t\t}\n"
    "\t\tcited, err := s.verifyIndependent(ctx, st, c)\n"
)
EV_OWN_CHECK_OUTFRONTPUT = "\t\tcited, err := s.verifyIndependent(ctx, st, c)\n"
EV_COUNT_ANCHOR = "\t\tindependent, dropped := s.countIndependent(c, cited, sourceID)\n"
EV_COUNT_AFTER_CHECK = (
    "\t\tif err := s.verifySourceOwnership(ctx, st, c); err != nil {\n"
    "\t\t\treturn err\n"
    "\t\t}\n"
    + EV_COUNT_ANCHOR
)
OWNER_KEY_OLD = (
    "\tdomain.SourceTableMemories: `SELECT user_id FROM memories WHERE id = $1`,\n"
)
OWNER_KEY_DRIFTED = (
    "\t\"journal_entries\": `SELECT user_id FROM journal_entries WHERE id = $1`,\n"
)
OWNER_NOROWS_OLD = (
    "\t\tif errors.Is(err, pgx.ErrNoRows) {\n"
    "\t\t\treturn \"\", false, nil\n"
    "\t\t}\n"
)
OWNER_NOROWS_AS_ERROR = (
    "\t\tif errors.Is(err, pgx.ErrNoRows) {\n"
    "\t\t\treturn \"\", false, wrap(\"source row owner\", err)\n"
    "\t\t}\n"
)
DECODE_FOREIGN_ROW_OLD = "\t{domain.ErrForeignSource, ReasonForeignSource},\n"
DECODE_TABLE_OLD = (
    "\t{domain.ErrUnsupportedSourceTable, ReasonSourceTypeNotEnabled},\n"
    "\t{domain.ErrForeignSource, ReasonForeignSource},\n"
    "\t{domain.ErrUnknownSourceRow, ReasonUnknownSourceRow},\n"
)
#: GR2 的等价改写：同样的三行，只是换了书写的先后（闸比的是「逐个哨兵都有码」）。
DECODE_TABLE_REORDERED = (
    "\t{domain.ErrUnknownSourceRow, ReasonUnknownSourceRow},\n"
    "\t{domain.ErrUnsupportedSourceTable, ReasonSourceTypeNotEnabled},\n"
    "\t{domain.ErrForeignSource, ReasonForeignSource},\n"
)
DECODE_LOOP_OLD = "\tfor _, item := range sourcePointerRejections {\n"
DECODE_LOOP_FIRST_ONLY = "\tfor _, item := range sourcePointerRejections[:1] {\n"
DECODE_CODE_DRIFT_OLD = '\tReasonForeignSource    = "foreign_source"\n'
DECODE_CODE_DRIFT_NEW = '\tReasonForeignSource    = "foreign"\n'
PY_TABLE_OLD = '    "reflections": False,\n'
PY_TABLE_ON = '    "reflections": True,\n'
PY_TABLE_KEYS_OLD = (
    '    "memories": True,\n'
    '    "reflections": False,\n'
    '    "mood_checkins": False,\n'
    '    "chat_messages": False,\n'
)
#: GR4 的等价改写：四格换序、值一个都不动（三方闸比的是「哪些格开着」这个集合）。
PY_TABLE_KEYS_REORDERED = (
    '    "chat_messages": False,\n'
    '    "mood_checkins": False,\n'
    '    "reflections": False,\n'
    '    "memories": True,\n'
)
SQL_CONFLICT_OLD = "ON CONFLICT (user_id, source_table, source_id) DO UPDATE SET updated_at = now()"
SQL_CONFLICT_SHORT = "ON CONFLICT (user_id, source_table) DO UPDATE SET updated_at = now()"
DDL_UNIQUE_OLD = "            UNIQUE (user_id, source_table, source_id)"
DDL_UNIQUE_SHORT = "            UNIQUE (user_id, source_table)"

MUTATIONS: list[tuple] = [
    # ── 1. Go 领域层：准入面的两半各自都要能被打红 ──
    (
        "D1", "摘掉 checkEvidenceCandidate 里的表名白名单：不认的表走到回查那一步，"
        "reason 从「这张表没开」变成「查库失败」",
        [(GO_EVIDENCE, EV_WHITELIST_OLD, "")],
        "go", GO_DOMAIN, SP_TABLE, False,
    ),
    (
        "D2", "归属比较反向：别人的行被放行、自己的行被永久拒（不变量 9 反着执行）",
        [(GO_POINTER, POINTER_OWN_OLD, POINTER_OWN_INVERTED)],
        "go", GO_DOMAIN, SP_FOREIGN, False,
    ),
    (
        "D3", "摘掉 found=false 那一格：「那一行不存在」被读成「那一行是别人的」，"
        "排障方向从「候选晚于清理到达」变成「有人越权」",
        [(GO_POINTER, POINTER_NOTFOUND_OLD, POINTER_NOTFOUND_AS_FOREIGN)],
        "go", GO_DOMAIN, SP_MISSING, False,
    ),
    (
        "D4", "回查报错包成 ErrUnknownSourceRow：一次 DB 抖动变成永久拒绝，"
        "连 30s/2m/10m 的重试阶梯都不走",
        [(GO_POINTER, POINTER_ERR_OLD, POINTER_ERR_BECOMES_PERMANENT)],
        "go", GO_DOMAIN, SP_TRANSIENT, False,
    ),
    (
        "D5", "归属核验挪到 UpsertSourceRecord 之后：越权提案先登记了一行指向别人"
        "业务行的指针，事务回滚掩盖不了次序本身",
        [
            (GO_EVIDENCE, EV_OWN_CHECK_OLD, EV_OWN_CHECK_OUTFRONTPUT),
            (GO_EVIDENCE, EV_COUNT_ANCHOR, EV_COUNT_AFTER_CHECK),
        ],
        "go", GO_DOMAIN, SP_FOREIGN, False,
    ),
    # ── 2. Go 存储层：回查面与准入面同集合，且「查不到」≠「查失败」──
    (
        "S1", "回查面的键漂成裸字符串 journal_entries：白名单与 SQL 各走各的，"
        "每条正常候选都会被读成 ErrUnknownSourceRow",
        [(GO_OWNER, OWNER_KEY_OLD, OWNER_KEY_DRIFTED)],
        "go", GO_STORE, OWNER_COVER, False,
    ),
    (
        "K1", "同一条编辑的另一个证人：Go 作业没库时整包 Skip，所以三方对账在"
        "pytest 作业里必须也咬得住（解不出键＝空集，那条正向证人自己会先红）",
        [(GO_OWNER, OWNER_KEY_OLD, OWNER_KEY_DRIFTED)],
        "pytest", THREE_WAY, "", False,
    ),
    (
        "S2", "把 pgx.ErrNoRows 写成一个 error：真库那条「没有这一行」用例拿到的"
        "是瞬态错误，永久／瞬态的分道在存储层就错了",
        [(GO_OWNER, OWNER_NOROWS_OLD, OWNER_NOROWS_AS_ERROR)],
        "go", GO_STORE, DB_MISSING, True,
    ),
    # ── 3. Go 入站层：三个哨兵都要有一个码，且码串两侧对得上 ──
    (
        "I1", "从 sourcePointerRejections 删一行：那条永久非法被读成瞬态，"
        "撞完三层才进 DLQ，而 inbox.error_code 那一格是空的",
        [(GO_DECODE, DECODE_FOREIGN_ROW_OLD, "")],
        "go", GO_INBOUND, SENTINELS, False,
    ),
    (
        "I2", "classify 只遍历表的第一行：表看起来三行齐全，实际只有第一个哨兵会转永久",
        [(GO_DECODE, DECODE_LOOP_OLD, DECODE_LOOP_FIRST_ONLY)],
        "go", GO_INBOUND, SENTINELS, False,
    ),
    (
        "P1", "Python 开关表把 reflections 点开（ADR-003 说本期只开 memories）："
        "命令面放行、候选面拒 ⇒ 每条正常候选永久落在 source_type_not_enabled，"
        "而两侧各自单测都还是绿的",
        [(PY_CONTRACT, PY_TABLE_OLD, PY_TABLE_ON)],
        "pytest", THREE_WAY, "", False,
    ),
    (
        "I3", "ReasonForeignSource 的串漂成 foreign：Go 自家用例比的是自家常量永远绿，"
        "只有跨语言词表闸看得见；运维在 Loki 搜 foreign_source 就少一半命中",
        [(GO_DECODE, DECODE_CODE_DRIFT_OLD, DECODE_CODE_DRIFT_NEW)],
        "pytest", VOCAB, "", False,
    ),
    # ── 4. 幂等成对：DDL 的 UNIQUE 与写入口的 ON CONFLICT 目标 ──
    (
        "P2", "InsertSourceRecord 的 ON CONFLICT 少一列：同指针第二条候选不再复用既有行，"
        "而是 23505 ⇒ 瞬时错误 ⇒ 阶梯 ⇒ DLQ，而真库幂等测在没库的作业里 Skip",
        [(GO_SQL, SQL_CONFLICT_OLD, SQL_CONFLICT_SHORT)],
        "pytest", PAIR, "", False,
    ),
    (
        "P3", "反向证人：动 DDL 那侧（UNIQUE 少一列）——同一格成对闸必须两边都咬得住，"
        "不然它只是「ON CONFLICT 与昨天那份 DDL 一致」",
        [(PY_MIG, DDL_UNIQUE_OLD, DDL_UNIQUE_SHORT)],
        "pytest", PAIR, "", False,
    ),
]

GREEN: list[tuple] = [
    (
        "GR1", "往 verifySourceOwnership 上方插 15 行注释：领域层那条闸判的是四格语义，"
        "不钉行号，文件变长不该红",
        [(GO_POINTER, "func (s *Service) verifySourceOwnership(",
          "".join("// 变异证人 %d\n" % i for i in range(15))
          + "func (s *Service) verifySourceOwnership(")],
        "go", GO_DOMAIN, SP, False,
    ),
    (
        "GR2", "把回查表的三行换个书写顺序：入站闸数的是「每个哨兵都有码」，不排行序",
        [(GO_DECODE, DECODE_TABLE_OLD, DECODE_TABLE_REORDERED)],
        "go", GO_INBOUND, SENTINELS, False,
    ),
    (
        "GR3", "往 SourceRowOwner 上方插 15 行注释：集合闸比的是键，不是文件长度",
        [(GO_OWNER, "func (t *canonicalTx) SourceRowOwner(",
          "".join("// 变异证人 %d\n" % i for i in range(15))
          + "func (t *canonicalTx) SourceRowOwner(")],
        "go", GO_STORE, "^TestSourceOwnerQueries", False,
    ),
    (
        "GR4", "Python 开关表四格换序、值一个都不动：三方闸比的是「哪些格开着」这个集合",
        [(PY_CONTRACT, PY_TABLE_KEYS_OLD, PY_TABLE_KEYS_REORDERED)],
        "pytest", THREE_WAY, "", False,
    ),
]

#: S2 需要真库；缺 TEST_DATABASE_URL 时记「未跑」，不算绿也不算闸红。
RESIDUE_PYTEST = [MQ, SCH]
RESIDUE_GO = [(GO_DOMAIN, ""), (GO_INBOUND, ""), (GO_STORE, "^TestSourceOwnerQueries")]


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


def db_available() -> bool:
    return bool(os.environ.get("TEST_DATABASE_URL"))


def run(runner: str, target: str, marker: str, needs_db: bool = False) -> tuple[int, str]:
    if runner == "go":
        if not go_available():
            return -2, f"{GO_BIN} 不存在：Go 侧用例没跑，不算绿"
        if needs_db and not db_available():
            return -2, "缺 TEST_DATABASE_URL：这条靶要真库，记「未跑」不算绿"
        cmd = [GO_BIN, "test", target, "-count=1", "-timeout", "300s"]
        if marker:
            cmd += ["-run", marker]
        cwd = GO_CORE
    elif runner == "pytest":
        cmd = [str(VENV_PY), "-m", "pytest", target, "-q", "--no-header", "-x"]
        cwd = REPO
    else:
        raise AssertionError(f"未知 runner：{runner}")
    proc = subprocess.run(cmd, cwd=cwd, env=GO_ENV, capture_output=True, text=True, timeout=1800)
    return proc.returncode, proc.stdout + proc.stderr


def _report_name(marker: str) -> str:
    """把 `-run` 的正则还原成**用例名**：只剥掉两端的锚点，中间的 `^`/`$` 不是锚点。

    第一版在这里用了 `marker.strip("^$")`，于是父名带 `$` 收尾、子名再跟一段的那种
    marker（`^TestX$/子格`）把中间的 `$` 留了下来，`--- FAIL:` 永远匹配不上——
    实际打红了却被记成「打红 ✗」（2026-10-18 S2 第一次跑就是这么记的）。
    """
    name = marker[1:] if marker.startswith("^") else marker
    return name[:-1] if name.endswith("$") else name


def is_red(code: int, out: str, runner: str, target: str, marker: str) -> bool:
    """「红」＝**那条判据自己**失败，工具链坏／没匹配到用例／skip 都不算。"""
    if code == 0:
        return False
    if runner == "go":
        if "[build failed]" in out or "no tests to run" in out or "no test files" in out:
            return False
        return f"--- FAIL: {_report_name(marker)}" in out
    if runner == "pytest":
        if "no tests ran" in out:
            return False
        return f"FAILED {target}" in out
    return False


BASELINE: dict[tuple[str, str, str], tuple[int, str]] = {}


def baseline(runner: str, target: str, marker: str, needs_db: bool) -> tuple[int, str]:
    key = (runner, target, marker)
    if key not in BASELINE:
        BASELINE[key] = run(runner, target, marker, needs_db)
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
        print(f"提示：没找到 {GO_BIN}，Go 侧用例会被记「未跑」，不算绿")
    if not db_available():
        print("提示：没设 TEST_DATABASE_URL，S2（真库那条）记「未跑」，不算绿")

    paths = sorted({e[0] for case in cases for e in case[2]})
    with tempfile.TemporaryDirectory(prefix="mutate-t203-") as tmp:
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
            for cid, desc, edits, runner, target, marker, needs_db in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = baseline(runner, target, marker, needs_db)
                if base_code == -2:
                    unrun.append(cid)
                    print(f"[{cid}] 未跑（{base_out}）：缺工具链／缺库不是绿，也不把它的红记在闸头上")
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
                    code, out = (-1, broken) if broken else run(runner, target, marker, needs_db)
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

            for cid, desc, edits, runner, target, marker, needs_db in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = baseline(runner, target, marker, needs_db)
                if base_code == -2:
                    unrun.append(cid)
                    print(f"[{cid}] 未跑（{base_out}）：证人没上场，不能报成「保持绿」")
                    continue
                if base_code != 0:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 保持绿 ✗（基线本来就不绿，exit={base_code}）\n  "
                          + "\n  ".join(base_out.splitlines()[-8:]))
                    continue
                _apply(edits)
                try:
                    code, out = run(runner, target, marker, needs_db)
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
        for node in RESIDUE_PYTEST:
            code, out = run("pytest", node, "")
            if code != 0:
                residue += 1
                print(f"残留（红！）：pytest {node}\n  " + "\n  ".join(out.splitlines()[-12:]))
        for pkg, marker in RESIDUE_GO:
            code, out = run("go", pkg, marker)
            if code != 0:
                residue += 1
                print(f"残留（红！）：go {pkg} {marker}\n  " + "\n  ".join(out.splitlines()[-12:]))
        print("复核 两侧契约／Go 三条整包：" + ("绿" if residue == 0 else f"{residue} 条残留"))

        print(f"\nT2-03 变异自证：打红 {red_ok}/{len(MUTATIONS)}，保持绿 {green_ok}/{len(GREEN)}，"
              f"失败 {red_fail + green_fail}，未跑 {len(unrun)}，残留 {residue}")
        if unrun:
            print(f"未跑清单（缺工具链／缺库，不算绿）：{', '.join(unrun)}")
        if failures:
            print(f"失败清单：{failures}")
        return 1 if (red_fail or green_fail or residue or unrun) else 0


if __name__ == "__main__":
    sys.exit(main())
