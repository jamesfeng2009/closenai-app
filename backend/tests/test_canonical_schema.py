"""canonical 表的验收闸（tasks.md N2 + N7）。

这一批表是 N3–N7 的地基，建错的代价是把同一套状态机写两遍（裁决 §八-6 / P5），
所以闸门拍的 not only「表存在」，而是**六件容易被写歪的事**：

1. 静态口径（无需数据库，常驻 CI）
   - DDL 里没有 Postgres enum、没有外键、没有 ON DELETE CASCADE、没有 jsonb
     —— 分别对应裁决 §五-4、D-5、以及实测库现状（`pg_type typtype='e'` 0 行、
     `information_schema.columns data_type='jsonb'` 0 行、全库仅 refresh_tokens 一条 FK）。
   - 每张表都带 user_id（D-5 不靠级联删，进不了 PURGE_TARGETS 就永远删不净），
     唯一例外是投递元数据表（inbox_messages）。
   - 迁移图仍是单头线性：N2 件 `c8a2e4b6d0f2` 挂在原 head 之后，N7 件 `d9b1f3a5c7e9`
     再挂在 N2 之上（**outbox 走新迁移而不是改已应用的 N2 件**，见裁决七-6 N7-1）。
   - Python 侧只允许 purge 一条路径引用 canonical 模型（V3 06 §5「禁直连 canonical」
     的例外登记在 D-5，闸门保证例外不扩大）。
   - sqlc 查询覆盖 V3 02 §37 的九个问题；生成物与 `.sql` 无漂移（缺 sqlc 则 skip）。
2. 实库对账（`TEST_DATABASE_URL`，指向 `alembic upgrade head` 后的库）
   - 22 张表的列名/可空性/唯一索引与 `backend/models/canonical.py` 的声明逐条一致。
     这条不是形式主义：指纹唯一索引最初在迁移里被建成普通索引，同一指纹的第二条
     pattern 直接写进去了，是 Go 侧幂等测试抓出来的；对账闸把它变成跑一次就红。
   - scheme 随便给（裸 `postgresql://` 也吃），由 `_pg_env.pg_test_url()` 归一到 psycopg3；
     缺 env 默认 skip，声明「同基线」时用 `CLOSEN_REQUIRE_PG=1` 把 skip 变成 fail。
3. 迁移往返（`N2_MIGRATE_DATABASE_URL`，指向一次性建的空库）
   - upgrade head → downgrade -1 → upgrade head 全绿，且回退后本批的表真的消失。

两件迁移的表清单**合并后**过闸（`_all_tables()`/`_all_index_stmts()`），不各写一遍闸：
outbox 与 canonical 21 表受的是同一套约束（无 enum/FK/CASCADE/jsonb、user_id、
user_id 打头索引），分件特判就是给下一批表留一个漏检的口子。
"""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _pg_env import pg_test_url

ROOT = Path(__file__).resolve().parents[2]
N2_MIGRATION_PATH = (
    ROOT / "backend" / "alembic" / "versions"
    / "c8a2e4b6d0f2_add_canonical_runtime_tables.py"
)
N7_MIGRATION_PATH = (
    ROOT / "backend" / "alembic" / "versions"
    / "d9b1f3a5c7e9_add_outbox_messages.py"
)
TRACE_MIGRATION_PATH = (
    ROOT / "backend" / "alembic" / "versions"
    / "e7a2c4b6d8f1_add_outbox_trace_id.py"
)
QUERIES_PATH = ROOT / "go-core" / "db" / "queries" / "canonical.sql"
GO_DIR = ROOT / "go-core"

N2_REVISION = "c8a2e4b6d0f2"
N2_PARENT = "b7d9e1f3a5c8"
N7_REVISION = "d9b1f3a5c7e9"
# 批 T-B（工作单 T-2）：outbox_messages.trace_id 列，链上新单头
TRACE_REVISION = "e7a2c4b6d8f1"
# W5 合规批（2026-10-18）三件线性挂在 trace 件之上，T1-01 的导出 job 件再挂在 W5 之上
W5_CONSENT_REVISION = "a1c3e5f7b9d1"  # T5-01 user_consents
W5_QUIET_REVISION = "b2d4f6a8c0e2"  # T5-02 users.quiet_start/quiet_end
W5_HEAD_REVISION = "c3e5a7b9d1f2"  # T5-03 chat_messages 幂等键 + journey 日序唯一
T101_EXPORT_REVISION = "d4f6a8b0c2e4"  # T1-01 data_export_jobs
T102_ERASURE_REVISION = "e5a7b9d1f3a5"  # T1-02 posthog_erasure_retries（当前 head）

