#!/usr/bin/env python3
"""KG-04／KG-05／KG-08 三张闸的变异自证（2026-11-02，批次 3）。

规矩与前几批一样，不许打折：
- **先跑基线**，基线不干净直接退出——不许把「本来就红」当成「我把文案包打红了」。
- **串行**，绝不并发：并发会把变异态当原件写回。
- 恢复只用 `copy2` 备份写回 + `filecmp` 逐字节复核，**禁用 `git checkout`**（工作区里有未提交改动）。
- 每条红靶除 exit≠0 外还要求**名片段**命中——红的必须是那条判据本身，不是别的用例被牵连崩了。

**这批多一种破坏方式**：文案包的「副标题要两版／关键词要两版」光靠文本锚点删不掉
（JSON 里删一个对象要连逗号一起删，锚点会脆），所以加了 `jsonpop` 靶型——
直接加载 JSON、弹出数组最后一项再写回。破坏面更精确，恢复仍走字节级备份。

用法：
    ./.venv/bin/python scripts/mutate_kg04_kg05_kg08_gates.py --check   # 只验锚点
    ./.venv/bin/python scripts/mutate_kg04_kg05_kg08_gates.py           # 全量
    ./.venv/bin/python scripts/mutate_kg04_kg05_kg08_gates.py --only F1,G3,H4
"""
from __future__ import annotations

import argparse
import copy
import filecmp
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = REPO / ".venv" / "bin" / "python"

PACK_JSON = REPO / "docs" / "aso" / "en-US-2026-11.json"
PACK_MD = REPO / "docs" / "aso" / "en-US-2026-11.md"
TRACKER = REPO / "docs" / "aso" / "keyword-tracker-2026-11.md"
BRIEF = REPO / "docs" / "aso" / "screenshot-brief-2026-11.md"
BOARD = REPO / "docs" / "keyword-growth-task-board-2026-11-01.md"

T_ASO = "backend/tests/test_aso_copy_pack.py"
T_TRACK = "backend/tests/test_keyword_tracker.py"
T_BRIEF = "backend/tests/test_screenshot_brief.py"
T_BOARD = "backend/tests/test_keyword_task_board.py"

