#!/usr/bin/env python3
"""KG-22 复盘模板闸／KG-29 研究方案闸的变异自证（2026-11-03，批次 9）。

规矩与前八批一样，不许打折：
- **先跑基线**，基线不干净直接退出——不许把「本来就红」当成「我把模板打红了」。
- **串行**，绝不并发：并发会把变异态当原件写回。
- 恢复只用 `copy2` 备份写回 + `filecmp(shallow=False)` 逐字节复核，**禁用 `git checkout`**。
- 每条红靶除 exit≠0 外还要求**名片段**命中——红的必须是那条判据本身。

**这批的靶型与前八批不同**：这两张闸判的都是**「还没有数据」这件事本身**。
所以红靶打的不是「数字算错」，而是三类假交付：
1. 把模板写成结论（§4 结论表出现真数据行／§5 成本表出现人日／§1 假设的通过线改成事后对齐）；
2. 把来源含糊地说成有据（簇不在 §4、标了原词却不是原词、落点标了已落地却没有页面）；
3. 把状态写成跑得到（KG-22/KG-29 自称 REVIEW、CAC 规则现在就能跑、T7-07 已到位）。
每条都在 desc 里写明打的是哪一类。

**红线那条（U7）特意打在文档侧而不是契约侧**：真去改 `contracts/openapi.json` 会连带打红契约冻结闸，
那是别的批次的靶面，混进来只会让「红的到底是哪条」说不清。

用法：
    ./.venv/bin/python scripts/mutate_kg22_kg29_gates.py --check
    ./.venv/bin/python scripts/mutate_kg22_kg29_gates.py
    ./.venv/bin/python scripts/mutate_kg22_kg29_gates.py --only M4,U5,GN1
"""
from __future__ import annotations

import argparse
import copy
import filecmp
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = REPO / ".venv" / "bin" / "python"

MR = REPO / "docs" / "growth" / "monthly-review-2026-12.md"
UL = REPO / "docs" / "research" / "unsent-letter-2026-12.md"
T_M = "backend/tests/test_monthly_review_template.py"
T_U = "backend/tests/test_unsent_letter_research.py"

