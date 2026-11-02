#!/usr/bin/env python3
"""KG-01／KG-03／KG-18 三张闸的变异自证（2026-11-02，批次 2）。

规矩与前几批一样，不许打折：
- **先跑基线**，基线不干净直接退出——不许把「本来就红」当成「我把代码打红了」。
- **串行**，绝不并发：并发会把变异态当原件写回（2026-10 那次教训，见记忆条）。
- 恢复只用 `copy2` 备份写回 + `filecmp` 逐字节复核，**禁用 `git checkout`**（工作区里有别人的未提交改动）。
- 每个红靶除 exit≠0 外，还要求**名片段**命中——红的必须是那条判据本身，
  不是别的用例被牵连崩了（前几批出现过「依赖链崩造成假红」）。

用法：
    ./.venv/bin/python scripts/mutate_kg01_kg03_kg18_gates.py --check     # 只验锚点
    ./.venv/bin/python scripts/mutate_kg01_kg03_kg18_gates.py             # 全量
    ./.venv/bin/python scripts/mutate_kg01_kg03_kg18_gates.py --only C1,D2,E3
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = REPO / ".venv" / "bin" / "python"

AUDIT = REPO / "docs" / "keyword-claims-audit-2026-11.md"
ADR = REPO / "docs" / "adr" / "2026-11-keyword-feature-scope.md"
GATE = REPO / "docs" / "keyword-content-gate.md"
PR = REPO / ".github" / "pull_request_template.md"
CLOSURE = REPO / "docs" / "growth" / "kg01-kg03-kg18-closure-2026-11-02.md"
BOARD = REPO / "docs" / "keyword-growth-task-board-2026-11-01.md"

T_AUDIT = "backend/tests/test_keyword_claims_audit.py"
T_ADR = "backend/tests/test_keyword_scope_adr.py"
T_GATE = "backend/tests/test_keyword_content_gate.py"
ALL3 = [T_AUDIT, T_ADR, T_GATE]

# (id, 说明, 文件, 原文锚, 坏文, runner, 目标用例, 名片段)
RED: list[tuple] = [
    # ── KG-01 审计表：证据完整性 ─────────────────────────────────
    ("C1", "把 en.ts:456 那行的「原文」换成文件里根本没有的字：引用与源文件脱钩",
     AUDIT, "| `frontend/src/i18n/locales/en.ts:456` | It remembers what you choose to share |",
     "| `frontend/src/i18n/locales/en.ts:456` | It remembers everything you ever said |",
     "pytest", [T_AUDIT], ["引用与源文件不一致"]),
    ("C2", "把锚点行号挪回方案文档里那个已漂 24 行的 :432：同源判据变假证据",
     AUDIT, "| `frontend/src/i18n/locales/en.ts:456` |", "| `frontend/src/i18n/locales/en.ts:432` |",
     "pytest", [T_AUDIT], ["引用与源文件不一致"]),
    ("C3", "删掉台账点名的权益虚标整行（web/index.html:645 性格画像不限）：点名锚消失",
     AUDIT,
     "| `web/index.html:645` | 性格画像不限 | 绝对化记忆 | 删除 | **权益虚标**：Pro+ 的真实刷新上限是每月 999 次"
     "（`backend/services/personality_service.py:563`，台账 §1.3 第 94 行），不是不限。改成：性格画像每月 999 次刷新 | GROWTH+BE |\n",
     "",
     "pytest", [T_AUDIT], ["审计表没有引用这些行"]),
    ("C4", "把四类风险之一的名字改成自造词（医疗边界→健康边界）：口径不再等于方案 §4.1",
     AUDIT, "| not for medical, legal, or financial decisions | 医疗边界 | 保留 |",
     "| not for medical, legal, or financial decisions | 健康边界 | 保留 |",
     "pytest", [T_AUDIT], ["风险类型只许方案 §4.1 那四类"]),
    ("C5", "把一条「改写」的改法写成「待补」：可执行结论退化成占位符",
     AUDIT, "改成：it remembers what you chose to share | BE+CONTENT |",
     "待补 | BE+CONTENT |",
     "pytest", [T_AUDIT], ["改法里塞着占位符"]),
    ("C6", "把一条保留判定的负责人写成不存在的角色（MGMT）：责任落不到人",
     AUDIT, "问的是用户要记什么，不是能力承诺 | FE |", "问的是用户要记什么，不是能力承诺 | MGMT |",
     "pytest", [T_AUDIT], ["改法/负责人不合格"]),
    ("C7", "把 §2 汇总的绝对化记忆条数改错一位（32→31）：手填数字与表脱钩",
     AUDIT, "| 绝对化记忆 | 32 | 17 |", "| 绝对化记忆 | 31 | 17 |",
     "pytest", [T_AUDIT], ["§2 汇总缺/错该行"]),
    ("C8", "删掉否定式免责句那一条（en.ts:589 保留行）：禁语闸失去正向证人",
     AUDIT,
     "| `frontend/src/i18n/locales/en.ts:589` | Observation only · no predictions, no promises | 无证据预测 | 保留 |"
     " 反思流程末步的注脚，明写「只观察、不预测、不许诺」，与 Baseline `:164` 一致，属要长期保留的否定式承诺 | FE |\n",
     "",
     "pytest", [T_AUDIT], ["以 `保留` 在册"]),
    ("C9", "把表头「改法」列改名（改法→建议）：六列口径漂走",
     AUDIT, "| 位置 | 原文 | 风险类型 | 判定 | 改法 | 负责人 |",
     "| 位置 | 原文 | 风险类型 | 判定 | 建议 | 负责人 |",
     "pytest", [T_AUDIT], ["表头必须恰好是方案 §4.1 的六列"]),
    # ── KG-03 ADR：定位与边界 ───────────────────────────────────
    ("D1", "把「温柔陪伴 AI 角色」的决定从「不做」改成「待定」：ADR 把板留给以后",
     ADR, "| 温柔陪伴 AI 角色 | 不做（含角色库、人设选择、浪漫向称呼与叙事） |",
     "| 温柔陪伴 AI 角色 | 待定 |",
     "pytest", [T_ADR], ["没有真决定"]),
    ("D2", "把上位判据的行号从 Baseline:91 挪到 :92：定位依据打不到那句「产品不是什么」",
     ADR, "`CLOSENAI_V3_PRODUCT_AND_ARCHITECTURE_BASELINE.md:91` 明写",
     "`CLOSENAI_V3_PRODUCT_AND_ARCHITECTURE_BASELINE.md:92` 明写",
     "pytest", [T_ADR], ["不再写着"]),
    ("D3", "引入一处裸行号引用（把 :164 那条的文件名删掉）：几个月后就是假证据",
     ADR, "`CLOSENAI_V3_PRODUCT_AND_ARCHITECTURE_BASELINE.md:164` 又把", "`:164` 又把",
     "pytest", [T_ADR], ["禁止不带文件名的行号引用"]),
    ("D4", "把五个决定的金句卡那行整条删掉：决定数量小于方案定的五个",
     ADR,
     "| 金句卡 | 只做用户原话卡，不做 AI 金句库 | 不重开（句子必须回到用户自己的记录，这是 KG-30 的端点契约，不是文案偏好） |\n",
     "",
     "pytest", [T_ADR], ["方案 §4.3 定的是五个决定"]),
    ("D5", "把「不新增端点，不新增表」改成「再加一个端点，新建一张表」：边界文件变成立项文件",
     ADR, "不改变任何已上线契约，不新增端点，不新增表",
     "不改变任何已上线契约，再加一个端点，新建一张表",
     "pytest", [T_ADR], ["ADR 要明说自己不动契约"]),
    ("D6", "把实验开关的引用行号从 config.py:211 挪到 :192（那行是配额注释）：回滚依据是假的",
     ADR, "`backend/config.py:211` 的 `mood_analysis_depths_enabled: bool = False`",
     "`backend/config.py:192` 的 `mood_analysis_depths_enabled: bool = False`",
     "pytest", [T_ADR], ["没有引用承重锚"]),
    # ── KG-18 内容质量闸：清单与 PR 模板同源 ────────────────────
    ("E1", "把清单里的「无虚假案例」改名成「案例留档」：方案 §4.7 点名的主题少一个",
     GATE,
     "| G06 | 无虚假案例 |", "| G06 | 案例留档 |",
     "pytest", [T_GATE], ["方案 §4.7 点名的主题在清单里找不到"]),
    ("E2", "把 hreflang 那项改名成「多语言标注」（PR 模板仍写 hreflang）：两边各漂一份",
     GATE, "| G11 | hreflang 互指 |", "| G11 | 多语言标注 |",
     "pytest", [T_GATE], ["方案 §4.7 点名的主题在清单里找不到"]),
    ("E3", "从 PR 模板里删掉 G09 那一行：模板与清单 ID 集合不再一致",
     PR, "- [ ] G09 1 个 CTA，且不做假按钮\n", "",
     "pytest", [T_GATE], ["PR 模板与清单的 ID 集合不一致"]),
    ("E4", "把 G03 的 PR 模板项改名（来源与日期→出处）：同名 ID 文字漂了",
     PR, "- [ ] G03 来源与日期", "- [ ] G03 出处与链接",
     "pytest", [T_GATE], ["同名 ID 的检查项文字漂了"]),
    ("E5", "把 G08 的机械判据整格写成「—」：清单项目没人能判",
     GATE, "内链 ≥2 且目标 slug 都在内容源的页面集合里（**断链＝红**）；孤儿页反向也要过 KG-17 的爬虫闸", "—",
     "pytest", [T_GATE], ["机械判据空洞"]),
    ("E6", "把 G04 的负责人写成一个不存在的角色（MGMT）：责任落不到人",
     GATE, "本页只能留草稿 | LEGAL+CONTENT |", "本页只能留草稿 | MGMT |",
     "pytest", [T_GATE], ["不在角色表里"]),
    ("E7", "把 canonical 那项的 ID 与顺序打乱（G10→G09B）：ID 不再是两位连续编号",
     GATE, "| G10 | canonical |", "| G09B | canonical |",
     "pytest", [T_GATE], ["ID 必须是 G01 这种两位编号"]),
    # ── 下游引用（2026-11-02 批次 3 补：本表加行会让下游节号/行数漂）────────
    ("C10", "把结案文档里本表 §4 的节号引用改回加行之前的 :142：上游加一行、下游不回头同步必须红",
     CLOSURE, "§4 缺陷登记 `:145`", "§4 缺陷登记 `:142`",
     "pytest", [T_AUDIT], ["把 §4 写成 :142"]),
    ("C11", "把台账 KG-01 行抄的审计行数从 70 改回 69：下游数字与本表现算脱钩",
     BOARD, "六列表 **70 行**", "六列表 **69 行**",
     "pytest", [T_AUDIT], ["写 69 行，本表现算 70 行"]),
    ("C12", "把台账 KG-01 行的判定分布里「待法务 4」写成 3：四类判定数不许手填旧值",
     BOARD, "删除 4／待法务 4`", "删除 4／待法务 3`",
     "pytest", [T_AUDIT], ["判定分布"]),
    ("C13", "在 §1 标题后插 4 行说明：本表 §2–§5 与表头行号整体下移，下游抄的行号必须一起红",
     AUDIT, "## 1. 审计表",
     "## 1. 审计表\n\n> 插入说明（变异 C13）：这四行把 §2–§5 与表头的行号整体下移 4 格。\n"
     "> 下游两处文档抄的是**行号**而不是节名，所以它们必须一起红——\n"
     "> 这条判据的存在理由，就是拦「上游改了、下游没回头」这种状态。\n",
     "pytest", [T_AUDIT], ["节号漂了"]),
]

GREEN: list[tuple] = [
    ("GA1", "审计文档**末尾**追加 12 行说明性文字（不动 §1–§5 与表头的行号）：含下游引用在内照旧绿（闸不靠字数判）",
     AUDIT, "| 随 KG-02 签字 | `:253`/`:436` 的 `Healer`、MBTI 商标、加密口径冲突、危机文案 | LEGAL |",
     "| 随 KG-02 签字 | `:253`/`:436` 的 `Healer`、MBTI 商标、加密口径冲突、危机文案 | LEGAL |\n\n"
     "<!--\n评审补充（2026-11-02，绿闸 GA1）：\n这张表的每一条都由闸读回源文件核对；\n"
     "行号漂、原文抄错、文件改名都会被拦下。\n本段加在**全文末尾**，因此 §1–§5 的行号一格未动，\n"
     "下游两处文档抄的节号引用照旧成立。\n下面的说明性文字不影响任何判据。\n"
     "这条绿闸存在的理由：证明闸不是靠段落长度或行数过关的。\n再补三行防止有人把注释行数当覆盖：\n"
     "第一行、第二行、第三行。\n最后一行。\n-->\n",
     "pytest", [T_AUDIT]),
    ("GA2", "ADR 末尾追加一节「变更记录」（不含新的行号引用）：五决定与证人照旧绿",
     ADR, "| 落地 | 对应模块负责人 | 新 ADR 或本 ADR 的修订记录（附日期） | 评审通过后一批内 |",
     "| 落地 | 对应模块负责人 | 新 ADR 或本 ADR 的修订记录（附日期） | 评审通过后一批内 |\n\n"
     "---\n\n## 5. 变更记录\n\n- 2026-11-02：首次落盘（KG-03 批次）。本条是绿闸 GA2 追加的段落，"
     "用来证明「追加一节说明」不会让五个决定的任何判据变红——闸判的是引用与代码事实，不是文档长度。",
     "pytest", [T_ADR]),
    ("GA3", "清单文档末尾追加「## 5. 变更记录」一节（不动 14 项与 PR 模板）：六条判据照旧绿",
     GATE, "这是为了防止「清单长成 14 项、PR 模板还停在 6 项」这种谁都没错但没人守的状态。",
     "这是为了防止「清单长成 14 项、PR 模板还停在 6 项」这种谁都没错但没人守的状态。\n\n"
     "## 5. 变更记录\n\n- 2026-11-02：首次落盘 14 项（KG-18 批次）。本条是绿闸 GA3 追加的段落，"
     "用来证明「追加一节说明」不会让任何判据变红——闸判的是主题覆盖、两边同源与代码事实，不是文档长度。",
     "pytest", [T_GATE]),
    ("GA4", "结案文档末尾追加一段说明（不动节号引用与抄录数字）：含下游引用在内的判据照旧绿",
     CLOSURE, "- 变异 harness：只读闸＋写临时备份，正常运行后仓库回到原状（`filecmp` 校验），无需回滚。",
     "- 变异 harness：只读闸＋写临时备份，正常运行后仓库回到原状（`filecmp` 校验），无需回滚。\n\n"     "追加说明（2026-11-02，绿闸 GA4）：本段的目的是证明「下游引用判据」不看文档长度——\n"     "只要节号引用、行数与判定分布仍与本表现算一致，多写几行不许让任何判据变红。\n",
     "pytest", [T_AUDIT]),
]

ALL_CASES = RED + GREEN


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


def run(runner: str, targets: list[str]) -> tuple[int, str]:
    assert runner == "pytest", "这批只有 pytest 面"
    return run_pytest(targets)


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

    if not args.no_baseline:
        print("── 基线 ──")
        for targets, label in [([T_AUDIT], "KG-01 审计闸"), ([T_ADR], "KG-03 ADR 闸"),
                               ([T_GATE], "KG-18 清单闸")]:
            code, out = run("pytest", targets)
            tail = [ln for ln in out.splitlines() if "passed" in ln or "failed" in ln]
            print(f"  {'✓' if code == 0 else '✗ 基线不干净，先修再跑变异'}  {label}  {tail[-1] if tail else ''}")
            if code != 0:
                return 1

    tmp = Path(tempfile.mkdtemp(prefix="mut-kg01-"))
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
        for cid, desc, path, old, new, runner, targets, markers in RED:
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

    print(f"\n红闸 {red_ok}/{red_ok + red_fail}；绿闸 {green_ok}/{green_ok + green_fail}；"
          f"红闸失手 {red_fail} 条")
    return 1 if (red_fail or green_fail) else 0


if __name__ == "__main__":
    raise SystemExit(main())
