"""N4 的变异自证：证据算分、九关补关与 insight 重算的每一条口径都要能被一次真实的破坏打红。

手法与 mutate_n3_gates.py 相同：备份 → 写坏 → 跑那一条测试 → 备份写回 → filecmp 核验，
**不用 git checkout**（仓里有并行未提交工作，checkout 会连带冲掉）。
库口令只从环境取（CLOSEN_PG_PASSWORD，否则现取容器 env），脚本与输出都不落明文。

用法：
    .venv/bin/python scripts/mutate_n4_gates.py            # 全部 14 条
    .venv/bin/python scripts/mutate_n4_gates.py 1 9 13     # 只跑点名几条
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
SCORE = GO / "internal/domain/evidence_score.go"
SERVICE_EV = GO / "internal/domain/service_evidence.go"
SERVICE = GO / "internal/domain/service.go"
SQL = GO / "db/queries/canonical.sql"
SQLC_OUT = GO / "internal/store/canonicaldb/canonical.sql.go"
SQLC = "/opt/homebrew/bin/sqlc"

DOMAIN_TESTS = GO / "internal/domain"
STORE_TESTS = GO / "internal/store"


def pg_dsn() -> str:
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
    tmp = Path(tempfile.mkdtemp(prefix="n4-gate-backup-"))
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


def sqlc_regen(marker: str) -> None:
    """重跑 sqlc 并核验生成物真的带上变异（sqlc 有 mtime 缓存，copy2 恢复会拨回旧值）。"""
    for f in (SQL, SQLC_OUT):
        os.utime(f, None)
    subprocess.run([SQLC, "generate"], cwd=GO, capture_output=True, text=True,
                   env=ENV, check=True)
    assert marker in SQLC_OUT.read_text(encoding="utf-8"), \
        f"sqlc 没把变异写进生成物，这条闸门未被破坏，结果无效：{marker!r}"


def run_go(target: Path, test_name: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["go", "test", "./" + str(target.relative_to(GO)).strip("/"), "-count=1",
         "-run", test_name, "-v"],
        cwd=GO, capture_output=True, text=True, env=ENV, timeout=900)
    return proc.returncode, proc.stdout + proc.stderr


# ── 变异片段的锚点（逐字取自当前源码，命中数由 patch/swap 断言）───────────────
DEDUP_OR = """\t\t\tif seenSource[row.SourceID] || seenDay[dayKey(row.ObservedAt, p.RepetitionWindowDays)] {
\t\t\t\tcontinue
\t\t\t}
\t\t\tseenSource[row.SourceID] = true
\t\t\tseenDay[dayKey(row.ObservedAt, p.RepetitionWindowDays)] = true
"""
DEDUP_COMPOSITE = """\t\t\tif seenSource[row.SourceID + "|" + dayKey(row.ObservedAt, p.RepetitionWindowDays)] {
\t\t\t\tcontinue
\t\t\t}
\t\t\tseenSource[row.SourceID + "|" + dayKey(row.ObservedAt, p.RepetitionWindowDays)] = true
"""
VERIFY_FIRST = """\t\tcited, err := s.verifyIndependent(ctx, st, c)
\t\tif err != nil {
\t\t\treturn err
\t\t}
"""
VERIFY_DEFERRED = "\t\tvar cited []Evidence\n"
COUNT_ANCHOR = "\t\tindependent, dropped := s.countIndependent(c, cited, sourceID)\n"
VERIFY_AFTER_WRITE = ("\t\tcited, err = s.verifyIndependent(ctx, st, c)\n"
                      "\t\tif err != nil {\n\t\t\treturn err\n\t\t}\n") + COUNT_ANCHOR
SAFETY_STEP = """\t\t{"safety", func() (RejectReason, string, error) {
\t\t\treason, detail := g.checkSafety(c)
\t\t\treturn reason, detail, nil
\t\t}},
"""
LANGUAGE_STEP = """\t\t{"language", func() (RejectReason, string, error) {
\t\t\treason, detail := g.checkLanguage(c)
\t\t\treturn reason, detail, nil
\t\t}},
"""
SUPPRESS_BRANCH = '\tif outcome.DuplicateOfID != "" {'

MUTATIONS = [
    (1, "独立性去重是「同源 或 同日」的 or 关系，不是组合键（03 §6:161-163）——"
        "N4 实测发现并修掉的真实缺陷，改回组合键必须打红",
     lambda: (backup(SCORE), patch(SCORE, DEDUP_OR, DEDUP_COMPOSITE)),
     restore_files, STORE_TESTS, "TestAdmitFactStaysCandidateWhenEvidenceSharesADay"),
    (2, "同上，聚合口径单测也要红（Combined 的同源/同日两条去重）",
     lambda: (backup(SCORE), patch(SCORE, DEDUP_OR, DEDUP_COMPOSITE)),
     restore_files, DOMAIN_TESTS, "TestEvidencePolicy_Combined"),
    (3, "默认算分口径＝几何平均：V3 §5 的字面连乘会把最正常的一档压到 C 级，"
        "偏离已在 tasks N4:487 登记，改回 product 必须让表驱动红",
     lambda: (backup(POLICY), patch(
         POLICY, 'ScoreMode: envString("CLOSEN_EVIDENCE_SCORE_MODE", scoreModeGeomean)',
         'ScoreMode: envString("CLOSEN_EVIDENCE_SCORE_MODE", scoreModeProduct)')),
     restore_files, DOMAIN_TESTS, "TestScoreEvidence_TableDriven"),
    (4, "独立重复的条数由 Go 核出来，绝不采信提案给的数字（03:108）",
     lambda: (backup(SERVICE_EV), patch(
         SERVICE_EV, "independent, dropped := s.countIndependent(c, cited, sourceID)",
         "independent, dropped := len(c.ProposedIndependentIDs)+1, 0")),
     restore_files, DOMAIN_TESTS, "TestService_AdmitEvidence_IndependenceIsVerifiedNotProposed"),
    (5, "核验必须早于写入：越权引用不得留下半状态（不变量 9）",
     lambda: (backup(SERVICE_EV), patch(SERVICE_EV, VERIFY_FIRST, VERIFY_DEFERRED),
              patch(SERVICE_EV, COUNT_ANCHOR, VERIFY_AFTER_WRITE)),
     restore_files, DOMAIN_TESTS, "TestService_AdmitEvidence_IndependenceIsVerifiedNotProposed"),
    (6, "冲突证据不得被平均掉：contradicting 不弱于最强支撑就是冲突（03 §7:186-187）",
     lambda: (backup(SCORE), patch(SCORE, "\t\t\tconflict = true\n\t\t\tbreak",
                                   "\t\t\tconflict = false\n\t\t\tbreak")),
     restore_files, DOMAIN_TESTS, "TestService_ReevaluateInsight_RetractsOnConflict"),
    (7, "过期是生命周期退化，不是拒绝：expired 不得写 reject_reason（03 §11:309-310）",
     lambda: (backup(SERVICE_EV), patch(SERVICE_EV, 'return "expired", ""',
                                        'return "expired", ReasonEvidenceBelowFloor.String()')),
     restore_files, DOMAIN_TESTS, "TestService_ReevaluateInsight_ExpiresAfterStaleWindow"),
    (8, "用户已表态的行系统不得撤回：confirmed 不属于可重算状态（03 §12）",
     lambda: (backup(SERVICE_EV), patch(
         SERVICE_EV, 'case "validated", "published", "acknowledged":',
         'case "validated", "published", "acknowledged", "confirmed":')),
     restore_files, DOMAIN_TESTS, "TestService_ReevaluateInsight_NeverTouchesUserStatedStatus"),
    (9, "抑制签名命中＝只补事件、不插行：再插一条同指纹会撞 23505（03 §12:335-338）",
     lambda: (backup(SERVICE), patch(SERVICE, SUPPRESS_BRANCH,
                                     '\tif false && outcome.DuplicateOfID != "" {')),
     restore_files, STORE_TESTS, "TestAdmitSuppressedByUserHistoryWritesNoSecondRow"),
    (10, "重算读的是 evidence.role，不是某次回链的 role（Gate 恒写 supporting）",
     lambda: (backup(SQL, SQLC_OUT), patch(
         SQL, "e.evidence_type, ie.role, e.role AS evidence_role",
         "e.evidence_type, ie.role, ie.role AS evidence_role"),
              sqlc_regen("ie.role AS evidence_role")),
     restore_files, STORE_TESTS, "TestReevaluateInsightRetractsOnContradictingEvidence"),
    (11, "推断事实的晋升门：≥2 条已算分独立观测（03 §7:175-181），降到 1 就该红",
     lambda: (backup(POLICY), patch(
         POLICY, 'FactMinIndependent: envInt("CLOSEN_FACT_MIN_INDEPENDENT", 2)',
         'FactMinIndependent: envInt("CLOSEN_FACT_MIN_INDEPENDENT", 1)')),
     restore_files, DOMAIN_TESTS, "TestService_FactPromotionNeedsScoredIndependentEvidence"),
    (12, "派生 claim 需要 ≥2 条已算分独立证据：0 下限等于对该 kind 不设限，必须红",
     lambda: (backup(POLICY), patch(
         POLICY, 'InsightMinIndependentForDerived: envInt("CLOSEN_INSIGHT_MIN_INDEPENDENT_EVIDENCE", 2)',
         'InsightMinIndependentForDerived: envInt("CLOSEN_INSIGHT_MIN_INDEPENDENT_EVIDENCE", 0)')),
     restore_files, DOMAIN_TESTS, "TestGate_Evidence"),
    (13, "第 7 关 language 是九关之一，不得空转（规范 §1.2-§1.3 / 03 §8）",
     lambda: (backup(GATE), patch(GATE, "reason, detail := g.checkLanguage(c)",
                                  'reason, detail := RejectReason(""), ""')),
     restore_files, DOMAIN_TESTS, "TestGate_Language"),
    (14, "九关定序里 safety 先于 language（03 §14:361-371），换序 golden 就复现不了",
     lambda: (backup(GATE), swap(GATE, SAFETY_STEP, LANGUAGE_STEP)),
     restore_files, DOMAIN_TESTS, "TestGate_OrderIsContract"),
    (15, "metadata_json 必须同时留算分过程与调用方原文（02 §37「Why do we know it」）",
     lambda: (backup(SERVICE_EV), patch(SERVICE_EV,
                                        "Components: scored.Components, Detail: scored.Detail}",
                                        "Detail: scored.Detail}")),
     restore_files, STORE_TESTS, "TestAdmitEvidenceScoresInDBAndKeepsProvenance"),
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
