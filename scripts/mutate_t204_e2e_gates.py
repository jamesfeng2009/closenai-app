#!/usr/bin/env python3
"""T2-04（证据链跨进程 E2E ＋ 双侧准入校验）的变异自证（2026-10-19）。

这一批交付的是**一条链的形状**：一行用户原文 → 一条 observation 命令 → 真 handler 抽取
（含子串闸、24h 成本闸、留痕）→ 候选 → 真 Go 入站 → `source_records`/`evidence` 两张权威表。
它坏起来的形态几乎都不报错：

* 子串校验一摘，模型编的那句「依据」会一路走进用户看得见的「为何相信这条」；
* 幂等指针一摘，同一行原文产两条来源登记，「这一行已经抽过了」的成本闸从此查不到；
* 计数一摘或双计，曲线要么少一类产出，要么虚高——两者都长得像「今天正常」；
* 永久/瞬态哨兵一摘，一条永远非法的候选撞完 30s/2m/10m 阶梯才进 DLQ，账上写着「重试过」。

**M1／M2／M3／M4 的证人点在这条链上，是因为工单 §验收点名的就是它**（2026-10-19 写台账时
逐条回查校正过口径，原来这里写的是「只有跨进程闸咬得住」，不准确）：这四格在两侧单测里
**各有自己的证人**（handler 的「2 合法＋1 非子串 ⇒ 只出 2」、metric 那条 `len(lines)==1`、
`promptVersion` 与常量对账、`already_extracted` 的分档），但单测里摘录只是内存字符串、
留痕号只是端口返回值——它们证不了「字真的跨过 broker 与库」，所以本批要的那半句只有这一轮给得出。
**真正整个仓只有跨进程看得见的是 M5**：破坏住在 Go 的 sqlc 生成物里，成对文本闸读的是 sqlc 的
**输入**（`go-core/db/queries/canonical.sql`），CI 又没有「重跑 sqlc 比对生成物」的漂移闸，
Python 侧一条单测都不会红。

纪律照抄既有口径（[[feedback-mutation-gates-no-concurrency]]、
[[feedback-mutation-restore-no-git-checkout]]、[[feedback-mutation-anchor-must-reach-target]]）：
**串行跑**、`cp -p` 备份＋`filecmp(shallow=False)` 逐字节写回、**不用 `git checkout`**、
每条变异前先有基线、名片段要求出现在报错原文里。

⚠️ 「红」在本文件有两种形态：`FAILED <节点>`，以及 **`ERROR <chain 用例节点>`＋名片段**。
后一种是故意的：本批的整条链跑在 module 级 fixture 里（一次链、多条判据），fixture 里的
`assert` 失败在 pytest 里落成 ERROR 而不是 FAILED。认它的前提是**名片段必须出现**——
那一句是判据自己写的（例如「没达成目标（expect=1/2）」），不是解释器或 import 的噪声。

前置（缺任一条本脚本**直接拒跑并回非零**，不许把「没跑」读成绿）：
`TEST_DATABASE_URL`（pytest 与 Go 读库）、`DATABASE_URL`（Python 驱动进程读库）、
`CLOSEN_AMQP_URL`（真 broker）、`GOROOT` 里那套点名过的工具链（[[go-toolchain-mixed-install]]）。

用法（env 由调用方给，凭据不进这个文件）：
    ./.venv/bin/python scripts/mutate_t204_e2e_gates.py --check
    GOROOT=/Users/fengyu/sdk/go1.27.1 PATH="$GOROOT/bin:$PATH" \\
      TEST_DATABASE_URL=... DATABASE_URL=... CLOSEN_AMQP_URL=... \\
      ./.venv/bin/python scripts/mutate_t204_e2e_gates.py
    ./.venv/bin/python scripts/mutate_t204_e2e_gates.py --only M1,M5
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

REPO = Path(__file__).resolve().parent.parent
GO_CORE = REPO / "go-core"
VENV_PY = REPO / ".venv" / "bin" / "python"
GOROOT = os.environ.get("GOROOT", "/Users/fengyu/sdk/go1.27.1")
GO_BIN = os.environ.get("GO_BIN", f"{GOROOT}/bin/go")
GO_ENV = {
    **os.environ,
    "GOROOT": GOROOT,
    "PATH": f"{GOROOT}/bin:{os.environ.get('PATH', '')}",
    "GO_BIN": os.environ.get("GO_BIN", GO_BIN),
}

PROMPTS = REPO / "backend" / "ai" / "prompts" / "observation.py"
HANDLER = REPO / "backend" / "ai" / "mq" / "handlers" / "observation.py"
SERVICE_GO = GO_CORE / "internal" / "domain" / "service_evidence.go"
UPSERT_GO = GO_CORE / "internal" / "store" / "canonicaldb" / "canonical.sql.go"
TOOL_GO = GO_CORE / "tools" / "r5interop" / "main.go"

E2E = "backend/tests/test_v3_evidence_e2e.py"
LOCATABLE = "backend/tests/test_evidence_excerpt_locatable.py"
CONTRACT_PY = "backend/tests/test_ai_mq_contract.py"
HANDLER_PY = "backend/tests/test_ai_mq_observation_handler.py"
GO_DOMAIN = "./internal/domain/"

#: 需要的前置 env：缺一条就不跑，而不是跑到一半把环境红当成闸红。
REQUIRED_ENV = {
    "TEST_DATABASE_URL": "pytest 读库＋Go 工具连库",
    "DATABASE_URL": "Python 驱动进程读库（少了它每一轮都是 source_missing）",
    "CLOSEN_AMQP_URL": "真 broker——这条链的地板",
}

# 每条：(id, 说明, [(文件, 原文, 坏文)], runner, target, node, 名片段)
MUTATIONS: list[tuple] = [
    # ── 1. 工单 §验收点名的那一条链：证人点在 E2E 上 ────────────────────────
    # 口径校正（2026-10-19 写台账时逐条回查过）：M1…M4 **不是**「只有跨进程看得见」——
    # 它们在两侧单测里各有证人（handler 的「2 合法＋1 非子串 ⇒ 只出 2」、metric 那条
    # `len(lines)==1`、`:346` 的 promptVersion 常量对账、`:415` 的 already_extracted 分档）。
    # 本批把证人点选在这条链上是因为**工单点名的就是它**，不代表单测抓不到。
    # 真正只有这一轮看得见的只有 M5 那一格（sqlc 生成物）。
    (
        "M1", "摘掉 excerpt 的逐字子串校验：假模型答三条里那条**原文根本没有**的摘录"
        "会一路出网、被 Go 接纳、进用户看得见的「依据」栏——工单 §验收点名的那条变异",
        [(PROMPTS,
          '        if not excerpt.strip() or excerpt not in content:\n            continue\n',
          '        if not excerpt.strip():  # 变异证人：摘掉子串校验\n            continue\n')],
        "pytest", f"{E2E}::test_chain_memory_to_evidence", "", "没达成目标（expect=1/2）",
    ),
    (
        "M2", "摘掉 24h 同源成本闸：同一行原文每次重投都重新问一次模型。"
        "候选面零产出这条判据、以及「重投没花新钱」那条留痕计数同时失效",
        [(HANDLER,
          "        if await self._ports.already_extracted(\n",
          "        if False and await self._ports.already_extracted(  # 变异证人：成本闸摘掉\n")],
        "pytest", f"{E2E}::test_replay_hit_cost_gate", "", "没达成目标（expect=1/0）",
    ),
    (
        "M3", "把抽取计数打两遍：`evidence_extracted_total` 的整条前提就是"
        "「每条命令一行、只有一个出口」，双计让曲线虚高而单测各自照绿",
        [(HANDLER,
          "        proposals, result = await self._run(command)\n"
          "        self._metric(command, result, len(proposals))\n",
          "        proposals, result = await self._run(command)\n"
          "        self._metric(command, result, len(proposals))\n"
          "        self._metric(command, result, len(proposals))  # 变异证人：双计\n")],
        "pytest", f"{E2E}::test_metric_line_crosses_process_boundary", "", "抽取计数打了 2 行",
    ),
    (
        "M4", "候选体的 `promptVersion` 写死成一个旧值（不再取常量）："
        "「同一句原文、不同 prompt 版本抽出不同观察」这件事的起点就查不出来了，"
        "而它是要摊进 `evidence.metadata_json` 给用户看的账面字段",
        [(HANDLER, '        "promptVersion": PROMPT_VERSION,\n',
          '        "promptVersion": "obs-v1",  # 变异证人：不再取常量\n')],
        "pytest", f"{E2E}::test_generation_lineage_crosses_processes", "",
        "'promptVersion': 'obs-v1'",
    ),
    (
        "M5", "把 `source_records` 的幂等指针摘掉（`ON CONFLICT` 换成按主键）："
        "两条候选各登记一行来源，于是 24h 成本闸的分组键从此查不到、"
        "「这一行原文变成过证据」这件事在库里对不上账。⚠️ 破坏住在 Go 的 sqlc 生成物里，"
        "**Python 侧一条单测都不会红**，只有跨进程闸咬得住",
        [(UPSERT_GO,
          "ON CONFLICT (user_id, source_table, source_id) DO UPDATE SET updated_at = now()",
          "ON CONFLICT (user_id, source_table, id) DO UPDATE SET updated_at = now()  -- 变异证人")],
        "pytest", f"{E2E}::test_chain_memory_to_evidence", "", "两条候选复用同一来源指针",
    ),
    # ── 2. 双侧准入校验（本批的另外两张闸）────────────────────────────────
    (
        "M6", "摘录上限写成 `> MaxExcerptChars+1`（一格 off-by-one）：501 字的摘录被收下，"
        "而「依据」栏会变成一整页——这是产物面边界，不是列宽",
        [(SERVICE_GO, "if utf8.RuneCountInString(c.Excerpt) > MaxExcerptChars {",
          "if utf8.RuneCountInString(c.Excerpt) > MaxExcerptChars+1 {  // 变异证人")],
        "go", GO_DOMAIN, "^TestService_AdmitEvidence_ExcerptCap$", "超限摘录必须在写库前挡住",
    ),
    (
        "M7", "摘掉终局分的定义域闸：因子表被改坏（或做成让 `math.Pow` 出 NaN 的负值）时，"
        "1.7 或 NaN 会落进 `evidence.strength`，而读路径会把它打印成置信度百分比",
        [(SERVICE_GO, "if !(scored.Strength >= 0 && scored.Strength <= 1) {",
          "if false && !(scored.Strength >= 0 && scored.Strength <= 1) {  // 变异证人")],
        "go", GO_DOMAIN, "^TestService_AdmitEvidence_StrengthMustStayInUnitInterval$",
        "因子表被改坏到 strength>1 时必须拒绝入账",
    ),
    (
        "M8", "形状类拒绝不再挂 `ErrCandidateShape` 哨兵：`inbound.classify` 只认哨兵，"
        "一条永远非法的候选会撞完 30s/2m/10m 阶梯才进 DLQ，而 `inbox.error_code` 是空白——"
        "值班看到的那一行写着「重试过」",
        [(SERVICE_GO,
          '\treturn fmt.Errorf("%w: %s", ErrCandidateShape, fmt.Sprintf(format, args...))\n',
          '\treturn fmt.Errorf("%s", fmt.Sprintf(format, args...)) // 变异证人：不挂哨兵\n')],
        "go", GO_DOMAIN, "^TestService_AdmitEvidence_RejectsMalformedProposals$", "没挂哨兵",
    ),
    (
        "M9", "只把 Python 那一侧的上限改成 400（Go 仍是 500）：同一条 450 字的摘录"
        "在这边被丢、在那边合法，两侧对同一句话结论不同 ⇒ 中间那段稳定进 DLQ",
        [(PROMPTS, "MAX_EXCERPT_CHARS = 500", "MAX_EXCERPT_CHARS = 400  # 变异证人：单侧改值")],
        "pytest",
        f"{CONTRACT_PY}::test_excerpt_length_cap_is_the_same_number_on_both_sides",
        "", "两侧对同一条摘录结论不同",
    ),
    (
        "M10", "摘掉生产者出网前的长度闸（改成放行）：把一条永远非法的摘录投上网络，"
        "让对面的永久拒绝去当校验——撞完阶梯再进 DLQ",
        [(PROMPTS,
          "        if len(excerpt) > MAX_EXCERPT_CHARS:\n            continue\n",
          "        if False and len(excerpt) > MAX_EXCERPT_CHARS:  # 变异证人：长度闸放行\n"
          "            continue\n")],
        "pytest",
        f"{LOCATABLE}::test_excerpt_cap_drops_the_overlong_item_and_keeps_the_boundary_one",
        "", "",
    ),
    (
        "M11", "把 `invalid`／`insufficient` 的分界并成一桶：模型给了东西却全被闸掉"
        "（＝该改 prompt）与「这段原文确实没东西」在曲线上长得一模一样",
        [(HANDLER,
          '        result = "ok" if proposals else ("invalid" if answered else "insufficient")\n',
          '        result = "ok" if proposals else "insufficient"  # 变异证人：两桶并一桶\n')],
        "pytest", f"{HANDLER_PY}::test_extracted_metric_reports_each_outcome_exactly_once",
        "", "'result': 'insufficient'",
    ),
]

#: 应当**保持绿**的破坏：证明闸钉的是语义，不是夹具文案与注释排版。
GREEN: list[tuple] = [
    (
        "GR1", "把 Go 那一档种的原文换成另一句同样合法的中文（仍 ≥30 字、仍不含那句编造摘录）："
        "E2E 判的是「摘录能不能指回库里那一行」，不该钉死夹具写了哪句话",
        [(TOOL_GO,
          'const observeMemoryContent = "这周有三天加班到十点以后，回家还是刷手机到一点多才睡，'
          '第二天早上整个人都很疲惫。"',
          'const observeMemoryContent = "最近连着几个周末都在整理旧照片，翻到很多以前'
          '随手写的东西，看着看着就到凌晨两点。"  # 变异证人：换夹具文案')],
        "pytest", f"{E2E}::test_chain_memory_to_evidence", "",
    ),
    (
        "GR2", "给 `MAX_EXCERPT_CHARS` 上方加一行注释：两侧同值闸读的是常量的**值**，"
        "不钉行号也不钉注释（钉排版的闸迟早被人改成 skip）",
        [(PROMPTS, "MAX_EXCERPT_CHARS = 500",
          "# 变异证人：只加一行注释，值不动\nMAX_EXCERPT_CHARS = 500")],
        "pytest",
        f"{CONTRACT_PY}::test_excerpt_length_cap_is_the_same_number_on_both_sides", "",
    ),
]

#: 恢复后必须复跑的东西：本批改过的闸＋跨进程链，一处残渣就当作失败。
RESIDUE = [
    E2E,
    LOCATABLE,
    HANDLER_PY,
    CONTRACT_PY,
    "backend/tests/test_ai_mq_consumer.py",
]


def _apply(edits: list) -> None:
    for path, old, new in edits:
        src = path.read_text(encoding="utf-8")
        n = src.count(old)
        assert n == 1, f"{path.name}：锚点命中 {n} 次（要求恰好 1 次）\n{old[:90]!r}"
        path.write_text(src.replace(old, new, 1), encoding="utf-8")


def _verify_anchor(edits: list) -> str | None:
    for path, old, _new in edits:
        if not path.exists():
            return "文件不存在"
        n = path.read_text(encoding="utf-8").count(old)
        if n != 1:
            return f"命中 {n} 次（要 1）"
    return None


def _sanity(edits: list) -> str | None:
    """变异体语法不通时红的是解释器而不是判据——先当场拦下。"""
    for path, _old, _new in edits:
        if path.suffix != ".py":
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            return f"{path.name}：变异体不是合法 Python（{exc}）"
    return None


def go_available() -> bool:
    return Path(GO_BIN).is_file()


def missing_env() -> list[str]:
    return [k for k in REQUIRED_ENV if not os.environ.get(k, "").strip()]


def run(runner: str, target: str, marker: str) -> tuple[int, str]:
    if runner == "go":
        if not go_available():
            return -2, f"{GO_BIN} 不存在：Go 侧用例没跑，不算绿"
        cmd = [GO_BIN, "test", target, "-count=1", "-timeout", "300s", "-run", marker]
        cwd = GO_CORE
    elif runner == "pytest":
        cmd = [str(VENV_PY), "-m", "pytest", target, "-q", "--no-header", "-x",
               "-p", "no:randomly"]
        cwd = REPO
    else:
        raise AssertionError(f"未知 runner：{runner}")
    proc = subprocess.run(cmd, cwd=cwd, env=GO_ENV, capture_output=True, text=True,
                          timeout=1800)
    return proc.returncode, proc.stdout + proc.stderr


def is_red(code: int, out: str, runner: str, target: str, marker: str) -> bool:
    """「红」＝**那条判据自己**失败；工具链坏／没匹配到用例／skip 都不算。

    pytest 侧认两种形态：`FAILED <节点>`（判据在测试体内），以及
    `ERROR <节点>` ＋名片段（本批的整条链跑在 module fixture 里，体内 assert 失败
    落成 ERROR；名片段保证那是判据写的句子而不是 import 噪声）。
    go 侧用 `-run` 精确过滤到那一条用例，所以 `--- FAIL:` 只可能是它，
    再要求名片段出现在报错原文里（名片段是从 `t.Fatalf` 文案里抄的）。
    """
    if code == 0:
        return False
    if "no tests ran" in out or "1 skipped" in out:
        return False
    if runner == "go":
        if "[build failed]" in out or "no tests to run" in out or "no test files" in out:
            return False
        return "--- FAIL:" in out and marker in out
    if f"ERROR {target}" in out:
        return bool(marker) and marker in out
    return f"FAILED {target}" in out


BASELINE: dict[tuple[str, str, str], tuple[int, str]] = {}


def baseline(runner: str, target: str, marker: str) -> tuple[int, str]:
    key = (runner, target, marker)
    if key not in BASELINE:
        BASELINE[key] = run(runner, target, marker)
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
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}（共 {len(cases)} 条）")
        return 1 if bad else 0

    gaps = missing_env()
    if gaps:
        print("前置 env 缺失，本脚本**一条都没跑**（「没跑」不是绿，回非零）：")
        for key in gaps:
            print(f"  {key}：{REQUIRED_ENV[key]}")
        return 2
    if not VENV_PY.is_file():
        print(f"解释器不存在：{VENV_PY}（变异自证要求先备好 .venv）")
        return 2
    if not go_available():
        print(f"提示：没找到 {GO_BIN}，Go 侧那三条会记「未跑」，不算绿")

    paths = sorted({e[0] for case in cases for e in case[2]})
    with tempfile.TemporaryDirectory(prefix="mutate-t204-") as tmp:
        originals: dict[Path, Path] = {}
        for idx, path in enumerate(paths):
            assert path.is_file(), f"靶文件不存在：{path}"
            # 备份名带序号＋父目录：本批同时打 domain/ 与 canonicaldb/ 下的 Go 文件，
            # 只用文件名会在 tmp 里互相覆盖，于是「恢复」把 A 写回 B。
            bak = Path(tmp) / f"{idx:02d}-{path.parent.name}-{path.name}"
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
            for cid, desc, edits, runner, target, node, marker in MUTATIONS:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = baseline(runner, target, node)
                if base_code == -2:
                    unrun.append(cid)
                    print(f"[{cid}] 未跑（{base_out}）：缺工具链不是绿，也不把它的红记在闸头上")
                    continue
                if base_code != 0:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 未跑（基线本来就不绿，exit={base_code}）："
                          "不能把环境的红记成闸的红\n  "
                          + "\n  ".join(base_out.splitlines()[-8:]))
                    continue
                _apply(edits)
                try:
                    broken = _sanity(edits)
                    code, out = (-1, broken) if broken else run(runner, target, node)
                finally:
                    restore()
                if code == -1:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（变异体自身不合法）：{out}")
                    continue
                if is_red(code, out, runner, target, marker):
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓（{runner}）  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（{runner} exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))

            for cid, desc, edits, runner, target, node in GREEN:
                if wanted and cid.upper() not in wanted:
                    continue
                base_code, base_out = baseline(runner, target, node)
                if base_code != 0:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 未跑（基线本来就不绿，exit={base_code}）：{desc}")
                    continue
                _apply(edits)
                try:
                    code, out = run(runner, target, node)
                finally:
                    restore()
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 保持绿 ✓（{runner}）  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 保持绿 ✗（{runner} exit={code}）{desc}")
                    print("  " + "\n  ".join(out.splitlines()[-18:]))
        except Exception as exc:  # noqa: BLE001
            restore()
            print(f"异常中止，已恢复：{exc!r}")
            return 2

        residue = 0
        for node in RESIDUE:
            code, out = run("pytest", node, "")
            if code != 0:
                residue += 1
                print(f"残留（红！）：{node}\n  " + "\n  ".join(out.splitlines()[-12:]))
        if go_available():
            proc = subprocess.run([GO_BIN, "test", GO_DOMAIN, "-count=1"], cwd=GO_CORE,
                                  env=GO_ENV, capture_output=True, text=True, timeout=1800)
            if proc.returncode != 0:
                residue += 1
                print(f"残留（红！）：go {GO_DOMAIN}\n"
                      + "\n  ".join((proc.stdout + proc.stderr).splitlines()[-12:]))
        else:
            print("复核 Go 整包：未跑（缺 go 工具链）")
        print("复核 两侧闸与跨进程链：" + ("绿" if residue == 0 else f"{residue} 条残留"))

        print(f"\nT2-04 变异自证：打红 {red_ok}/{len(MUTATIONS)}，保持绿 {green_ok}/{len(GREEN)}，"
              f"失败 {red_fail + green_fail}，未跑 {len(unrun)}，残留 {residue}")
        if unrun:
            print(f"未跑清单（缺工具链，不算绿）：{', '.join(unrun)}")
        if failures:
            print(f"失败清单：{failures}")
        return 1 if (red_fail or green_fail or residue or unrun) else 0


if __name__ == "__main__":
    sys.exit(main())
