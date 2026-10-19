"""canonical DDL 的单一事实源闸门（T6-03）。

工单 §验收 三句在本文件里各有落点：

    删一行 alembic 列定义 ⇒ 生成物变红   ①（离线，两侧不同源）
    反向改 go schema       ⇒ 变红        ①（同一句判据的反方向）＋ ③（逐字节）＋ Go ④
    test_canonical_schema.py 仍绿         本文件不替代它，只补它看不见的那一侧（.sql 快照）

为什么还要再写一份闸：`test_canonical_schema.py` 拍的是 **alembic DDL ↔ ORM ↔ 实库**
三方的逐列对账，从来没人把第四方 **`go-core/db/schema/*.sql`** 摆上天平——sqlc 拿它当
schema 输入，Go 的类型、NULL 语义、列宽全从它推出来。四方里少拍一方，就是「线上库与 Go
编译期看到的表不是一张」的那种红要等到集成测试甚至运行时才出现。本文件把第四方接上，
并把它能离线判红的部分（①②④⑤）与必须连库判红的部分（③）分开：CI 的 go 作业没有 PG，
那边的 `go-core/db/schema/drift_test.go` 就只带文件面的六条。

- ① **alembic 推导的列集合 ↔ 快照的列集合**（无需数据库，常驻 CI）：一侧读迁移件的
  `_TABLES` 文本＋链上后续 add/drop_column，另一侧读 pg_dump 出来的 CREATE TABLE 体。
  两侧不同源（一个是 Python 侧 DDL 原文，一个是 PostgreSQL 的 dump 渲染），
  所以「删 alembic 一列」与「手改快照一列」都必然在一边留下痕迹。
- ② **表名单不分裂**：三份快照的表并集必须逐字等于「alembic `_TABLES` 的那 22 张」∪
  「legacy 白名单那 2 张」，且每张只属于一个文件——工单 §为什么 点名的 `users` 被
  再次建表声明就是这一格的靶。
- ③ **可复现**（需 PG）：把 `scripts/export_pg_schema.py --check` 当子进程跑一次，
  它内部 drop/create 一次性库 → `alembic upgrade head` → `pg_dump` → 与仓库文件比 DDL
  主体字节。这一条覆盖 ① 看不见的部分：列宽、可空性、默认值、索引、FK。
- ④ **指纹清单**：`body.sha256` 覆盖三份快照且与实际文件一致；并核对 Go 侧那条
  「从 dump 标记切到 EOF」的切法常量与本文件同一个串（两侧各实现一次，必须先对得上）。
- ⑤ **head 不许再抄**：`backend/tests/` 里 `HEAD = "<12 hex>"` 这种字面量归零
  （正向证人：三个文件真的在调 `head_revision()`）。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _pg_env import pg_test_url

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "go-core" / "db" / "schema"
VERSIONS_DIR = ROOT / "backend" / "alembic" / "versions"
EXPORTER = ROOT / "scripts" / "export_pg_schema.py"
GO_DRIFT_GATE = SCHEMA_DIR / "drift_test.go"

BODY_MARKER = "--\n-- PostgreSQL database dump"
CHECKSUM_NAME = "body.sha256"
CANONICAL_MIGRATIONS = (
    "c8a2e4b6d0f2_add_canonical_runtime_tables.py",
    "d9b1f3a5c7e9_add_outbox_messages.py",
)
# 与导出器同一份 legacy 白名单：这两张表建在没有 _TABLES 约定的早期迁移里。
LEGACY_TABLES = ("users", "refresh_tokens")
SNAPSHOT_FILES = ("0001_legacy_auth.sql", "0002_canonical_runtime.sql",
                  "0003_outbox_messages.sql")

# 表名 → 该表的 DDL 主体（alembic 侧原文）
_TABLES_RE = re.compile(r'\(\s*"([a-z_]+)",\s*"""(.*?)"""', re.S)
_ADD_COLUMN_RE = re.compile(r'op\.add_column\(\s*["\']([a-z_]+)["\'],\s*sa\.Column\(\s*["\']([a-z_]+)["\']')
_DROP_COLUMN_RE = re.compile(r'op\.drop_column\(\s*["\']([a-z_]+)["\'],\s*["\']([a-z_]+)["\']')
_CREATE_TABLE_RE = re.compile(r"^CREATE TABLE public\.([a-z_]+) \($", re.M)
_DUMP_LINE_RE = re.compile("^ {4}(\\w+)(?: .*)?$", re.M)
# DDL 体里不是列的那几行（内表约束、注释、行内主键）
_NON_COLUMN_PREFIXES = ("--", "CHECK", "CONSTRAINT", "UNIQUE", "PRIMARY KEY",
                        "FOREIGN KEY", "EXCLUDE")
# pg_dump 把 `_TABLES` 里的内表 CHECK 渲成 `CONSTRAINT <表>_<列>_check CHECK (...)`，
# 首词就不是列名；这一格不排掉，① 永远报「快照比 alembic 多一列 CONSTRAINT」。
_NON_COLUMN_WORDS = {"CONSTRAINT", "CHECK", "UNIQUE", "PRIMARY", "FOREIGN", "EXCLUDE"}


def _module_tables(migration: str) -> dict[str, str]:
    """从迁移件的 `_TABLES` 取 表名 → DDL 主体（与 test_canonical_schema 同一个真源）。"""
    text = (VERSIONS_DIR / migration).read_text(encoding="utf-8")
    at = text.index("_TABLES")
    body = text[at:text.index("def upgrade")] if "def upgrade" in text[at:] else text[at:]
    return {name: ddl for name, ddl in _TABLES_RE.findall(body)}


def _upgrade_body(text: str) -> str:
    """只取 `def upgrade` 到 `def downgrade` 之间那一段。

    不加这一格就会自相矛盾：同一件迁移里 upgrade 加列、downgrade 删列，两条正则都在
    同一份全文上匹配，先加后删等于白加——trace_id／goal_id／skipped_at 三列第一轮就是
    这样被吞掉的。回退方向不是 schema 的事实。
    """
    at = text.find("def upgrade")
    if at < 0:
        return ""
    end = text.find("def downgrade", at)
    return text[at:end if end > 0 else len(text)]


def _ddl_columns(ddl: str) -> set[str]:
    out = set()
    for raw in ddl.splitlines():
        line = raw.strip()
        if not line or line.startswith(_NON_COLUMN_PREFIXES):
            continue
        out.add(line.split()[0])
    return out


def alembic_columns() -> dict[str, set[str]]:
    """alembic 链**应当**建出的列集合：两件建表件 + 链上后续件的加/删列。

    后续件按正则吃 `op.add_column("<表>", sa.Column("<列>"` 与 `op.drop_column("<表>", "<列>")`
    两种写法——本仓 canonical 面的加列件（trace 件、T4-01 件）全是这个形状；
    实测 `grep -rn "ALTER TABLE" backend/alembic/versions` 里没有一条指向 canonical 表，
    所以不需要再解裸 SQL。表名不在 canonical 集合里的加列自动被忽略。
    """
    tables: dict[str, set[str]] = {}
    for migration in CANONICAL_MIGRATIONS:
        for name, ddl in _module_tables(migration).items():
            assert name not in tables, f"{name} 在两件建表迁移里各建一次"
            tables[name] = _ddl_columns(ddl)
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        if path.name in CANONICAL_MIGRATIONS:
            continue
        text = _upgrade_body(path.read_text(encoding="utf-8"))
        if not text:
            continue
        for table, column in _ADD_COLUMN_RE.findall(text):
            if table in tables:
                tables[table].add(column)
        for table, column in _DROP_COLUMN_RE.findall(text):
            tables.get(table, set()).discard(column)
    return tables


def snapshot_ddls() -> dict[str, dict[str, str]]:
    """快照文件名 → (表名 → CREATE TABLE 体)。"""
    out: dict[str, dict[str, str]] = {}
    for filename in SNAPSHOT_FILES:
        text = (SCHEMA_DIR / filename).read_text(encoding="utf-8")
        per_table: dict[str, str] = {}
        for match in _CREATE_TABLE_RE.finditer(text):
            start = match.end()
            per_table[match.group(1)] = text[start:text.index("\n);", start)]
        out[filename] = per_table
    return out


def snapshot_columns() -> dict[str, set[str]]:
    """表名 → 快照里的列名集合（跨三份文件；每张表只属于一份，由 ② 拍）。"""
    merged: dict[str, set[str]] = {}
    for per_table in snapshot_ddls().values():
        for table, body in per_table.items():
            assert table not in merged, f"{table} 在两份快照里各建一次（② 的靶）"
            merged[table] = {m for m in _DUMP_LINE_RE.findall(body)
                           if m.upper() not in _NON_COLUMN_WORDS}
    return merged


# ── ① 列集合：alembic 推导 ↔ 快照 ───────────────────────────────────────────


def test_alembic_column_set_matches_go_snapshot_column_set():
    """工单 §验收 前两句同一条判据：删 alembic 一列 ⇒ 快照多一列；手改快照一列 ⇒ 少一列。

    两侧不同源：`alembic_columns()` 读 Python 侧 DDL 原文，`snapshot_columns()` 读
    pg_dump 渲出来的 CREATE TABLE 体。任何一侧单独动都会在这一条上露出差集。
    """
    from_alembic = alembic_columns()
    from_snapshot = snapshot_columns()

    # 正向证人：闸不许扫空集。22 张 canonical 表、每张至少 2 列，先立起来。
    assert len(from_alembic) == 22, f"alembic 侧只推出 {len(from_alembic)} 张表，应为 22"
    assert all(len(cols) >= 2 for cols in from_alembic.values()), "有表的推导结果不到 2 列"
    assert all(len(cols) >= 2 for cols in from_snapshot.values())

    diffs: list[str] = []
    for table, cols in sorted(from_alembic.items()):
        snap = from_snapshot.get(table)
        if snap is None:
            diffs.append(f"{table}: 快照里没有这张表（重导 go-core/db/schema）")
            continue
        only_alembic = cols - snap
        only_snapshot = snap - cols
        if only_alembic or only_snapshot:
            diffs.append(
                f"{table}: alembic 有而快照没有 {sorted(only_alembic)}；"
                f"快照有而 alembic 没有 {sorted(only_snapshot)}")
    for table in sorted(set(from_snapshot) - set(from_alembic)):
        if table not in LEGACY_TABLES:
            diffs.append(f"{table}: 快照里有这张表，alembic 侧推不出来（谁手加了？）")
    assert not diffs, "canonical DDL 出现两份真源的漂移：\n" + "\n".join(diffs)


# ── ② 表名单不分裂 ──────────────────────────────────────────────────────────


def test_snapshot_table_sets_are_disjoint_and_cover_exactly_the_derived_list():
    """`users` 只许有一个家：三份快照的表并集＝canonical 22 ∪ legacy 2，且两两不重叠。

    legacy 那两张的**列**面不在这里对（早期迁移里混着裸 ALTER TABLE，正则推不干净），
    它们由 ③ 的逐字节闸兜；这里只拍「归谁」。
    """
    per_file = snapshot_ddls()
    seen: dict[str, str] = {}
    for filename, tables in per_file.items():
        for table in tables:
            if table in seen:
                pytest.fail(f"{table} 被 {seen[table]} 与 {filename} 各声明一次："
                            "DDL 属主分裂，两份里必然有一份是错的")
            seen[table] = filename

    expected = set(alembic_columns()) | set(LEGACY_TABLES)
    assert set(seen) == expected, (
        f"快照表集合与推导结果不符：多 {set(seen) - expected}／少 {expected - set(seen)}")
    # 正向证人：总数与每份文件的份数都钉死，扫空集过不了这一格
    assert len(seen) == 24, f"合计 {len(seen)} 张表，应为 24（canonical 22 + legacy 2）"
    counts = {filename: len(tables) for filename, tables in per_file.items()}
    assert counts == {"0001_legacy_auth.sql": 2, "0002_canonical_runtime.sql": 21,
                      "0003_outbox_messages.sql": 1}, counts
    assert seen["users"] == "0001_legacy_auth.sql", "users 的家变了，先改工单再改代码"


# ── ③ 逐字节可复现（需 PG）──────────────────────────────────────────────────


def test_snapshot_is_reproducible_from_the_alembic_chain():
    """`export_pg_schema.py --check` 内部会 drop/create 一次性库、`alembic upgrade head`、
    pg_dump，再拿 DDL 主体与仓库文件逐字节比。这一条覆盖 ① 看不见的那半（列宽/可空/
    默认值/索引/FK），也是工单 §怎么做.5 里「CI 校验生成物可复现」的本体。

    env 口径与全仓实库闸一致：缺 `TEST_DATABASE_URL` 默认 skip，`CLOSEN_REQUIRE_PG=1`
    时改判 fail；pg_dump 不在 PATH 又没给 `SCHEMA_EXPORT_DOCKER` 则另记一条 skip
    （那是宿主没有工具链，不是漂移）。
    """
    pg_test_url(purpose="T6-03 快照可复现，需要在一次性库上重跑 alembic + pg_dump")
    args = [sys.executable, str(EXPORTER), "--check"]
    docker = os.environ.get("SCHEMA_EXPORT_DOCKER", "")
    if shutil.which("pg_dump") is None:
        if not docker:
            pytest.skip("pg_dump 不在 PATH 且未给 SCHEMA_EXPORT_DOCKER：本条按「未跑」记 skip")
        args += ["--docker", docker]
    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0, (
        f"快照不可复现（exit={proc.returncode}）：\n{proc.stdout}\n{proc.stderr}")
    assert proc.stdout.count(": DDL 主体一致（") == len(SNAPSHOT_FILES), proc.stdout


# ── ④ 指纹清单与两侧的切法 ──────────────────────────────────────────────────


def test_checksum_manifest_covers_every_snapshot_and_both_sides_cut_the_same_bytes():
    """`body.sha256` 与实际文件一致，且 Go 侧那条「从 dump 标记切到 EOF」与本文件同串。

    两侧各实现了一次切法（Python 与 Go），所以对同一份字节算出的哈希必须相同；
    这里先静态确认两侧用的是同一个标记，再逐份比指纹。
    """
    import hashlib

    go_src = GO_DRIFT_GATE.read_text(encoding="utf-8")
    marker_line = re.search(r'bodyMarker\s*=\s*"(--\\n[^"]+)"', go_src)
    assert marker_line, "go-core/db/schema/drift_test.go 里找不到 bodyMarker 常量：切法失去了对撞对象"
    assert marker_line.group(1).replace("\\n", "\n") == BODY_MARKER, (
        f"Go 侧切的起点与本文件不同：Go={marker_line.group(1)!r} 本侧={BODY_MARKER!r}")

    lines = (SCHEMA_DIR / CHECKSUM_NAME).read_text(encoding="utf-8").splitlines()
    entries = [line for line in lines if line and not line.startswith("--")]
    listed = {line.split("  ", 1)[1].strip(): line.split("  ", 1)[0].strip()
              for line in entries}
    assert set(listed) == set(SNAPSHOT_FILES), (
        f"{CHECKSUM_NAME} 覆盖的文件={sorted(listed)}，应为 {sorted(SNAPSHOT_FILES)}")
    bad = []
    for filename in SNAPSHOT_FILES:
        text = (SCHEMA_DIR / filename).read_text(encoding="utf-8")
        body = text[text.index(BODY_MARKER):]
        if hashlib.sha256(body.encode("utf-8")).hexdigest() != listed[filename]:
            bad.append(filename)
    assert not bad, f"这三份的 DDL 主体与指纹不符（手改？）：{bad}"


# ── ⑤ head 不许再抄成字面量 ─────────────────────────────────────────────────


def test_alive_tests_do_not_pin_the_alembic_head_literal():
    """实库伴生测的「库到没到头」必须拿推导值比，不再各钉一份字面量（T4-01 登记的缺陷 ①）。

    归零闸配正向证人：`head_revision()` 的调用点恰好三处（export/posthog/delivery），
    少一处就是有人把这一格改回字面量了。
    """
    pinned = []
    for path in sorted((ROOT / "backend" / "tests").glob("*.py")):
        if re.search(r'^\s*HEAD\w*\s*=\s*["\'][0-9a-f]{12}["\']',
                     path.read_text(encoding="utf-8"), re.M):
            pinned.append(path.name)
    assert not pinned, f"这些文件还把 alembic head 抄成字面量：{pinned}"

    here = Path(__file__).name
    callers = [path.name for path in sorted((ROOT / "backend" / "tests").glob("*.py"))
               if path.name != here and "head_revision()" in path.read_text(encoding="utf-8")]
    assert sorted(callers) == ["test_data_export_pg.py", "test_delivery_audit_pg.py",
                               "test_posthog_erasure_retry_pg.py"], callers

    # 唯一保留的字面量在链形状闸那儿，并且它拍的确实是「链的形状」而不是「库够不够新」
    from backend.core.alembic_chain import head_revision, versions_on_disk

    canonical_path = ROOT / "backend" / "tests" / "test_canonical_schema.py"
    anchor = re.search(r'T401_COMMITMENT_REVISION = "([0-9a-f]{12})"',
                       canonical_path.read_text(encoding="utf-8"))
    assert anchor, "链形状闸里那个 head 锚点不见了：它现在是全套闸里唯一保留字面量的那一张"
    assert anchor.group(1) == head_revision(), (
        f"test_canonical_schema.py 钉的 head={anchor.group(1)}，链的 head="
        f"{head_revision()}：链形状变了，请按那张闸的口径确认一次")
    assert len(versions_on_disk()) >= 30, "versions 目录读空了，链形状闸在扫空集"