# N2 的 21 张新表（ai_generation_runs 属 O4 已交付，N2 沿用同一份 DDL，不在此列）
N2_TABLES = (
    "personal_models",
    "personal_model_versions",
    "personal_model_events",
    "source_records",
    "evidence",
    "personal_facts",
    "fact_evidence",
    "personal_goals",
    "personal_commitments",
    "patterns",
    "pattern_evidence",
    "insights",
    "insight_evidence",
    "insight_patterns",
    "proactives",
    "proactive_insights",
    "proactive_patterns",
    "outcomes",
    "outcome_links",
    "user_feedback",
    "inbox_messages",
)
# N7 的投递面（18 §3 的 outbox；裁决七-6 N7-1）
N7_TABLES = ("outbox_messages",)
CANONICAL_TABLES = N2_TABLES + N7_TABLES
# 不带 user_id 的表：18 §7 的投递元数据，无个人内容，故不入 purge 清单
NO_USER_ID_TABLES = ("inbox_messages",)
# 纯关联表：V3 02 §12/§14/§16/§19/§21/§23 原文即只有端点列 + 主键，无 created_at，
# 时间语义由端点表（evidence/insight/outcome…）承担。inbox_messages 用 18 §4 的 received_at。
ASSOCIATION_TABLES = (
    "fact_evidence",
    "pattern_evidence",
    "insight_evidence",
    "insight_patterns",
    "proactive_insights",
    "proactive_patterns",
    "outcome_links",
)

# V3 02 §37「Minimum V1 Queries」逐问 → sqlc 查询名（缺一问即验收不成立）
MINIMUM_V1_QUERIES = {
    "what_do_we_know": ("ListActiveFacts",),
    "why_do_we_know": ("ListEvidenceForFact",),
    "what_patterns_exist": ("ListLivePatterns",),
    "why_does_a_pattern_exist": ("ListEvidenceForPattern",),
    "what_did_the_ai_tell_the_user": ("ListPublishedInsights",),
    "why_was_it_said": ("ListEvidenceForInsight", "ListPatternsForInsight"),
    "what_did_the_user_correct": ("ListUserFeedback",),
    "what_changed": ("ListModelEvents",),
    "what_happened_after_intervention": ("ListOutcomesForUser", "ListOutcomeLinks"),
    # §37 之外，N2 验收点名的两条硬语义：上次何时确认 / 当前在用的模型版本
    "when_was_it_last_confirmed": ("GetFact",),
    "which_model_version_is_current": ("GetPersonalModel",),
}


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migrations():
    return (_load_module("n2_migration", N2_MIGRATION_PATH),
            _load_module("n7_migration", N7_MIGRATION_PATH))


def _all_tables() -> dict[str, str]:
    """两件迁移的表名 → DDL 主体。合并后过闸，键序即两件迁移的先后。"""
    out: dict[str, str] = {}
    for mod in _migrations():
        for name, body in mod._TABLES:
            assert name not in out, f"{name} 在两件迁移里各建一次，DDL 属主分裂"
            out[name] = body
    return out


def _all_plain_indexes() -> list[str]:
    """普通索引清单（不含唯一索引）：两件迁移合并。分开取是因为「唯一索引混进普通
    清单」这条闸必须拿两份清单对撞，合并后就查不出来了。"""
    out: list[str] = []
    for mod in _migrations():
        out += list(mod._INDEXES)
    return out


def _all_unique_indexes() -> list[str]:
    out: list[str] = []
    for mod in _migrations():
        out += list(mod._UNIQUE_INDEXES)
    return out


def _all_index_stmts() -> list[str]:
    return _all_plain_indexes() + _all_unique_indexes()


# ── 1. 静态口径 ──


def test_ddl_has_no_enum_no_fk_no_cascade_no_jsonb():
    """裁决 §五-4 + D-5：状态用 String、删除靠 purge，不靠库级 enum/FK/CASCADE。

    只看真正的 DDL：每张表的体里夹了带 V3 出处的注释，注释里会出现「JSONB」「REFERENCES」
    这类被否掉的字面量，不排除就会自我打脸（2026-10-13 首跑即因此误报）。
    """
    ddl = "\n".join(_all_tables().values())
    ddl += "\n" + "\n".join(_all_index_stmts())
    ddl = "\n".join(line for line in ddl.splitlines() if not line.strip().startswith("--"))
    upper = ddl.upper()
    for forbidden in ("CREATE TYPE", "AS ENUM", "REFERENCES", "ON DELETE", "JSONB"):
        assert forbidden not in upper, f"DDL 里出现了 {forbidden}：本批表的四条覆写口径见迁移 docstring"
    # 状态类列必须是 varchar（enum 的替代口径）
    for match in re.finditer(r"^\s*(\w+)\s+([A-Z0-9(, )]+),?$", ddl, re.MULTILINE):
        name, sql_type = match.group(1), match.group(2).upper()
        if name in {"status", "role", "origin", "result", "claim_type", "evidence_type",
                    "feedback_type", "trigger_type", "outcome_type", "target_type",
                    "source_type", "event_type", "aggregate_type", "reason", "consumer"}:
            assert sql_type.startswith("VARCHAR"), f"{name} 不是 VARCHAR：{sql_type}"


