"""N3 领域闸门的变异自证：七关判定、落库口径与分层约束，每一条都要能被一次真实的破坏打红。

手法只用「备份 → 写坏 → 跑那一条测试 → 备份写回 → filecmp 核验」，不用 git checkout
（2026-10-13 约定：仓里有并行未提交工作，checkout 会连带冲掉）。

库口令只从环境取（CLOSEN_PG_PASSWORD），脚本文件与输出都不落明文。

用法：
    .venv/bin/python scripts/mutate_n3_gates.py            # 全部 12 条
    .venv/bin/python scripts/mutate_n3_gates.py 3 7 12     # 只跑点名几条
"""
from __future__ import annotations

import filecmp
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path("/Users/fengyu/Downloads/myproject/workspace/closenai-app")
GO = ROOT / "go-core"
GATE = GO / "internal/domain/gate.go"
POLICY = GO / "internal/domain/policy.go"
SERVICE = GO / "internal/domain/service.go"
REPO = GO / "internal/store/canonical_repo.go"
SQL = GO / "db/queries/canonical.sql"
SQLC_OUT = GO / "internal/store/canonicaldb/canonical.sql.go"
PROBE_HTTP = GO / "internal/httpapi/n3_probe.go"
PROBE_DOMAIN = GO / "internal/domain/n3_probe.go"
SQLC = "/opt/homebrew/bin/sqlc"

DOMAIN_TESTS = GO / "internal/domain"
STORE_TESTS = GO / "internal/store"


def pg_dsn() -> str:
    """口令 env-only：优先 CLOSEN_PG_PASSWORD，否则从容器 env 现取，绝不写进本文件。"""
    pw = os.environ.get("CLOSEN_PG_PASSWORD")
    if not pw:
        env = subprocess.run(
            ["docker", "inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}", "closenai-pg-dev"],
            capture_output=True, text=True, check=True).stdout
        pw = next(line[len("POSTGRES_PASSWORD="):] for line in env.splitlines()
                  if line.startswith("POSTGRES_PASSWORD="))
    return f"postgresql://postgres:{pw}@127.0.0.1:55432/closenai"


GOROOT = "/Users/fengyu/sdk/go1.27.1"
ENV = {
    **os.environ,
    "GOROOT": GOROOT,
    "PATH": f"{GOROOT}/bin:" + os.environ["PATH"],
    "TEST_DATABASE_URL": pg_dsn(),
}
BACKUPS: dict[Path, Path] = {}


def backup(*paths: Path) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="n3-gate-backup-"))
    for path in paths:
        bak = tmp / path.name
        shutil.copy2(path, bak)
        BACKUPS[path] = bak


def restore_files() -> None:
    for path, bak in BACKUPS.items():
        shutil.copy2(bak, path)
        assert filecmp.cmp(bak, path, shallow=False), f"恢复失败：{path}"
    BACKUPS.clear()