CASES: list[dict] = [
    # ── M 族：KG-22 复盘模板（11 红）─────────────────────────────
    dict(id="M1", kind="red", targets=[T_M], class_="来源含糊说成有据",
         desc="把 Q-08 的簇改成一个台账 §4 里根本没有的簇",
         patches=[(MR, "| Q-08 | `self reflection questions` | 自我反思 | §4 原词 | `self-reflection` | 已落地 |",
                   "| Q-08 | `self reflection questions` | 隐私 | §4 原词 | `self-reflection` | 已落地 |")],
         markers=["这些簇不在台账 §4 的映射表里"]),
    dict(id="M2", kind="red", targets=[T_M], class_="来源含糊说成有据",
         desc="把标了 `§4 原词` 的题面换成 §4 里没有的句子，标记却不改：出处造假",
         patches=[(MR, "| Q-19 | `goal tracker templates` | 目标 | §4 原词 | `goal-tracker` | 已落地 |",
                   "| Q-19 | `goal setting checklist app` | 目标 | §4 原词 | `goal-tracker` | 已落地 |")],
         markers=["这些题面标了 §4 原词，但 §4 里没有逐字命中"]),
    dict(id="M3", kind="red", targets=[T_M], class_="来源含糊说成有据",
         desc="把 Q-13（self-discovery 还没落地）的落点状态改成已落地：复盘的第一手靶子成了幻觉",
         patches=[(MR, "| Q-13 | `self discovery app` | 自我发现 | §4 原词 | `self-discovery` | 未落地（KG-13） |",
                   "| Q-13 | `self discovery app` | 自我发现 | §4 原词 | `self-discovery` | 已落地 |")],
         markers=["这些行标了已落地，但 pages.json 里没有该页"]),
    dict(id="M4", kind="red", targets=[T_M], class_="模板写成结论",
         desc="在结论表填一行真数据（KG-21 仍 BLOCKED，这行只能是无中生有）",
         patches=[(MR, "| 保留 | 待填 | 待填 | 待填 | GROWTH | 待填 |",
                   "| 保留 | `ai-journal` | `search_reach` 8 周 1.2K | ↑ | GROWTH | 2026-12-31 |")],
         markers=["KG-21 还 `BLOCKED`，结论表却出现了真数据行"]),
    dict(id="M5", kind="red", targets=[T_M], class_="模板写成结论",
         desc="把阈值表挂的指标键换成指标字典里没有的名字：另造一套无法对账的读数",
         patches=[(MR, "| `install`、`due_outcome` | 先修产品，不扩内容；结论落 `KG-25`／`KG-28` 那两面 | ARCH |",
                   "| `install`、`keyword_retention` | 先修产品，不扩内容；结论落 `KG-25`／`KG-28` 那两面 | ARCH |")],
         markers=["这些键不在 `docs/metrics-dictionary.md` 里"]),
    dict(id="M6", kind="red", targets=[T_M], class_="状态写成跑得到",
         desc="删掉 CAC 那一行：台账的四条触发条件在阈值表里只剩三条",
         patches=[(MR, "| 付费渠道 CAC 超内部上限 | 未启用（无投放账户，`KG-21` 数据面 BLOCKED） | "
                       "暂停该渠道；本行当前**不可触发**，写在这里是为了别到时候现编 | GROWTH |\n", "")],
         markers=["阈值表 3 行 ≠ 台账 4 条触发条件"]),
    dict(id="M7", kind="red", targets=[T_M], class_="状态写成跑得到",
         desc="把 CAC 那行的「当前不可触发」改成「现在就跑得起来，上限 30 美元」",
         patches=[(MR, "暂停该渠道；本行当前**不可触发**，写在这里是为了别到时候现编",
                   "暂停该渠道；本行现在就跑得起来，上限 30 美元")],
         markers=["CAC 那行却写得像跑得起来"]),
    dict(id="M8", kind="red", targets=[T_M], class_="来源含糊说成有据",
         desc="把四个引擎从「本批选定的观测面，属实现选择」改成「台账规定的观测面」",
         patches=[(MR, "这四个是本批**选定**的观测面，台账没规定，属实现选择",
                   "这四个是台账规定的观测面，不许替换")],
         markers=["引擎面没标注成实现选择"]),
    dict(id="M9", kind="red", targets=[T_M], class_="模板写成结论",
         desc="把与 T7-10 的合并口径改成「结论可以各写一份／两份同时各有一张决定表」：正是验收要防的第二份结论",
         patches=[(MR, "结论只写一次、写在 §4**；", "结论可以各写一份；"),
                  (MR, "判据：**两份文档不许同时各有一张「决定」表**", "判据：**两份文档可以同时各有一张「决定」表**")],
         markers=["合并口径没写成可判的禁令"]),
    dict(id="M10", kind="red", targets=[T_M], class_="来源含糊说成有据",
         desc="把记录字段里的「引用 URL」改名成「引用」：台账点名的字段清单少一格",
         patches=[(MR, "| 引用 URL | 完整 URL 或 `无` |", "| 引用 | 完整 URL 或 `无` |")],
         markers=["记录字段表缺这些台账点名的字段"]),
    dict(id="M11", kind="red", targets=[T_M], class_="状态写成跑得到",
         desc="把文档自称的状态从 TODO 改成 REVIEW（台账行没跟着改）：状态漂移",
         patches=[(MR, "状态：**仍 `TODO`**。理由只有一句", "状态：**已 `REVIEW`**。理由只有一句")],
         markers=["文档说自己是 REVIEW"]),
    dict(id="M12", kind="red", targets=[T_M], class_="来源含糊说成有据",
         desc="把表下计数句改回首版的 16／8（表格实为 18／6）：复述表格的那句话本身就是靶",
         patches=[(MR, "共 24 条：`§4 原词` 18 条、`同簇问法` 6 条",
                   "共 24 条：`§4 原词` 16 条、`同簇问法` 8 条")],
         markers=["句子说 `§4 原词` 16 条"]),
    # ── U 族：KG-29 研究方案（11 红）─────────────────────────────
    dict(id="U1", kind="red", targets=[T_U], class_="状态写成跑得到",
         desc="把文档自称的状态从 TODO 改成 REVIEW（访谈仍是 0 场）",
         patches=[(UL, "状态：**仍 `TODO`**。验收要的是", "状态：**已 `REVIEW`**。验收要的是")],
         markers=["文档 REVIEW ≠ 台账"]),
    dict(id="U2", kind="red", targets=[T_U], class_="模板写成结论",
         desc="伪造一份访谈纪要行塞进提纲后面：访谈 0 场却出现 R-01 的转写",
         patches=[(UL, "**提纲的硬规矩**：第 7、8 题不许合并",
                   "| R-01 | 2026-11-20 | 「写完就删了，不想给任何人看」 |\n\n**提纲的硬规矩**：第 7、8 题不许合并")],
         markers=["出现了 R-0x 的纪要行"]),
    dict(id="U3", kind="red", targets=[T_U], class_="来源含糊说成有据",
         desc="把招募封顶从 10 场改成 20 场（台账写的是 5–10 名）",
         patches=[(UL, "**5 场起算，10 场封顶**", "**5 场起算，20 场封顶**")],
         markers=["§2 的招募上下限没对上台账的 5–10"]),
    dict(id="U4", kind="red", targets=[T_U], class_="模板写成结论",
         desc="把假设的判据从「预注册」改成「访谈后对齐」：通过线等数据出来再定",
         patches=[(UL, "本表是预注册的判据，不是事后解释", "本表是访谈后对齐的判据，按回收情况定通过线")],
         markers=["阈值没写成预注册"]),
    dict(id="U5", kind="red", targets=[T_U], class_="模板写成结论",
         desc="给成本表的「审核」那一格填上 12 人日：没有访谈也没有运营记录，这数只能是编的",
         patches=[(UL, "| 审核 | UGC 队列、抽检比例、误伤申诉 | 未估 | PLAT |",
                   "| 审核 | UGC 队列、抽检比例、误伤申诉 | 12 人日 | PLAT |")],
         markers=["这格填了数"]),
    dict(id="U6", kind="red", targets=[T_U], class_="状态写成跑得到",
         desc="在决定矩阵加第四种出口「先做起来再看看」：正是验收句要防的那一种",
         patches=[(UL, "\n## 7. 红线与现状取证",
                   "\n| 先做起来再看看 | 任一受访者提到即可 | 直接开工，边做边看 |\n\n## 7. 红线与现状取证")],
         markers=["决定矩阵 4 行"]),
    dict(id="U7", kind="red", targets=[T_U], class_="来源含糊说成有据",
         desc="把取证处的红线原句改成「见台账」：判据两侧都失去可对照的句子",
         patches=[(UL, "**红线（验收原文）**：不在未验证前上线任何 UGC／公开分享入口。",
                   "**红线（验收原文）**：见台账，此处不再复述。")],
         markers=["文档没把红线原句抄在取证处"]),
    dict(id="U8", kind="red", targets=[T_U], class_="模板写成结论",
         desc="把「不与用户 ID 关联」改成「与用户 ID 关联以便回访」：匿名化只剩措辞",
         patches=[(UL, "访谈结论**不与用户 ID 关联**", "访谈结论**与用户 ID 关联以便后续回访**")],
         markers=["访谈结论与账号的关联没写清"]),
    dict(id="U9", kind="red", targets=[T_U], class_="状态写成跑得到",
         desc="删掉重开条件的第三条（法务口径）：三条合取变成两条",
         patches=[(UL, "3. 法务（`KG-02`）对匿名性与危机处置的书面口径就位。\n", "")],
         markers=["§8 的重开条件 2 条"]),
    dict(id="U10", kind="red", targets=[T_U], class_="来源含糊说成有据",
         desc="把 Baseline 的行引用改成 :9999（该文件只有 996 行）：完整引用必须打到真行",
         patches=[(UL, "CLOSENAI_V3_PRODUCT_AND_ARCHITECTURE_BASELINE.md:160` 一带",
                   "CLOSENAI_V3_PRODUCT_AND_ARCHITECTURE_BASELINE.md:9999` 一带")],
         markers=["Baseline 引用 :9999"]),
    dict(id="U11", kind="red", targets=[T_U], class_="状态写成跑得到",
         desc="把 §9 的「T7-07 未到位 ⇒ 招募跑不起来」改成已到位：残余清单与实际阻塞漂移",
         patches=[(UL, "`T7-07`（种子用户验证闸）未到位 ⇒ §2 的招募渠道现在跑不起来",
                   "`T7-07`（种子用户验证闸）已到位 ⇒ 招募渠道现在就能跑")],
         markers=["§9 把 T7-07 写成了"]),
    # ── 绿闸：正常收尾动作不许被判成破坏 ─────────────────────────
    dict(id="GN1", kind="green", targets=[T_M],
         desc="在阈值表下方加一条「校准记录：暂无」的说明段：四行阈值与键名都没动",
         patches=[(MR, "阈值是「内部建议值」这四个字的意思",
                   "**校准记录**：暂无。首月末在此写第一条，带绝对日期与依据来源。\n\n"
                   "阈值是「内部建议值」这四个字的意思")],
         markers=[]),
    dict(id="GN2", kind="green", targets=[T_M],
         desc="把 Q-20 的题面换成台账 §4 的真原词、来源改成 `§4 原词`，并同批把表下计数句改成 19／5：改表同批改句是正常收尾，不该被判红",
         patches=[(MR, "| Q-20 | `is my journal app private` | AI 日记 | 同簇问法 | `ai-journal` | 已落地 |",
                   "| Q-20 | `journal app` | AI 日记 | §4 原词 | `ai-journal` | 已落地 |"),
                  (MR, "共 24 条：`§4 原词` 18 条、`同簇问法` 6 条",
                   "共 24 条：`§4 原词` 19 条、`同簇问法` 5 条")],
         markers=[]),
    dict(id="GN3", kind="green", targets=[T_U],
         desc="给提纲追加第 13 题：判据只设下限 ≥10，题多不是破坏",
         patches=[(UL, "12. 如果这个功能明天下线，你会在意吗？\n",
                   "12. 如果这个功能明天下线，你会在意吗？\n"
                   "13. 如果它变成只有你能看到的私密抽屉，你会每周打开几次？\n")],
         markers=[]),
    dict(id="GN4", kind="green", targets=[T_U],
         desc="在匿名化表里再加一条「复核」规则：五条既有规矩逐条在场，多一条不算破坏",
         patches=[(UL, "| 二次用途 | 研究数据不得用于营销素材、金句卡或对外引用（`KG-30` 只取用户自己的记录，与本面无关） |",
                   "| 二次用途 | 研究数据不得用于营销素材、金句卡或对外引用（`KG-30` 只取用户自己的记录，与本面无关） |\n"
                   "| 复核 | 纪要对外引用前由 `KG-02` 法务面复扫一次措辞（危机表述的处置不写成医疗建议） |")],
         markers=[]),
]


