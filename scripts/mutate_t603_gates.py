#!/usr/bin/env python3
"""T6-03（canonical DDL 的单一事实源：迁移单一化 + 派生快照 + 指纹）的变异自证（2026-10-20）。

靶文件七个（alembic 件 1 + 快照 3 + 指纹清单 1 + 导出器 1 + 消费者测 1），红闸 21 条、绿闸 6 条。

**归一化那条腿有一条本机取不了证**：`--check` 比较主体时摘掉 `-- Dumped ... version` 两行，
为的是 CI runner 用发行版自带的 pg_dump、本机用容器里那一份，minor 版本号天生不同，计入比较就会
在换机器时假报漂移。本机没法造出第二个 pg_dump 版本，所以「环境真的不同时它挡得住误报」这一句
按**未取证**登记（留给 CI 首跑）；能钉住的反面钉住了——C3/C4 证明摘的只是比较那一条腿，
逐字节指纹（Go ④／Py ③ 的清单核）仍按原字节算，手改环境行照样红。

工单 §验收 三句，每句都有**它自己的**靶与红闸（[[feedback-mutation-anchor-must-reach-target]]）：

1. 「删一行 alembic 列定义 ⇒ 生成物变红」⇒ N1 把 N2 件 `_TABLES` 里 `outcomes.outcome_type`
   那一行删掉（迁移不写这列了），红的是 ①（alembic 推导列集 ↔ 快照列集）。
   这一句的**难点是两侧不许同源**：一侧读 Python 迁移件的 DDL 原文，另一侧读 pg_dump 渲出来的
   CREATE TABLE 体，所以「删一列」在两边留下的是方向相反的差集，N1 与 S1 是同一条判据的两个入射角。
2. 「反向改 go schema ⇒ 红」⇒ 一个坏法一条靶：S1 删列（①）、S2 改类型（列名没变，① 看不见，
   红的是逐字节 ④ 与编译面 SQLC）、S3/S4 把 S2 那同一格分别钉到 Python 侧 ④ 与 sqlc 对账、
   S5 钉到 ③（重建库重导的逐字节可复现）、S6 只在主体里加一行注释（④ 仍要红——
   「重导一次就绿」的那种闸挡不住键入改动），H1/H2 钉表头来路（①），H3 钉白名单（②），
   U1/U2 把 `users` 再声明一次钉两侧（Go ③ ＋ Py ②）。
3. 「`test_canonical_schema.py` 22 表列级对账仍绿」⇒ 本脚本不动它，只用 SQLC 那条（它内部的
   `sqlc diff`）证明快照这份**第四方**接上了；列级对账本身的回归在实测计数里报。

另外三格本票顺手消掉的「第二真源」也各有靶：E1 改导出器里那份 legacy 名单（Go ⑥：名单与
0001 实际内容各说各话）、K1 把 alembic head 抄回字面量（Py ⑤：归零闸 + 三处消费者证人）、
C1/C2 篡改指纹清单（④：哈希不符 / 清单覆盖不到实际文件）、M1 改 Python 侧的切点常量
（④：两侧各实现一次「从 dump 标记切到 EOF」，这个对撞不是装饰——不钉住就会各算各的一半）。

N2/N3 与 U1/U2、S2/S3/S4/S5 是**同一格判据在两侧各钉一次**：Go 侧那份能在没有 PG 的 CI go 作业里
判红（手改仓库文本时库里当然没有对应变化），Python 侧那份管列集与逐字节可复现。

**一轮就抓到的等价变异（留档以免下轮重猜）**：把 ① 的靶写成「改表头日期」不会红——
指纹与主体比较都只覆盖 `BODY_MARKER` 往后的字节，表头有 `captured:` 逐日变，这是**设计**
而不是漏检；同理，只改 alembic 那条 `--` 注释（G6）也不该红，否则 ① 就退化成文本 diff。

纪律：串行执行（[[feedback-mutation-gates-no-concurrency]]）、copy2 备份＋`filecmp(shallow=False)`
逐字节写回、不用 git checkout（[[feedback-mutation-restore-no-git-checkout]]）；每条红闸先跑基线，
基线本来就不绿或前置缺失一律记「未跑」（[[feedback-gate-needs-positive-witness]]）；
Go 侧 `--- SKIP` 与 `[build failed]`、pytest 侧 `ERROR <节点>` 与 `no tests ran` 都**不算打红**——
那些红的是环境，不是判据。③ 需要 PG 且要 pg_dump（或 `SCHEMA_EXPORT_DOCKER`），
SQLC 需要 sqlc 在 PATH：缺前置的那几条整条记「未跑」，不算绿。

用法：
    set -a && . /tmp/t206_env.sh && set +a
    export SCHEMA_EXPORT_DOCKER=closenai-pg-dev
    GOROOT=/Users/fengyu/sdk/go1.27.1 ./.venv/bin/python scripts/mutate_t603_gates.py --check
    GOROOT=/Users/fengyu/sdk/go1.27.1 ./.venv/bin/python scripts/mutate_t603_gates.py
    ./.venv/bin/python scripts/mutate_t603_gates.py --only S6,H1,U1
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
    "GOTOOLCHAIN": "local",
}
PG_ENV = "TEST_DATABASE_URL"
DOCKER_ENV = "SCHEMA_EXPORT_DOCKER"

# ── 靶文件 ───────────────────────────────────────────────────────────────────
N2_MIGRATION = (REPO / "backend" / "alembic" / "versions"
                / "c8a2e4b6d0f2_add_canonical_runtime_tables.py")
SNAP_DIR = GO_CORE / "db" / "schema"
LEGACY = SNAP_DIR / "0001_legacy_auth.sql"
CANON = SNAP_DIR / "0002_canonical_runtime.sql"
OUTBOX = SNAP_DIR / "0003_outbox_messages.sql"
MANIFEST = SNAP_DIR / "body.sha256"
DRIFT_GO = SNAP_DIR / "drift_test.go"
EXPORTER = REPO / "scripts" / "export_pg_schema.py"
POSTHOG = REPO / "backend" / "tests" / "test_posthog_erasure_retry_pg.py"

PY_GATE_PATH = REPO / "backend" / "tests" / "test_pg_schema_snapshot.py"
PY_GATE = "backend/tests/test_pg_schema_snapshot.py"
SQLC_GATE = "backend/tests/test_canonical_schema.py::test_sqlc_generated_code_matches_queries"
G1 = f"{PY_GATE}::test_alembic_column_set_matches_go_snapshot_column_set"
G2 = f"{PY_GATE}::test_snapshot_table_sets_are_disjoint_and_cover_exactly_the_derived_list"
G3 = f"{PY_GATE}::test_snapshot_is_reproducible_from_the_alembic_chain"
G4 = f"{PY_GATE}::test_checksum_manifest_covers_every_snapshot_and_both_sides_cut_the_same_bytes"
G5 = f"{PY_GATE}::test_alive_tests_do_not_pin_the_alembic_head_literal"

PKG_SCHEMA = "./db/schema/"
GO_PROV = "^TestEverySnapshotDeclaresItsProvenance$"
GO_LIST = "^TestSnapshotWhitelistMatchesWhatTheFileCreates$"
GO_DUP = "^TestNoTableIsDeclaredByTwoSnapshots$"
GO_HASH = "^TestSnapshotBodiesMatchTheChecksumManifest$"
GO_ALNB = "^TestCanonicalSnapshotsMatchAlembicTableLists$"
GO_EXPL = "^TestExporterLegacyWhitelistMatchesLegacySnapshot$"

# ── 锚点（逐字取自靶文件，命中数必须恰好 1）────────────────────────────────
ALEMBIC_TYPE_LINE = "            outcome_type VARCHAR(40) NOT NULL,\n"
ALEMBIC_TABLE_ENTRY = '        "outcomes",\n        """\n'
ALEMBIC_PROBE_COMMENT = "            -- 04 §15 九类；02 §22 的表只有 status/result，缺类型列\n"
SNAP_OUTCOME_TYPE = "    outcome_type character varying(40) NOT NULL,\n"
SNAP_MODEL_VERSION = ("    origin character varying(24) NOT NULL,\n"
                      "    model_version bigint,\n")
