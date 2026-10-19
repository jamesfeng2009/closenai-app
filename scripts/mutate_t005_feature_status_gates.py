#!/usr/bin/env python3
"""T0-05 功能状态台账闸的变异自证（2026-10-18）。

台账这种「人写状态、机器背书」的东西，最坏的死法不是写错一格，而是**判据看着在、
其实什么都判不动**：把 `verify()` 写成只查 JSON 能否解析，则整份台账无论填什么都绿。
所以这里不验代码覆盖，只验一件事——**每一条阶梯都能被一次具体的谎打红**。

靶全部打在 `docs/feature-status.json`（唯一事实源）上，判据是
`backend/tests/test_feature_status_truth.py::test_current_ledger_has_no_evidence_violation`
——现账必须零违例，所以任何一处虚高声明都只能由那一条闸打红，红的又必须是指名那条规则的
文字（marker），不是「反正整包红了」。

三条工作单点名的谎（M1/M3/M4）＋七条同族谎（M2、M5…M10），两条绿闸：

- M1 WIRED 但前端调用点清空／M2 WIRED 但调用点写成零调用方封装（`clearPushToken`：
  api.ts 里定义着、全前端零调用，`orphanEndpoints.test.ts` 登记为「有意不接」）；
- M3 PRODUCTION_ENABLED 但 cron 换成契约里有、crontab 里没排程的 `/api/v1/cron/follow-up`；
- M4 E2E_VERIFIED 的端到端证据指向不存在的测试文件；
- M5 verified_by 去掉「N passed」计数（只剩一句没有出处的「跑过了」）；
- M6 状态名写出六态之外；M7 把一行矩阵行名改掉，让 §16 某行失去覆盖；
- M8 切流键拼错一个字母；M9 static 功能点名一个不存在的发布资产；
- M10 PRODUCTION_ENABLED 但不给带日期的上线出处。

绿闸证明判据钉的是规则不是字样：G1 把 proactive.message 合法推到 PRODUCTION_ENABLED
（cron 已排程＋出处带日期＋无灰度键）**不该**红——这条同时反证 M3 的红来自「未排程」
而不是「PRODUCTION_ENABLED 这个词」；G2 只改写 next_gap 的措辞，也不该红。

⚠️ G1 是**一次性内存谎**：真实台账里 proactive.message 仍停在 E2E_VERIFIED，
因为「宿主 VPS 上 crontab 装没装」这件事本机无法取证，脚本管不到线上，人得管得住自己。

恢复一律 **copy2 + filecmp 逐字节校验**，不用 git checkout
（见 [[feedback-mutation-restore-no-git-checkout]]）；
**严禁并发跑**（见 [[feedback-mutation-gates-no-concurrency]]）。

用法：
    ./.venv/bin/python scripts/mutate_t005_feature_status_gates.py --check
    ./.venv/bin/python scripts/mutate_t005_feature_status_gates.py
    ./.venv/bin/python scripts/mutate_t005_feature_status_gates.py --only M1,M3
"""
from __future__ import annotations

import argparse
import filecmp
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "docs" / "feature-status.json"
GATE = "backend/tests/test_feature_status_truth.py::test_current_ledger_has_no_evidence_violation"