def patch_text(path: Path, old: str, new: str) -> None:
    src = path.read_text(encoding="utf-8")
    n = src.count(old)
    assert n == 1, f"{path.name}：锚点命中 {n} 次（要求恰好 1 次）\n{old[:80]!r}"
    path.write_text(src.replace(old, new, 1), encoding="utf-8")


def run_pytest(targets: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        [str(PY), "-m", "pytest", *targets, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def apply(case: dict) -> None:
    for path, old, new in case["patches"]:
        patch_text(path, old, new)


def main() -> int:
    ap = argparse.ArgumentParser(description="KG-22/KG-29 模板与方案闸的变异自证")
    ap.add_argument("--check", action="store_true", help="只验锚点命中数")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    ap.add_argument("--no-baseline", action="store_true", help="跳过基线（只在排障时用）")
    args = ap.parse_args()

    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}
    cases = [c for c in CASES if not wanted or c["id"].upper() in wanted]

    if args.check:
        bad = 0
        for case in cases:
            for path, old, _ in case["patches"]:
                n = path.read_text(encoding="utf-8").count(old)
                if n != 1:
                    print(f"坏锚点 {case['id']}：{path.name} 命中 {n} 次 ⇒ {old[:60]!r}")
                    bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 处有问题'}（共 {len(cases)} 条用例）")
        return 1 if bad else 0

    if not args.no_baseline:
        print("── 基线 ──")
        for targets, label in (([T_M], "KG-22 复盘模板闸"), ([T_U], "KG-29 研究方案闸")):
            code, out = run_pytest(targets)
            tail = [ln for ln in out.splitlines() if "passed" in ln or "failed" in ln]
            mark = "✓" if code == 0 else "✗ 基线不干净，先修再跑变异"
            print(f"  {mark}  {label}  {tail[-1] if tail else out[-300:]}")
            if code != 0:
                return 1

    tmp = Path(tempfile.mkdtemp(prefix="mut-kg22-"))
    originals: dict[Path, Path] = {}
    for i, case in enumerate(cases):
        for p, _, _ in case["patches"]:
            if p not in originals:
                bak = tmp / f"{i}_{p.name}"
                shutil.copy2(p, bak)
                originals[p] = bak

    def restore() -> None:
        for p, bak in originals.items():
            shutil.copy2(bak, p)
            assert filecmp.cmp(bak, p, shallow=False), f"{p} 恢复后字节不一致"

    red_ok = red_fail = green_ok = green_fail = 0
    try:
        for case in cases:
            green = case.get("kind") == "green"
            snapshot = copy.deepcopy(case)
            try:
                apply(snapshot)
                code, out = run_pytest(snapshot["targets"])
                hit = all(m in out for m in snapshot["markers"])
                if green:
                    if code == 0:
                        green_ok += 1
                        print(f"[{snapshot['id']}] 应保持绿 ✓  {snapshot['desc']}")
                    else:
                        green_fail += 1
                        print(f"[{snapshot['id']}] 应保持绿 ✗（exit={code}）{snapshot['desc']}")
                        print("  " + "\n  ".join(out.splitlines()[-14:]))
                else:
                    if code != 0 and hit:
                        red_ok += 1
                        print(f"[{snapshot['id']}]（{snapshot['class_']}）打红 ✓  {snapshot['desc']}")
                    else:
                        red_fail += 1
                        why = "exit=0（没打红）" if code == 0 else f"exit={code} 但名片段没命中：{snapshot['markers']}"
                        print(f"[{snapshot['id']}] 打红 ✗  {why}  {snapshot['desc']}")
                        print("  " + "\n  ".join(out.splitlines()[-10:]))
            finally:
                restore()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n红闸 {red_ok}/{red_ok + red_fail}；绿闸 {green_ok}/{green_ok + green_fail}；红闸失手 {red_fail} 条")
    return 0 if red_fail == 0 and green_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
