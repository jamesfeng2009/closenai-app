#!/usr/bin/env python3
"""KG-28 三层次情绪分析的变异自证（2026-11-02）。

口径沿用前几批：**每条变异只喂它该打红的那一张闸**，并且要求报错原文里出现该用例的名片段，
否则「红」可能只是别的用例连带崩（假红）。GREEN 族反过来证这些闸不是假红制造机：
在服务源码／前端纯逻辑里加注释行，不该弄红任何判据。

三族靶点各自的形状，跑之前先读这段：

* **P 族（后端语义）**——这一族判的是「引用没过逐字校验就不给解释」「危机排在配额之前」
  「AI 写的话不能回头当用户的原话」。这三条都是**静默**的：改错不会报错、不会让进程起不来，
  它只在用户看到一条模型自己编的「思维模式」被标成「你的原话」那天咬人。
  P4/P5 是同一族的**排序**两条：P5 把配额整块挪到危机筛查之前（线上表现是「正在说想伤害自己的
  人收到一句『今天次数用完了』」），P4 把同意判定挪到开关之前（关着时回 40303 就泄露了功能存在）。
  ↳ 2026-11-02 踩到的一条**判据区分度**问题，记在这里而不是顺手改：把服务层那句开关兜底
  改成永不触发，`test_flag_off_rejects_before_any_business_judgement` 仍然绿——因为紧接着
  「今天没签到」那一条也是 40402，两条在同一格出口收敛。所以服务层的这条闸只证「不花额度、
  不调模型」，**泄露方向**的判据只能挂在路由层，P4 因此打的是 router 而不是 service。
* **C 族（契约面与路由面）**——P8 新增一条没登台账的端点、P9 把 mood 域整段前缀切给 go-api。
  这两条都是**放量那天才咬**的类（KG-26 的 `/reflections/prompts` 404 就是同一族的先例），
  所以红的必须是台账闸与 Ingress 闸本身，不是某个功能用例。
* **F 族（前端）**——`.tsx` 在本机 jest 的 node 环境里渲染不了，屏与面板的判据是源码扫描。
  因此这一族的变异全打在**源码形状**上：把 `quotaDone` 换成 `remaining{n:0}`、把 `!crisis`
  从 `canSave` 里摘掉、把用户正文塞进 `track` 的属性、把入口的第二个条件删掉。
  F10/F11/F12 打的是 15 语言文案面（键少了、天数漂了、分隔符丢了）。

⚠️ 纪律（见 [[feedback-mutation-gates-no-concurrency]]、
[[feedback-mutation-restore-no-git-checkout]]、[[feedback-definition-of-done-tests]]）：
**串行跑**、`copy2` 备份写回 ＋ `filecmp` 逐字节复核、**不用 `git checkout` 恢复**。
备份名带序号＋父目录（本批同时打 `locales/en.ts` 与 `locales/zh.ts` 两个同名不同目录的文件）。

用法：
    ./.venv/bin/python scripts/mutate_kg28_gates.py --check   # 只验锚点，各命中一次
    ./.venv/bin/python scripts/mutate_kg28_gates.py           # 基线 + 全量变异
    ./.venv/bin/python scripts/mutate_kg28_gates.py --only P1,F5
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FRONT = REPO / "frontend"
PY = REPO / ".venv" / "bin" / "python"

SERVICE = REPO / "backend" / "services" / "mood_analysis_service.py"
ROUTER = REPO / "backend" / "routers" / "mood.py"
GROWTH = REPO / "backend" / "services" / "growth_service.py"
INGRESS = REPO / "deploy" / "k8s" / "50-ingress.yaml"
LOGIC = FRONT / "src" / "services" / "moodAnalysis.ts"
SHEET = FRONT / "src" / "components" / "MoodAnalysisSheet.tsx"
APITS = FRONT / "src" / "services" / "api.ts"
HOME = FRONT / "src" / "screens" / "HomeScreen.tsx"
EN = FRONT / "src" / "i18n" / "locales" / "en.ts"
ZH = FRONT / "src" / "i18n" / "locales" / "zh.ts"

MOOD_TESTS = "backend/tests/test_mood_analysis.py"
T = lambda name: f"{MOOD_TESTS}::{name}"  # noqa: E731  简写：后端用例节点 id

# ---------------------------------------------------------------- 红闸清单
# (id, 破坏说明, 文件, 原文, 坏文, runner, 目标用例, 必须出现在报错里的名片段)
RED: list[tuple[str, str, Path, str, str, str, list[str], list[str]]] = [
    (
        "P1",
        "深潜档的「最少 2 条独立来源」改成 0：没证据也敢给思维模式",
        SERVICE,
        'DEPTH_MIN_SOURCES: Dict[str, int] = {"light": 0, "explore": 1, "deep": 2}',
        'DEPTH_MIN_SOURCES: Dict[str, int] = {"light": 0, "explore": 1, "deep": 0}',
        "pytest",
        [T("test_deep_needs_two_independent_sources_not_two_quotes")],
        ["test_deep_needs_two_independent_sources_not_two_quotes"],
    ),
    (
        "P2",
        "证据池只排 AI 草稿、不排上一轮分析自己写的句子（自我引用回来了）",
        SERVICE,
        'if _reflection_source(r) in ("draft", "mood_analysis"):',
        'if _reflection_source(r) in ("draft",):',
        "pytest",
        [T("test_previous_analysis_summary_is_not_evidence"),
         T("test_ai_authored_reflection_sources_match_the_quote_card")],
        ["test_previous_analysis_summary_is_not_evidence",
         "test_ai_authored_reflection_sources_match_the_quote_card"],
    ),
    (
        "P3",
        "purpose 前缀打错一个字母：配额池永远数不到，免费档变成不限次",
        SERVICE,
        '_PURPOSE_PREFIX = "services.mood_analysis."',
        '_PURPOSE_PREFIX = "services.mood_analyse."',
        "pytest",
        [T("test_every_depth_reports_a_purpose_the_quota_can_count")],
        ["test_every_depth_reports_a_purpose_the_quota_can_count"],
    ),
    (
        "P4",
        "路由层把同意判定挪到开关之前：关着时回一条 40303，就把「这里藏了一个功能」泄露出去了",
        ROUTER,
        '    if not settings.mood_analysis_depths_enabled:\n'
        '        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, "资源不存在")\n'
        '    await user_service.require_ai_consent(db, user_id)',
        '    await user_service.require_ai_consent(db, user_id)\n'
        '    if not settings.mood_analysis_depths_enabled:\n'
        '        raise AppException(ErrorCode.RESOURCE_NOT_FOUND, "资源不存在")',
        "pytest",
        [T("test_http_returns_40402_while_the_flag_is_off")],
        ["test_http_returns_40402_while_the_flag_is_off"],
    ),
    (
        "P5",
        "把配额检查整块挪到危机筛查**之前**：额度耗尽的人正在说想伤害自己，回的是「今天次数用完了」",
        SERVICE,
        '        # 3. 危机筛查：focus + 今日原文，确定性词表，命中即出、不调 LLM、不扣额度\n',
        '        # 变异：配额排在危机之前（这条功能最不该犯的错）\n'
        '        if await quota_service.is_mood_analysis_quota_exceeded(db, user):\n'
        '            raise AppException(ErrorCode.QUOTA_EXCEEDED, "今天的分析次数已经用完，请明天再来")\n\n'
        '        # 3. 危机筛查：focus + 今日原文，确定性词表，命中即出、不调 LLM、不扣额度\n',
        "pytest",
        [T("test_crisis_is_checked_before_the_quota")],
        ["test_crisis_is_checked_before_the_quota"],
    ),
    (
        "P6",
        "引用长度下限降到 1：中文两个字、英文三个字母都能「命中」，降级判定被假通过骗过去",
        SERVICE,
        "        if len(needle) < MIN_QUOTE_CHARS:",
        "        if len(needle) < 1:",
        "pytest",
        [T("test_a_quote_shorter_than_the_floor_is_not_evidence")],
        ["test_a_quote_shorter_than_the_floor_is_not_evidence"],
    ),
    (
        "P7",
        "逐字没命中也硬指一条原文当出处：改述（paraphrase）从此带着「你的原话」的壳出门",
        SERVICE,
        "        for normalized, material in index.items():\n            if needle in normalized:\n                return material\n        return None",
        "        for normalized, material in index.items():\n            if needle in normalized:\n                return material\n        return next(iter(index.values()), None)",
        "pytest",
        [T("test_paraphrase_quote_drops_the_pattern_entirely")],
        ["test_paraphrase_quote_drops_the_pattern_entirely"],
    ),
    (
        "P8",
        "新增一条没登台账的端点（合同冻结与台账真值必须当场抓到）",
        ROUTER,
        '@router.post("/mood/analysis")',
        '@router.post("/mood/diag")\nasync def mood_diag_probe():\n    """变异占位：没登台账的一条新端点。"""\n    return success({"ok": True})\n\n\n'
        '@router.post("/mood/analysis")',
        "pytest",
        ["backend/tests/test_contract_freeze.py::test_register_count_equals_decorator_count"],
        ["test_register_count_equals_decorator_count"],
    ),
    (
        "P8b",
        "同一条新端点，第二个证人：台账逐行重扫真值（计数会被人顺手改成 119，重扫不会）",
        ROUTER,
        '@router.post("/mood/analysis")',
        '@router.post("/mood/diag")\nasync def mood_diag_probe():\n    """变异占位：没登台账的一条新端点。"""\n    return success({"ok": True})\n\n\n'
        '@router.post("/mood/analysis")',
        "pytest",
        ["backend/tests/test_contract_ledger_truth.py::test_ledger_matches_fresh_scan"],
        ["test_ledger_matches_fresh_scan"],
    ),
    (
        "P9",
        "把 mood 域整段前缀切给 go-api（Exact 那两条是端点级，新增端点会被静默搬走并 404）",
        INGRESS,
        "          - path: /api/v1/mood/history\n",
        "          - path: /api/v1/mood\n            pathType: Prefix\n            backend:\n              service:\n                name: go-api\n                port:\n                  name: http\n          - path: /api/v1/mood/history\n",
        "pytest",
        ["backend/tests/test_k8s_manifests.py::test_mood_analysis_stays_on_py_api"],
        ["test_mood_analysis_stays_on_py_api"],
    ),
    (
        "P10",
        "仪表盘的 features 写死 True：开关关着时入口照样出现在首页（端点却 40402）",
        GROWTH,
        "mood_analysis_depths=settings.mood_analysis_depths_enabled",
        "mood_analysis_depths=True",
        "pytest",
        ["backend/tests/test_growth_service.py::test_dashboard_features_mirror_the_mood_analysis_flag"],
        ["test_dashboard_features_mirror_the_mood_analysis_flag"],
    ),
    (
        "F1",
        "前端深潜档窗口 30 天改成 14 天：两边各写各的，用户看的窗口不是服务端用的窗口",
        LOGIC,
        "  deep: { key: 'deep', minEvidence: 2, windowDays: 30, maxMaterials: 20, maxAlternatives: 2 },",
        "  deep: { key: 'deep', minEvidence: 2, windowDays: 14, maxMaterials: 20, maxAlternatives: 2 },",
        "jest",
        ["__tests__/moodAnalysis.test.ts"],
        ["四格全对"],
    ),
    (
        "F2",
        "入口判据从 `=== true` 放宽成 `!== false`：升级前的本地缓存（没有 features）也会显示入口",
        LOGIC,
        "  return features?.mood_analysis_depths === true;",
        "  return features?.mood_analysis_depths !== false;",
        "jest",
        ["__tests__/moodAnalysis.test.ts"],
        ["showMoodAnalysisEntry 只认真值"],
    ),
    (
        "F3",
        "「这不像我」正文把文案自带的分隔符裁掉：存进成长记录的是一句读不通的话",
        LOGIC,
        "  return `${prefix}${body}`;",
        "  return `${prefix.trimEnd()}${body}`;",
        "jest",
        ["__tests__/moodAnalysis.test.ts"],
        ["correctionContent 保留文案自带的分隔符"],
    ),
    (
        "F4",
        "额度耗尽回的是「今天还能看 0 次」而不是那句专用文案",
        SHEET,
        "    quota: t('moodAnalysis.quotaDone'),",
        "    quota: t('moodAnalysis.remaining', { n: 0 }),",
        "jest",
        ["__tests__/moodAnalysis.test.ts"],
        ["四码答复文案与错误码一一对应"],
    ),
    (
        "F5",
        "危机那条路径不再挡保存：把热线文案存成一条「用户自己的反思」",
        SHEET,
        "  const canSave = !!result && !crisis && !busy;",
        "  const canSave = !!result && !busy;",
        "jest",
        ["__tests__/moodAnalysis.test.ts"],
        ["危机与降级是两种呈现"],
    ),
    (
        "F6",
        "存下来的反思不带来源标记（下一轮它就回到了证据池，等于自我引用）",
        SHEET,
        "      await saveReflection(content, null, { source: 'mood_analysis' });",
        "      await saveReflection(content, null);",
        "jest",
        ["__tests__/moodAnalysis.test.ts"],
        ["两条保存出口都带 source=mood_analysis"],
    ),
    (
        "F7",
        "把用户这次打的 focus 塞进埋点属性（正文上分析面）",
        SHEET,
        "      track(MOOD_ANALYSIS_EVENTS.requested, analysisTrackProps({ depth: chosen }));",
        "      track(MOOD_ANALYSIS_EVENTS.requested, analysisTrackProps({ depth: chosen, focus }));",
        "jest",
        ["__tests__/moodAnalysis.test.ts"],
        ["埋点全部经 analysisTrackProps"],
    ),
    (
        "F8",
        "封装路径打错一个字母（/mood/analyse）：契约、台账与调用点三处不再同字",
        APITS,
        "'/mood/analysis'",
        "'/mood/analyse'",
        "jest",
        ["__tests__/moodAnalysis.test.ts"],
        ["analyzeMood 打的是 /mood/analysis"],
    ),
    (
        "F9",
        "入口只看开关、不看今天是否已签到：没签到点进去就是 40402",
        HOME,
        "showMoodAnalysisEntry(dashboard?.features) && !!dashboard?.today_mood && (",
        "showMoodAnalysisEntry(dashboard?.features) && (",
        "jest",
        ["__tests__/moodAnalysis.test.ts"],
        ["入口同时要求「开关为真」和「今天已签到」"],
    ),
    (
        "F10",
        "en 的深潜说明写 14 天而后端表仍是 30：文案与参数各自漂",
        EN,
        '    "deepDesc": "Last 30 days · a timeline, 2 other readings, a next step",',
        '    "deepDesc": "Last 14 days · a timeline, 2 other readings, a next step",',
        "jest",
        ["__tests__/moodAnalysis.test.ts"],
        ["三档说明里的天数与 DEPTH_SPECS 一致"],
    ),
    (
        "F11",
        "en 的 quotaDone 键整格删掉：面板读出来是空的（键清单与 locale 不再相等）",
        EN,
        '    "quotaDone": "No analyses left for today — come back tomorrow.",\n',
        "",
        "jest",
        ["__tests__/moodAnalysis.test.ts"],
        ["15 语言 × 30 键齐全且非空"],
    ),
    (
        "F12",
        "zh 的 correctionPrefix 冒号丢了：存下来的是一句粘连的话",
        ZH,
        '    "correctionPrefix": "这不像我：",',
        '    "correctionPrefix": "这不像我",',
        "jest",
        ["__tests__/moodAnalysis.test.ts"],
        ["correctionPrefix 一定以冒号收尾"],
    ),
]

# ---------------------------------------------------------------- 应保持绿
# (id, 说明, 文件, 原文, 坏文（只加注释）, runner, 目标)
GREEN: list[tuple[str, str, Path, str, str, str, list[str]]] = [
    (
        "G1",
        "服务源码三档表旁边加 12 行注释（同类增长）：后端 30 条判据都该照旧绿",
        SERVICE,
        "EXCERPT_MAX_CHARS = 500\n",
        "".join(f"# 增长占位 {i}\n" for i in range(12)) + "EXCERPT_MAX_CHARS = 500\n",
        "pytest",
        [MOOD_TESTS],
    ),
    (
        "G2",
        "前端纯逻辑里加 12 行注释：moodAnalysis.test.ts 全表都该照旧绿",
        LOGIC,
        "export function depthSpec(depth: MoodAnalysisDepth): DepthSpec {\n",
        "".join(f"// 增长占位 {i}\n" for i in range(12))
        + "export function depthSpec(depth: MoodAnalysisDepth): DepthSpec {\n",
        "jest",
        ["__tests__/moodAnalysis.test.ts"],
    ),
]

ALL_CASES = [*RED, *GREEN]


def patch(path: Path, old: str, new: str) -> None:
    src = path.read_text(encoding="utf-8")
    count = src.count(old)
    assert count == 1, f"{path.name}：锚点命中 {count} 次（要求恰好 1 次）\n{old[:80]!r}"
    path.write_text(src.replace(old, new, 1), encoding="utf-8")


def run_pytest(targets: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        [str(PY), "-m", "pytest", *targets, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def run_jest(targets: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        ["npx", "jest", "--silent", *targets],
        cwd=FRONT, capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def run(runner: str, targets: list[str]) -> tuple[int, str]:
    return run_pytest(targets) if runner == "pytest" else run_jest(targets)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    ap.add_argument("--no-baseline", action="store_true", help="跳过基线（只在排障时用）")
    args = ap.parse_args()

    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    if args.check:
        bad = 0
        for case in ALL_CASES:
            path, old = case[2], case[3]
            n = path.read_text(encoding="utf-8").count(old)
            if n != 1:
                print(f"坏锚点 {case[0]}：{path.name} 命中 {n} 次")
                bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}")
        return 1 if bad else 0

    # ── 基线：先把每张要认领的闸跑一遍，缺证据不许当绿 ──────────────
    if not args.no_baseline:
        print("── 基线 ──")
        for runner, targets, label in [
            ("pytest", [MOOD_TESTS], f"后端 {MOOD_TESTS}"),
            ("pytest", ["backend/tests/test_contract_freeze.py",
                        "backend/tests/test_k8s_manifests.py",
                        "backend/tests/test_growth_service.py"], "后端 合同/清单/仪表盘三张闸"),
            ("jest", ["__tests__/moodAnalysis.test.ts"], "前端 moodAnalysis.test.ts"),
        ]:
            code, out = run(runner, targets)
            tail = [ln for ln in out.splitlines() if "passed" in ln or "failed" in ln]
            print(f"  {'✓' if code == 0 else '✗ 基线不干净，先修再跑变异'}  {label}  {tail[-1] if tail else ''}")
            if code != 0:
                return 1

    tmp = Path(tempfile.mkdtemp(prefix="mut-kg28-"))
    originals: dict[Path, Path] = {}
    for i, case in enumerate(ALL_CASES):
        p = case[2]
        if p not in originals:
            bak = tmp / f"{i}_{p.parent.name}_{p.name}"
            shutil.copy2(p, bak)
            originals[p] = bak

    def restore() -> None:
        for p, bak in originals.items():
            shutil.copy2(bak, p)
            assert filecmp.cmp(bak, p, shallow=False), f"{p} 恢复后字节不一致"

    red_ok = red_fail = green_ok = green_fail = 0
    try:
        for case in RED:
            cid, desc, path, old, new, runner, targets, markers = case
            if wanted and cid.upper() not in wanted:
                continue
            try:
                patch(path, old, new)
                code, out = run(runner, targets)
                hit = all(m in out for m in markers) and "failed" in out
                if code != 0 and hit:
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    print(f"[{cid}] 打红 ✗（exit={code}，名片段命中={hit}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-8:]))
            finally:
                restore()
        for cid, desc, path, old, new, runner, targets in GREEN:
            if wanted and cid.upper() not in wanted:
                continue
            try:
                patch(path, old, new)
                code, out = run(runner, targets)
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 应保持绿 ✓  {desc}")
                else:
                    green_fail += 1
                    print(f"[{cid}] 应保持绿 ✗（exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-12:]))
            finally:
                restore()
    finally:
        restore()
        shutil.rmtree(tmp, ignore_errors=True)

    print(
        f"\n红族 {red_ok} 打红 / {red_fail} 未打红；绿族 {green_ok} 保持绿 / {green_fail} 误红"
        f"（合计 {len(RED)} 条破坏、{len(GREEN)} 条正向证人）"
    )
    return 0 if red_fail == 0 and green_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
