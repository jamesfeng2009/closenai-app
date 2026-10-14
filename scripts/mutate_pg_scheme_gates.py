"""TEST_DATABASE_URL 方言归一这批闸门的变异自证（2026-10-14）。

拍的两条台账欠账：N4 末「env 口径坑」（裸串回落未安装的 psycopg2 ⇒ 假红 5 条；pgx 只认裸串
⇒ 同一个变量喂不了两侧）与 N7 末「pytest 基线口径坑」（漏导 env 时 5 条从 passed 变 skipped，
总数不变，「同基线」这句话失去取证）。

手法与其它批次一致：备份 → 写坏 → 只跑受害测试 → copy2 写回 + filecmp 逐字节核验；
不用 git checkout。绿闸（GR1/GR2）插的是无害注释，证明切片守卫不是假红制造机。

用法（单实例，严禁并发跑变异脚本）：
    .venv/bin/python -u scripts/mutate_pg_scheme_gates.py
"""
from __future__ import annotations

import filecmp
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path("/Users/fengyu/Downloads/myproject/workspace/closenai-app")
GO = ROOT / "go-core"
SCHEME = ROOT / "backend/core/pg_scheme.py"
PGENV = ROOT / "backend/tests/_pg_env.py"
DIALECT = "backend/tests/test_pg_env_dialect.py"
CANON = ROOT / "backend/tests/test_canonical_schema.py"
DBPY = ROOT / "backend/database.py"
DSN = GO / "internal/dsn/dsn.go"
DSN_TEST = GO / "internal/dsn/dsn_test.go"
GOAPI = GO / "cmd/go-api/main.go"
XCHECK = GO / "tools/crosscheck/main.go"
STOREIT = GO / "internal/store/store_integration_test.go"
INTEROP = "backend/tests/test_go_auth_interop.py"

GOROOT = "/Users/fengyu/sdk/go1.27.1"


def pg_dsn() -> str:
    """口令 env-only：优先 CLOSEN_PG_PASSWORD，否则从容器 env 现取，不回显。

    刻意用 psycopg 形态（pyproject 声明的驱动）：这样 Go 侧两条接线变异（G4/G5）
    才有牙——它们赌的就是「pgx 吃到带驱动段的串必须先归一」。
    """
    pw = os.environ.get("CLOSEN_PG_PASSWORD")
    if not pw:
        env = subprocess.run(
            ["docker", "inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}",
             "closenai-pg-dev"],
            capture_output=True, text=True, check=True).stdout
        pw = next(line[len("POSTGRES_PASSWORD="):] for line in env.splitlines()
                  if line.startswith("POSTGRES_PASSWORD="))
    return f"postgresql+psycopg://postgres:{pw}@127.0.0.1:55432/closenai"


ENV = {
    **os.environ,
    "GOROOT": GOROOT,
    "PATH": f"{GOROOT}/bin:" + os.environ["PATH"],
    "GO_BIN": f"{GOROOT}/bin/go",
    "TEST_DATABASE_URL": pg_dsn(),
}
BACKUPS: dict[Path, Path] = {}


def backup(*paths: Path):
    tmp = Path(tempfile.mkdtemp(prefix="pg-scheme-gate-backup-"))

    def run() -> None:
        for path in paths:
            bak = tmp / path.name
            shutil.copy2(path, bak)
            BACKUPS[path] = bak

    return run


def patch(path: Path, old: str, new: str):
    def run() -> None:
        text = path.read_text(encoding="utf-8")
        assert text.count(old) == 1, f"变异锚点命中 {text.count(old)} 次，应为 1：{old[:70]!r}"
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    return run


def run_pytest(target: str, test: str) -> tuple[int, str]:
    node = f"{ROOT / target}::{test}" if test else str(ROOT / target)
    proc = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-m", "pytest", "-q", "--no-header",
         "-p", "no:cacheprovider", node],
        cwd=ROOT, capture_output=True, text=True, env=ENV, timeout=900)
    return proc.returncode, proc.stdout + proc.stderr


def run_go(pkg: str, test: str) -> tuple[int, str]:
    cmd = ["go", "test", pkg, "-count=1"]
    if test:
        cmd += ["-run", test]
    proc = subprocess.run(cmd, cwd=GO, capture_output=True, text=True, env=ENV, timeout=900)
    return proc.returncode, proc.stdout + proc.stderr


def m(num: str, claim: str, kind: str, target: str, test: str, *steps):
    return (num, claim, kind, target, test, steps)


