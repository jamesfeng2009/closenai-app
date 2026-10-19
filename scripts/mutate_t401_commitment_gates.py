#!/usr/bin/env python3
"""T4-01（行动闭环的库面：personal_commitments 加 goal_id / skipped_at）的变异自证（2026-10-19）。

工单 §验收三句，每句都有一侧的靶（[[feedback-mutation-anchor-must-reach-target]]）：

1. **「迁移在空库与含数据库都能跑」** ⇒ 空库那一半由 `alembic upgrade/downgrade` 往返闸承担（RT）。
   RT1 把 downgrade 的三步顺序倒过来（先删列、再删那条覆盖该列的索引）：静态闸**看不出差别**
   （它按字面查 drop_index/drop_column 在不在），只有真 PG 会红——列一掉，PG 自动带走依赖它的
   索引，紧跟着的 `drop_index` 就报「索引不存在」。这一格正是「往返闸不是静态闸的复读」的证据。
2. **「go test ./internal/store 绿」** ⇒ 生成物面（G1…G5）与仓储面（R1）：幂等靠 SQL 的 COALESCE、
   归属靠 `AND user_id = $2`、状态值必须是 skipped、goal_id 必须真的进出、0 行必须折算成
   ErrInvalidOwnership。靶一律打在**生成物**（`canonicaldb/canonical.sql.go`）而不是
   `db/queries/canonical.sql`——后者不参与执行，改它一条 Go 测试都不会红（那由 SQLC 对账闸兜住，
   见 Q1/Q2：改 .sql 只让 `sqlc diff` 红，这是「源与产物两个真源」的现行代价，已登记归 T6-03）。
3. **「列级对账通过」** ⇒ ORM 面（O1…O3）：撤列、把可空改成非空、把索引列序换掉，
   三句都只有 `test_live_schema_matches_orm_declaration` 这条**对实库**的闸能接住。

静态口径面（M1…M9，靶 = 迁移件本身）拍的是这一件迁移特有的四条「不许」：
NOT NULL（expand 形退化成 break 形，存量行当场卡住）、外键（D-5 让 purge 顺序变成硬依赖）、
CHECK（裁决 §五-4 把状态集合的判据留在 Go domain）、索引前导列与名字与回退完整性。
M10 拍链形状：挂错父件 ⇒ 两个 head，`alembic upgrade` 自己就先报错。

一条**刻意不纳入**的等价变异（留档以免下轮重猜）：把 ORM 里那条索引**改名**而不动列元组——
`test_live_schema_matches_orm_declaration` 按 (唯一性, 列元组) 比较而不按索引名（该闸 docstring
写明的既有决定：库里 UNIQUE 约束会附带反射出自动命名索引，比名字会误报），所以它在实库闸上恒绿；
这一格真正的判据是静态闸的索引名断言（M6），已单独有靶。

绿闸 GR1/GR2/GR3 证的是「闸不钉行号与排版」：加 15 行注释不该让任何判据变红。

纪律：串行执行（[[feedback-mutation-gates-no-concurrency]]）、copy2 备份＋`filecmp(shallow=False)`
逐字节写回、不用 git checkout（[[feedback-mutation-restore-no-git-checkout]]）；每条红闸先跑基线，
基线本来就不绿或前置缺失一律记「未跑」（[[feedback-gate-needs-positive-witness]]）；
Go 侧 `--- SKIP` 与 `[build failed]`、pytest 侧 `ERROR <节点>` 与 `no tests ran` 都**不算打红**——
那些红的是环境，不是判据。

用法（RT 需要能重建的一次性空库，缺 env 的那侧全部记「未跑」不算绿）：
    set -a && . /tmp/t206_env.sh && set +a
    export CLOSEN_REQUIRE_PG=1
    export N2_MIGRATE_DATABASE_URL="${TEST_DATABASE_URL%/*}/n2_roundtrip"
    GOROOT=/Users/fengyu/sdk/go1.27.1 ./.venv/bin/python scripts/mutate_t401_commitment_gates.py --check
    GOROOT=/Users/fengyu/sdk/go1.27.1 ./.venv/bin/python scripts/mutate_t401_commitment_gates.py
    ./.venv/bin/python scripts/mutate_t401_commitment_gates.py --only M1,G1,RT1
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
from urllib.parse import urlsplit

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
RT_ENV = "N2_MIGRATE_DATABASE_URL"
RT_CONTAINER = "closenai-pg-dev"

MIG = REPO / "backend" / "alembic" / "versions" / "f6b8d0e2a4c6_add_commitment_goal_and_skip.py"
MODELS = REPO / "backend" / "models" / "canonical.py"
QUERIES = GO_CORE / "db" / "queries" / "canonical.sql"
GEN = GO_CORE / "internal" / "store" / "canonicaldb" / "canonical.sql.go"
REPO_GO = GO_CORE / "internal" / "store" / "canonical_repo.go"

SCHEMA = "backend/tests/test_canonical_schema.py"
STATIC = f"{SCHEMA}::test_t401_commitment_columns_are_expand_shaped_and_index_is_user_id_leading"
GRAPH = f"{SCHEMA}::test_migration_graph_is_single_head_with_n7_on_top"
SQLC = f"{SCHEMA}::test_sqlc_generated_code_matches_queries"
LIVE = f"{SCHEMA}::test_live_schema_matches_orm_declaration"
RT = f"{SCHEMA}::test_alembic_roundtrip_on_empty_database"

PKG_STORE = "./internal/store/"
T = "\t"
GO_ROUNDTRIP = "^TestCommitmentGoalIDRoundTripsAndNullMeansNoGoal$"
GO_CLOCK = "^TestSkipCommitmentIsIdempotentOnTheClock$"
GO_OWNER = "^TestSkipCommitmentRejectsForeignUser$"

SKIP_SET = "COALESCE(skipped_at, now())"
SET_LINE = f"SET status = 'skipped', skipped_at = {SKIP_SET}, updated_at = now()\n"

MUTATIONS: list[tuple] = [
    # ── 1. 迁移件本身：四条「不许」＋索引与回退（靶 MIG，闸 STATIC／GRAPH，离线）──
    (
        "M1", "goal_id 建成 NOT NULL ⇒ expand 形退化成 break 形：老代码不写这一列，存量行在迁移当场卡住",
        [(MIG, '        sa.Column("goal_id", sa.String(length=36), nullable=True),\n',
          '        sa.Column("goal_id", sa.String(length=36), nullable=False),\n')],
        "pytest", STATIC, "",
    ),
    (
        "M2", "skipped_at 建成 NOT NULL＋server_default ⇒ 「没跳过」与「跳过了但没记时间」在类型上并成一个",
        [(MIG, '        sa.Column("skipped_at", sa.DateTime(timezone=True), nullable=True),\n',
          '        sa.Column("skipped_at", sa.DateTime(timezone=True), nullable=False,\n'
          '                                server_default=sa.text("now()")),\n')],
        "pytest", STATIC, "",
    ),
    (
        "M3", "给 goal_id 加外键 ⇒ D-5 的逐表 rowcount 删除变成硬依赖顺序，purge 会随机撞墙",
        [(MIG, '        sa.Column("goal_id", sa.String(length=36), nullable=True),\n    )\n',
          '        sa.Column("goal_id", sa.String(length=36), nullable=True),\n'
          '        sa.ForeignKeyConstraint(["goal_id"], ["personal_goals.id"]),\n    )\n')],
        "pytest", STATIC, "",
    ),
    (
        "M4", "给 status 加库级 CHECK ⇒ 裁决 §五-4 判在 Go domain 的状态集合，改一次取值就要改一次迁移",
        [(MIG, '    op.create_index(\n        "ix_personal_commitments_goal",\n',
          '    op.execute(\n'
          '        "ALTER TABLE personal_commitments ADD CONSTRAINT ck_commitment_status "\n'
          '        "CHECK (status IN (\'active\', \'completed\'))"\n'
          '    )\n'
          '    op.create_index(\n        "ix_personal_commitments_goal",\n')],
        "pytest", STATIC, "",
    ),
    (
        "M5", "索引列序换成 (goal_id, user_id, status) ⇒ 前导列变了，purge 那条 user_id 打头的闸退化成顺扫，\n"
             "     而「某人某目标下的行动」这个查询看起来仍然走了索引——只有静态闸看得见",
        [(MIG, '        ["user_id", "goal_id", "status"],\n',
          '        ["goal_id", "user_id", "status"],\n')],
        "pytest", STATIC, "",
    ),
    (
        "M6", "索引改名 ⇒ 台账与快照里的名字对不上，回退件按名删会删不掉任何东西",
        [(MIG, '        "ix_personal_commitments_goal",\n', '        "ix_personal_commitments_model",\n')],
        "pytest", STATIC, "",
    ),
    (
        "M7", "回退件漏掉 drop_index ⇒ 退完列还剩一条引用不存在列的索引，下一件迁移以为自己在干净库上跑",
        [(MIG, '    op.drop_index("ix_personal_commitments_goal", table_name="personal_commitments")\n', "")],
        "pytest", STATIC, "",
    ),
    (
        "M8", "回退件漏掉 goal_id 那一列 ⇒ 本地回退后库里长期多挂一列，快照与库漂移",
        [(MIG, '    op.drop_column("personal_commitments", "goal_id")\n', "")],
        "pytest", STATIC, "",
    ),
    (
        "M9", "upgrade 根本不建索引 ⇒ 行动读路径退化成顺扫，而列还是对的",
        [(MIG, '    op.create_index(\n        "ix_personal_commitments_goal",\n'
              '        "personal_commitments",\n        ["user_id", "goal_id", "status"],\n    )\n', "")],
        "pytest", STATIC, "",
    ),
    (
        "M10", "把 T4-01 件挂到 T1-01 的导出件之下 ⇒ 链上两个 head，alembic upgrade 自己先失败",
        [(MIG, "down_revision: Union[str, Sequence[str], None] = 'e5a7b9d1f3a5'\n",
          "down_revision: Union[str, Sequence[str], None] = 'd4f6a8b0c2e4'\n")],
        "pytest", GRAPH, "",
    ),
    # ── 2. 查询源 vs 生成物：两个真源的代价（靶 QUERIES，闸 SQLC）──
    (
        "Q1", "只改不参与执行的 `db/queries/canonical.sql`（摘掉 COALESCE）⇒ Go 一条测试都不红，"
              "必须靠 `sqlc diff` 这条对账闸兜住（登记归 T6-03）",
        [(QUERIES, SET_LINE, SET_LINE.replace(SKIP_SET, "now()"))],
        "pytest-sqlc", SQLC, "",
    ),
    (
        "Q2", "查询源里的 INSERT 不带 goal_id ⇒ 源与产物漂移，下次 sqlc generate 会把 Go 侧的绑定抹掉",
        [(QUERIES, "\tid, user_id, personal_model_id, title, description, status, due_at, goal_id\n"
                  ") VALUES ($1, $2, $3, $4, $5, $6, $7, $8);\n",
          "\tid, user_id, personal_model_id, title, description, status, due_at\n"
          ") VALUES ($1, $2, $3, $4, $5, $6, $7);\n")],
        "pytest-sqlc", SQLC, "",
    ),
    # ── 3. 生成物 SQL：真库跑的就是这一侧（靶 GEN，闸 = 三条 store 集成测）──
    (
        "G1", "摘掉 COALESCE ⇒ 第二次跳过把「什么时候跳过的」刷成最后一次点击的时刻，审计事实在这里被改写",
        [(GEN, SET_LINE, SET_LINE.replace(SKIP_SET, "now()"))],
        "go", PKG_STORE, GO_CLOCK,
    ),
    (
        "G2", "跳过写成 status='completed' ⇒ Baseline 要的「跳过是一等状态」没了，它仍然只是「完成」的别名",
        [(GEN, SET_LINE, SET_LINE.replace("status = 'skipped'", "status = 'completed'"))],
        "go", PKG_STORE, GO_CLOCK,
    ),
    (
        "G3", "skip 的 WHERE 摘掉 `AND user_id = $2` ⇒ 别人的行动变成我的可写对象，而返回形状一模一样",
        [(GEN, SET_LINE + "WHERE id = $1 AND user_id = $2\n", SET_LINE + "WHERE id = $1\n")],
        "go", PKG_STORE, GO_OWNER,
    ),
    (
        "G4", "InsertCommitment 把 goal_id 绑成恒 NULL ⇒ 行动与目标的连接在写入这一跳就断了，读回来还是 NULL",
        [(GEN, "\t\targ.DueAt,\n\t\targ.GoalID,\n", "\t\targ.DueAt,\n\t\tpgtype.Text{},\n")],
        "go", PKG_STORE, GO_ROUNDTRIP,
    ),
    (
        "G5", "ListCommitments 用 `NULL AS goal_id` 占位而不是真读这一列 ⇒ 形状对、列数对，只有值恒空",
        [(GEN, "       goal_id, created_at, updated_at\n",
          "       NULL::varchar AS goal_id, created_at, updated_at\n")],
        "go", PKG_STORE, GO_ROUNDTRIP,
    ),
    # ── 4. 仓储面：0 行不等于成功（靶 REPO_GO）──
    (
        "R1", "摘掉 ErrNoRows→ErrInvalidOwnership 的折算 ⇒ 跨用户跳过返回「成功」，越权没有回声",
        [(REPO_GO,
          "\t_, err := t.q.SkipCommitment(ctx,\n"
          "\t\tcanonicaldb.SkipCommitmentParams{ID: commitmentID, UserID: userID})\n"
          "\tif errors.Is(err, pgx.ErrNoRows) {\n"
          '\t\treturn fmt.Errorf("%w: commitment %s", domain.ErrInvalidOwnership, commitmentID)\n'
          "\t}\n",
          "\t_, err := t.q.SkipCommitment(ctx,\n"
          "\t\tcanonicaldb.SkipCommitmentParams{ID: commitmentID, UserID: userID})\n"
          "\t// 变异 R1：0 行不再折算成归属错误\n")],
        "go", PKG_STORE, GO_OWNER,
    ),
    # ── 5. ORM 与实库的列级对账（靶 MODELS，闸 LIVE）──
    (
        # 第一版这里只撤 goal_id 一列、留着那条引用它的 Index，实测**打不红**（2026-10-19）：
        # SQLAlchemy 在建表时就报 `ConstraintColumnNotFoundError: no column named 'goal_id'`，
        # 红的是 import/收集期的 ERROR，不是这条对账判据本身（[[feedback-mutation-anchor-must-reach-target]]）。
        # 换成「模型整体回到 T4-01 之前」的两行撤法——那才是这一格真实会发生的坏法。
        "O1", "ORM 把 goal_id 连同它的索引一起撤掉（模型回到 T4-01 之前，库里两列都还在）"
              "⇒ 权威模型与 DDL 分家，T4-02 按模型写会写不到列",
        [(MODELS,
          "    # 无 FK（D-5：删除靠逐表 rowcount，FK 会把 purge 顺序变成硬依赖）；归属靠 user_id 谓词。\n"
          "    goal_id: Mapped[str | None] = mapped_column(String(36), nullable=True)\n",
          "    # 变异 O1：ORM 撤掉这一列，库里的列还在\n"),
         (MODELS,
          "\n        # 读路径是「某人名下、某目标下按状态筛」；前导列 user_id 同时兜住 purge 那条闸。\n"
          '        Index("ix_personal_commitments_goal", "user_id", "goal_id", "status"),\n',
          "\n")],
        "pytest-pg", LIVE, "",
    ),
    (
        "O2", "ORM 把 skipped_at 声明成非空 ⇒ 对账闸两侧的可空性相反，下一件迁移会照着错的形状写",
        [(MODELS,
          "    skipped_at: Mapped[datetime | None] = mapped_column(\n"
          "        DateTime(timezone=True), nullable=True\n    )\n",
          "    skipped_at: Mapped[datetime] = mapped_column(\n"
          "        DateTime(timezone=True), nullable=False\n    )\n")],
        "pytest-pg", LIVE, "",
    ),
    (
        "O3", "ORM 的索引列序换成 (goal_id, user_id, status) ⇒ 声明侧的前导列变了，而库里那条还是 user_id 打头",
        [(MODELS, '        Index("ix_personal_commitments_goal", "user_id", "goal_id", "status"),\n',
          '        Index("ix_personal_commitments_goal", "goal_id", "user_id", "status"),\n')],
        "pytest-pg", LIVE, "",
    ),
    # ── 6. 真库回退顺序（靶 MIG，闸 RT：只有 PG 会红的判据）──
    (
        "RT1", "downgrade 先删列、再删那条覆盖该列的索引 ⇒ PG 已随列自动带走索引，drop_index 当场报「不存在」；"
               "静态闸按字面查 drop_index 在不在，看不见这个顺序（这一格证明往返闸不是静态闸的复读）",
        [(MIG,
          '    op.drop_index("ix_personal_commitments_goal", table_name="personal_commitments")\n'
          '    op.drop_column("personal_commitments", "skipped_at")\n'
          '    op.drop_column("personal_commitments", "goal_id")\n',
          '    op.drop_column("personal_commitments", "skipped_at")\n'
          '    op.drop_column("personal_commitments", "goal_id")\n'
          '    op.drop_index("ix_personal_commitments_goal", table_name="personal_commitments")\n')],
        "pytest-rt", RT, "",
    ),
]

PY_COMMENTS = "".join(
    f"    # 绿闸注脚 {i:02d}：这一格判可空性、列序与回退完整性，不数行数也不钉排版。\n"
    for i in range(1, 16)
)
GO_COMMENTS = "".join(
    f"{T}// 绿闸注脚 {i:02d}：这套闸判的是语义与字面，不钉行号也不钉排版。\n" for i in range(1, 16)
)
PY_MODEL_COMMENTS = "".join(
    f"    # 绿闸注脚 {i:02d}：对账闸比的是列元组与可空性，不是声明的位置。\n" for i in range(1, 16)
)

GREEN: list[tuple] = [
    (
        "GR1", "迁移 upgrade 体内插 15 行注释 ⇒ 静态口径闸照旧绿（它不钉行号）",
        [(MIG, "def upgrade() -> None:\n", "def upgrade() -> None:\n" + PY_COMMENTS)],
        "pytest", STATIC, "",
    ),
    (
        "GR2", "canonical_repo.go 的 SkipCommitment 上方插 15 行注释 ⇒ 真库归属闸照旧绿",
        [(REPO_GO, "func (t *canonicalTx) SkipCommitment(ctx context.Context, userID, commitmentID string) error {\n",
          GO_COMMENTS + "func (t *canonicalTx) SkipCommitment(ctx context.Context, userID, commitmentID string) error {\n")],
        "go", PKG_STORE, GO_OWNER,
    ),
    (
        "GR3", "models/canonical.py 的 __table_args__ 上方插 15 行注释 ⇒ 列级对账闸照旧绿",
        [(MODELS, '    __table_args__ = (\n        Index("ix_personal_commitments_user_status", "user_id", "status"),\n',
          PY_MODEL_COMMENTS + '    __table_args__ = (\n        Index("ix_personal_commitments_user_status", "user_id", "status"),\n')],
        "pytest-pg", LIVE, "",
    ),
]


def _apply(edits: list) -> None:
    """锚点替换成变异体；命中数必须恰好 1——命中两次说明靶写错了。"""
    for path, old, new in edits:
        assert path.exists(), f"{path}：靶文件不存在"
        src = path.read_text(encoding="utf-8")
        n = src.count(old)
        assert n == 1, f"{path.name}：锚点命中 {n} 次（要求恰好 1 次）\n{old[:120]!r}"
        mutated = src.replace(old, new, 1)
        if path.suffix == ".py":
            ast.parse(mutated, filename=str(path))
        path.write_text(mutated, encoding="utf-8")


def _verify_anchor(edits: list) -> str | None:
    for path, old, _new in edits:
        if not path.exists():
            return "文件不存在"
        n = path.read_text(encoding="utf-8").count(old)
        if n != 1:
            return f"锚点命中 {n} 次（要 1）：{old[:60]!r}"
        if path.suffix == ".py":
            try:
                ast.parse(path.read_text(encoding="utf-8").replace(old, _new, 1))
            except SyntaxError as exc:
                return f"变异体不是合法 Python：{exc}"
    return None


def rt_db_name() -> str:
    return urlsplit(os.environ.get(RT_ENV, "")).path.lstrip("/")


def exec_sql(stmt: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["docker", "exec", RT_CONTAINER, "psql", "-U", "postgres", "-c", stmt],
        capture_output=True, text=True, timeout=300,
    )
    return proc.returncode, proc.stdout + proc.stderr


def run_pytest(node: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(VENV_PY), "-m", "pytest", node, "-q", "--no-header", "-p", "no:randomly"],
        cwd=REPO, capture_output=True, text=True, timeout=2400,
    )
    return proc.returncode, proc.stdout + proc.stderr


def run(kind: str, target: str) -> tuple[int, str]:
    if kind == "go":
        if not Path(GO_BIN).is_file():
            return -2, f"{GO_BIN} 不存在：Go 侧用例没跑，不算绿"
        if not os.environ.get(PG_ENV):
            return -2, f"{PG_ENV} 未设置：store 集成测会整包 skip，不算绿也不算红"
        proc = subprocess.run([GO_BIN, "test", target, "-count=1", "-timeout", "300s"],
                              cwd=GO_CORE, env=GO_ENV, capture_output=True, text=True, timeout=1800)
        return proc.returncode, proc.stdout + proc.stderr
    if kind == "pytest-pg" and not os.environ.get(PG_ENV):
        # 缺库时这条用例是 skip，pytest 退出码仍是 0——不拦下来就会把「没跑」念成「基线绿」。
        return -2, f"{PG_ENV} 未设置：{target} 没跑，实库对账不算绿"
    if kind == "pytest-sqlc" and shutil.which("sqlc") is None:
        # sqlc 不在 PATH 时那条闸自己 pytest.skip（退出码 0），不拦下来就会把「没跑」念成「基线绿」。
        return -2, "sqlc 不在 PATH：源与生成物的对账闸没跑，不算绿"
    if kind == "pytest-rt":
        if not os.environ.get(RT_ENV):
            return -2, f"{RT_ENV} 未设置：往返闸没跑，不算绿"
        if shutil.which("docker") is None:
            return -2, "docker 不在 PATH：往返闸的库没法重建，不算绿"
        name = rt_db_name()
        # 往返闸每次都要一座**空库**（库里有 alembic_version 时 upgrade head 直接不对账），
        # 而一条变异要跑两遍（基线＋变异体），所以每次跑之前先重建（[[feedback-mutation-anchor-must-reach-target]]）。
        for stmt in (f"DROP DATABASE IF EXISTS {name} WITH (FORCE)", f"CREATE DATABASE {name}"):
            code, out = exec_sql(stmt)
            if code != 0:
                return -2, f"重建空库 {name} 失败（不算绿）：{out.strip()[-200:]}"
    return run_pytest(target)


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
    for env in (PG_ENV, RT_ENV):
        if not os.environ.get(env):
            print(f"提示：{env} 未设置，依赖它的靶全部记「未跑」，不算绿")

    paths = sorted({e[0] for case in cases for e in case[2]})
    with tempfile.TemporaryDirectory(prefix="mutate-t401-") as tmp:
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
