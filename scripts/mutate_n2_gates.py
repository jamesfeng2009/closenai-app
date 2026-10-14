"""N2 闸门的变异自证：每条不变量都要能被一次真实的破坏打红。

手法只用「备份 → 写坏 → 跑那一条测试 → 备份写回 / SQL 反操作 → diff -q 核验」，
不用 git checkout（2026-10-13 约定：仓里有并行未提交工作，checkout 会连带冲掉）。

2026-10-14 结案（台账 N2 节记录的锚点漂移）：两条 nodeid 已被后续批次改名/拆分，命中 0 次
等于空转——现换成 `test_migration_graph_is_single_head_with_n7_on_top` 与
`test_idempotency_unique_keys_are_declared`，并各补一条现断言另一半的破坏（迁移链条第二段、
inbox 唯一键），10 条 → 12 条。`TEST_DATABASE_URL` 同时改成裸 `postgresql://`：Python 侧由
`backend/tests/_pg_env.py` 补驱动，Go 侧由 `go-core/internal/dsn` 剥驱动，一个变量喂两侧。

用法：
    .venv/bin/python scripts/mutate_n2_gates.py
"""
from __future__ import annotations

import filecmp
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path("/Users/fengyu/Downloads/myproject/workspace/closenai-app")
MIG = ROOT / "backend/alembic/versions/c8a2e4b6d0f2_add_canonical_runtime_tables.py"
N7_MIG = ROOT / "backend/alembic/versions/d9b1f3a5c7e9_add_outbox_messages.py"
SQL = ROOT / "go-core/db/queries/canonical.sql"
USER_SERVICE = ROOT / "backend/services/user_service.py"
PROBE = ROOT / "backend/services/_n2_mutation_probe.py"
PG = ["docker", "exec", "closenai-pg-dev", "psql", "-U", "postgres", "-d", "closenai", "-c"]


def pg_dsn() -> str:
    """口令 env-only（2026-10-14 改）：优先 CLOSEN_PG_PASSWORD，否则从容器 env 现取。

    这里原先写死的是本机 dev 容器的**现役口令**明文，属于「凭据不入库」范畴；
    N3–N9 的脚本同口径，本条对齐它们。
    """
    pw = os.environ.get("CLOSEN_PG_PASSWORD")
    if not pw:
        env = subprocess.run(
            ["docker", "inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}", "closenai-pg-dev"],
            capture_output=True, text=True, check=True,
        ).stdout
        pw = next(line[len("POSTGRES_PASSWORD="):] for line in env.splitlines()
                  if line.startswith("POSTGRES_PASSWORD="))
    return f"postgresql://postgres:{pw}@127.0.0.1:55432/closenai"


ENV = {
    **os.environ,
    # 裸 scheme：Python 侧由 backend/tests/_pg_env 补 psycopg3 驱动，Go 侧直接吃
    "TEST_DATABASE_URL": pg_dsn(),
    "GOROOT": "/Users/fengyu/sdk/go1.27.1",
    "PATH": "/Users/fengyu/sdk/go1.27.1/bin:" + os.environ["PATH"],
}
BACKUPS: dict[Path, Path] = {}


def backup(*paths: Path) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="n2-gate-backup-"))
    for path in paths:
        bak = tmp / path.name
        shutil.copy2(path, bak)
        BACKUPS[path] = bak


def restore_files() -> None:
    for path, bak in BACKUPS.items():
        shutil.copy2(bak, path)
        assert filecmp.cmp(bak, path, shallow=False), f"恢复失败：{path}"


def psql(sql: str) -> None:
    subprocess.run([*PG, sql], capture_output=True, text=True, check=False)


def patch(path: Path, old: str, new: str = "") -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"变异锚点命中 {text.count(old)} 次，应为 1：{old[:70]!r}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def run(test_name: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-m", "pytest", "-q", "--no-header",
         "-p", "no:cacheprovider",
         str(ROOT / "backend/tests/test_canonical_schema.py") + f"::{test_name}"],
        cwd=ROOT, capture_output=True, text=True, env=ENV, timeout=900)
    return proc.returncode, proc.stdout + proc.stderr