MUTATIONS = [
    # ── 归一器本体（Python）──
    m("P1", "归一的目标必须是声明过的驱动；写成裸串就等于把「回落 psycopg2」这个原始坑留在原地",
      "pytest", DIALECT, "test_bare_scheme_gets_the_declared_driver",
      backup(SCHEME),
      patch(SCHEME, 'PSYCOPG_SCHEME = "postgresql+psycopg://"',
            'PSYCOPG_SCHEME = "postgresql://"')),

    m("P2", "scheme 里的驱动段必须被识别（可缺省）；识别不了，历史跑法给的 +asyncpg/+psycopg "
            "形态就会在归一器里当场报错——换方言不该换出故障",
      "pytest", DIALECT, "test_psycopg_scheme_is_idempotent",
      backup(SCHEME),
      patch(SCHEME, 'r"^(?:postgres(?:ql)?)(?:\\+[^:/]*)?://"',
            'r"^(?:postgres(?:ql)?)://"')),

    m("P3", "归一不是原样返回：Python 侧不补驱动就还是两套 scheme 各跑各的",
      "pytest", DIALECT, "test_bare_scheme_gets_the_declared_driver",
      backup(SCHEME),
      patch(SCHEME, '    return f"{PSYCOPG_SCHEME}{_target(raw)}"', "    return raw")),

    m("P4", "libpq 侧必须剥驱动：pg_dump 与 pgx 吃带驱动段的串是同一类失败",
      "pytest", DIALECT, "test_both_stacks_point_at_the_same_database",
      backup(SCHEME),
      patch(SCHEME, '    return f"{LIBPQ_SCHEME}{_target(raw)}"',
            '    return f"{PSYCOPG_SCHEME}{_target(raw)}"')),

    m("P5", "非 PG 串要 fail-closed：静默改写会让 sqlite/mysql 的串被当成 PG 打到错误的库上",
      "pytest", DIALECT, "test_non_postgres_url_is_rejected_without_leaking_the_password",
      backup(SCHEME),
      patch(SCHEME, "    match = _PG_SCHEME.match(raw)\n    if not match:",
            '    match = _PG_SCHEME.match(raw) or re.compile(r"^[^:]+://").match(raw)\n'
            "    if not match:")),

    m("P6", "报错不许回显连接串：这条闸存在的意义是口令不进终端、不进 CI 日志",
      "pytest", DIALECT, "test_non_postgres_url_is_rejected_without_leaking_the_password",
      backup(SCHEME),
      patch(SCHEME, 'f"不是带 scheme 的 PostgreSQL 连接串（前缀 {head!r}）——"',
            'f"不是带 scheme 的 PostgreSQL 连接串（{raw}）——"')),

    m("P7", "归一后的串必须解析出真的装了驱动（dialect.driver == psycopg）："
            "只测字符串形状会放过「scheme 对了但驱动没装」这类故障",
      "pytest", DIALECT, "test_translated_url_resolves_to_an_installed_driver",
      backup(SCHEME),
      patch(SCHEME, '    return f"{PSYCOPG_SCHEME}{_target(raw)}"',
            '    return f"postgresql+asyncpg://{_target(raw)}"')),

    # ── 缺 env 的降级可见性 ──
    m("P8", "空串等于没导：否则 `export TEST_DATABASE_URL=` 会一路走到归一器再抛 ValueError，"
            "把「没配 env」报成「配错了」",
      "pytest", DIALECT, "test_pg_test_url_treats_empty_env_as_missing",
      backup(PGENV),
      patch(PGENV, "    if not raw:", "    if raw is None:")),

    m("P9", "CLOSEN_REQUIRE_PG 必须把 skip 改判 fail——这条开关是「同基线」唯一的自证手段",
      "pytest", DIALECT, "test_pg_test_url_fails_instead_of_skipping_when_required",
      backup(PGENV),
      patch(PGENV, "        if os.environ.get(REQUIRE_ENV):", "        if False:")),

    m("P10", "默认仍然 skip：宿主没有本机库时不该把 CI 打红，这条不许被改成无条件 fail",
      "pytest", DIALECT, "test_pg_test_url_skips_when_env_is_missing",
      backup(PGENV),
      patch(PGENV, "        pytest.skip(message)", "        pytest.fail(message)")),

    m("P11", "入口必须真的过归一器：否则消费方拿到的还是原始 env，P1–P4 全成装饰",
      "pytest", DIALECT, "test_pg_test_url_normalises_an_exported_bare_url",
      backup(PGENV),
      patch(PGENV, "    return psycopg_url(raw)", "    return raw")),

    # ── 反漂移：规则只许有一条路 ──
    m("P12", "消费方不许再各自搓 scheme 替换（这条就是当年漂移的成因）",
      "pytest", DIALECT, "test_no_hand_rolled_driver_translation_survives",
      backup(CANON),
      patch(CANON, "    engine = create_engine(pg_test_url())\n    insp = inspect(engine)\n    diffs",
            '    engine = create_engine(pg_test_url().replace("+asyncpg", "+psycopg"))\n'
            "    insp = inspect(engine)\n    diffs")),

    m("P13", "实库 env 只许从 _pg_env 读一次：绕过它就绕过 skip/fail 开关与归一",
      "pytest", DIALECT, "test_real_db_tests_take_the_env_only_through_the_helper",
      backup(CANON),
      patch(CANON, "def test_live_schema_matches_orm_declaration():",
            'def test_live_schema_matches_orm_declaration():\n'
            '    os.environ.get("TEST_DATABASE_URL")  # noqa: B018')),

    m("P14", "豁免表不许留死条目：database.py 那条 asyncpg 分支一旦消失，豁免必须被删掉",
      "pytest", DIALECT, "test_scheme_mention_exception_is_still_true",
      backup(DBPY),
      patch(DBPY, 'elif settings.database_url.startswith("postgresql+asyncpg"):',
            'elif settings.database_url.startswith("postgresql+psycopg3"):')),

    # ── Go 侧归一器与三处接线 ──
    m("G1", "驱动段识别是这条函数的全部工作：识别不了就退回「pgx 只认裸串」的老口径",
      "go", "./internal/dsn/", "TestPostgresStripsSQLAlchemyDriverSegment",
      backup(DSN),
      patch(DSN, '`^(postgres(?:ql)?)\\+[^:/]+://`', '`^(postgres(?:ql)?)://`')),

    m("G2", "必须真的返回剥段结果，而不是原样交给 pgx",
      "go", "./internal/dsn/", "TestPostgresPoolAcceptsEveryPythonScheme",
      backup(DSN),
      patch(DSN, "\treturn driverScheme.ReplaceAllString(raw, \"$1://\")", "\treturn raw")),

    m("G3", "建池点结构闸：cmd/go-api 绕过归一器就是灰度期「同一个变量喂不了两侧」的现行版",
      "go", "./internal/dsn/", "TestEveryPoolConstructorTakesATranslatedURL",
      backup(GOAPI),
      patch(GOAPI, "dsn.Postgres(cfg.DatabaseURL)", "cfg.DatabaseURL")),

    m("G4", "crosscheck 接线（同一条 +psycopg 串先喂 Python 再喂 Go）：剥段调用一撤，"
            "跨语言互认的两条用例立刻连不上库",
      "pytest", INTEROP, "test_go_reads_row_written_by_python",
      backup(XCHECK),
      # 回退必须可编译：只撤 dsn.Postgres 会让 dsn 变成未使用 import，go run 直接编译失败，
      # 互认用例的 _require_go 探针随之 skip 掉整组——退出码 0 会被看成「闸门很绿」。
      # 所以这里复原成「当年的旧写法」：补回 strings、删掉 dsn、只认 asyncpg 前缀。
      patch(XCHECK, '\t"os"\n\t"runtime"\n', '\t"os"\n\t"runtime"\n\t"strings"\n'),
      patch(XCHECK, '\t"github.com/jamesfeng2009/closenai-app/go-core/internal/dsn"\n', ""),
      patch(XCHECK, "dsn.Postgres(databaseURL)",
            'strings.Replace(databaseURL, "postgresql+asyncpg://", "postgresql://", 1)')),

    m("G5", "store 集成测接线：退回旧的只认 asyncpg 的 strings.Replace，"
            "+psycopg 形态在建池这一步就 Fatal",
      "go", "./internal/store/", "TestCanonicalIdempotency",
      backup(STOREIT),
      # 同 G4：回退件必须能编译，否则 dsn 变成未使用 import，go test 报 build failed 而不是 FAIL。
      patch(STOREIT, '\t"github.com/jamesfeng2009/closenai-app/go-core/internal/dsn"\n', ""),
      patch(STOREIT, "\turl := dsn.Postgres(raw)",
            '\turl := strings.Replace(raw, "postgresql+asyncpg://", "postgresql://", 1)')),

    # ── 绿闸：无害注释必须还是绿的 ──
    m("GR1", "绿闸：往归一器加一行注释，受害测试不该红（否则上面那批红是切片窗口偏出来的假红）",
      "pytest-green", DIALECT, "",
      backup(SCHEME),
      patch(SCHEME, "def psycopg_url(raw: str) -> str:",
            "# 绿闸注释：不改变任何行为。\n"
            "def psycopg_url(raw: str) -> str:")),

    m("GR2", "绿闸：往 dsn.go 加一行注释，go test 不该红",
      "go-green", "./internal/dsn/", "",
      backup(DSN),
      patch(DSN, "func Postgres(raw string) string {",
            "// green gate comment\nfunc Postgres(raw string) string {")),
]