def test_migration_graph_is_single_head_with_n7_on_top():
    """33 件迁移（含本批）里出现过并行 head（b3c5d7e9f1a2 就是历史上的合并件），所以这条必须拍。

    用 alembic 自己的 ScriptDirectory 而不是手搓正则：仓库里 `revision` 有两种写法
    （`revision: str = '...'` 与 `revision = "..."`），首跑的正则漏了后者，把 8 个迁移
    当成孤儿、误报出 9 个 head。离线解析不连库。

    链条拍三段：单头 = N7 件 → N7 挂 N2 → N2 挂原 head。**N7 走新迁移而不是改 N2 件**
    的理由见裁决七-6 N7-1（改已应用的件＝版本记录与真实 schema 漂移）。
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(ROOT / "backend" / "alembic.ini")))
    heads = script.get_heads()
    assert heads == [T102_ERASURE_REVISION], (
        f"alembic head = {heads}，T1-02 之后须是单头 {T102_ERASURE_REVISION}"
    )
    # 新件必须挂在既有 head 之上（不是另起一条链）：退一件就回到 T1-01 的导出件。
    assert script.get_revision(T102_ERASURE_REVISION).down_revision == T101_EXPORT_REVISION, (
        "T1-02 的重试台账件没挂在 T1-01 导出件之上，链形状变了")
    export_down = script.get_revision(T101_EXPORT_REVISION).down_revision
    assert export_down == W5_HEAD_REVISION, (
        f"T1-01 导出件挂在 {export_down}，应为 T5-03 的 {W5_HEAD_REVISION}"
    )
    w5_down = script.get_revision(W5_HEAD_REVISION).down_revision
    assert w5_down == W5_QUIET_REVISION, f"T5-03 件挂在 {w5_down}，应为 T5-02 的 {W5_QUIET_REVISION}"
    quiet_down = script.get_revision(W5_QUIET_REVISION).down_revision
    assert quiet_down == W5_CONSENT_REVISION, f"T5-02 件挂在 {quiet_down}，应为 T5-01 的 {W5_CONSENT_REVISION}"
    trace_down = script.get_revision(TRACE_REVISION).down_revision
    assert trace_down == N7_REVISION, f"trace 件挂在 {trace_down}，应为 N7 的 {N7_REVISION}"
    n7_down = script.get_revision(N7_REVISION).down_revision
    assert n7_down == N2_REVISION, f"N7 迁移挂在 {n7_down}，应为 N2 的 {N2_REVISION}"
    down = script.get_revision(N2_REVISION).down_revision
    assert down == N2_PARENT, f"N2 迁移挂在 {down}，应为原 head {N2_PARENT}"


def test_every_canonical_table_declares_user_id_and_a_time_column():
    """D-5：删除靠逐表 rowcount，没有 user_id 的表等于删不净。

    时间列按 V3 原文：实体表用 created_at，inbox_messages 用 18 §4 的 received_at，
    纯关联表原文就没有时间列（时间语义在端点表上），此处不擅自加列。
    """
    tables = _all_tables()
    assert set(tables) == set(CANONICAL_TABLES), (
        f"迁移表集合与验收清单不一致：多 {set(tables) - set(CANONICAL_TABLES)} / "
        f"少 {set(CANONICAL_TABLES) - set(tables)}")
    for name, body in tables.items():
        if name in NO_USER_ID_TABLES:
            assert "user_id" not in body, f"{name} 不该带 user_id（无个人内容，不入 purge 清单）"
            assert "received_at TIMESTAMPTZ" in body, f"{name} 缺 18 §4 的 received_at"
            continue
        assert re.search(r"^\s*user_id VARCHAR\(36\) NOT NULL,$", body, re.MULTILINE), (
            f"{name} 缺 user_id VARCHAR(36) NOT NULL")
        if name not in ASSOCIATION_TABLES:
            assert "created_at TIMESTAMPTZ" in body, f"{name} 缺 created_at"
    # 投递面（N7）自己的时间三面：入队、可再取、已发布，缺一就没法算退避与租约
    outbox_body = _all_tables()["outbox_messages"]
    for col in ("available_at TIMESTAMPTZ", "published_at TIMESTAMPTZ",
                "updated_at TIMESTAMPTZ"):
        assert col in outbox_body, f"outbox_messages 缺 {col.split()[0]}（18 §3 的投递面列）"


def test_purge_targets_cover_every_canonical_table():
    from backend.models.canonical import Base  # noqa: F401  触发 metadata 注册
    from backend.services.user_service import PURGE_TARGETS

    keys = {key for _, key in PURGE_TARGETS}
    missing = [
        table for table in CANONICAL_TABLES
        if table not in NO_USER_ID_TABLES and table not in keys
    ]
    assert not missing, f"以下 canonical 表未登记 PURGE_TARGETS：{missing}"
    for table in NO_USER_ID_TABLES:
        assert table not in keys, f"{table} 无 user_id，purge 循环会炸在 AttributeError 上"


def test_every_canonical_table_has_a_user_id_leading_index():
    """D-5 的代价：删号靠 `DELETE … WHERE user_id = ?` 逐表跑 22 次，没有以 user_id
    打头的索引就是一次顺扫。单列索引不重复建（复合索引的前导列已覆盖），
    所以这条拍的是「至少有且只由复合索引承担」。"""
    index_stmts = _all_index_stmts()
    for name, body in _all_tables().items():
        if name in NO_USER_ID_TABLES:
            continue
        leading = [stmt for stmt in index_stmts
                   if re.search(rf"\bON {name} \(user_id[,)]", stmt)]
        if not leading:
            pk = re.search(r"PRIMARY KEY \(user_id[,)]", body)
            assert pk, f"{name} 没有以 user_id 打头的索引，purge 会退化成顺扫"


def test_idempotency_unique_keys_are_declared():
    """四处唯一性是这几批表的领域语义，不是可选优化：
    一用户一模型（02 §5）、同版本不重复（02 §6）、同消息同消费者只消费一次（18 §7）、
    同一权威事件只有一条投递意图（18 §10 的稳定 message_id）。"""
    bodies = _all_tables()
    assert "UNIQUE (personal_model_id, version)" in bodies["personal_model_versions"]
    assert "UNIQUE (message_id, consumer)" in bodies["inbox_messages"]
    # 18 §10：message_id 是逻辑操作边界的稳定 ID，重试不得新造 ⇒ 唯一键兜住双写
    assert "UNIQUE (message_id)" in bodies["outbox_messages"]
    # D-2 薄版：同一既有行只登记一次
    assert "UNIQUE (user_id, source_table, source_id)" in bodies["source_records"]
    unique_index_names = {stmt.split()[0] for stmt in _all_unique_indexes()}
    assert {"ix_personal_models_user_unique", "ix_patterns_user_fingerprint_unique"} <= (
        unique_index_names)
    # 唯一索引不许混在普通索引清单里（2026-10-13 实际犯过：指纹索引被建成普通索引）
    plain_names = {stmt.split()[0] for stmt in _all_plain_indexes()}
    assert not (plain_names & unique_index_names), f"唯一索引重复出现在普通清单：{plain_names & unique_index_names}"


def test_source_record_upsert_conflicts_on_the_declared_unique_key():
    """「同一指针只登记一行」这条幂等，**约束与写入口必须成对**（T2-03 §欠的四件之一）。

    上一条闸只证明 UNIQUE 在 DDL 里，它证明不了另一半：``InsertSourceRecord`` 的
    ``ON CONFLICT`` 目标要是写成别的（``(id)``、漏一列），数据库会把同指针的
    第二条候选当成 23505 抛出来 ⇒ ``AdmitEvidence`` 得到一个**瞬时**错误 ⇒
    同一条候选在 30s/2m/10m 阶梯上撞三次库，最后带着空白的 ``error_code`` 进 DLQ。
    而真库的幂等测（``go-core/internal/store/evidence_integration_test.go``）在没有
    ``TEST_DATABASE_URL`` 的 CI 作业里是 Skip——所以这一格必须是一条不需要库的文本闸。
    """
    declared = re.search(r"UNIQUE \(([^)]*)\)", _all_tables()["source_records"])
    assert declared, "source_records 里没有 UNIQUE(...) 了：上一条闸会先红，这里是同一格的防线"
    unique_cols = [col.strip() for col in declared.group(1).split(",")]

    sql = QUERIES_PATH.read_text(encoding="utf-8")
    stmt = re.search(r"-- name: InsertSourceRecord :one\n(.*?)(?=\n-- name: )", sql, re.DOTALL)
    assert stmt, "解不出 InsertSourceRecord 那条语句：canonical.sql 的分块写法变了，先修这条闸"
    conflict = re.search(r"ON CONFLICT \(([^)]*)\)", stmt.group(1))
    assert conflict, f"InsertSourceRecord 不带 ON CONFLICT 目标了（重跑会变成 23505）：{stmt.group(1)}"
    conflict_cols = [col.strip() for col in conflict.group(1).split(",")]

    assert conflict_cols == unique_cols, (
        f"ON CONFLICT {conflict_cols} 与 UNIQUE {unique_cols} 不成对：不匹配的那次重跑"
        "不复用既有行而是抛唯一冲突，幂等口径就变成了「重跑必失败」"
    )
    assert "DO UPDATE" in stmt.group(1), (
        "同指针复用要的是 DO UPDATE（更新 updated_at 并 RETURNING 既有 id）；"
        "DO NOTHING 的 RETURNING id 在 PG 里回空行，Go 那边就是 sqlc: no rows in result set"
    )


#: 第二条例外：**只读**。T2-02（2026-10-18）交付 observation 提取的 handler 时，
#: 工作单点名的三张表全在 canonical 侧——按 `sourceRef` 读回原文之外还要
#: `user_feedback`（负样本）与 `evidence ⋈ source_records`（24h 成本闸）。
#: Python 侧当时没有第二条路（R3b 的 gRPC 读面仍未交付，见工作单 §R3b）。
#: 所以这条例外只扩**读**，并由下面的断言钉住「一个写形态都不许出现」。
CANONICAL_READ_ONLY: dict[Path, tuple[str, ...]] = {
    ROOT / "backend" / "services" / "ai_observation.py": ("Evidence", "SourceRecord", "UserFeedback"),
}


def _canonical_write_forms(source: str, names: tuple[str, ...]) -> list[str]:
    """文件里出现的「对这几个 canonical 类的写形态」（文本级，四种就够覆盖 ORM 的写法）。"""
    hits: list[str] = []
    for name in names:
        forms = {
            "构造新行": rf"\b{name}\(",
            "add／merge": rf"\.(?:add|merge|add_all)\(\s*{name}\b",
            "语句级写": rf"\b(?:insert|update|delete)\(\s*{name}\b",
            "改字段": rf"\b{name}\.\w+\s*=[^=]",
        }
        hits.extend(f"{name}:{label}" for label, pat in forms.items() if re.search(pat, source))
    return hits


def test_python_reaches_canonical_models_only_through_deletion():
    """V3 06 §5 禁 Python 直连 canonical；例外只有两格，各自带日期与归属：
    ① **写**＝`user_service.py` 的 purge 删除路径（D-5）；
    ② **读**＝`services/ai_observation.py`（T2-02，只读，写形态一条都不许有）。
    第三个引用点仍算扩大例外 ⇒ 红。名字里的 "deletion" 指的是①，②是 2026-10-18 加的，
    保留原 nodeid 是因为 `scripts/mutate_n2_gates.py` 按这个名字打红过它（改名将让那条变异空转）。
    """
    allowed = {
        ROOT / "backend" / "models" / "canonical.py",
        ROOT / "backend" / "models" / "__init__.py",
        ROOT / "backend" / "services" / "user_service.py",
    }
    pattern = re.compile(r"from backend\.models\.canonical import|models\.canonical import")
    offenders = []
    for path in (ROOT / "backend").rglob("*.py"):
        if path in allowed or path in CANONICAL_READ_ONLY or "/tests/" in str(path):
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, (
        f"以下模块引用了 canonical 模型，越过那两条登记过的例外（V3 06 §5 / D-5 写 + T2-02 只读）："
        f"{offenders}")
    for path, names in CANONICAL_READ_ONLY.items():
        assert path.exists(), f"只读例外的文件不在了：{path} ⇒ 这条例外该跟着删，不许留着空转"
        writes = _canonical_write_forms(path.read_text(encoding="utf-8"), names)
        assert not writes, (
            f"{path.relative_to(ROOT)} 是**只读**例外，却出现了写形态 {writes}"
            "（不变量 7：canonical 态只能由 Go 的 domain 改）")


@pytest.mark.parametrize("question, names", sorted(MINIMUM_V1_QUERIES.items()))
def test_sqlc_queries_cover_minimum_v1(question, names):
    generated = set(re.findall(r"^-- name: (\w+) :", QUERIES_PATH.read_text(encoding="utf-8"),
                               re.MULTILINE))
    missing = [name for name in names if name not in generated]
    assert not missing, f"02 §37「{question}」缺 sqlc 查询：{missing}（现有 {len(generated)} 条）"


def test_sqlc_generated_code_matches_queries():
    """V3 08 §4：生成物入库且禁手改。缺 sqlc 二进制则 skip，宿主无工具链不会红。"""
    if shutil.which("sqlc") is None:
        pytest.skip("sqlc 不在 PATH")
    env = dict(os.environ)
    goroot = env.get("GOROOT") or "/Users/fengyu/sdk/go1.27.1"
    env["GOROOT"] = goroot
    env["PATH"] = f"{goroot}/bin:" + env["PATH"]
    proc = subprocess.run(["sqlc", "diff"], cwd=GO_DIR, capture_output=True, text=True,
                          env=env, timeout=180)
    assert proc.returncode == 0, (
        f"sqlc 生成物与 .sql 漂移，重跑 `cd go-core && sqlc generate`：\n"
        f"{proc.stdout}\n{proc.stderr}")


# ── 2. 实库对账（需 alembic upgrade head 后的库）──


def test_live_schema_matches_orm_declaration():
    """逐表比列名/可空性与索引（含唯一性与列序）。ORM 声明与 alembic DDL 是两个
    事实源，不拍平就会漂移——指纹唯一索引那次就是靠这条变成跑一次就红。

    按 (唯一性, 列元组) 比较而不按索引名：库里的 UNIQUE 约束会同时以
    `<table>_<cols>_key` 的自动命名索引反射出来，比名字会误报。
    """
    from sqlalchemy import create_engine, inspect

    from backend.database import Base
    import backend.models  # noqa: F401  注册全部表

    engine = create_engine(pg_test_url())
    insp = inspect(engine)
    diffs: list[str] = []
    try:
        live_tables = set(insp.get_table_names())
        absent = [t for t in CANONICAL_TABLES if t not in live_tables]
        assert not absent, f"库里没有这些 canonical 表（alembic upgrade head 跑过吗？）：{absent}"

        for table in CANONICAL_TABLES:
            declared = Base.metadata.tables[table]
            live_columns = {c["name"]: c["nullable"] for c in insp.get_columns(table)}
            declared_columns = {c.name: c.nullable for c in declared.columns}
            if live_columns != declared_columns:
                diffs.append(
                    f"{table} 列不一致 缺{set(declared_columns) - set(live_columns)} "
                    f"多{set(live_columns) - set(declared_columns)} "
                    f"可空性差异"
                    f"{[n for n in declared_columns if live_columns.get(n) != declared_columns[n]]}"
                )

            pk_cols = tuple(insp.get_pk_constraint(table)["constrained_columns"])
            indexes = insp.get_indexes(table)
            live_unique = {tuple(i["column_names"]) for i in indexes if i["unique"]}
            live_unique |= {tuple(u["column_names"])
                            for u in insp.get_unique_constraints(table)}
            live_unique |= {pk_cols} if pk_cols else set()
            live_plain = {tuple(i["column_names"]) for i in indexes if not i["unique"]}

            declared_unique = {pk_cols} if declared.primary_key else set()
            declared_plain: set[tuple[str, ...]] = set()
            for idx in declared.indexes:
                cols = tuple(c.name for c in idx.columns)
                (declared_unique if idx.unique else declared_plain).add(cols)
            for cons in declared.constraints:
                if type(cons).__name__ == "UniqueConstraint":
                    declared_unique.add(tuple(c.name for c in cons.columns))

            if live_unique != declared_unique:
                diffs.append(f"{table} 唯一键不一致 库里多{live_unique - declared_unique} "
                             f"声明里多{declared_unique - live_unique}")
            if live_plain != declared_plain:
                diffs.append(f"{table} 普通索引不一致 库里多{live_plain - declared_plain} "
                             f"声明里多{declared_plain - live_plain}")
    finally:
        engine.dispose()
    assert not diffs, "\n".join(diffs)


def test_live_db_has_every_index_the_migration_declares():
    """迁移 `_INDEXES` 是 DDL 属主，库里缺一条就等于查询计划默默退化成顺扫。
    2026-10-13 的指纹索引漂移属于「名字在、性质错」，那条由唯一键对账闸兜住。"""
    from sqlalchemy import create_engine, inspect

    expected = {stmt.split()[0] for stmt in _all_index_stmts()}
    engine = create_engine(pg_test_url())
    insp = inspect(engine)
    try:
        live_names: set[str] = set()
        for table in CANONICAL_TABLES:
            live_names |= {i["name"] for i in insp.get_indexes(table)}
            live_names |= {u["name"] for u in insp.get_unique_constraints(table)}
    finally:
        engine.dispose()
    missing = sorted(expected - live_names)
    assert not missing, f"迁移声明的索引在库里不存在：{missing}"



def test_live_db_has_no_enum_or_jsonb_or_new_fk():
    """把「覆写 V3 的四条口径」拍成库级事实，而不是文件里的注释。"""
    from sqlalchemy import create_engine, text

    engine = create_engine(pg_test_url())
    try:
        with engine.connect() as conn:
            enums = conn.execute(text(
                "select typname from pg_type where typtype = 'e'")).scalars().all()
            jsonb = conn.execute(text(
                "select table_name || '.' || column_name from information_schema.columns "
                "where data_type = 'jsonb'")).scalars().all()
            fks = conn.execute(text(
                "select conrelid::regclass::text from pg_constraint where contype = 'f'"
            )).scalars().all()
            # confdeltype 是 catalog 里的 "char"，非 FK 约束填的是空格 ' ' 而不是 NULL，
            # 所以必须限定 contype='f'，否则主键/CHECK 全被当成 ON DELETE 子句命中。
            cascades = conn.execute(text(
                "select conname from pg_constraint "
                "where contype = 'f' and confdeltype <> 'a'")).scalars().all()
    finally:
        engine.dispose()

    assert not enums, f"库里出现 enum 类型 {enums}（裁决 §五-4 要求 String + Go 侧校验）"
    assert not jsonb, f"库里出现 jsonb 列 {jsonb}（本项目全库口径是 Text 存 JSON 字符串）"
    assert set(fks) <= {"refresh_tokens"}, (
        f"canonical 新表引入了外键 {fks}；D-5 让删除走逐表 rowcount，FK 会把 purge 顺序变成硬依赖")
    assert not cascades, f"带 ON DELETE 子句的约束 {cascades}（D-5 明确不引入 CASCADE）"


# ── 3. 迁移往返（一次性空库，可选）──


def test_alembic_roundtrip_on_empty_database():
    """N2/N7/trace/W5/T1-01/T1-02 验收的「alembic upgrade/downgrade 隔离库实测」（八件逐级回退）。

    指向一个**空的**库（库里连 alembic_version 都没有），用独立 env 变量避免误打
    共享库；跑完库里有 alembic_version，所以下一次取证前要重建：
        docker exec closenai-pg-dev psql -U postgres -c 'CREATE DATABASE n2_roundtrip'
        N2_MIGRATE_DATABASE_URL='postgresql://postgres:***@127.0.0.1:55432/n2_roundtrip'
    """
    from sqlalchemy import create_engine, text

    sync_url = pg_test_url("N2_MIGRATE_DATABASE_URL", purpose="迁移往返，需一次性空库")
    engine = create_engine(sync_url)
    with engine.connect() as conn:
        existing = conn.execute(text(
            "select count(*) from information_schema.tables where table_schema = 'public'"
        )).scalar()
        has_version = conn.execute(text(
            "select count(*) from information_schema.tables "
            "where table_name = 'alembic_version'")).scalar()
    engine.dispose()
    assert not has_version, (
        f"目标库已有 alembic_version（{existing} 张表），不是空库——"
        "本测要求一次性新建库，免得对共享库做 downgrade")

    def alembic(*args: str) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "-c",
             str(ROOT / "backend" / "alembic.ini"), *args],
            cwd=ROOT, capture_output=True, text=True, timeout=600,
            env={**os.environ, "DATABASE_URL": sync_url},
        )
        assert proc.returncode == 0, f"alembic {args} 失败：\n{proc.stdout}\n{proc.stderr}"

    def tables_and_head() -> tuple[set[str], str | None]:
        engine = create_engine(sync_url)
        with engine.connect() as conn:
            names = {r[0] for r in conn.execute(text(
                "select tablename from pg_tables where schemaname = 'public'"))}
            head = conn.execute(text("select version_num from alembic_version")).scalar()
        engine.dispose()
        return names, head

    def has_trace_col() -> bool:
        from sqlalchemy import inspect as sa_inspect

        engine = create_engine(sync_url)
        try:
            cols = {c["name"] for c in sa_inspect(engine).get_columns("outbox_messages")}
        finally:
            engine.dispose()
        return "trace_id" in cols

    def has_columns(table: str, names: set[str]) -> bool:
        from sqlalchemy import inspect as sa_inspect

        engine = create_engine(sync_url)
        try:
            cols = {c["name"] for c in sa_inspect(engine).get_columns(table)}
        finally:
            engine.dispose()
        return names <= cols

    def index_names() -> set[str]:
        engine = create_engine(sync_url)
        try:
            with engine.connect() as conn:
                return {r[0] for r in conn.execute(text(
                    "select indexname from pg_indexes where schemaname = 'public'"))}
        finally:
            engine.dispose()

    alembic("upgrade", "head")
    after_up, head = tables_and_head()
    assert head == T102_ERASURE_REVISION, f"upgrade 后 head = {head}，应为 {T102_ERASURE_REVISION}"
    assert set(CANONICAL_TABLES) <= after_up, (
        f"缺表 {set(CANONICAL_TABLES) - after_up}")
    assert has_trace_col(), "upgrade 后 outbox_messages 应带 trace_id（批 T-B 的正向证人）"
    assert "user_consents" in after_up, "W5 件没建 user_consents"
    assert has_columns("users", {"quiet_start", "quiet_end"}), "W5 件没加免打扰两列"
    assert has_columns("chat_messages", {"idempotency_key"}), "W5 件没加幂等键列"
    assert {
        "uq_chat_messages_proactive_key", "uq_journey_events_user_kind_day"
    } <= index_names(), "W5 件的两条部分唯一索引没建起来"
    # T1-01 的正向证人：表与两条复合索引（cron 取 queued、GET 按用户看历史）。
    assert "data_export_jobs" in after_up, "T1-01 件没建 data_export_jobs"
    assert {
        "ix_data_export_jobs_user_created", "ix_data_export_jobs_status_created"
    } <= index_names(), "T1-01 件的两条索引没建起来"
    # T1-02 的正向证人：重试台账表＋user_id 唯一（防叠行）＋next_attempt_at 索引（cron 的扫法）。
    assert "posthog_erasure_retries" in after_up, "T1-02 件没建 posthog_erasure_retries"
    assert {
        "uq_posthog_erasure_retries_user_id", "ix_posthog_erasure_retries_next_attempt"
    } <= index_names(), "T1-02 件的唯一约束/索引没建起来"

    # 逐级回退：先退 T1-02 重试台账（表整个走），再退 T1-01 导出件（表整个走），
    # 然后退 W5 三件（T5-03 → T5-02 → T5-01），
    # 接着退 trace 件（只该走一列），最后退 N7 件（表整个走）与 N2 件（21 张全走）。
    alembic("downgrade", "-1")
    after_down_t102, head = tables_and_head()
    assert head == T101_EXPORT_REVISION, f"退 T1-02 后 head = {head}，应回到 {T101_EXPORT_REVISION}"
    assert "posthog_erasure_retries" not in after_down_t102, "T1-02 的表没退干净"
    assert "data_export_jobs" in after_down_t102, "退 T1-02 带走了 T1-01 的表——迁移边界没切开"

    alembic("downgrade", "-1")
    after_down_t101, head = tables_and_head()
    assert head == W5_HEAD_REVISION, f"退 T1-01 后 head = {head}，应回到 {W5_HEAD_REVISION}"
    assert "data_export_jobs" not in after_down_t101, "T1-01 的表没退干净"
    assert "user_consents" in after_down_t101 and set(CANONICAL_TABLES) <= after_down_t101, (
        "退 T1-01 带走了别人的表——迁移边界没切开")

    alembic("downgrade", "-1")
    _, head = tables_and_head()
    assert head == W5_QUIET_REVISION, f"退 T5-03 后 head = {head}"
    left_t503 = {"uq_chat_messages_proactive_key", "uq_journey_events_user_kind_day"} & index_names()
    assert not left_t503, f"退掉 T5-03 仍残留幂等索引 {left_t503}"
    assert not has_columns("chat_messages", {"idempotency_key"}), "T5-03 的列没退掉"

    alembic("downgrade", "-1")
    _, head = tables_and_head()
    assert head == W5_CONSENT_REVISION, f"退 T5-02 后 head = {head}"
    assert not has_columns("users", {"quiet_start", "quiet_end"}), "T5-02 的两列没退掉"

    alembic("downgrade", "-1")
    after_down_w5, head = tables_and_head()
    assert head == TRACE_REVISION, f"退 T5-01 后 head = {head}，应回到 {TRACE_REVISION}"
    assert "user_consents" not in after_down_w5, "T5-01 的表没退干净"
    assert set(CANONICAL_TABLES) <= after_down_w5, (
        "退 W5 三件把不该动的表也带走了——迁移边界没切开")

    alembic("downgrade", "-1")
    after_down_t, head = tables_and_head()
    assert head == N7_REVISION, f"退 trace 件后 head = {head}，应回到 {N7_REVISION}"
    assert "outbox_messages" in after_down_t, "退一列不该带走整张投递表"
    assert not has_trace_col(), "downgrade 后 trace_id 列仍在，回退件不完整"

    alembic("downgrade", "-1")
    after_down1, head = tables_and_head()
    assert head == N2_REVISION, f"退一件后 head = {head}，应回到 {N2_REVISION}"
    left1 = set(N7_TABLES) & after_down1
    assert not left1, f"退掉 N7 件后仍残留 {left1}（回退件不完整，§11.4 的「仅正向 + 本地可回退」失效）"
    assert set(N2_TABLES) <= after_down1, (
        f"退一件就把 N2 的表也带走了，迁移边界没切开：缺 {set(N2_TABLES) - after_down1}")

    alembic("downgrade", "-1")
    after_down2, head = tables_and_head()
    assert head == N2_PARENT, f"退两件后 head = {head}，应回到 {N2_PARENT}"
    left2 = set(CANONICAL_TABLES) & after_down2
    assert not left2, f"回退到 N2 之前仍残留 {left2}"

    alembic("upgrade", "head")
