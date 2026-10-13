"""N9 的变异自证：洞察依据展开读面（归属 / 可见性 / 活判据 / 裁剪 / memory_ref 回链 /
线上形状 / 回退开关），每一条口径都要能被一次真实的破坏打红。

手法与 mutate_n2…n7_gates.py 相同：备份 → 写坏 → 只跑那一条（组）测试 → 备份写回 →
filecmp 核验，**不用 git checkout**（仓里有并行未提交工作，checkout 会连带冲掉）。
库口令只从环境取（CLOSEN_PG_PASSWORD，否则现取容器 env），脚本与输出都不落明文。

SQL 那几条（#11 #12 #13 #14）必须经 sqlc 重生成才进得了编译路径，所以每条带
marker/absent 双侧断言，且作用域**限定在那一条命名查询的生成文本内**——
`s.status <> 'deleted'` 这条字面在一跳与二跳两条语句里都存在，拿整个生成文件做
absent 判定会永远判不过，等于闸门空转。

几处刻意不做的事，写明白免得被当成漏了：
1. 「excerpt 一字不截断」（N9-11）没有对应的代码面可破坏：读面上根本不存在截断语句，
   无锚点即无变异。它的可观察证据是 TestN9CapsTrimListsNotContent 里那句
   `view.Evidence[19].Excerpt == "今晚又三点才睡"` 与真库测的 HasExcerpt 断言。
2. `isVisibleStatus` 的空串守卫不做变异：词表里没有空串，把它改成恒假后行为一模一样
   （循环本来就匹配不到），打不出红 ⇒ 那条守卫是冗余防御，不是一条闸门。
3. `var _ domain.InsightEvidenceReader = (*Canonical)(nil)` 是编译期断言，删掉它
   没有任何测试会变红（编译本来就通过），它的作用在「接口下次加方法时先红」，
   那是编译面而不是测试面。
4. `optionalFloat` 的四位舍入不做变异：现有断言用的 0.9/0.6 在两位与四位精度下
   相等，改精度打不出红。要钉它得先加一条 0.123456 的断言——那是补测试，不在本批口径内。
5. handler 的 `s.evidence == nil` 那一半不做变异：拆掉后 no_reader 那条用例是在
   nil 解释器上调用方法 ⇒ panic 到 500，红是红了，但红的原因是崩溃而不是断言，
   按 N7 的口径（编不过/挂超时的红不作数）这类结果不计入。它由 #16 的开关侧覆盖。
6. 二跳「只往返一次」不做变异：那是接口签名决定的（一个方法取所有 pattern 的依据），
   把它改成 N+1 需要重写 domain 与 store 两处而不是一个锚点。它由
   TestN9PatternSecondHopIsOneQuery 的调用计数与真库侧「2 pattern 各 1 行」双向钉。

用法：
    .venv/bin/python scripts/mutate_n9_gates.py            # 全部
    .venv/bin/python scripts/mutate_n9_gates.py --check    # 只验锚点命中数，不跑 go test
    .venv/bin/python scripts/mutate_n9_gates.py 11 12     # 只跑点名几条
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
ER = GO / "internal/domain/evidence_read.go"
RD = GO / "internal/store/canonical_read.go"
HD = GO / "internal/httpapi/handlers_evidence.go"
CF = GO / "internal/config/config.go"
SQL = GO / "db/queries/canonical.sql"
SQLC_OUT = GO / "internal/store/canonicaldb/canonical.sql.go"
SQLC = "/opt/homebrew/bin/sqlc"

DOMAIN_TESTS = GO / "internal/domain"
STORE_TESTS = GO / "internal/store"
API_TESTS = GO / "internal/httpapi"
CFG_TESTS = GO / "internal/config"


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
    tmp = Path(tempfile.mkdtemp(prefix="n9-gate-backup-"))

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


def disabled(anchor: str) -> str:
    return re.sub(r" \{$", " && false {", anchor, count=1)


def sqlc_regen(name: str, marker: str = "", absent: str = ""):
    """重跑 sqlc，并核验**那一条命名查询**的生成文本真的带上变异。

    sqlc 有 mtime 缓存，copy2 恢复会把生成物拨回旧值，所以两个文件都 touch。
    """

    def run() -> None:
        for f in (SQL, SQLC_OUT):
            os.utime(f, None)
        subprocess.run([SQLC, "generate"], cwd=GO, capture_output=True, text=True,
                       env=ENV, check=True)
        generated = SQLC_OUT.read_text(encoding="utf-8")
        found = re.search(r"-- name: " + re.escape(name) + r" :\w+\n.*?\n`",
                          generated, re.S)
        assert found, f"生成物里没定位到 {name}，sqlc 没重生成，这条结果无效"
        scope = found.group(0)
        if marker:
            assert marker in scope, \
                f"sqlc 没把变异写进 {name} 的生成物，这条闸门未被破坏，结果无效：{marker!r}"
        if absent:
            assert absent not in scope, \
                f"{name} 的生成物里仍留着被删掉的片段，变异没落到编译路径上：{absent!r}"

    return run


def run_go(target: Path, test_name: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["go", "test", "./" + str(target.relative_to(GO)).strip("/"), "-count=1",
         "-run", test_name, "-v"],
        cwd=GO, capture_output=True, text=True, env=ENV, timeout=900)
    return proc.returncode, proc.stdout + proc.stderr


def m(num: int, claim: str, target: Path, test: str, *steps):
    return (num, claim, target, test, steps)


# ── 变异锚点（逐字取自当前源码，命中数由 patch 断言）─────────────────────────
VISIBLE_LIST = 'var InsightVisibleStatuses = []string{"published", "acknowledged", "confirmed"}\n'
EMPTY_USER = '\tif userID == "" {\n'
VISIBILITY = "\tif !isVisibleStatus(insight.Status) {\n"
WITHDRAWN = ("\tif counts.Total > counts.Live {\n"
             "\t\tsummary.WithdrawnCount = int(counts.Total - counts.Live)\n\t}\n")
WITHDRAWN_FLIP = ("\tif counts.Total < counts.Live {\n"
                  "\t\tsummary.WithdrawnCount = int(counts.Live - counts.Total)\n\t}\n")
STRENGTH_DENOM = "\t\tif item.HasStrength {\n"
TRUNCATED_TRUE = "\treturn capped, true\n"
PATTERN_CAP = "\tif maxPatterns > 0 && limit > maxPatterns {\n"
CAP_ALLOC = "\t\tcapped := make([]EvidenceItem, len(items))\n"
MEMORY_GUARD = ('\tif item.Source.SourceTable != SourceTableMemories || '
                'item.Source.SourceID == "" {\n')
NOTFOUND_WRAP = ('\t\treturn domain.InsightReadable{}, '
                 'fmt.Errorf("%w: %s", domain.ErrInsightNotFound, insightID)\n')
NOTFOUND_OPAQUE = ('\t\treturn domain.InsightReadable{}, '
                   'fmt.Errorf("store: insight %s is not visible", insightID)\n')
NULL_CONFIDENCE = ("\t\tConfidence: confidence, HasConfidence: hasConfidence,\n"
                   "\t\tTimeFrom: timeOf(row.TimeFrom), HasTimeFrom: row.TimeFrom.Valid,\n")
NULL_TIME = ("\t\tTimeFrom: timeOf(row.TimeFrom), HasTimeFrom: row.TimeFrom.Valid,\n"
             "\t\tTimeTo: timeOf(row.TimeTo), HasTimeTo: row.TimeTo.Valid,\n")
VISIBLE_BRANCH = "\t\t\terrors.Is(err, domain.ErrInsightNotVisible) {\n"
CODE_40402 = ('writeJSONError(w, r, apierr.New(apierr.CodeResourceNotFound,\n'
              '\t\t\t\t"资源不存在 / Resource not found"))')
KILL_SWITCH = "if s.evidence == nil || s.cfg.InsightEvidenceRead == evidenceReadOff {"
MEMORY_ID_TAG = '\tMemoryID  string     `json:"memory_id"`\n'
SWITCH_DEFAULT = '\t\tInsightEvidenceRead:    envString("CLOSEN_INSIGHT_EVIDENCE_READ", "on"),\n'
SWITCH_WHITELIST = ('	switch cfg.InsightEvidenceRead {\n	case "on", "off":\n	default:\n'
                    "\t\treturn cfg, errors.New(\n"
                    '\t\t\t"unsupported CLOSEN_INSIGHT_EVIDENCE_READ: only on|off are accepted")\n'
                    "\t}\n")
LIVE_FILTER = "  AND s.status <> 'deleted'\n"
ONE_HOP = ("WHERE ie.user_id = $1 AND ie.insight_id = $2\n" + LIVE_FILTER +
           "ORDER BY e.created_at DESC;")
SECOND_HOP = ("WHERE ip.user_id = $1 AND ip.insight_id = $2\n" + LIVE_FILTER +
              "ORDER BY pe.pattern_id, e.created_at DESC;")
COUNT_FILTER = ("       count(*) FILTER (WHERE s.status <> 'deleted')::bigint AS live_support\n")

MUTATIONS = [
    m(1, "空 user_id 守卫（05 §20 Never trust client user_id 的入口侧）：放行就等于"
         "拿一个没有归属的主体去下发查询",
      DOMAIN_TESTS, "TestN9ExplainerRequiresAuthenticatedUser",
      backup(ER), patch(ER, EMPTY_USER, disabled(EMPTY_USER))),

    m(2, "可见性闸：draft/validated/corrected/dismissed/retracted/expired 一旦被放行，"
         "内部候选态与已撤回历史就铺到了客户端（05:387 + N9-5）",
      DOMAIN_TESTS, "TestN9VisibleStatusGate",
      backup(ER), patch(ER, VISIBILITY, disabled(VISIBILITY))),

    m(3, "词表漂移哨兵：Go 侧多加一个 draft，就与 §37 SQL 的字面不是同一份了（N9-5 要求"
         "「判据只能有一份」）。这条打的是对齐闸本身，不是可见性判断",
      DOMAIN_TESTS, "TestN9VisibleStatusesMatchSQLLiteral",
      backup(ER),
      patch(ER, VISIBLE_LIST,
            VISIBLE_LIST.replace('}', ', "draft"}'))),

    m(4, "withdrawn 的负数钳位：并发撤回时 total < live，反向相减就报出「撤回了几条」"
         "这种不存在的事实（N9-6）",
      DOMAIN_TESTS, "TestN9WithdrawalClampedToZero",
      backup(ER), patch(ER, WITHDRAWN, WITHDRAWN_FLIP)),

    m(5, "强度均值分母把 NULL 算进去：没有强度的依据以 0 进分母，均值被压成一个假的「弱」"
         "（N9-12 的均值口径）",
      DOMAIN_TESTS, "TestN9SummaryAndMemoryRef",
      backup(ER), patch(ER, STRENGTH_DENOM, disabled(STRENGTH_DENOM))),

    m(6, "超限不标 truncated：用户看到「依据只有 20 条」却不知道该端点其实有 25 条，"
         "裁剪变成静默丢数据（N9-11）",
      DOMAIN_TESTS, "TestN9CapsTrimListsNotContent",
      backup(ER), patch(ER, TRUNCATED_TRUE, "\treturn capped, false\n")),

    m(7, "pattern 裁剪闸失效：PatternMaxItems 不设上限，一次响应把该用户所有模式连依据"
         "全铺出去（N9-11 的读路径上限）",
      DOMAIN_TESTS, "TestN9PatternCapTrimsGroups",
      backup(ER), patch(ER, PATTERN_CAP, disabled(PATTERN_CAP))),

    m(8, "空依据组退化成 nil：capItems 的分配从 make(len) 换成 nil 切片，编码出来就是 "
         "`evidence: null`，前端对 pattern.evidence 直接 .map 会炸（N9-11）",
      DOMAIN_TESTS, "TestN9PatternSecondHopIsOneQuery",
      backup(ER), patch(ER, CAP_ALLOC, "\t\tvar capped []EvidenceItem\n")),

    m(9, "memory_ref 判据的「来源是 memories」那一半：任何来源（chat_messages 等）都被"
         "当成用户素材回链，等于把非素材行递进 /memories 解析（N9-8）",
      DOMAIN_TESTS, "TestN9SummaryAndMemoryRef",
      backup(ER),
      patch(ER, MEMORY_GUARD, '\tif false || item.Source.SourceID == "" {\n')),

    m(10, "memory_ref 判据的「source_id 非空」那一半：空主键也回链，前端 "
          "resolveMemoryRefs() 的既有行为是解析不到即丢弃，回链静默消失（N9-8）",
       DOMAIN_TESTS, "TestN9MemoryRefNeedsRealID",
       backup(ER),
       patch(ER, MEMORY_GUARD,
             '\tif item.Source.SourceTable != SourceTableMemories || false {\n')),

    m(11, "一跳的活判据（SQL）：去掉 `s.status <> 'deleted'` 后，源已失效的依据照样"
          "返回，N6 的传播在读面被抹平（N9-6）",
      STORE_TESTS, "TestN9LiveEvidenceReadKeepsOnlyLiveSupport",
      backup(SQL, SQLC_OUT),
      patch(SQL, ONE_HOP, ONE_HOP.replace(LIVE_FILTER, "")),
      sqlc_regen("ListLiveEvidenceForInsight", absent=LIVE_FILTER.strip())),

    m(12, "二跳的活判据（SQL）：同一句字面只在一跳里生效不算数——第二跳漏掉它，pattern 的"
          "依据面就会把已失效的素材铺出去（N9-6/§37 的 Ins→Pattern→Evidence）",
      STORE_TESTS, "TestN9SecondHopIsOneQueryForAllPatterns",
      backup(SQL, SQLC_OUT),
      patch(SQL, SECOND_HOP, SECOND_HOP.replace(LIVE_FILTER, "")),
      sqlc_regen("ListLiveEvidenceForInsightPatterns", absent=LIVE_FILTER.strip())),

    m(13, "withdrawn 的取数面：COUNT 的 FILTER 一失效，live 恒等于 total，"
          "「凭空少掉的那几条」永远数不出来（N9-6 的可解释性）",
      STORE_TESTS, "TestN9LiveEvidenceReadKeepsOnlyLiveSupport",
      backup(SQL, SQLC_OUT),
      patch(SQL, COUNT_FILTER, "       count(*)::bigint AS live_support\n"),
      sqlc_regen("CountInsightEvidenceSupport", marker="count(*)::bigint AS live_support")),

    m(14, "一跳查询的归属谓词（SQL 侧的 05 §20）：从「user_id = $1 AND insight_id = $2」"
          "松成「或」，别人那条洞察的依据就能被自己的 id 组合读出来",
      STORE_TESTS, "TestN9ReadPathIsScopedToOwner",
      backup(SQL, SQLC_OUT),
      patch(SQL, ONE_HOP,
            ONE_HOP.replace("WHERE ie.user_id = $1 AND ie.insight_id = $2",
                            "WHERE (ie.user_id = $1 OR ie.insight_id = $2)")),
      sqlc_regen("ListLiveEvidenceForInsight",
                 marker="(ie.user_id = $1 OR ie.insight_id = $2)")),

    m(15, "仓储把「查不到」换成不透明错误：errors.Is 判不出来，handler 就落不到同一个 "
          "40402，越权与不存在回的是两个东西（N9-4）",
      STORE_TESTS, "TestN9ReadPathIsScopedToOwner|TestN9GetInsightForReadingMapsNullableColumns",
      backup(RD), patch(RD, NOTFOUND_WRAP, NOTFOUND_OPAQUE)),

    m(16, "NULL 强度搬成「有值」：可空列的 Has* 是读面唯一区分「没有评分」与「0 分」的"
          "依据，伪装成有值就是一条假的分位数（store 只搬列，不做判断）",
      STORE_TESTS, "TestN9GetInsightForReadingMapsNullableColumns",
      backup(RD),
      patch(RD, NULL_CONFIDENCE,
            NULL_CONFIDENCE.replace("HasConfidence: hasConfidence", "HasConfidence: true"))),

    m(17, "NULL 时间窗搬成「有值」：公元 1 年零时会被编成 time_range.from，"
          "用户看到的「覆盖这段时间」是无中生有（同上）",
      STORE_TESTS, "TestN9GetInsightForReadingMapsNullableColumns",
      backup(RD),
      patch(RD, NULL_TIME, NULL_TIME.replace("HasTimeFrom: row.TimeFrom.Valid",
                                             "HasTimeFrom: true"))),

    m(18, "handler 丢掉不可见分支：ErrInsightNotVisible 落到通用错误面，与「不存在」"
          "回的不是同一个体，端点变成 id 枚举 oracle（N9-4）",
      API_TESTS, "TestN9NotExistAndInvisibleAreSameBody",
      backup(HD), patch(HD, VISIBLE_BRANCH, disabled(VISIBLE_BRANCH))),

    m(19, "不可见回 403 而不是 40402：状态码一漂移，「这颗 id 是真的」就写进了响应",
      API_TESTS, "TestN9NotExistAndInvisibleAreSameBody",
      backup(HD),
      patch(HD, CODE_40402, CODE_40402.replace("apierr.CodeResourceNotFound",
                                               "apierr.CodeForbidden"))),

    m(20, "回退开关的判据（D-3）：off 不再生效就等于这条新读路径关不掉，出事只能回滚整个 "
          "Go 部署（N9 回退开关落点）",
      API_TESTS, "TestN9KillSwitchOff",
      backup(HD),
      patch(HD, KILL_SWITCH,
            KILL_SWITCH.replace("== evidenceReadOff {", '== "off-but-spelled-wrong" {'))),

    m(21, "memory_id 的 JSON tag 改名：前端兼容表（frontend-compat.md:91）把这条风险标为"
          "「最危险的一类」——字段名一动，依据回链静默消失",
      API_TESTS, "TestN9EvidenceEndpointShapeAndEnvelope",
      backup(HD), patch(HD, MEMORY_ID_TAG, '\tMemoryID  string     `json:"memoryId"`\n')),

    m(22, "开关默认值翻成 off：新端点没有既有消费者，默认关着等于这条权威读路径永远不上线",
      CFG_TESTS, "TestN9EvidenceReadSwitchValues",
      backup(CF),
      patch(CF, SWITCH_DEFAULT, SWITCH_DEFAULT.replace('"on"', '"off"'))),

    m(23, "on|off 白名单校验删除：拼错的 "
          '"false"/"OFF"/"off " 会被静默接受，"以为关了其实没关"的开关比没有开关更糟',
      CFG_TESTS, "TestN9EvidenceReadSwitchValues",
      backup(CF), patch(CF, SWITCH_WHITELIST, "\t_ = cfg.InsightEvidenceRead\n")),
]


def main() -> int:
    if "--check" in sys.argv[1:]:
        bad = 0
        for num, _, _, _, steps in MUTATIONS:
            for step in steps:
                try:
                    step()
                except AssertionError as exc:
                    bad += 1
                    print(f"[ANCHOR-BAD] #{num}: {exc}")
                    break
            restore_files()
        print(f"锚点自检：{len(MUTATIONS)} 条，坏锚点 {bad} 个")
        return 1 if bad else 0

    wanted = {int(a) for a in sys.argv[1:] if not a.startswith("--")}
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

    # 复跑受影响包：证明每份备份都写回去了。变异脚本最怕的不是打红，是打红之后把源码留在坏状态。
    for pkg in ("./internal/domain/", "./internal/store/", "./internal/httpapi/",
                "./internal/config/"):
        proc = subprocess.run(["go", "test", pkg, "-count=1"], cwd=GO,
                              capture_output=True, text=True, env=ENV, timeout=900)
        tail = (proc.stdout + proc.stderr).strip().splitlines()
        print(f"[{'GREEN' if proc.returncode == 0 else 'BROKEN'}] 复跑 {pkg}："
              f"{tail[-1] if tail else ''}")
        if proc.returncode != 0:
            print((proc.stdout + proc.stderr)[-2000:])
            return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