# 每条用例是一组 (旧串, 新串) 编辑：JSON 的谎常常要两处一起改才自洽（改状态就得补出处）。
MUTATIONS: list[tuple[str, str, list[tuple[str, str]], str]] = [
    (
        "M1",
        "reflect.record 升到 WIRED 却把前端调用点清空：§15 末句要求「有 API 有调用」才谈得上接通",
        [('      "frontend_calls": ["generateReflectionDraft", "saveReflection", "listReflections"],\n',
          '      "frontend_calls": [],\n')],
        "WIRED 要求声明前端调用点",
    ),
    (
        "M2",
        "reflect.record 的调用点写成 clearPushToken（api.ts 里有定义、全前端零调用方）：拿死封装冒充接线",
        [('      "frontend_calls": ["generateReflectionDraft", "saveReflection", "listReflections"],\n',
          '      "frontend_calls": ["clearPushToken"],\n')],
        "零调用方",
    ),
    (
        "M3",
        "proactive.message 写 PRODUCTION_ENABLED，cron 换成契约里有、crontab 里没排程的 follow-up：调度是空的",
        [('      "state": "E2E_VERIFIED",\n      "verified_on": "2026-10-18",\n      "verified_by": "pytest test_delivery_off_e2e.py',
          '      "state": "PRODUCTION_ENABLED",\n      "production_evidence": "2026-10-18 宿主 VPS 已 crontab deploy/schedule/crontab",\n'
          '      "verified_on": "2026-10-18",\n      "verified_by": "pytest test_delivery_off_e2e.py'),
         ('      "cron": ["/api/v1/cron/daily-message"],\n',
          '      "cron": ["/api/v1/cron/follow-up"],\n')],
        "不在 deploy/schedule/crontab",
    ),
    (
        "M4",
        "life_archive.pdf 的端到端证据指向一个不存在的测试文件：E2E_VERIFIED 成了没有出处的话",
        [('      "e2e_tests": ["backend/tests/test_life_archive.py"],\n',
          '      "e2e_tests": ["backend/tests/test_life_archive_e2e.py"],\n')],
        "指向不存在的文件",
    ),
    (
        "M5",
        "journey.week1 的 verified_by 换成一句没有计数的「本地跑过」：E2E 结论失去出处",
        [('      "verified_by": "pytest test_journey.py 28 passed（含 deliver/dispatch 链）＋test_idempotency_indexes.py 16 passed'
          '（journey 日序唯一索引）＋test_cron_schedule_coverage.py 排程对账；jest journey.test.ts 在同批 1325 passed 内",',
          '      "verified_by": "本地跑过一遍，都过了",')],
        "verified_by 必须带实测计数",
    ),
    (
        "M6",
        "export.data 的状态写成 DONE：§15 六态之外的词不该被放过",
        [('      "state": "SPECIFIED",\n      "verified_on": null,\n      "verified_by": null,\n'
          '      "next_gap": "T1-01 未开工',
          '      "state": "DONE",\n      "verified_on": null,\n      "verified_by": null,\n'
          '      "next_gap": "T1-01 未开工')],
        "不在 §15 六态之内",
    ),
    (
        "M7",
        "把「模式/洞察」这行矩阵名改掉：§16 十行少一行覆盖，台账却没人报警",
        [('      "matrix_row": "模式/洞察",\n', '      "matrix_row": "模式洞察",\n')],
        "没有任何 feature 覆盖",
    ),
    (
        "M8",
        "切流键拼错一个字母（MEMORIES→MEMORY）：指向一个 config.go 里不存在的灰度开关",
        [('      "cutover_key": "CLOSEN_CUTOVER_MEMORIES",\n',
          '      "cutover_key": "CLOSEN_CUTOVER_MEMORY",\n')],
        "在 go-core/internal/config 里不存在",
    ),
    (
        "M9",
        "privacy.terms 点名一份不存在的韩语法务页：静态发布功能的资产列成了空气",
        [('        "site/src/pages/zh/terms.astro",\n',
          '        "site/src/pages/zh/terms.astro",\n        "site/src/pages/ko/terms.astro",\n')],
        "assets 里 site/src/pages/ko/terms.astro 不存在",
    ),
    (
        "M10",
        "proactive.message 写 PRODUCTION_ENABLED 却不给带日期的上线出处：cron 排了程，装没装是另一件事",
        [('      "state": "E2E_VERIFIED",\n      "verified_on": "2026-10-18",\n      "verified_by": "pytest test_delivery_off_e2e.py',
          '      "state": "PRODUCTION_ENABLED",\n      "verified_on": "2026-10-18",\n      "verified_by": "pytest test_delivery_off_e2e.py')],
        "production_evidence",
    ),
]

GREEN: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "G1",
        "proactive.message 合法推到 PRODUCTION_ENABLED（cron 已排程＋出处带日期＋无灰度键）：不该红，"
        "反证 M3 的红来自「未排程」而不是这个词",
        [('      "state": "E2E_VERIFIED",\n      "verified_on": "2026-10-18",\n      "verified_by": "pytest test_delivery_off_e2e.py',
          '      "state": "PRODUCTION_ENABLED",\n      "production_evidence": "2026-10-18 宿主 VPS 已 crontab deploy/schedule/crontab",\n'
          '      "verified_on": "2026-10-18",\n      "verified_by": "pytest test_delivery_off_e2e.py')],
    ),
    (
        "G2",
        "只改写 reflect.record 的 next_gap 措辞（缺口还在、字不同）：判据钉的是「有没有写缺口」，不是某句话",
        [('      "next_gap": "§16 必测异常四条（网络失败、重复提交、空内容、越权读取）没有一条端到端闸；'
          'N8 灰度键 CLOSEN_CUTOVER_REFLECTIONS 默认 0"',
          '      "next_gap": "四条必测异常尚未立端到端闸；N8 灰度键 CLOSEN_CUTOVER_REFLECTIONS 默认 0"')],
    ),
]


