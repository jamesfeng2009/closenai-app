"""N5 的变异自证：主动触达资格门的每一条口径都要能被一次真实的破坏打红。

手法与 mutate_n3_gates.py / mutate_n4_gates.py 相同：备份 → 写坏 → 跑那一条测试 →
备份写回 → filecmp 核验，**不用 git checkout**（仓里有并行未提交工作，checkout 会连带冲掉）。
库口令只从环境取（CLOSEN_PG_PASSWORD，否则现取容器 env），脚本与输出都不落明文。

其中 #1（周窗 7 天）、#20/#21（insert 漏 block/reject 两列）是本批集成测**真实跑出过红**
的三条：#1 是我写错的口径，后两条是漏掉的列。打了绿即表示该结论没有测试覆盖。

用法：
    .venv/bin/python scripts/mutate_n5_gates.py            # 全部
    .venv/bin/python scripts/mutate_n5_gates.py 1 20 21    # 只跑点名几条
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
FREQ = GO / "internal/domain/frequency.go"
GATE = GO / "internal/domain/proactive_gate.go"
SVC = GO / "internal/domain/service_proactive.go"
PROPOS = GO / "internal/domain/proactive_policy.go"
REPO = GO / "internal/store/canonical_repo.go"
SQL = GO / "db/queries/canonical.sql"
SQLC_OUT = GO / "internal/store/canonicaldb/canonical.sql.go"
SQLC = "/opt/homebrew/bin/sqlc"

DOMAIN_TESTS = GO / "internal/domain"
STORE_TESTS = GO / "internal/store"
GATE_TABLE = "TestProactive_GateRejectsEachHardGate"


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
    tmp = Path(tempfile.mkdtemp(prefix="n5-gate-backup-"))

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


def swap(path: Path, a: str, b: str):
    def run() -> None:
        text = path.read_text(encoding="utf-8")
        assert text.count(a) == 1 and text.count(b) == 1, "交换锚点不唯一"
        path.write_text(text.replace(a, "\x00", 1).replace(b, a, 1).replace("\x00", b, 1),
                        encoding="utf-8")

    return run


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


# ── 变异锚点（逐字取自当前源码，命中数由 patch/swap 断言）─────────────────────
WEEK_END = "\tweekEnd := weekStart.AddDate(0, 0, 7)\n"
DAY_START = "\tdayStart := localDayStartUTC(in.Now, in.Prefs.Timezone)\n"
COUNT_SENT = """\tfor _, row := range in.History {
\t\tif !row.HasSentAt {
\t\t\tcontinue
\t\t}
\t\tif !row.SentAt.Before(dayStart) && row.SentAt.Before(dayEnd) {
\t\t\tsent24h++
\t\t}
"""
COUNT_ANY = """\tfor _, row := range in.History {
\t\tif !row.HasScheduledAt {
\t\t\tcontinue
\t\t}
\t\tif !row.ScheduledAt.Before(dayStart) && row.ScheduledAt.Before(dayEnd) {
\t\t\tsent24h++
\t\t}
"""
COMMIT_CONSERVATIVE = """\tif row.Detail == "" {
\t\treturn true
\t}
\tpacket := EligibilityPacket{}
\tif err := json.Unmarshal([]byte(row.Detail), &packet); err != nil {
\t\treturn true
\t}
"""
COMMIT_STRICT = """\tif row.Detail == "" {
\t\treturn false
\t}
\tpacket := EligibilityPacket{}
\tif err := json.Unmarshal([]byte(row.Detail), &packet); err != nil {
\t\treturn false
\t}
"""
LAYERS_STEP = '\t\t{"layers", func() (RejectReason, string, bool) { return g.checkLayers(c, in) }},\n'
CONF_STEP = ('\t\t{"confidence", func() (RejectReason, string, bool) '
             '{ return g.checkProactiveConfidence(c, in) }},\n')
LAYER2 = """\t\tif (ref.RefType == RefPattern || ref.RefType == RefInsight) &&
\t\t\t!isStaleStatus(ref.RefType, ref.Status) {
"""
LAYER2_ANY = "\t\tif ref.RefType == RefPattern || ref.RefType == RefInsight {\n"
LAYER3_ANCHOR = "\t\tif !anchorDay.Before(today) {\n"
LAYER4_CLOSED = """\t\t\tfor _, closed := range closedQuestionTerms {
\t\t\t\tif strings.Contains(lowered, closed) {
\t\t\t\t\treturn false
\t\t\t\t}
\t\t\t}
"""
SCORE_DIV = "\treturn float64(len(hit)) / 4.0\n"
SAFETY_NIL = """\tif c.Safety == nil {
\t\treturn ReasonSafetyUnavailable, "缺少安全判定输入，按 fail-closed 拦下（D-8 / 01 不变量 10）", true
\t}
"""
SAFETY_NIL_PASS = '\tif c.Safety == nil {\n\t\treturn "", "", false\n\t}\n'
PREF_CONSENT = "\tif !in.Prefs.AIConsent {\n"
EVIDENCE_OWNED = "\t\tif !owned {\n"
FRESH_RETURNED = "\t\tif !returned[id] {\n"
TERMINAL_GUARD = "\t\tif isTerminalProactive(state.Status) {\n"
DEADLINE_GUARD = "\t\tif state.HasExpiresAt && !now.Before(state.ExpiresAt) {\n"
# SetProactiveStatus 里是同名两行，插入路径的锚点必须带上前一行才唯一。
INSERT_HEAD = '\t\tEligibilityScore: score, EligibilityDetail: textOf(row.EligibilityDetail),\n'
BLOCK_COL = INSERT_HEAD + '\t\tBlockReason:       textOf(row.BlockReason),\n'
BLOCK_COL_NULL = INSERT_HEAD + '\t\tBlockReason:       textOf(""),\n'
REJECT_COL = BLOCK_COL + '\t\tRejectReason:      textOf(row.RejectReason),\n'
REJECT_COL_NULL = BLOCK_COL + '\t\tRejectReason:      textOf(""),\n'
ROLE_FILTER = ("\t\tSELECT max(created_at) FROM chat_messages "
               "WHERE user_id = $1 AND role = 'user'`\n")
ROLE_ANY = "\t\tSELECT max(created_at) FROM chat_messages WHERE user_id = $1`\n"
SENT_GUARD = """WHERE id = $1 AND user_id = $2
  AND status IN ('candidate', 'eligible', 'scheduled')
RETURNING sent_at;
"""
SENT_GUARD_OFF = "WHERE id = $1 AND user_id = $2\nRETURNING sent_at;\n"


def negated(guard: str) -> str:
    """把守卫短路成恒假，同时保住缩进（拼字符串容易写出不能编译的 Go）。"""
    return re.sub(r"^(\t+)if ", r"\1if false && ", guard, count=1)


MUTATIONS = [
    m(1, "周回访窗是本地周一起**整 7 天**，不是 1 天（closenai.html:2953 + "
         "commitment_service.py:250-256）——本批集成测真红过的一条",
      DOMAIN_TESTS, "TestProactive_CommitmentFollowUpCaps",
      backup(FREQ),
      patch(FREQ, WEEK_END, WEEK_END.replace(", 0, 7)", ", 0, 1)"))),


    m(2, "「每日 1 条」按**用户本地日历日**开窗：起点往前挪一天就会把昨天那条算进今天",
      DOMAIN_TESTS, "TestProactive_CheckFrequencyCaps",
      backup(FREQ),
      patch(FREQ, DAY_START, DAY_START.rstrip("\n") + ".AddDate(0, 0, -1)\n")),


    m(3, "频次只数 sent_at：eligible/scheduled 还没吵到用户，不得占额度（P-10 + 04:151）",
      DOMAIN_TESTS, "TestProactive_CheckFrequencyCaps",
      backup(FREQ),
      patch(FREQ, COUNT_SENT, COUNT_ANY)),

    m(4, "承诺归属核不出来时按「命中同一承诺」算：反骚扰上限宁可少发（closenai.html:2953）",
      DOMAIN_TESTS, "TestProactive_CommitmentFollowUpCaps",
      backup(FREQ),
      patch(FREQ, COMMIT_CONSERVATIVE, COMMIT_STRICT)),

    m(5, "沉默 14 天即停推（closenai.html:674 的 2/5/14 阶梯），抬到 30 天这一档就形同虚设",
      DOMAIN_TESTS, "TestProactive_CadenceLadder",
      backup(PROPOS),
      patch(PROPOS, 'SilentStopDays:     envInt("CLOSEN_SILENT_STOP_DAYS", 14)',
            'SilentStopDays:     envInt("CLOSEN_SILENT_STOP_DAYS", 30)')),

    m(6, "静默窗只拦本地 00:00-08:00：22:30 的睡前复盘必须放行（七-4 P-5，V3 04 §8 的 22:00 不采纳）",
      DOMAIN_TESTS, "TestProactive_QuietHoursUseUserTimezone",
      backup(PROPOS),
      patch(PROPOS, 'QuietStartHour: envInt("CLOSEN_PROACTIVE_QUIET_START_HOUR", 0)',
            'QuietStartHour: envInt("CLOSEN_PROACTIVE_QUIET_START_HOUR", 21)')),

    m(7, "校验序是契约：layers 必须先于 confidence，换序后 golden 复现不了（七-4 的插门位置）",
      DOMAIN_TESTS, "TestProactive_GateOrderIsTheContract",
      backup(GATE),
      swap(GATE, LAYERS_STEP, CONF_STEP)),

    m(8, "第②层「发现了模式」只认库里的活行：引用到一行 invalidated 不等于发现（closenai.html:637）。"
      "整跑资格门时这条被 freshness 关掩盖，故按 hitLayers 函数级隔离验证",
      DOMAIN_TESTS, "TestProactive_LayerTwoOnlyCountsLiveRows",
      backup(GATE),
      patch(GATE, LAYER2, LAYER2_ANY)),

    m(9, "第③层「关联当下」要求锚点落在本地今天或未来：三天前的锚点不算",
      DOMAIN_TESTS, "TestProactive_ContextLayerUsesLocalDayAnchor",
      backup(GATE),
      patch(GATE, LAYER3_ANCHOR, "\t\tif true {\n")),

    m(10, "第④层不认封闭式问句：把「是不是」算成开放层就能凑够 2 层发出去",
        DOMAIN_TESTS, "TestProactive_ClosedQuestionIsNotLayerFour",
        backup(GATE),
        patch(GATE, LAYER4_CLOSED, "")),

    m(11, "eligibility_score = 命中层数/4（P-8）：改成 /3 就不再是 DECIMAL(5,4) 允许的四档",
        DOMAIN_TESTS, "TestProactive_EligibilityScoreIsLayerFraction",
        backup(GATE),
        patch(GATE, SCORE_DIV, "\treturn float64(len(hit)) / 3.0\n")),

    m(12, "四层公式命中 <2 层即拦（PRD 原话「宁可不发」）：下限降到 1 就形同虚设",
        DOMAIN_TESTS, GATE_TABLE + "/只命中一层，宁可不发",
        backup(PROPOS),
        patch(PROPOS, 'MinLayers:                     envInt("CLOSEN_PROACTIVE_MIN_LAYERS", 2)',
              'MinLayers:                     envInt("CLOSEN_PROACTIVE_MIN_LAYERS", 1)')),

    m(13, "安全判定输入缺失＝fail-closed 拦下，不得当成放行（D-8 / 01 不变量 10）",
        DOMAIN_TESTS, GATE_TABLE + "/缺少安全判定输入",
        backup(GATE),
        patch(GATE, SAFETY_NIL, SAFETY_NIL_PASS)),

    m(14, "偏好只认库里的 users.ai_consent：一票否决在现行链 deliver:267-270 就是 skip",
        DOMAIN_TESTS, GATE_TABLE + "/未授予_AI_权限",
        backup(GATE),
        patch(GATE, PREF_CONSENT, negated(PREF_CONSENT))),

    m(15, "引用证据的归属必须核验（不变量 9）：别人的证据不得进本用户的触达",
        DOMAIN_TESTS, GATE_TABLE + "/引用证据不属于该用户",
        backup(GATE),
        patch(GATE, EVIDENCE_OWNED, negated(EVIDENCE_OWNED))),

    m(16, "引用到的权威行查不到即 cancelled（04 §10 不得引入无依据的引用）",
        DOMAIN_TESTS, GATE_TABLE + "/引用的权威行查不到",
        backup(GATE),
        patch(GATE, FRESH_RETURNED, negated(FRESH_RETURNED))),

    m(17, "Go 侧终态守卫：sent/expired/cancelled/responded 行不得被二次校验回头改",
        DOMAIN_TESTS, "TestProactiveService_RecheckTerminalRefuses",
        backup(SVC),
        patch(SVC, TERMINAL_GUARD, negated(TERMINAL_GUARD))),

    m(18, "发送时刻的到期检查：沉默过期必须置 expired 而不是补发（04 §19）",
        DOMAIN_TESTS, "TestProactiveService_RecheckExpiresAtDeadline",
        backup(SVC),
        patch(SVC, DEADLINE_GUARD, negated(DEADLINE_GUARD))),

    m(19, "SQL 侧终态守卫（08 §7 条件更新）：去掉 status 白名单后「0 行⇒ErrProactiveTerminal」就没了",
        STORE_TESTS, "TestProactiveRecheckMarksSentAndRealHistoryBlocksSecondSend",
        backup(SQL, SQLC_OUT),
        patch(SQL, SENT_GUARD, SENT_GUARD_OFF),
        sqlc_regen(
          marker="eligibility_detail = $3, updated_at = now()\n"
                 "WHERE id = $1 AND user_id = $2\nRETURNING sent_at",
          absent="SET status = 'sent', sent_at = now(), eligibility_detail = $3, "
                 "updated_at = now()\nWHERE id = $1 AND user_id = $2\n"
                 "  AND status IN")),

    m(20, "拒因必须在插入时就落 block_reason：生成时刻被挡的候选不再走 verdict 改写——本批真红过",
        STORE_TESTS, "TestProactivePrefsComeFromLegacyUsersRow",
        backup(REPO),
        patch(REPO, BLOCK_COL, BLOCK_COL_NULL)),

    m(21, "同上 reject_reason（P-9 的列分工）：取消是「本身没资格」，读路径按这一列拆",
        STORE_TESTS, "TestProactiveUserCorrectionCancelsQueuedSend",
        backup(REPO),
        patch(REPO, REJECT_COL, REJECT_COL_NULL)),

    m(22, "沉默阶梯只认 role='user' 的消息（与现行链 chat_repository.get_last_user_message 同口径）",
        STORE_TESTS, "TestProactiveSilenceFallsBackToRegistrationWhenOnlyAssistantSpoke",
        backup(REPO),
        patch(REPO, ROLE_FILTER, ROLE_ANY)),
]


def main() -> int:
    wanted = {int(a) for a in sys.argv[1:]}
    failures = []
    attempted = 0
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
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
