"""N6 的变异自证：outcome 闭环、用户表态、源失效传播的每一条口径都要能被一次真实的破坏打红。

手法与 mutate_n3/n4/n5_gates.py 相同：备份 → 写坏 → 只跑那一条测试 → 备份写回 →
filecmp 核验，**不用 git checkout**（仓里有并行未提交工作，checkout 会连带冲掉）。
库口令只从环境取（CLOSEN_PG_PASSWORD，否则现取容器 env），脚本与输出都不落明文。

三条 #15/#8/#13 打在 SQL/生成物上，必须经 sqlc 重生成才进得了编译路径，所以那几条带
marker/absent 双侧断言：生成物没带上变异就判定这条闸门未被破坏、结果无效。

用法：
    .venv/bin/python scripts/mutate_n6_gates.py           # 全部
    .venv/bin/python scripts/mutate_n6_gates.py 4 15      # 只跑点名几条
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
OUT = GO / "internal/domain/outcome.go"
SVC = GO / "internal/domain/service_outcome.go"
REPO = GO / "internal/store/canonical_repo.go"
SQL = GO / "db/queries/canonical.sql"
SQLC_OUT = GO / "internal/store/canonicaldb/canonical.sql.go"
SQLC = "/opt/homebrew/bin/sqlc"

DOMAIN_TESTS = GO / "internal/domain"
STORE_TESTS = GO / "internal/store"


def pg_dsn() -> str:
    pw = os.environ.get("CLOSEN_PG_PASSWORD")
    if not pw:
        env = subprocess.run(
            ["docker", "inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}",
             "closenai-pg-dev"],
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


def backup(*paths: Path):
    tmp = Path(tempfile.mkdtemp(prefix="n6-gate-backup-"))

    def run() -> None:
        for path in paths:
            bak = tmp / path.name
            shutil.copy2(path, bak)
            BACKUPS[path] = bak

    return run


def restore_files() -> None:
    for path, bak in BACKUPS.items():
        shutil.copy2(bak, path)
        assert filecmp.cmp(bak, path, shallow=False), f"恢复失败：{path}"
    BACKUPS.clear()


def patch(path: Path, old: str, new: str):
    def run() -> None:
        text = path.read_text(encoding="utf-8")
        assert text.count(old) == 1, f"变异锚点命中 {text.count(old)} 次，应为 1：{old[:70]!r}"
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    return run


def patch_many(path: Path, old: str, new: str, expect: int):
    """同名锚点出现多次时用它（ListDerivedSupportForEvidence 的三个 UNION 分支）。

    必须一次改全：只改第一个分支的话，另外两个分支仍然是对的，测试红得没有意义。
    """

    def run() -> None:
        text = path.read_text(encoding="utf-8")
        assert text.count(old) == expect, \
            f"变异锚点命中 {text.count(old)} 次，应为 {expect}：{old[:70]!r}"
        path.write_text(text.replace(old, new), encoding="utf-8")

    return run


def disabled(anchor: str) -> str:
    """把守卫改成恒假，同时保住编译：条件里引用到的变量一个都不能从式子里消失，
    否则 `declared and not used` 会让测试因为编不过而「红」，那条结果不作数。"""
    return re.sub(r" \{$", " && false {", anchor, count=1)


def sqlc_regen(marker: str = "", absent: str = ""):
    """重跑 sqlc 并核验生成物真的带上变异（sqlc 有 mtime 缓存，copy2 恢复会拨回旧值）。

    sqlc 会把语句末尾的分号去掉，所以「删掉某一段」要用 absent 断言，别拿原文去匹配。
    """

    def run() -> None:
        for f in (SQL, SQLC_OUT):
            os.utime(f, None)
        subprocess.run([SQLC, "generate"], cwd=GO, capture_output=True, text=True,
                       env=ENV, check=True)
        generated = SQLC_OUT.read_text(encoding="utf-8")
        if marker:
            assert marker in generated, \
                f"sqlc 没把变异写进生成物，这条闸门未被破坏，结果无效：{marker!r}"
        if absent:
            assert absent not in generated, \
                f"生成物里仍然留着被删掉的片段，变异没落到编译路径上：{absent!r}"

    return run


def run_go(target: Path, test_name: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["go", "test", "./" + str(target.relative_to(GO)).strip("/"), "-count=1",
         "-run", test_name, "-v"],
        cwd=GO, capture_output=True, text=True, env=ENV, timeout=900)
    return proc.returncode, proc.stdout + proc.stderr


def m(num: int, claim: str, target: Path, test: str, *steps):
    return (num, claim, target, test, steps)


# ── 变异锚点（逐字取自当前源码，命中数由 patch/patch_many 断言）───────────────
ORIGIN_MAP = """	for _, a := range AuthoritativeOutcomeOrigins {
		if a == o {
			return OutcomeStatusRecorded
		}
	}
	return OutcomeStatusCandidate