def pytest(node: str) -> tuple[int, str]:
    proc = subprocess.run(
        [str(REPO / ".venv/bin/python"), "-m", "pytest", node, "-q", "--no-header"],
        cwd=REPO, capture_output=True, text=True, timeout=600,
    )
    return proc.returncode, proc.stdout + proc.stderr


def apply(edits: list[tuple[str, str]]) -> None:
    src = LEDGER.read_text(encoding="utf-8")
    for old, new in edits:
        n = src.count(old)
        assert n == 1, f"锚点命中 {n} 次（要求恰好 1 次）\n{old[:90]!r}"
        src = src.replace(old, new, 1)
    LEDGER.write_text(src, encoding="utf-8")
    import json

    json.loads(LEDGER.read_text(encoding="utf-8"))  # 坏 JSON 会让所有用例一起红，那是噪声不是判据


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数＋JSON 可解析")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    args = ap.parse_args()

    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}

    if args.check:
        src = LEDGER.read_text(encoding="utf-8")
        bad = 0
        for case in [*MUTATIONS, *GREEN]:
            for old, _ in case[2]:
                n = src.count(old)
                if n != 1:
                    print(f"坏锚点 {case[0]}：命中 {n} 次 → {old[:70]!r}")
                    bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}"
              f"（共 {sum(len(c[2]) for c in MUTATIONS + GREEN)} 处编辑）")
        return 1 if bad else 0

    base_code, base_out = pytest(GATE)
    if base_code != 0:
        print("基线就是红的，变异结果无法归因——先修台账再跑本脚本：")
        print("\n".join(base_out.splitlines()[-15:]))
        return 1
    print("基线绿 ✓（现账零违例）")

    tmp = Path(tempfile.mkdtemp(prefix="t005-gate-backup-"))
    bak = tmp / LEDGER.name
    shutil.copy2(LEDGER, bak)

    def restore() -> None:
        shutil.copy2(bak, LEDGER)
        assert filecmp.cmp(bak, LEDGER, shallow=False), "台账恢复后字节不一致"

    red_ok = red_fail = green_ok = green_fail = 0
    failures: list[str] = []
    try:
        for cid, desc, edits, marker in MUTATIONS:
            if wanted and cid.upper() not in wanted:
                continue
            try:
                apply(edits)
                code, out = pytest(GATE)
                hit = marker in out and re.search(r"\d+ (failed|error)", out)
                if code != 0 and hit:
                    red_ok += 1
                    print(f"[{cid}] 打红 ✓  {desc}")
                else:
                    red_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 打红 ✗（exit={code}，marker={bool(hit)}）{desc}")
                    print("  " + "\n".join(out.splitlines()[-10:]))
            finally:
                restore()
        for cid, desc, edits in GREEN:
            if wanted and cid.upper() not in wanted:
                continue
            try:
                apply(edits)
                code, out = pytest(GATE)
                if code == 0:
                    green_ok += 1
                    print(f"[{cid}] 应保持绿 ✓  {desc}")
                else:
                    green_fail += 1
                    failures.append(cid)
                    print(f"[{cid}] 应保持绿 ✗（exit={code}）{desc}")
                    print("  " + "\n".join(out.splitlines()[-12:]))
            finally:
                restore()
    finally:
        restore()
        shutil.rmtree(tmp, ignore_errors=True)

    total = red_ok + red_fail + green_ok + green_fail
    print(
        f"\n变异自证合计：{total} 条｜红闸 {red_ok}/{red_ok + red_fail}"
        f"｜绿闸 {green_ok}/{green_ok + green_fail}"
    )
    if failures:
        print(f"未如预期：{', '.join(failures)}")
    print("docs/feature-status.json 已按备份写回并逐字节校验一致。")
    return 0 if red_fail == 0 and green_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