def outcome(out: str) -> str:
    """把跑完的输出分成四档，退出码分不出「真跑了」和「整组被 skip」。

    ran   ⇒ 汇总行里有 passed/failed 或 go 的 ok/--- FAIL；
    compile/nothing/skipped ⇒ 一条受害测试都没执行。
    """
    if "[build failed]" in out or "imported and not used" in out or "undefined:" in out:
        return "compile"
    if "collected 0 items" in out or "no tests ran" in out or "[no tests to run]" in out:
        return "nothing"
    if (re.search(r"\b\d+ (?:passed|failed)\b", out) or "--- FAIL" in out
            or re.search(r"^ok\s", out, re.M) or "FAIL\t" in out):
        return "ran"
    return "skipped"


def verdict(kind: str, code: int, out: str) -> tuple[str, str | None]:
    """红闸要求受害测试真的断言失败；绿闸要求它原样通过。

    「没跑起来」单列一档：Go 编译失败会让互认用例被 fixture skip，pytest 的 skip 也给退出码 0，
    只看退出码就会把「闸门失效」读成「闸门绿」。2026-10-14 首跑就是靠这条抓到 G4 的假绿。
    """
    state = outcome(out)
    tail = out[-500:]
    if kind.endswith("-green"):
        if state != "ran":
            return "ERROR", f"绿闸这轮是 {state}（退出码 {code}），受害测试没真跑过，绿不算自证：\n{tail}"
        if code == 0:
            return "GREEN(好)", None
        return "RED(坏)", f"无害注释把闸门跑红了，先看输出：\n{tail}"
    if state != "ran":
        return "ERROR", f"变异后是 {state}（退出码 {code}），受害测试没执行，打红不成立：\n{tail}"
    if re.search(r"\bskipped\b", out):
        return "ERROR", f"变异后有用例被 skip，先确认不是环境不可用冒充了红：\n{tail}"
    if code == 0:
        return "GREEN(坏)", "破坏后闸门仍然绿，等于没拍住"
    if re.search(r"\b\d+ failed\b", out) or "--- FAIL" in out or "FAIL\t" in out:
        return "RED ", None
    return "ERROR", f"没跑出断言失败：\n{tail}"