SNAP_OUTCOMES_HEAD = "CREATE TABLE public.outcomes (\n"
SNAP_EVIDENCE_HEAD = "CREATE TABLE public.evidence (\n"
DO_NOT_EDIT = ("-- DO NOT EDIT: generated by scripts/export_pg_schema.py from the "
               "alembic chain (T6-03).\n")
HEADER_TAIL = "-- and backend/tests/test_pg_schema_snapshot.py.\n"
CAPTURED_LINE = "--   captured: 2026-10-20; alembic head revision f6b8d0e2a4c6\n"
OUTBOX_WHITELIST_ARG = "-t public.outbox_messages"
EXPORTER_LEGACY = 'LEGACY_TABLES = ("public.users", "public.refresh_tokens")\n'
MANIFEST_LEGACY_LINE = ("eec0da5e13fd32b83ff4724b71f4f13e7c50a54645cab7656d0f65cb8f8e835e"
                        "  0001_legacy_auth.sql\n")
MANIFEST_OUTBOX_LINE = ("0c88734d1d03b6667510d592c2685658896ca90280279d9e6463744a352996bc"
                        "  0003_outbox_messages.sql\n")
ENV_VERSION_LINE = "-- Dumped by pg_dump version 16.15 (Debian 16.15-1.pgdg12+2)\n"
HEAD_CALL = "HEAD = head_revision()\n"
PY_BODY_MARKER = 'BODY_MARKER = "--\\n-- PostgreSQL database dump"\n'
USERS_RERUN = ("CREATE TABLE public.users (\n"
               "    id character varying(36) NOT NULL\n"
               ");\n\n")