def patch(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"变异锚点命中 {text.count(old)} 次，应为 1：{old[:70]!r}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def swap(path: Path, a: str, b: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(a) == 1 and text.count(b) == 1, "交换锚点不唯一"
    path.write_text(text.replace(a, "\x00", 1).replace(b, a, 1).replace("\x00", b, 1),
                    encoding="utf-8")


def write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def remove(path: Path) -> None:
    if path.exists():
        path.unlink()


def sqlc_regen(marker: str) -> None:
    """重跑 sqlc，并核验生成物真的带上了变异。

    2026-10-13 实测：sqlc 有按文件 mtime 的解析缓存，restore_files() 用 copy2 会把
    mtime 拨回旧值，紧接着的 generate 可能拿旧解析结果生成、静默 no-op——
    闸门变异在这种前提下会假绿（测试当然还是 PASS）。所以这里 touch + 断言生成物含变异。
    """
    for f in (SQL, SQLC_OUT):
        os.utime(f, None)
    subprocess.run([SQLC, "generate"], cwd=GO, capture_output=True, text=True,
                   env=ENV, check=True)
    assert marker in SQLC_OUT.read_text(encoding="utf-8"), \
        f"sqlc 没把变异写进生成物（缓存或配置问题），这条闸门未被破坏，结果无效：{marker!r}"


def run_go(target: Path, test_name: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["go", "test", "./" + str(target.relative_to(GO)).strip("/") + "", "-count=1",
         "-run", test_name, "-v"],
        cwd=GO, capture_output=True, text=True, env=ENV, timeout=900)
    return proc.returncode, proc.stdout + proc.stderr


CONFIDENCE_STEP = """\t\t{"confidence", func() (RejectReason, string, error) {
\t\t\treason, detail := g.checkConfidence(c)
\t\t\treturn reason, detail, nil
\t\t}},
"""
SAFETY_STEP = """\t\t{"safety", func() (RejectReason, string, error) {
\t\t\treason, detail := g.checkSafety(c)
\t\t\treturn reason, detail, nil
\t\t}},
"""
REJECT_FACT_INSERT = """\tcase KindFact:
\t\tif err := st.InsertFact(ctx, s.factRow(c, id, status, version, outcome.Reason.String())); err != nil {
\t\t\treturn err
\t\t}
\tcase KindPattern:
"""
REACTIVATE_LINK = """\tif err := t.LinkEvidence(ctx, domain.KindPattern, patternID, userID, evidenceIDs); err != nil {
\t\treturn 0, err
\t}
"""

# (编号, 这条不变量在说什么, 事前破坏, 事后反操作, 测试目标, 测试名)
MUTATIONS = [
    (1, "证据归属：引用别人的证据必须拒（tasks N3「id 必须属于该 user」）",
     lambda: (backup(GATE), patch(GATE, "if row.UserID != c.UserID {",
                                 "if false && row.UserID != c.UserID {")),
     restore_files, DOMAIN_TESTS, "TestGate_Evidence$"),
    (2, "校验序契约：置信关先于安全关，否则 N4 九关 golden 无法复现（G-3）",
     lambda: (backup(GATE), swap(GATE, CONFIDENCE_STEP, SAFETY_STEP)),
     restore_files, DOMAIN_TESTS, "TestGate_OrderIsContract"),
    (3, "时间关：绝对量词在有界窗口下必须拒（V3 03:416-424）",
     lambda: (backup(GATE), patch(GATE, "reason, detail := g.checkTemporal(c)",
                                  'reason, detail := RejectReason(""), ""')),
     restore_files, DOMAIN_TESTS, "TestGate_Temporal"),
    (4, "D 级证据下限：低强度证据不得支持用户可见 claim（V3 03:158-159）",
     lambda: (backup(POLICY), patch(POLICY, 'envFloat("CLOSEN_EVIDENCE_D_FLOOR", 0.45)',
                                    'envFloat("CLOSEN_EVIDENCE_D_FLOOR", 0)')),
     restore_files, DOMAIN_TESTS, "TestGate_Evidence"),
    (5, "拒绝也留权威行：可审计 + N4 拒绝签名抑制要样本（G-2）",
     lambda: (backup(SERVICE), patch(SERVICE, REJECT_FACT_INSERT,
                                     "\tcase KindFact:\n\t\t_ = id\n\tcase KindPattern:\n")),
     restore_files, DOMAIN_TESTS, "TestService_RejectStillWritesRow"),
    (6, "拒绝必须留 candidate_rejected 事件（不变量 6/8 的可追溯面）",
     lambda: (backup(SERVICE), patch(SERVICE, '"candidate_rejected",', '"candidate_dropped",')),
     restore_files, DOMAIN_TESTS, "TestService_RejectStillWritesRow"),
    (7, "insight_status 没有 rejected 态，被拒的 insight 停在 draft（02:144-154）",
     lambda: (backup(SERVICE), patch(SERVICE, '\t\t\treturn "draft", false\n',
                                     '\t\t\treturn "rejected", false\n')),
     restore_files, DOMAIN_TESTS, "TestService_RejectStillWritesRow"),
    (8, "真库口径：被拒 insight 落 draft 的同时，rejected 会被 DDL check 打回",
     lambda: (backup(SERVICE), patch(SERVICE, '\t\t\treturn "draft", false\n',
                                     '\t\t\treturn "rejected", false\n')),
     restore_files, STORE_TESTS, "TestAdmitRejectsThreeKindsAndKeepsAuditRow"),
    (9, "指纹命中＝刷新而非重复插：reactivate 必须把新证据链上（V3 03:310-311）",
     lambda: (backup(REPO), patch(REPO, REACTIVATE_LINK, "")),
     restore_files, STORE_TESTS, "TestAdmitReactivatesPatternOnFingerprint"),
    (10, "乐观并发：0 行更新要翻成 ErrVersionConflict，consumer 才知道该重试（02 §32）",
     lambda: (backup(REPO), patch(REPO, "return 0, domain.ErrVersionConflict",
                                  'return 0, fmt.Errorf("store: bump: %w", err)')),
     restore_files, STORE_TESTS, "TestAdmitVersionConflictRollsBack"),
    (11, "分层不变量 7：sqlc 生成物只准 internal/store 引用（G-7）",
     lambda: (backup(), write(PROBE_HTTP,
             "package httpapi\n\nimport canonicaldb \"github.com/jamesfeng2009/closenai-app"
             "/go-core/internal/store/canonicaldb\"\n\nvar _ = canonicaldb.New\n")),
     lambda: remove(PROBE_HTTP), DOMAIN_TESTS, "TestOnlyStoreTouchesCanonicalDB"),
    (12, "纯决策层：internal/domain 不 import HTTP/传输（G-7）",
     lambda: (backup(), write(PROBE_DOMAIN,
             "package domain\n\nimport \"net/http\"\n\nvar _ = http.StatusOK\n")),
     lambda: remove(PROBE_DOMAIN), DOMAIN_TESTS, "TestDomainDoesNotDependOnTransportLayers"),
    (13, "活事实查重只看活行：invalidated/outdated 的旧值必须能重新接纳",
     lambda: (backup(SQL, SQLC_OUT), patch(SQL, "  AND status IN ('candidate', 'active')",
                                           "  AND status IN ('candidate', 'active', 'invalidated')"),
              sqlc_regen("status IN ('candidate', 'active', 'invalidated')")),
     restore_files, STORE_TESTS, "TestAdmitFactDuplicateOnlyAgainstLiveRows"),
]


def main() -> int:
    wanted = {int(a) for a in sys.argv[1:]}
    failures = []
    attempted = 0
    for num, claim, break_, undo, target, test_name in MUTATIONS:
        if wanted and num not in wanted:
            continue
        attempted += 1
        rc, output = 0, ""
        try:
            break_()
            rc, output = run_go(target, test_name)
        finally:
            undo()
            restore_files()
        red = rc != 0
        print(f"[{'RED' if red else 'GREEN-FAIL'}] #{num:<3} {claim}")
        if not red:
            failures.append((num, claim, test_name, output))
            print(output[-1200:])
    print(f"\n{attempted - len(failures)}/{attempted} 条变异打红")
    for num, claim, test_name, _ in failures:
        print(f"  #{num} {test_name} 仍然通过：{claim}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