# (测试名, 破坏动作, 事后反操作, 这条不变量在说什么)
MUTATIONS = [
    ("test_ddl_has_no_enum_no_fk_no_cascade_no_jsonb",
     lambda: patch(MIG, "            fact_id VARCHAR(36) NOT NULL,\n",
                   "            fact_id VARCHAR(36) NOT NULL REFERENCES personal_facts(id),"
                   " ON DELETE CASCADE,\n"),
     [], "DDL 里不许出现 FK/CASCADE（D-5：删除走逐表 rowcount）"),

    ("test_migration_graph_is_single_head_with_n7_on_top",
     lambda: patch(MIG, "down_revision: Union[str, Sequence[str], None] = 'b7d9e1f3a5c8'",
                   "down_revision: Union[str, Sequence[str], None] = 'a2f4b6c8d0e2'"),
     [], "N2 必须挂在原 head 之后（链条拍三段的第三段，2026-10-14 换名："
         "N7 批把用例改成 _with_n7_on_top，旧 nodeid 从此 not found 空转）"),

    ("test_migration_graph_is_single_head_with_n7_on_top",
     lambda: patch(N7_MIG, "down_revision: Union[str, Sequence[str], None] = 'c8a2e4b6d0f2'",
                   "down_revision: Union[str, Sequence[str], None] = 'b7d9e1f3a5c8'"),
     [], "N7 件必须挂在 N2 之上而不是跳过它——跳过就同时多出 N2 一个 head，"
         "灰度期的 downgrade 路径会退到错误版本"),

    ("test_every_canonical_table_declares_user_id_and_a_time_column",
     lambda: patch(MIG, "            id VARCHAR(36) PRIMARY KEY,\n"
                        "            user_id VARCHAR(36) NOT NULL,\n"
                        "            -- 02 §32：UPDATE",
                   "            id VARCHAR(36) PRIMARY KEY,\n            -- 02 §32：UPDATE"),
     [], "每张实体表都要有 user_id，否则 purge 删不净"),

    ("test_every_canonical_table_has_a_user_id_leading_index",
     lambda: patch(MIG, '    "ix_personal_goals_user_status ON personal_goals (user_id, status)",\n'),
     [], "删号按 user_id 逐表 DELETE，没有前导索引就是顺扫"),

    ("test_idempotency_unique_keys_are_declared",
     lambda: patch(MIG, '    "ix_personal_models_user_unique ON personal_models (user_id)",\n'),
     [], "一用户一模型（02 §5）的唯一性必须声明在 DDL 里（2026-10-14 换名：旧 nodeid 的断言"
         "已拆进这条，原名字 not found 后这条闸空转）"),

    ("test_idempotency_unique_keys_are_declared",
     lambda: patch(MIG, "            UNIQUE (message_id, consumer),\n"),
     [], "同消息同消费者只消费一次（18 §7）——同一个用例的另一半断言，"
         "不各自破坏一次就不知道它是不是只在第一条断言上活着"),

    ("test_purge_targets_cover_every_canonical_table",
     lambda: patch(USER_SERVICE, '    (PersonalFact, "personal_facts"),\n'),
     [], "canonical 表全部登记进 PURGE_TARGETS"),

    ("test_python_reaches_canonical_models_only_through_deletion",
     lambda: PROBE.write_text("from backend.models.canonical import Insight\n\n\n"
                              "def read(_user_id):\n    return Insight\n", encoding="utf-8"),
     [], "Python 只许通过 purge 碰 canonical（V3 06 §5）"),

    ("test_sqlc_queries_cover_minimum_v1",
     lambda: patch(SQL, "-- name: ListActiveFacts :many", "-- name: ListActiveFactsV2 :many"),
     [], "V3 02 §37 九问必须逐问有 sqlc 查询"),

    ("test_live_schema_matches_orm_declaration",
     lambda: psql("ALTER TABLE personal_facts ALTER COLUMN user_id DROP NOT NULL"),
     ["ALTER TABLE personal_facts ALTER COLUMN user_id SET NOT NULL"],
     "实库列/唯一键与 ORM 声明逐条对账"),

    ("test_live_db_has_no_enum_or_jsonb_or_new_fk",
     lambda: psql("CREATE TABLE _n2_mutation_fk (id VARCHAR(36) PRIMARY KEY,"
                  " user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE)"),
     ["DROP TABLE IF EXISTS _n2_mutation_fk"],
     "库里不许有 enum / jsonb / 新外键（四条覆写的库级事实）"),
]


def main() -> int:
    backup(MIG, N7_MIG, SQL, USER_SERVICE)
    problems: list[str] = []
    for test_name, mutate, cleanup, claim in MUTATIONS:
        try:
            if PROBE.exists():
                PROBE.unlink()
            mutate()
            code, out = run(test_name)
            reason = re.search(r"(?m)^E +(.{1,120})", out)
            if code == 0:
                problems.append(f"{test_name}：破坏后闸门仍然绿，等于没拍住")
                verdict = "GREEN(坏)"
            elif re.search(r"\b\d+ failed\b", out):
                verdict = "RED "
            else:
                verdict = "ERROR"
                problems.append(f"{test_name}：没跑出断言失败，先看输出\n{out[-500:]}")
            print(f"{verdict} {test_name[:56]:58} {claim}")
            if reason:
                print(f"      └─ {reason.group(1).strip()[:120]}")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{test_name}：变异脚本自身出错 {exc}")
            print(f"SKIP {test_name[:56]:58} 变异脚本出错：{exc}")
        finally:
            for sql in cleanup:
                psql(sql)
    if PROBE.exists():
        PROBE.unlink()
    restore_files()

    print("\n=== 恢复核验 ===")
    for path, bak in BACKUPS.items():
        same = filecmp.cmp(bak, path, shallow=False)
        print(f"{'OK   ' if same else 'DRIFT'} {path.relative_to(ROOT)}")
        if not same:
            problems.append(f"{path.name} 恢复后内容与备份不一致")
    print(f"\n=== 汇总：{len(MUTATIONS)} 条变异，{len(problems)} 条异常 ===")
    for row in problems:
        print(" -", row)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