MUTATIONS: list[tuple] = [
    # ── 1. 真源侧：alembic 才是 DDL 属主（靶 N2 迁移件）──
    (
        "N1", "把 `_TABLES` 里 `outcomes.outcome_type` 那一行删掉（§验收 逐字第一句）⇒ 迁移不再建这列，"
              "而仓库里的快照还留着它：① 报「快照有而 alembic 没有」——这一格是**派生物落后于真源**"
              "唯一的离线证人，没有它，加一列忘重导永远没人知道",
        [(N2_MIGRATION, ALEMBIC_TYPE_LINE, "")],
        "pytest", G1, G1,
    ),
    (
        "N2", "把 `_TABLES` 里那张表改个名（`outcomes` → `outcome`）⇒ 迁移侧的表名与快照的表名分家："
              "Go ⑤ 红——sqlc 拿的是快照，编译期看不见库里那张表已经不叫这个名字了",
        [(N2_MIGRATION, ALEMBIC_TABLE_ENTRY, ALEMBIC_TABLE_ENTRY.replace('"outcomes"', '"outcome"'))],
        "go", PKG_SCHEMA, GO_ALNB,
    ),
    (
        "N3", "同一处改名打**另一侧**的闸 ⇒ Py ② 红：推导出来的 22 张与三份快照的并集不再相等，"
              "「名单不分裂」这条判据在 Python 侧也有自己的证人",
        [(N2_MIGRATION, ALEMBIC_TABLE_ENTRY, ALEMBIC_TABLE_ENTRY.replace('"outcomes"', '"outcome"'))],
        "pytest", G2, G2,
    ),
    # ── 2. 派生物侧：手改快照（靶三份 .sql 的主体与表头）──
    (
        "S1", "反向：手删快照里 `outcomes.outcome_type` 那一列（§验收 逐字第二句）⇒ ① 红"
              "「alembic 有而快照没有」。与 N1 是同一条判据的两个入射角，两侧不同源才挡得住两个方向",
        [(CANON, SNAP_OUTCOME_TYPE, "")],
        "pytest", G1, G1,
    ),
    (
        "S2", "手改快照里 `outcomes.model_version` 的类型（bigint → text，**列名没变**）⇒ ① 看不见这格"
              "（它比的是列名集合），红的是逐字节指纹 ④：列宽/可空/类型这类漂移只有字节级判据挡得住",
        [(CANON, SNAP_MODEL_VERSION, SNAP_MODEL_VERSION.replace("model_version bigint,",
                                                                "model_version text,"))],
        "go", PKG_SCHEMA, GO_HASH,
    ),
    (
        "S3", "同一格类型改动打 Python 侧的 ④ ⇒ 两侧各自算一次 DDL 主体的 sha256，任何一侧没跟上就红；"
              "只钉 Go 侧的话，`--check` 之外的 Python 消费者拿不到这格保护",
        [(CANON, SNAP_MODEL_VERSION, SNAP_MODEL_VERSION.replace("model_version bigint,",
                                                                "model_version text,"))],
        "pytest", G4, G4,
    ),
    (
        "S4", "同一格类型改动打**编译面**：`sqlc diff` ⇒ Go 的结构体字段是快照喂出来的，"
              "手改快照而没重跑 sqlc，红的是生成物对账闸（工单 §为什么 说的「Go 编译期看到的表与线上不是一张」）",
        [(CANON, SNAP_MODEL_VERSION, SNAP_MODEL_VERSION.replace("model_version bigint,",
                                                                "model_version text,"))],
        "pytest-sqlc", SQLC_GATE, "",
    ),
    (
        "S5", "同一格类型改动打**可复现面** ③：一次性空库 → `alembic upgrade head` → pg_dump → 与文件比字节。"
              "这一条挡的是「手改快照」与「alembic 变了但没人重导」两种坏法的共同出口（需 PG）",
        [(CANON, SNAP_MODEL_VERSION, SNAP_MODEL_VERSION.replace("model_version bigint,",
                                                                "model_version text,"))],
        "pytest-repro", G3, G3,
    ),
    (
        "S6", "只在快照主体里加一行注释 ⇒ ④ 仍要红：指纹覆盖的是**从 dump 标记到 EOF 的全部字节**，"
              "「反正重导一次就绿」不是判据，键入改动必须先在这里红",
        [(CANON, SNAP_OUTCOMES_HEAD, "-- t603 probe：主体里的注释也是产物字节\n" + SNAP_OUTCOMES_HEAD)],
        "go", PKG_SCHEMA, GO_HASH,
    ),
    (
        "H1", "删掉 0003 表头那行 DO NOT EDIT ⇒ Go ① 红：这份快照**必须能指回一次可复现的导出**，"
              "否则下一个人只知道它是一张 DDL，不知道该跑哪个脚本",
        [(OUTBOX, DO_NOT_EDIT, "")],
        "go", PKG_SCHEMA, GO_PROV,
    ),
    (
        "H2", "把 0002 表头记的 alembic revision 改成一个仓库里不存在的号 ⇒ Go ① 红："
              "provenance 不能是编的——revision 那 12 位必须真有一条迁移件的前缀",
        [(CANON, CAPTURED_LINE, CAPTURED_LINE.replace("f6b8d0e2a4c6", "deadbeefcafe"))],
        "go", PKG_SCHEMA, GO_PROV,
    ),
    (
        "H3", "改 0003 表头的 `-t` 白名单（少一个 s）⇒ Go ② 红：表头写着导这张表，文件里其实是另一张。"
              "白名单是「Go 工具链读哪些表」的字面凭据，与文件内容分家就没人知道快照少了谁",
        [(OUTBOX, OUTBOX_WHITELIST_ARG, "-t public.outbox_message")],
        "go", PKG_SCHEMA, GO_LIST,
    ),
    (
        "U1", "把 `CREATE TABLE public.users` 再往 0002 里抄一份（工单 §为什么 点名的那格坏法）⇒ Go ③ 红："
              "一张表两个家＝DDL 属主分裂，两份里必然有一份是错的（本票之前 users 正是这种状态）",
        [(CANON, SNAP_EVIDENCE_HEAD, USERS_RERUN + SNAP_EVIDENCE_HEAD)],
        "go", PKG_SCHEMA, GO_DUP,
    ),
    (
        "U2", "同一处再声明打 Python 侧 ⇒ Py ② 红：三份快照的表必须两两不重叠且份数钉死（2/21/1）。"
              "两侧各钉一次是因为 Go 侧看的是文件名，Python 侧看的是归属表",
        [(CANON, SNAP_EVIDENCE_HEAD, USERS_RERUN + SNAP_EVIDENCE_HEAD)],
        "pytest", G2, G2,
    ),
    # ── 3. 名单与指纹本身（靶导出器／指纹清单）──
    (
        "E1", "导出器里那份 legacy 白名单少写一张（`refresh_tokens` 摘掉）⇒ Go ⑥ 红：名单与 0001 实际"
              "导出的表各说各话。这一格是**白名单**（Go 要读哪些 legacy 表）而不是 DDL 副本，"
              "但它同样只许有一个家",
        [(EXPORTER, EXPORTER_LEGACY, 'LEGACY_TABLES = ("public.users",)\n')],
        "go", PKG_SCHEMA, GO_EXPL,
    ),
    (
        "C1", "篡改指纹清单里 0001 那行的一位十六进制 ⇒ Go ④ 红：清单是「重导一次就绿」那条路的封口——"
              "文件与清单里只改一边，字节级立刻对不上",
        [(MANIFEST, MANIFEST_LEGACY_LINE,
          MANIFEST_LEGACY_LINE.replace("eec0da5e", "eec0da5f"))],
        "go", PKG_SCHEMA, GO_HASH,
    ),
    (
        "C2", "把清单里 0003 那行的**文件名**改掉 ⇒ Py ④ 红：清单必须逐份覆盖实际存在的那三份，"
              "漏一份＝那一份从此没有字节级证人",
        [(MANIFEST, MANIFEST_OUTBOX_LINE,
          MANIFEST_OUTBOX_LINE.replace("0003_outbox_messages.sql", "0003_outbox.sql"))],
        "pytest", G4, G4,
    ),
    (
        "C3", "改主体里那行 `Dumped by pg_dump version` ⇒ Go ④ 红。归一化只作用在「主体比较」那一条腿上，"
              "逐字节那条不松：环境行被手改同样红（换机器时两边一起变的正当路径是重跑导出器写盘）",
        [(CANON, ENV_VERSION_LINE, "-- Dumped by pg_dump version 9.9 (elsewhere)\n")],
        "go", PKG_SCHEMA, GO_HASH,
    ),
    (
        "C4", "同一处环境行改动打**可复现面** ⇒ Py ③ 也红：`--check` 在比完主体后还要核指纹清单，"
              "清单算的是原字节。这一格钉住的是「归一化＝给 ③ 开后门」那种想法——它没有",
        [(CANON, ENV_VERSION_LINE, "-- Dumped by pg_dump version 9.9 (elsewhere)\n")],
        "pytest-repro", G3, G3,
    ),
    # ── 4. 抄来的 head 与两侧的切法（靶消费者测／本闸文件）──
    (
        "K1", "把某条实库伴生测的期望值抄回字面量（T4-01 登记的缺陷①）⇒ Py ⑤ 红：字面量归零闸＋"
              "`head_revision()` 的三个消费者证人一起塌。抄一份字面量的代价是每落一件迁移要同步改四处",
        [(POSTHOG, HEAD_CALL, 'HEAD = "f6b8d0e2a4c6"\n')],
        "pytest", G5, G5,
    ),
    (
        "M1", "把 Python 侧的切点常量改成多一个换行（「两侧各实现一次从 dump 标记切到 EOF」里的一边）"
              "⇒ Py ④ 红：这条对撞先比常量再比哈希，不钉住就会各算各的一半，哈希永远对不上也永远说不清是谁错",
        [(PY_GATE_PATH, PY_BODY_MARKER,
          'BODY_MARKER = "--\\n-- PostgreSQL database dump\\n"\n')],
        "pytest", G4, G4,
    ),
]

