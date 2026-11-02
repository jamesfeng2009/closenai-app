"""KG-29 私人「未寄出的信」研究方案闸（2026-11-03，批次 9）。

**这张闸判什么**：`docs/research/unsent-letter-2026-12.md` 交的是**研究方案**，不是访谈结果。
所以最容易造假的地方有两个：把「没访谈」写成「有结论」，和把「成本未估」写成「成本很贵所以不做」。
本文件用两组判据对着这两处：
① 文档内部——假设必须写成可证伪、成本五维必须全部 `未估`、决定矩阵只许三种出口、重开条件必须三条齐全；
② 文档外部——「未验证前不上线任何 UGC/公开分享入口」这条红线**不引文档的自我声明**，
   直接查契约快照 `contracts/openapi.json` 的路径集合。

**两侧不同源**：访谈人数区间（5–10）、成本维度（审核/举报/危机干预/匿名性）、红线原句、任务状态，
全部从台账 §KG-29 现读再比；台账改了措辞，本闸跟着红，不许长期放行旧口径。
红线那条是**反向归零**（没有浏览面路径），所以配了正向证人（路径总数与 `share` 路径条数必须在场），
否则契约文件一旦被读空，这条判据会对着空集永远点头。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "docs" / "research" / "unsent-letter-2026-12.md"
BOARD = REPO / "docs" / "keyword-growth-task-board-2026-11-01.md"
ATTRIBUTION = REPO / "docs" / "growth" / "attribution-2026-11.md"
BASELINE = REPO / "CLOSENAI_V3_PRODUCT_AND_ARCHITECTURE_BASELINE.md"
CONTRACT = REPO / "contracts" / "openapi.json"

TEXT = DOC.read_text(encoding="utf-8")
BOARD_TEXT = BOARD.read_text(encoding="utf-8")


def _norm(s: str) -> str:
    """全角标点不参与比对：台账用 `UGC/公开分享`，文档用 `UGC／公开分享`，那是同一句话。"""
    return (s.replace("／", "/").replace("（", "(").replace("）", ")")
             .replace("：", ":").replace("，", ",").replace(" ", "").replace("*", ""))


def _section(md: str, head: str) -> str:
    start = md.index(head)
    nxt = md.find("\n## ", start + 1)
    return md[start:] if nxt < 0 else md[start:nxt]


def _rows(block: str) -> list[list[str]]:
    out = []
    for line in block.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(c and set(c) <= {"-", ":", " "} for c in cells):
            continue
        out.append(cells)
    return out


def _task_detail(kg: str) -> str:
    start = BOARD_TEXT.index(f"#### {kg} ·")
    nxt = re.search(r"\n(#### |---\n)", BOARD_TEXT[start + 10:])
    return BOARD_TEXT[start:start + 10 + (nxt.start() if nxt else len(BOARD_TEXT))]


KG29 = _task_detail("KG-29")
KG29_NORM = _norm(KG29)
DOC_NORM = _norm(TEXT)
BOARD_ROWS = _rows(_section(BOARD_TEXT, "## 1. "))


def test_status_agrees_with_the_board_row() -> None:
    """文档自称的状态必须与台账行一致，而且两边现在都必须是 `TODO`。"""
    row = next(r for r in BOARD_ROWS if r and r[0] == "KG-29")
    declared = re.search(r"状态：\*\*(?:仍|已|保持)? ?`([A-Z]+)`", TEXT)
    assert declared, "文档开头没有『状态：**`XXX`**』这一行 ⇒ 状态这个载体本身没了"
    assert declared.group(1) == row[5].strip("`"), f"文档 {declared.group(1)} ≠ 台账 {row[5]}"
    assert row[5] == "`TODO`", "台账 KG-29 若已升档，本闸的『结论未产生』判据要整体重写，不是留旧口径"


def test_no_transcript_and_no_conclusion_yet() -> None:
    """反向归零（0 场访谈、0 份纪要、结论未产生）＋正向证人（编号区间与提纲条数在场）。"""
    assert _norm("本批访谈 0 场") in DOC_NORM, "文档没写明本批访谈 0 场 ⇒ 读者会以为已经做完了"
    assert not re.search(r"^\| `?R-\d", TEXT, re.M), "出现了 R-0x 的纪要行 ⇒ 访谈数据被写进来了，那是编的"
    assert "结论未产生" in DOC_NORM, "文档没写明结论未产生"
    qs = re.findall(r"^\d+\. ", _section(TEXT, "## 3."), re.M)
    assert len(qs) >= 10, f"访谈提纲只有 {len(qs)} 题，不足以支撑 §1 的四条假设"


def test_the_interview_count_is_the_boards_own_range() -> None:
    """台账写 5–10 名，文档就必须既写起算 5、也写封顶 10——只写一边等于没有边界。"""
    m = re.search(r"(\d+)[–-](\d+) 名目标用户访谈", KG29)
    assert m, "台账 §KG-29 的『N–M 名目标用户访谈』换了写法，本闸要跟着重写"
    lo, hi = m.group(1), m.group(2)
    s2 = _section(TEXT, "## 2.")
    assert f"{lo} 场起算" in s2 and f"{hi} 场封顶" in s2, f"§2 的招募上下限没对上台账的 {lo}–{hi}"
    assert "停止招募" in s2, "没有停止条件 ⇒ 访谈会一直加人，把噪声买成结论"


def test_the_hypotheses_are_falsifiable_before_any_data() -> None:
    """四条假设，每条都要有『判它成立』与『判它不成立』两栏，且阈值先写在数据之前。"""
    rows = [r for r in _rows(_section(TEXT, "## 1.")) if r and re.match(r"^H\d$", r[0])]
    assert len(rows) == 4, f"§1 的假设现算 {len(rows)} 条，判据按 4 条钉"
    for r in rows:
        assert len(r) >= 4 and r[2] and r[3], f"{r[0]} 缺『成立/不成立』某一侧 ⇒ 这条假设不可证伪"
    assert "预注册" in DOC_NORM, "阈值没写成预注册 ⇒ 通过线可以等访谈完再定"
    assert "事后解释" in DOC_NORM, "文档没写明这些判据先于数据"


def test_the_cost_grid_has_the_boards_dimensions_and_no_numbers() -> None:
    """成本维度逐条对台账点名，且每一维当前必须是『未估』——数字一旦先填上，『不做』就成了预设结论。"""
    m = re.search(r"成本估算\((.+?)\)", KG29_NORM)
    assert m, f"台账 §KG-29 的成本维度括号读不到：{KG29_NORM[:120]!r}"
    dims = [d for d in re.split(r"[、,]", m.group(1)) if d]
    assert len(dims) >= 4, f"从台账抠出的成本维度只有 {dims}"
    s5 = _section(TEXT, "## 5.")
    rows = {r[0]: r for r in _rows(s5) if r and r[0] not in {"维度", ""}}
    for d in dims:
        hit = [k for k in rows if d in k or k in d]
        assert hit, f"台账点名的成本维度『{d}』在 §5 表里没有行"
        assert "未估" in rows[hit[0]][2], f"『{d}』这格填了数 ⇒ 访谈 0 场，数字只能是编的"
    assert re.search(r"\d+\s*人日", s5) is None, "§5 出现了人日数字 ⇒ 未访谈先估算"


def test_the_decision_matrix_has_three_exits_only() -> None:
    """三种出口（不做／做私密形态／做含公开面），第四种『先做起来再看看』正是验收要防的。"""
    rows = [r for r in _rows(_section(TEXT, "## 6.")) if len(r) >= 3 and not r[0].startswith("出口")]
    assert len(rows) == 3, f"决定矩阵 {len(rows)} 行，判据钉 3 种出口"
    joined = _norm(" ".join(r[0] for r in rows))
    assert "不做" in joined and "做" in joined
    assert "先做" not in joined and "边做边看" not in joined, f"矩阵里混进了未验证先上线的出口：{joined}"
    for r in rows:
        assert r[2] and "待填" not in r[2], f"『{r[0]}』这一出口没写之后必须发生什么"
    assert "先推翻台账" in _norm(" ".join(" ".join(r) for r in rows)), (
        "含公开面的那一格必须先推翻台账 §5.2，否则 §5.2 会变成一句可以绕过的注释")


def test_the_red_line_is_proved_against_the_contract_not_the_prose() -> None:
    """红线原句从台账 §KG-29 验收现读，取证面是契约快照的路径集合，不是本文档的自我声明。"""
    assert "不在未验证前上线任何UGC/公开分享入口" in KG29_NORM, (
        "台账 §KG-29 的红线原句改了，本闸与文档 §7 一起重写")
    s7 = _norm(_section(TEXT, "## 7."))
    assert "未验证前上线任何UGC/公开分享入口" in s7, "文档没把红线原句抄在取证处（§7）"
    assert "contracts/openapi.json" in s7.replace("`", ""), "取证处没写明读的是契约快照 ⇒ 红线又回到自我声明"

    paths = list(json.loads(CONTRACT.read_text(encoding="utf-8"))["paths"])
    assert len(paths) >= 100, f"契约只读到 {len(paths)} 条路径 ⇒ 下面的归零判据对空集没意义"
    lowered = {p.lower() for p in paths}
    ugc = sorted(
        p for p in lowered
        if any(k in p for k in ("feed", "community", "treehole", "social", "follow", "stranger"))
        and p not in {"/api/v1/cron/follow-up", "/api/v1/insights/{insight_id}/feedback"}
    )
    assert not ugc, f"出现了 UGC/浏览面路径，红线被破：{ugc}"
    cards = sorted(p for p in lowered if p.startswith("/api/v1/share/"))
    assert len(cards) >= 10, f"`share` 路径只读到 {len(cards)} 条 ⇒ 正向证人失效，契约形状变了要重看"
    listed = [p for p in cards if re.search(r"/(list|index|all|explore)$", p)]
    assert not listed, f"分享面出现了可浏览的列表端点：{listed}"


def test_the_anonymisation_rules_borrow_the_attribution_ban() -> None:
    """访谈数据「不上送」不是新立的规矩，是 `KG-20` 那三条禁令的沿用：另一侧必须真在那份文档里。"""
    attr = _norm(ATTRIBUTION.read_text(encoding="utf-8"))
    assert "为什么今天不上送" in attr, "归因文档的禁令标题改了，本闸的『沿用』说法过期"
    s4 = _norm(_section(TEXT, "## 4."))
    assert "attribution-2026-11.md" in s4, "§4 没指回归因文档 ⇒ 『沿用』成了空话"
    for token in ("不上送任何访谈字段", "脱敏", "原始录音不落盘", "不入库"):
        assert token in s4, f"§4 缺匿名化规矩『{token}』"
    assert "不与用户ID关联" in s4, "访谈结论与账号的关联没写清 ⇒ 匿名化只是措辞"


def test_the_reopen_conditions_are_three_and_all_required() -> None:
    """验收句「若不做，写重开条件」要有可数载体，且三条是合取不是或。"""
    s8 = _section(TEXT, "## 8.")
    items = re.findall(r"^\d+\.\s", s8, re.M)
    assert len(items) == 3, f"§8 的重开条件 {len(items)} 条，判据按 3 条钉"
    assert "同时满足" in s8 and "缺一条都不重开" in s8, "重开条件写成了择一 ⇒ 等于随时可重开"
    assert "不重开≠永久不做" in _norm(s8), "没写『不重开 ≠ 永久不做』⇒ 读者会当成永久禁令"
    assert "另立实现任务" in _norm(_section(TEXT, "## 6.")), "验收要求『若决定做，另立实现任务』，矩阵里要有这一格"
    assert "评审" in _norm(_section(TEXT, "## 6.")), "验收要求『补隐私/安全评审』，矩阵里没落成动作"


def test_the_upstream_decisions_are_named_and_reachable() -> None:
    """§10 指回台账 §5.2 与 Baseline §3，两侧都要真在场（引用即自证）。"""
    assert "### 5.2" in BOARD_TEXT, "台账不再有 §5.2，本批的『上游决定』叙事要重写"
    s52 = _section(BOARD_TEXT, "### 5.2 ")
    assert "KG-29" in s52, "台账 §5.2 的『替代：私人未寄出的信（KG-29）』指回没了"
    assert "台账 §5.2" in _norm(TEXT) or "§5.2" in TEXT, "文档没指回台账 §5.2"

    s10 = _norm(_section(TEXT, "## 10."))
    m = re.search(r"product_and_architecture_baseline\.md:(\d+)", s10, re.I)
    assert m, "§10 没带 Baseline 的完整行引用"
    lines = BASELINE.read_text(encoding="utf-8").splitlines()
    n = int(m.group(1))
    assert 1 <= n <= len(lines), f"Baseline 引用 :{n}，该文件只有 {len(lines)} 行"
    assert _norm("社区") in _norm("\n".join(lines[max(0, n - 3):n + 8])), f"Baseline :{n} 附近没有『社区』那句话"


def test_the_two_dependency_faces_are_still_declared_as_unmet() -> None:
    """§9 的残余必须与台账一致：`KG-03` 已 REVIEW、`T7-07` 仍欠 ⇒ 招募渠道现在跑不起来。"""
    s9 = _norm(_section(TEXT, "## 9."))
    assert "KG-03" in s9, "§9 没交代 KG-03 的依赖关系"
    row03 = next(r for r in BOARD_ROWS if r and r[0] == "KG-03")
    assert row03[5].strip("`") in {"REVIEW", "DONE"}, f"文档说 KG-03 已 REVIEW，台账却是 {row03[5]}"
    m77 = re.search(r"`T7-07`\(([^)]*)\)(.{0,16})", s9)
    assert m77, "§9 读不到 `T7-07` 那一行 ⇒ 依赖面失去了载体"
    tail77 = m77.group(2)
    bad77 = "已到位" in tail77 or not any(k in tail77 for k in ("未到位", "BLOCKED", "未启动", "未解"))
    assert not bad77, f"§9 把 T7-07 写成了 {tail77!r} ⇒ 读者会以为现在就能招募"