CASES: list[dict] = [
    # ── F 族：KG-04 文案包 ───────────────────────────────────────
    dict(id="F1", kind="text", file=PACK_JSON, targets=[T_ASO],
         desc="把标题加到 31 字符：ASC 会硬拦的那条判据必须红",
         old='"title": "ClosenAI: AI Journal & Growth",',
         new='"title": "ClosenAI: AI Journal & Growthes",',
         markers=["title 超长度：31/30"]),
    dict(id="F2", kind="text", file=PACK_JSON, targets=[T_ASO],
         desc="把 JSON 的 limits 改成 200：上限不许由被检对象自己声明",
         old='"title": 30,\n    "subtitle": 30,\n    "keywords": 100',
         new='"title": 200,\n    "subtitle": 200,\n    "keywords": 200',
         markers=["limits.title 与 ASC 真实上限 30 不符"]),
    dict(id="F3", kind="text", file=PACK_JSON, targets=[T_ASO],
         desc="关键词字段塞进标题已收录的 journal：白占字符必须被抓",
         old='personality test,mbti,enneagram',
         new='personality test,journal,mbti,enneagram',
         markers=["重复了标题/副标题已收录的词"]),
    dict(id="F4", kind="text", file=PACK_JSON, targets=[T_ASO],
         desc="关键词字段塞进竞品品牌词 rosebud：R3 的红靶",
         old='reflection,goals,diary",',
         new='reflection,goals,diary,rosebud app",',
         markers=["含竞品品牌词"]),
    dict(id="F5", kind="text", file=PACK_JSON, targets=[T_ASO],
         desc="副标题 B 里写进 therapy：医疗边界禁语必须红",
         old='"text": "It asks how it actually went",',
         new='"text": "It asks how therapy went",',
         markers=["命中禁用词"]),
    dict(id="F6", kind="jsonpop", file=PACK_JSON, targets=[T_ASO],
         desc="删掉副标题 B 版（只剩一版）：台账要 A/B 两版",
         path="fields.subtitle_variants", markers=["副标题要有 A/B 两版"]),
    dict(id="F7", kind="text", file=PACK_JSON, targets=[T_ASO],
         desc="把 A 版的判停改成占位句：两周后没人能判",
         old='"两周后产品页浏览→安装转化低于 A 版基线或 KG-01 审计里「remembers you」被判改写；任一成立即换回 B。",',
         new='"看情况再说，不行就换回去。",',
         markers=["不是可判定的内容"]),
    dict(id="F8", kind="text", file=PACK_MD, targets=[T_ASO],
         desc="把 md §1 里标题的字符数从 29/30 手改成 28/30：文档与实测脱钩",
         old="| 标题 | — | `ClosenAI: AI Journal & Growth` | 29/30 |",
         new="| 标题 | — | `ClosenAI: AI Journal & Growth` | 28/30 |",
         markers=["md 里 标题 写 28，实测 29"]),
    dict(id="F9", kind="text", file=PACK_MD, targets=[T_ASO],
         desc="把 §1 表里副标题 A 的文本改一个字：表与文案包两面对拉必须红",
         old="`The journal that remembers you` | 30/30",
         new="`The journal that remembers all` | 30/30",
         markers=["的文本与文案包漂了"]),
    dict(id="F10", kind="text", file=PACK_JSON, targets=[T_ASO],
         desc="从包里删掉一条禁语声明（best）：清单与判据脱节要红",
         old='    "AI friend",\n    "best",\n',
         new='    "AI friend",\n',
         markers=["脚本判了但文案包没声明"]),
    # ── G 族：KG-05 关键词表 ─────────────────────────────────────
    dict(id="G1", kind="text", file=TRACKER, targets=[T_TRACK],
         desc="把一个词的月搜索量从 1.9K 写成 19K：量不许在下游表里被改写",
         old="| `ai journal` | 1.9K |", new="| `ai journal` | 19K |",
         markers=["台账写 1.9K，本表写"]),
    dict(id="G2", kind="text", file=TRACKER, targets=[T_TRACK],
         desc="删掉台账里有、本表该有的一个词（ai friend 整行）：覆盖漏登",
         old="| `ai friend` | 12.1K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 要一个 AI 朋友 | 无（不建造词对板页） | 淘汰 | 定位改判：ADR 决定 3 不做陪伴角色；`AI friend` 本身就在 KG-04 禁语清单，任何对外面都不写 |\n",
         new="", markers=["本表多出", "缺"]),
    dict(id="G3", kind="text", file=TRACKER, targets=[T_TRACK],
         desc="凭空加一个台账没抓过的词：不许用没核过的量占位",
         old="| `ai journal` | 1.9K |",
         new="| `ai diary app` | 3.2K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 泛找 | `/en/ai-journal`（KG-10） | 保留 | 假造的词，台账 §4 没抓过 |\n| `ai journal` | 1.9K |",
         markers=["本表多出"]),
    dict(id="G4", kind="text", file=TRACKER, targets=[T_TRACK],
         desc="抹掉一个 outdated 标记：工具自己标过期的事实被藏起来",
         old="| `journal prompts` | 40.5K（工具标 outdated） |",
         new="| `journal prompts` | 40.5K |",
         markers=["outdated 标记与台账不符"]),
    dict(id="G5", kind="text", file=TRACKER, targets=[T_TRACK],
         desc="把保留词的落点改成一个仓内不存在、也没挂 KG 的面：词不对板",
         old="| `personal growth journal` | 720 | Ubersuggest 美国/英语·单源 | 2026-10-27 | 成长＋书写 | `/en/self-improvement`（KG-14） |",
         new="| `personal growth journal` | 720 | Ubersuggest 美国/英语·单源 | 2026-10-27 | 成长＋书写 | `docs/planned/somewhere-that-is-not-here.md` |",
         markers=["既不指向台账真有的 KG 任务"]),
    dict(id="G6", kind="text", file=TRACKER, targets=[T_TRACK],
         desc="把 ai companion 那行从淘汰改成保留·主投：ADR 决定 3 被推翻",
         old="| `ai companion` | 18.1K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 找角色/陪伴 | 无（不建造词对板页） | 淘汰 |",
         new="| `ai companion` | 18.1K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 找角色/陪伴 | `/en/self-discovery`（KG-13） | 保留·主投 |",
         markers=["未淘汰，与 ADR 决定 3 冲突"]),
    dict(id="G7", kind="text", file=TRACKER, targets=[T_TRACK],
         desc="把 day one 那行的状态从仅限对比页改成保留：竞品词规则失效",
         old="| `day one journal` | 2.9K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 同上 | `/en/compare/day-one-alternative`（KG-16） | 保留·仅限对比页 |",
         new="| `day one journal` | 2.9K | Ubersuggest 美国/英语·单源 | 2026-10-27 | 同上 | `/en/compare/day-one-alternative`（KG-16） | 保留 |",
         markers=["含竞品品牌"]),
    dict(id="G8", kind="text", file=TRACKER, targets=[T_TRACK],
         desc="把 §3 分布的淘汰数改错一位（9→8）：手填数字与表现算脱钩",
         old="| 淘汰 | 9 |", new="| 淘汰 | 8 |",
         markers=["§3 汇总缺/错该行"]),
    dict(id="G9", kind="text", file=TRACKER, targets=[T_TRACK],
         desc="把一行的来源写成已交叉核对：单源事实被粉饰成双源",
         old="| `mood tracker` | 4.4K | Ubersuggest 美国/英语·单源 |",
         new="| `mood tracker` | 4.4K | Ubersuggest＋SearchAds 已交叉 |",
         markers=["来源没标单源"]),
    dict(id="G10", kind="text", file=TRACKER, targets=[T_TRACK],
         desc="删掉「R1 本轮零命中」这句话：未验证的规则被读成已在跑",
         old="| **零命中**：本轮 43 词最小 590",
         new="| 已生效：本轮 43 词最小 590",
         markers=["R1（<500）本轮零命中必须写在 R1 那一行"]),
    dict(id="G11", kind="text", file=TRACKER, targets=[T_TRACK],
         desc="把 §4 的规划 URL 数从 12 改成 11：与表里现算的结果脱钩",
         old="12 个 URL 目前不存在", new="11 个 URL 目前不存在",
         markers=["§4 的规划 URL 数与表里现算的"]),
    dict(id="G12", kind="text", file=TRACKER, targets=[T_TRACK],
         desc="把 R1 那行的「本轮最小 590」写成 720：结论与量列现算脱钩",
         old="本轮 43 词最小 590", new="本轮 43 词最小 720",
         markers=["表里现算最小"]),
    dict(id="G13", kind="text", file=TRACKER, targets=[T_TRACK],
         desc="把 R3 那行的「命中 4 条」写成 6 条：竞品词命中数不许手填",
         old="命中 4 条（Day One ×2、Rosebud ×2）", new="命中 6 条（Day One ×2、Rosebud ×2）",
         markers=["R3 声明命中 6 条"]),
    # ── H 族：KG-08 截图简报 ─────────────────────────────────────
    dict(id="H1", kind="text", file=BRIEF, targets=[T_BRIEF],
         desc="首帧主张回退成绝对化：`remembers everything` 必须红",
         old="| 1 | `It remembers what you choose to share` |",
         new="| 1 | `It remembers everything you say` |",
         markers=["第 1 帧主张命中禁语"]),
    dict(id="H2", kind="text", file=BRIEF, targets=[T_BRIEF],
         desc="首帧直接抄待法务的副标题 A：两面对拉必须红",
         old="| 1 | `It remembers what you choose to share` |",
         new="| 1 | `The journal that remembers you` |",
         markers=["首帧直接复制了待法务的副标题 A"]),
    dict(id="H3", kind="text", file=BRIEF, targets=[T_BRIEF],
         desc="把第 4 帧（结果回访）换成通用日记卖点：台账点名的差异化帧消失",
         old="| 4 | `You said you'd do it. How did it go?` | 行动—结果回访（四选一） |",
         new="| 4 | `Write today in two minutes` | 快速记录入口 |",
         markers=["没有任何一帧落在结果回访上"]),
    dict(id="H3b", kind="text", file=BRIEF, targets=[T_BRIEF],
         desc="结果回访那帧的界面源换成别的屏：差异化帧没有代码撑着等于空口",
         old="| `frontend/src/screens/GrowthRecordsScreen.tsx:217` |",
         new="| `frontend/src/screens/ReflectScreen.tsx:1` |",
         markers=["结果回访那帧没指到真代码"]),
    dict(id="H4", kind="text", file=BRIEF, targets=[T_BRIEF],
         desc="把第 4 帧的界面源行号挪一格（217→218）：引用与闸内锚点两面漂开必须红",
         old="`frontend/src/screens/GrowthRecordsScreen.tsx:217`",
         new="`frontend/src/screens/GrowthRecordsScreen.tsx:218`",
         markers=["写在闸里但文档没引用它", "test_screen_anchor_points_at_the_claimed_code"]),
    dict(id="H5", kind="text", file=BRIEF, targets=[T_BRIEF],
         desc="把第 2 帧的界面源指向不存在的屏文件：帧没有真界面撑着",
         old="`frontend/src/screens/HomeScreen.tsx:85`",
         new="`frontend/src/screens/MoodHeatScreen.tsx:85`",
         markers=["指了不存在的文件"]),
    dict(id="H6", kind="text", file=BRIEF, targets=[T_BRIEF],
         desc="把第 6 帧的取图条件整格写成「—」：六帧表退化成没人能判的表",
         old="| 导出任务能真跑出文件（`backend/services/export_service.py`），删除入口真在页上 |",
         new="| — |",
         markers=["是空的或占位"]),
    dict(id="H7", kind="text", file=BRIEF, targets=[T_BRIEF],
         desc="把「截图未取」改口成已就绪：本机没渲染条件却宣称交付素材",
         old="**状态**：简报就绪，**截图未取**。",
         new="**状态**：简报与六张成品图均已就绪。",
         markers=["简报必须声明截图未取"]),
    dict(id="H8", kind="text", file=BRIEF, targets=[T_BRIEF],
         desc="把开头引用的审计条数从 70 改回 69：上游数字过期不回头同步",
         old="（KG-01，70 条承诺审计）", new="（KG-01，69 条承诺审计）",
         markers=["简报里的审计条数与表里的"]),
    dict(id="H9", kind="text", file=BRIEF, targets=[T_BRIEF],
         desc="把第 5 帧主张加长到 40 字符以上：截图文字会换行吃画面",
         old="| 5 | `Look back through what you wrote` |",
         new="| 5 | `Look back through everything you wrote over the last year` |",
         markers=["超 40 要换行"]),
    # ── 绿闸：加说明性文字不许让判据变红 ─────────────────────────
    # ── B 族：台账自身的一致性闸（§1 现算 ↔ §2/按工作流/§3 抄录）────
    dict(id="B1", kind="text", file=BOARD, targets=[T_BOARD],
         desc="给台账某行的状态写成 `REVIEW`/`DONE` 并列：§0 的「一行一个状态」必须红",
         old="| GROWTH | P1 | `REVIEW` | KG-04 |",
         new="| GROWTH | P1 | `REVIEW`/`DONE` | KG-04 |",
         markers=["不是单个反引号包着的词"]),
    dict(id="B2", kind="text", file=BOARD, targets=[T_BOARD],
         desc="把 §2 快照的 TODO 条数从 5 改成 4：手算少一个必须红",
         old="| `TODO` | 5 |", new="| `TODO` | 4 |",
         markers=["§2 把 TODO 写成 4，§1 现算 5"]),
    dict(id="B3", kind="text", file=BOARD, targets=[T_BOARD],
         desc="把「按工作流」B 行的 REVIEW 从 3 改成 2：分组计数不许手填",
         old="| B ASO | 5 | 0 | 3 | 2 | 0 |", new="| B ASO | 5 | 0 | 2 | 2 | 0 |",
         markers=["工作流 B ASO 的 REVIEW 写 2，§1 现算 3"]),
    dict(id="B4", kind="text", file=BOARD, targets=[T_BOARD],
         desc="把 §3 的 KG-30 详情小节标题改坏（任务号多个尾巴）：§1 与 §3 两边漂开",
         old="#### KG-30 · ", new="#### KG-30X · ",
         markers=["§1 有任务但 §3 没写详情"]),
    dict(id="B5", kind="text", file=BOARD, targets=[T_BOARD],
         desc="把台账里 en.ts:432 那处完整引用的行号改成 99999：引用打不到真行",
         old="的 `step1Tagline`（`frontend/src/i18n/locales/en.ts:432`）和官网 hero",
         new="的 `step1Tagline`（`frontend/src/i18n/locales/en.ts:99999`）和官网 hero",
         markers=["台账引用 frontend/src/i18n/locales/en.ts:99999"]),
    dict(id="B6", kind="cell", file=BOARD, targets=[T_BOARD], row="KG-08", col=7,
         new="六帧脚本口头评审通过，文件稍后补（本条是变异 B6：证据列被清空成一句形容词）",
         desc="把 KG-08 行的证据列换成一句没有文件路径的形容词：`REVIEW` 必须有真交付物",
         markers=["标 REVIEW 但证据列没有指向仓内真存在的文件"]),
    dict(id="B7", kind="cell", file=BOARD, targets=[T_BOARD], row="KG-08", col=0,
         new="KG-8",
         desc="把 KG-08 的任务 ID 写成 KG-8：§0 的两位编号格式与 §2 清单同源必须红",
         markers=["任务 ID 必须是 KG-01 这种两位编号"]),
    dict(id="B8", kind="text", file=TRACKER, targets=[T_BOARD],
         desc="把词表开头引用的台账 §4 行号从 813 改成 147（那是 §3 的标题行）：节号漂了必须红",
         old="`docs/keyword-growth-task-board-2026-11-01.md:813` 起的映射表",
         new="`docs/keyword-growth-task-board-2026-11-01.md:147` 起的映射表",
         markers=["节号漂了"]),
    dict(id="B9", kind="text", file=PACK_MD, targets=[T_BOARD],
         desc="把 ASO 文档引的台账副标题 A 行号从 232 改成 249（那是 KG-05 的小节头）：引用的不是它说的那件事",
         old="`docs/keyword-growth-task-board-2026-11-01.md:232` 那条 `副标题 A`）先于 KG-01 审计写定",
         new="`docs/keyword-growth-task-board-2026-11-01.md:249` 那条 `副标题 A`）先于 KG-01 审计写定",
         markers=["引用的不是它说的那件事"]),
    dict(id="GA4", kind="green_text", file=BOARD, targets=[T_BOARD],
         desc="台账末尾追加一节「## 8. 变更记录」（不动 §1/§2/§3 任何行）：九条判据照旧绿",
         old="- 关键词量会波动；本表只用于排序与取舍，不作为投放承诺。",
         new="- 关键词量会波动；本表只用于排序与取舍，不作为投放承诺。\n\n"
             "## 8. 变更记录\n\n- 2026-11-02：批次 3 结案，KG-04／KG-05／KG-08 置 `REVIEW`。"
             "本条是绿闸 GA4 追加的段落，用来证明「追加一节说明」不会让任何计数判据变红——"
             "闸判的是 §1 现算与 §2、按工作流、§3 的抄录是否同源，不是文档长度。\n",
         markers=[]),
    dict(id="GA1", kind="green_text", file=PACK_MD, targets=[T_ASO],
         desc="ASO 文档末尾追加一节「变更记录」（不动字段文本与数字）：判据照旧绿",
         old="   第二来源（Apple Search Ads / Google Keyword Planner）未接入 ⇒ KG-05 的「至少两个来源」这条验收**未达**。",
         new="   第二来源（Apple Search Ads / Google Keyword Planner）未接入 ⇒ KG-05 的「至少两个来源」这条验收**未达**。\n\n"
             "---\n\n## 7. 变更记录\n\n- 2026-11-02：首次落盘（KG-04 批次）。本条是绿闸 GA1，用来证明"
             "「追加一节说明」不会让六类判据变红——闸判的是长度、用词重叠、竞品词、禁语与假设可判性，不是文档长度。"),
    dict(id="GA2", kind="green_text", file=TRACKER, targets=[T_TRACK],
         desc="词表末尾追加一节「变更记录」（不加表格行）：14 条判据照旧绿",
         old="- 回滚：本表与闸都是新增文件，删掉即回到台账 §4 单表状态，不影响任何运行时代码。",
         new="- 回滚：本表与闸都是新增文件，删掉即回到台账 §4 单表状态，不影响任何运行时代码。\n\n"
             "## 6. 变更记录\n\n- 2026-11-02：首次落盘 43 词（KG-05 批次）。本条是绿闸 GA2，用来证明"
             "「追加一节说明」不会让两侧对拉变红——闸比的是台账 §4 的词集合、量与 outdated 标记，不是本节的字数。"),
    dict(id="GA3", kind="green_text", file=BRIEF, targets=[T_BRIEF],
         desc="简报末尾追加一节「变更记录」（不动六帧表与引用）：20 条判据照旧绿",
         old="   导出/删除的真实边界（含延迟、含云端备份留存）要和 KG-02 隐私对照一起过一遍。",
         new="   导出/删除的真实边界（含延迟、含云端备份留存）要和 KG-02 隐私对照一起过一遍。\n\n"
             "## 6. 变更记录\n\n- 2026-11-02：首次落盘 6 帧（KG-08 批次）。本条是绿闸 GA3，用来证明"
             "「追加一节说明」不会让界面源引用变红——闸打开的是每一行代码文件并核对 needle，不是数段落。"),
]