"""
BUMP_GUARD = "\t\tif status == OutcomeStatusRecorded {\n"
NO_PROPAGATE = """\t\tif status != OutcomeStatusRecorded {
\t\t\t// 不变量 5/7 的交集：推断只登记，不改变模型相信什么。
"""
EVIDENCE_COUNT_KEEP = ("Confidence: conf, HasConfidence: changed, "
                       "EvidenceCount: state.EvidenceCount,")
FLOOR_GUARD = "\t\tconf >= s.gate.Policy.EvidenceStrengthFloorForD {\n"
LOWER_ONLY = "\tnext := confidence * (1 - p.DisconfirmStep)\n"
ONLY_DEPENDENT = "\treturn d.TotalSupport > 0 && d.LiveSupport == 0\n"
LIVE_FILTER = " AND s.status <> 'deleted')"
OWNERSHIP_GUARD = "\t\tif _, ok := found[t.String()]; !ok {\n"
COMPLETED_GUARD = ('\t\tif ref, ok := states[string(want)+":"+id]; ok && '
                   'ref.Status == "completed" {\n')
REF_GUARD = "\t\tif !containsOne(packet.RefIDs, targetID) {\n"
DUP_GUARD = "\t\tif latest.Found && latest.FeedbackType == string(f.Type) {\n"
WRITABLE_GUARD = "\t\tif f.TargetType == RefOutcome && !outcomeWritable(current.Status) {\n"
EVENT_TYPE = """\t\teventType := "outcome_recorded"
\t\tif status != OutcomeStatusRecorded {
\t\t\teventType = "outcome_candidate"
\t\t}
"""
DISMISS = '\t\tcase FeedbackForgetFromModel:\n\t\t\treturn "dismissed", true\n'
PATTERN_REJECT = "\t\treturn PatternWeaken, true\n"
UNKNOWN_STATE = "\tif !current.live() || !current.Valid() {\n"
SILENCE_GUARD = "\t\t!hasAnyFeedbackSignal(p.Description) {\n"
VERSION_REASON = "\tif target == RefFact && f == FeedbackCorrect {\n"
SUPPORTED_AT = "    last_supported_at = COALESCE($7, last_supported_at),\n"
SUPPORTED_AT_RAW = "    last_supported_at = $7,\n"
LATEST_ORDER = """WHERE user_id = $1 AND target_type = $2 AND target_id = $3
ORDER BY created_at DESC, id DESC
LIMIT 1
"""
LATEST_ORDER_ASC = """WHERE user_id = $1 AND target_type = $2 AND target_id = $3
ORDER BY created_at ASC, id DESC
LIMIT 1
"""

MUTATIONS = [
    m(1, "origin 决定 status：非三条权威来源不可能产出 recorded（04:285「LLM inference "
         "is a candidate only」）",
      DOMAIN_TESTS, "TestOutcomeStatusForOrigin",
      backup(OUT),
      patch(OUT, ORIGIN_MAP, "\treturn OutcomeStatusRecorded\n")),

    m(2, "只有 recorded 才推进模型版本：candidate 不动版本账本（O-5 + 02:1069）",
      DOMAIN_TESTS, "TestRecordOutcome_LLMInferenceIsCandidateOnly",
      backup(SVC),
      patch(SVC, BUMP_GUARD,
            "\t\tif status == OutcomeStatusRecorded || status == OutcomeStatusCandidate {\n")),

    m(3, "candidate 一律不传播：LLM 的推断不是模型既成事实（不变量 7 的方向）",
      DOMAIN_TESTS, "TestRecordOutcome_LLMInferenceIsCandidateOnly",
      backup(SVC),
      patch(SVC, NO_PROPAGATE, NO_PROPAGATE.replace(
          "if status != OutcomeStatusRecorded {", "if false && status != OutcomeStatusRecorded {"))),

    m(4, "单个反例不清除历史证据：evidence_count 原样带回（04:313）",
      DOMAIN_TESTS, "TestRecordOutcome_DisconfirmLowersConfidenceOnly",
      backup(SVC),
      patch(SVC, EVIDENCE_COUNT_KEEP,
            "Confidence: conf, HasConfidence: changed, EvidenceCount: 0,")),

    m(5, "削穿 D 级下界才换状态：0.45 之上只降分数、状态不动（O-4）",
      STORE_TESTS, "TestN6DisconfirmBelowDFloorWeakensPattern",
      backup(SVC),
      patch(SVC, FLOOR_GUARD, "\t\tconf >= 0 {\n")),

    m(6, "传播只降不升：DisconfirmedConfidence 是 ×(1−step)，不是 ×(1+step)",
      DOMAIN_TESTS, "TestDisconfirmedConfidenceOnlyLowers",
      backup(OUT),
      patch(OUT, LOWER_ONLY, LOWER_ONLY.replace("(1 - p.DisconfirmStep)",
                                                "(1 + p.DisconfirmStep)"))),

    m(7, "「仅依赖它」＝曾依赖且活支撑归零：只按曾依赖就失效会错撤别人撑着的行",
      STORE_TESTS, "TestN6SourceWithdrawalLeavesSharedSupportRowsAlone",
      backup(OUT),
      patch(OUT, ONLY_DEPENDENT, "\treturn d.TotalSupport > 0\n")),

    m(8, "活支撑的判据是 source_records.status <> 'deleted'：去掉它 live==total，"
         "不变量 9 的传播整条失效（三个 UNION 分支一次改全）",
      STORE_TESTS, "TestN6SourceWithdrawalPropagatesOnlyToDependentRows",
      backup(SQL, SQLC_OUT),
      patch_many(SQL, LIVE_FILTER, ")", 3),
      sqlc_regen(absent="AND s.status <> 'deleted'")),

    m(9, "归属核验：提案引用的目标不在该用户名下必须报错（不变量 9 的跨用户面）",
      DOMAIN_TESTS, "TestRecordOutcome_RejectsForeignTargetBeforeAnyWrite",
      backup(SVC),
      patch(SVC, OWNERSHIP_GUARD, "\t\tif _, ok := found[t.String()]; !ok && false {\n")),

    m(10, "完成是幂等的：已 completed 的目标不二次刷 completed_at，否则历史里的完成时刻是假的",
      STORE_TESTS, "TestN6GoalCompletionCancelsDependentQueuedProactive",
      backup(SVC),
      patch(SVC, COMPLETED_GUARD, COMPLETED_GUARD.replace(
          'ref.Status == "completed"', 'ref.Status == "never-matches"'))),

    m(11, "只取消显式依赖该目标的触达：漏撤 < 错撤（04 §12 的取消条件要可核实）",
      STORE_TESTS, "TestN6GoalCompletionCancelsDependentQueuedProactive",
      backup(SVC),
      patch(SVC, REF_GUARD, REF_GUARD.replace("if !containsOne(", "if false && !containsOne("))),

    m(12, "重复判定：同一目标的最近一条同型表态就是重复提交（07 §6 ErrDuplicateFeedback）",
      STORE_TESTS, "TestN6DuplicateFeedbackWritesNoRowButInterleavedDoes",
      backup(SVC),
      patch(SVC, DUP_GUARD, disabled(DUP_GUARD))),

    m(13, "「最近一条」的排序就是这条判据的全部：改成取最早一条，夹过表态的重复就检不出来",
      STORE_TESTS, "TestN6DuplicateFeedbackWritesNoRowButInterleavedDoes",
      backup(SQL, SQLC_OUT),
      patch(SQL, LATEST_ORDER.rstrip("\n") + ";", LATEST_ORDER_ASC.rstrip("\n") + ";"),
      sqlc_regen(marker=LATEST_ORDER_ASC.rstrip("\n"), absent=LATEST_ORDER.rstrip("\n"))),

    m(14, "终态 outcome 不再被表态改写：预检拿掉后连 corrected 的行也会被推成 invalidated",
      STORE_TESTS, "TestN6FeedbackOnOutcomeRewritesStatusOnlyWhenWritable",
      backup(SVC),
      patch(SVC, WRITABLE_GUARD, disabled(WRITABLE_GUARD))),

    m(15, "生命周期转移不是新的支撑观测：last_supported_at 必须 COALESCE 保留，"
          "直写 NULL 会抹掉 04:231 与 N5 freshness 读的那一列",
      STORE_TESTS, "TestN6FeedbackConfirmWritesLifecycleAndKeepsLastSupportedAt",
      backup(SQL, SQLC_OUT),
      patch(SQL, SUPPORTED_AT, SUPPORTED_AT_RAW),
      sqlc_regen(marker="last_supported_at = $7,",
                 absent="last_supported_at = COALESCE($7, last_supported_at)")),

    m(16, "表态的目标状态一律取 02 §3 词表原值：insight 的 forget_from_model 是 dismissed，"
          "写成词表外的值等于给读路径造第二个枚举",
      DOMAIN_TESTS, "TestStatusForDerivedTarget",
      backup(OUT),
      patch(OUT, DISMISS, DISMISS.replace('"dismissed"', '"deleted"'))),

    m(17, "用户的纠正是反面证据（01:320 weakened），不是直接否决：描述不准的模式可能仍成立",
      DOMAIN_TESTS, "TestPatternActionForFeedback",
      backup(OUT),
      patch(OUT, PATTERN_REJECT, "\t\treturn PatternReject, true\n")),

    m(18, "未知状态不接受任何转移：把脏行当 candidate 复活，等于让脏数据获得被确认的资格",
      DOMAIN_TESTS, "TestNextPatternStatusMatrix",
      backup(OUT),
      patch(OUT, UNKNOWN_STATE, "\tif !current.live() {\n")),

    m(19, "沉默不构成 outcome（不变量 5）：proactive_not_helpful 必须有可核实的负反馈陈述",
      DOMAIN_TESTS, "TestCheckOutcomeProposal",
      backup(OUT),
      patch(OUT, SILENCE_GUARD, "\t\t!hasAnyFeedbackSignal(p.Description) && false {\n")),

    m(20, "版本留痕的 reason 守文档词表：fact 的纠正是 fact_corrected（02:260）",
      DOMAIN_TESTS, "TestVersionReasonKeepsDocumentedVocabulary",
      backup(SVC),
      patch(SVC, VERSION_REASON,
            "\tif target == RefFact && f == FeedbackReject {\n")),

    m(21, "candidate 与 recorded 的事件类型必须分叉：都记 outcome_recorded 会让 O 侧把"
          "「登记了但没采信」当成既成事实统计",
      STORE_TESTS, "TestN6LLMInferenceOutcomeStaysCandidateAndPropagatesNothing",
      backup(SVC),
      patch(SVC, EVENT_TYPE, '\t\teventType := "outcome_recorded"\n')),
]


def main() -> int:
    wanted = {int(a) for a in sys.argv[1:]}
    failures = []
    attempted = 0
    try:
        for num, claim, target, test, steps in MUTATIONS:
            if wanted and num not in wanted:
                continue
            attempted += 1
            rc, output = 0, ""
            try:
                for step in steps:
                    step()
                rc, output = run_go(target, test)
            finally:
                restore_files()
            red = rc != 0
            print(f"[{'RED' if red else 'GREEN-FAIL'}] #{num:<3} {claim}")
            if not red:
                failures.append((num, claim, test, output))
                print(output[-1500:])
        print(f"\n{attempted - len(failures)}/{attempted} 条变异打红")
        for num, claim, test, _ in failures:
            print(f"  #{num} {test} 仍然通过：{claim}")
    finally:
        restore_files()

    # 复跑全量：证明每份备份都写回去了。变异脚本最怕的不是打红，是打红之后把源码留在坏状态。
    for pkg in ("./internal/domain/", "./internal/store/"):
        proc = subprocess.run(["go", "test", pkg, "-count=1"], cwd=GO,
                              capture_output=True, text=True, env=ENV, timeout=900)
        print(f"[{'GREEN' if proc.returncode == 0 else 'BROKEN'}] 复跑 {pkg}："
              f"{(proc.stdout + proc.stderr).strip().splitlines()[-1] if (proc.stdout or proc.stderr) else ''}")
        if proc.returncode != 0:
            print((proc.stdout + proc.stderr)[-2000:])
            return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