# ── 绿闸：这些坏法**不该**红，红了就说明判据退化成了文本 diff ────────────────
SQL_COMMENTS = "".join(
    f"-- 绿闸注脚 {i:02d}：来路与白名单看的是字面，不看注释密度。\n" for i in range(1, 16)
)
GO_COMMENTS = "".join(
    f"// 绿闸注脚 {i:02d}：切主体的判据看的是字节，不钉行号也不钉排版。\n" for i in range(1, 16)
)
PY_COMMENTS = "".join(
    f"# 绿闸注脚 {i:02d}：这一格判的是列集语义，不钉排版。\n" for i in range(1, 16)
)
ALEMBIC_COMMENTS = "".join(
    f"            -- 绿闸注脚 {i:02d}：注释不是列，① 不该被它伤到。\n" for i in range(1, 16)
)

GREEN: list[tuple] = [
    (
        "GR1", "导出器里 snapshot_targets 上方插 15 行注释 ⇒ Go ⑥ 照旧绿："
               "legacy 名单对撞的是 `LEGACY_TABLES` 那三行内容，不是脚本的注释密度",
        [(EXPORTER, "def snapshot_targets() -> dict[str, tuple[str, ...]]:\n",
          PY_COMMENTS + "def snapshot_targets() -> dict[str, tuple[str, ...]]:\n")],
        "go", PKG_SCHEMA, GO_EXPL,
    ),
    (
        "GR2", "0003 的表头里插 15 行注释（dump 标记之前）⇒ Go ① 照旧绿："
               "provenance 要求的是那五行字面凭据都在，表头多几行解释不算没来路",
        [(OUTBOX, HEADER_TAIL, HEADER_TAIL + SQL_COMMENTS)],
        "go", PKG_SCHEMA, GO_PROV,
    ),
    (
        "GR3", "指纹清单里追加 15 行 `--` 注释 ⇒ Go ④ 照旧绿：清单读的是 `<sha>  <文件>` 那几行，"
               "注释行按同一规则跳掉（这一格两侧实现一致，所以它绿不代表 ④ 松）",
        [(MANIFEST, MANIFEST_LEGACY_LINE, SQL_COMMENTS + MANIFEST_LEGACY_LINE)],
        "go", PKG_SCHEMA, GO_HASH,
    ),
    (
        "GR4", "drift_test.go 里 ddlBody 上方插 15 行注释 ⇒ Go ④ 照旧绿：字节级判据不钉自己的行号",
        [(DRIFT_GO, "func ddlBody(t *testing.T, name string) string {\n",
          GO_COMMENTS + "func ddlBody(t *testing.T, name string) string {\n")],
        "go", PKG_SCHEMA, GO_HASH,
    ),
    (
        "GR5", "快照闸里 _upgrade_body 上方插 15 行注释 ⇒ Py ① 照旧绿：列集推导读的是 upgrade 段里的行，"
               "不看这个函数自己排了几行",
        [(PY_GATE_PATH, "def _upgrade_body(text: str) -> str:\n",
          PY_COMMENTS + "def _upgrade_body(text: str) -> str:\n")],
        "pytest", G1, G1,
    ),
    (
        "GR6", "往 `_TABLES` 的 `outcomes` 段里插 15 行 SQL 注释 ⇒ Py ① 照旧绿："
               "`_ddl_columns` 跳掉 `--` 开头的行；G6/N1 的对照——同一段文本里注释与列定义不是一回事",
        [(N2_MIGRATION, ALEMBIC_PROBE_COMMENT, ALEMBIC_PROBE_COMMENT + ALEMBIC_COMMENTS)],
        "pytest", G1, G1,
    ),
]