def patch_text(path: Path, old: str, new: str) -> None:
    src = path.read_text(encoding="utf-8")
    n = src.count(old)
    assert n == 1, f"{path.name}：锚点命中 {n} 次（要求恰好 1 次）\n{old[:80]!r}"
    path.write_text(src.replace(old, new, 1), encoding="utf-8")


def patch_jsonpop(path: Path, dotted: str) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    obj = data
    for part in dotted.split(".")[:-1]:
        obj = obj[part]
    last = dotted.split(".")[-1]
    assert len(obj[last]) >= 2, f"{dotted} 只剩一版，这条靶没有破坏空间"
    obj[last] = obj[last][:-1]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_pytest(targets: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        [str(PY), "-m", "pytest", *targets, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def patch_cell(path: Path, row_id: str, col: int, new: str) -> None:
    """改 `| KG-xx |` 那一行的第 col 列（0 起）——台账行的破坏要精确到列。"""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    hits = [i for i, ln in enumerate(lines) if ln.startswith(f"| {row_id} |")]
    assert len(hits) == 1, f"{path.name}：`| {row_id} |` 命中 {len(hits)} 行（要求恰好 1 行）"
    i = hits[0]
    tail = "\n" if lines[i].endswith("\n") else ""
    cells = lines[i].rstrip("\n").strip("|").split("|")
    assert col < len(cells), f"{row_id} 只有 {len(cells)} 列，改不到第 {col} 列"
    cells[col] = f" {new} "
    lines[i] = "|" + "|".join(cells) + "|" + tail
    path.write_text("".join(lines), encoding="utf-8")


def apply(case: dict) -> None:
    kind = case["kind"]
    if kind in ("text", "green_text"):
        patch_text(case["file"], case["old"], case["new"])
    elif kind == "jsonpop":
        patch_jsonpop(case["file"], case["path"])
    elif kind == "cell":
        patch_cell(case["file"], case["row"], case["col"], case["new"])
    else:  # pragma: no cover
        raise SystemExit(f"未知靶型 {kind}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只验锚点命中数")
    ap.add_argument("--only", default="", help="逗号分隔的用例 id")
    ap.add_argument("--no-baseline", action="store_true", help="跳过基线（只在排障时用）")
    args = ap.parse_args()

    wanted = {x.strip().upper() for x in args.only.split(",") if x.strip()}
    cases = [c for c in CASES if not wanted or c["id"] in wanted]

    if args.check:
        bad = 0
        for case in cases:
            if case["kind"] in ("text", "green_text"):
                n = case["file"].read_text(encoding="utf-8").count(case["old"])
                if n != 1:
                    print(f"坏锚点 {case['id']}：{case['file'].name} 命中 {n} 次")
                    bad += 1
            elif case["kind"] == "cell":
                n = sum(1 for ln in case["file"].read_text(encoding="utf-8").splitlines()
                        if ln.startswith(f"| {case['row']} |"))
                if n != 1:
                    print(f"坏靶 {case['id']}：`| {case['row']} |` 命中 {n} 行")
                    bad += 1
            else:
                data = json.loads(case["file"].read_text(encoding="utf-8"))
                obj = data
                for part in case["path"].split("."):
                    obj = obj[part]
                if len(obj) < 2:
                    print(f"坏靶 {case['id']}：{case['path']} 不足两版")
                    bad += 1
        print(f"锚点校验：{'全部命中一次' if bad == 0 else f'{bad} 条有问题'}")
        return 1 if bad else 0

    if not args.no_baseline:
        print("── 基线 ──")
        for targets, label in [([T_ASO], "KG-04 文案包闸"), ([T_TRACK], "KG-05 词表闸"), ([T_BRIEF], "KG-08 简报闸"), ([T_BOARD], "台账一致性闸")]:
            code, out = run_pytest(targets)
            tail = [ln for ln in out.splitlines() if "passed" in ln or "failed" in ln]
            print(f"  {'✓' if code == 0 else '✗ 基线不干净，先修再跑变异'}  {label}  {tail[-1] if tail else out[-300:]}")
            if code != 0:
                return 1

    tmp = Path(tempfile.mkdtemp(prefix="mut-kg04-"))
    originals: dict[Path, Path] = {}
    for i, case in enumerate(cases):
        p = case["file"]
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
            green = case["kind"] == "green_text"
            snapshot = copy.copy(case)
            try:
                apply(case)
                code, out = run_pytest(case["targets"])
                if green:
                    if code == 0:
                        green_ok += 1
                        print(f"[{case['id']}] 应保持绿 ✓  {case['desc']}")
                    else:
                        green_fail += 1
                        print(f"[{case['id']}] 应保持绿 ✗（exit={code}）{case['desc']}")
                        print("  " + "\n  ".join(out.splitlines()[-12:]))
                else:
                    hit = all(m in out for m in case["markers"])
                    if code != 0 and hit:
                        red_ok += 1
                        print(f"[{case['id']}] 打红 ✓  {case['desc']}")
                    else:
                        red_fail += 1
                        print(f"[{case['id']}] 打红 ✗（exit={code}，名片段命中={hit}）{case['desc']}")
                        print("  " + "\n  ".join(out.splitlines()[-10:]))
            finally:
                del snapshot
                restore()
    finally:
        restore()

    print(f"\n红闸 {red_ok}/{red_ok + red_fail}；绿闸 {green_ok}/{green_ok + green_fail}；"
          f"红闸失手 {red_fail} 条")
    return 1 if (red_fail or green_fail) else 0


if __name__ == "__main__":
    raise SystemExit(main())