def restore_and_verify() -> list[str]:
    """写回备份并逐字节核验——恢复是本脚本唯一动源文件的地方，必须自证。"""
    bad = []
    for path, bak in BACKUPS.items():
        shutil.copy2(bak, path)
        if not filecmp.cmp(bak, path, shallow=False):
            bad.append(f"{path.relative_to(ROOT)} 恢复后与备份不一致")
    BACKUPS.clear()
    return bad


def main(argv: list[str]) -> int:
    only: set[str] | None = None
    if len(argv) > 1 and argv[1] == "--only":
        only = {name.strip() for name in argv[2].split(",")}

    problems: list[str] = []
    red = green = ran = 0
    for num, claim, kind, target, test, steps in MUTATIONS:
        if only and num not in only:
            continue
        ran += 1
        try:
            for step in steps:
                step()
            code, out = (run_pytest(target, test) if kind.startswith("pytest")
                         else run_go(target, test))
            mark, problem = verdict(kind, code, out)
        except Exception as exc:  # noqa: BLE001
            mark, problem = "SKIP", f"变异脚本自身出错：{exc}"
        print(f"{mark} {num:4} {claim[:74]}")
        if problem:
            problems.append(f"{num}：{problem}")
            print(f"      └─ {str(problem)[:300]}")
        elif kind.endswith("-green"):
            green += 1
        else:
            red += 1
        problems += restore_and_verify()

    print(f"\n=== 汇总：清单 {ran} 条 ⇒ 红闸 {red} 打红 / 绿闸 {green} 保持绿，"
          f"异常 {len(problems)} 条 ===")
    for row in problems:
        print(" -", row)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