def _apply(edits: list) -> None:
    """锚点替换成变异体；命中数必须恰好 1——命中两次说明靶写错了。"""
    for path, old, new in edits:
        assert path.exists(), f"{path}：靶文件不存在"
        src = path.read_text(encoding="utf-8")
        n = src.count(old)
        assert n == 1, f"{path.name}：锚点命中 {n} 次（要求恰好 1 次）\n{old[:160]!r}"
        path.write_text(src.replace(old, new, 1), encoding="utf-8")


def _verify_anchor(edits: list) -> str | None:
    for path, old, _new in edits:
        if not path.exists():
            return "文件不存在"
        n = path.read_text(encoding="utf-8").count(old)
        if n != 1:
            return f"锚点命中 {n} 次（要 1）：{old[:80]!r}"
        if path.suffix == ".py":
            try:
                ast.parse(path.read_text(encoding="utf-8").replace(old, _new, 1))
            except SyntaxError as exc:
                return f"变异体不是合法 Python：{exc}"
    return None


def run_pytest(node: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(VENV_PY), "-m", "pytest", node, "-q", "--no-header", "-p", "no:randomly"],
        cwd=REPO, capture_output=True, text=True, timeout=2400,
    )
    return proc.returncode, proc.stdout + proc.stderr


def run(kind: str, target: str) -> tuple[int, str]:
    if kind == "go":
        if not Path(GO_BIN).is_file():
            return -2, f"{GO_BIN} 不存在：Go 侧闸门没跑，不算绿"
        proc = subprocess.run([GO_BIN, "test", target, "-count=1", "-timeout", "300s"],
                              cwd=GO_CORE, env=GO_ENV, capture_output=True, text=True, timeout=900)
        return proc.returncode, proc.stdout + proc.stderr
    if kind == "pytest-sqlc":
        if not VENV_PY.is_file():
            return -2, f"解释器不存在：{VENV_PY}"
        if shutil.which("sqlc") is None:
            # sqlc 不在 PATH 时那条闸自己 pytest.skip（退出码仍是 0），
            # 不拦下来就会把「没跑」念成「基线绿」。
            return -2, "sqlc 不在 PATH：源与生成物的对账闸没跑，不算绿"
        return run_pytest(target)
    if kind == "pytest-repro":
        if not VENV_PY.is_file():
            return -2, f"解释器不存在：{VENV_PY}"
        if not os.environ.get(PG_ENV):
            return -2, f"{PG_ENV} 未设置：③ 会在 pg_test_url 处 skip，不算绿也不算红"
        if shutil.which("pg_dump") is None and not os.environ.get(DOCKER_ENV):
            return -2, (f"pg_dump 不在 PATH 且未设 {DOCKER_ENV}：③ 要重建库再 dump，"
                        "这一格按「未跑」记")
        return run_pytest(target)
    if kind == "pytest":
        if not VENV_PY.is_file():
            return -2, f"解释器不存在：{VENV_PY}"
        return run_pytest(target)
    return -2, f"未知的闸类型 {kind}"


def report_name(marker: str) -> str:
    name = marker[1:] if marker.startswith("^") else marker
    return name[:-1] if name.endswith("$") else name


def is_red(code: int, out: str, kind: str, target: str, marker: str) -> bool:
    """「红」＝**那条判据自己**失败；编译坏掉、没匹配到用例、skip、收集期报错都不算。"""
    if code == 0:
        return False
    if kind == "go":
        if "[build failed]" in out or "no tests to run" in out or "no test files" in out:
            return False
        if "--- SKIP:" in out and "--- FAIL:" not in out:
            return False
        return f"--- FAIL: {report_name(marker)}" in out
    if "no tests ran" in out:
        return False
    if f"ERROR {target}" in out:
        return False
    return f"FAILED {target}" in out


BASELINE: dict[tuple[str, str, str], tuple[int, str]] = {}


def baseline(kind: str, target: str, marker: str) -> tuple[int, str]:
    key = (kind, target, marker)
    if key not in BASELINE:
        BASELINE[key] = run(kind, target)
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
        print(f"锚点校验：{'全部命中一次且变异体合法' if bad == 0 else f'{bad} 条有问题'}（共 {len(cases)} 条）")
        return 1 if bad else 0

    if not VENV_PY.is_file():
        print(f"解释器不存在：{VENV_PY}（变异自证要求先备好 .venv）")
        return 2
    if not os.environ.get(PG_ENV):
        print(f"提示：{PG_ENV} 未设置，③ 那一侧的靶全部记「未跑」，不算绿")

    paths = sorted({e[0] for case in cases for e in case[2]})
    with tempfile.TemporaryDirectory(prefix="mutate-t603-") as tmp:
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
            for cid, desc, edits, kind, target, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = baseline(kind, target, marker)
                if base_code == -2:
                    unrun.append(cid)
                    print(f"[{cid}] 未跑（{base_out}）：缺前置不是绿，也不把它的红记在闸头上")
                    continue
                if base_code != 0:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 未跑（基线本来就不绿，exit={base_code}）："
                          "不能把环境的红记成闸的红\n  " + "\n  ".join(base_out.splitlines()[-8:]))
                    continue
                try:
                    _apply(edits)
                    code, out = run(kind, target)
                finally:
                    restore()
                if code != 0 and kind == "go" and "[build failed]" in out:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（变异体编译不过，红的是工具链不是判据）：\n  "
                          + "\n  ".join(out.splitlines()[-10:]))
                    continue
                if is_red(code, out, kind, target, marker):
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗  {desc}\n  " + "\n  ".join(out.splitlines()[-14:]))

            for cid, desc, edits, kind, target, marker in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = baseline(kind, target, marker)
                if base_code != 0:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 保持绿 ✗（基线就不绿，exit={base_code}）：{desc}")
                    continue
                try:
                    _apply(edits)
                    code, out = run(kind, target)
                finally:
                    restore()
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 保持绿 ✓  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 被误伤 ✗  {desc}\n  " + "\n  ".join(out.splitlines()[-14:]))
        finally:
            restore()
            residue = [p for p in paths if not filecmp.cmp(originals[p], p, shallow=False)]
            print(f"\n残留复核：{'0 个靶文件与原件逐字节一致' if not residue else ''}")
            if residue:
                print("  以下靶文件恢复后仍不一致：" + "、".join(str(p) for p in residue))

        total_red = red_ok + red_fail
        total_green = green_ok + green_fail
        print(f"\n汇总：红闸 {red_ok}/{total_red}｜绿闸 {green_ok}/{total_green}｜"
              f"未跑 {len(unrun)}{'（' + ','.join(unrun) + '）' if unrun else ''}")
        if failures:
            print("失败条目：" + ",".join(failures))
        if unrun:
            print("ⓘ 有靶未跑：缺 env／缺工具链一律不算绿")
        return 1 if (red_fail or green_fail or unrun) else 0


if __name__ == "__main__":
    sys.exit(main())
