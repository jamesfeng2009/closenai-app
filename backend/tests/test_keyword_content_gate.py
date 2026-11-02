"""KG-18 内容质量闸的完整性（2026-11-02）。

这个文件守的是**清单本身**，不是页面内容——页面内容的机器判据在
`scripts/build_seo_pages.py --check` 与 KG-17 的内链/发布闸里（本批之后落地）。

三条设计决定：
1. **清单与 PR 模板必须同源**。两边分别是 `docs/keyword-content-gate.md` 与
   `.github/pull_request_template.md`，本文件比对两边的 ID 集合与逐项名称。
   这是「断言两侧不许同源」的反向用法：正因为两个文件会各自漂，才要机器对拉。
2. **方案 §4.7 点名的 12 个主题一个都不许少**（`test_the_twelve_named_topics_are_covered`）。
   清单从 12 项涨到 14 项可以，把某项改名改到机器找不到不行。
3. **空抽检表只在「还没有页面」时合法**。`docs/content/drafts/` 与内容源一旦有页，
   抽检行数就要求 ≥ min(2, 页数)——这正是台账 §KG-18 验收的「抽检 2 页通过」，
   现在写死成 2 会让本批直接红，将来又没人回来补，所以做成会自己收紧的判据。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GATE_DOC = REPO / "docs" / "keyword-content-gate.md"
PR_TEMPLATE = REPO / ".github" / "pull_request_template.md"
PAGES_JSON = REPO / "site-content" / "pages.json"

ROLES = {"FE", "BE", "PLAT", "ARCH", "GROWTH", "CONTENT", "LEGAL"}
PLACEHOLDERS = {"", "-", "—", "–", "?", "？", "tbd", "todo", "n/a", "待补", "待定", "暂无", "略"}

#: 方案 §4.7 逐字点名的 12 个主题，映射到「检查项」列必须出现的字样。
NAMED_TOPICS = {
    "具体问题": "具体问题",
    "原创模板": "原创模板",
    "来源/日期": "来源与日期",
    "作者/主体": "作者与运营主体",
    "非医疗": "非医疗声明",
    "无虚假案例": "无虚假案例",
    "无批量同义词": "无批量同义词",
    "内链": "内链",
    "canonical": "canonical",
    "hreflang": "hreflang",
    "CTA": "CTA",
    "可回滚": "可回滚",
}


def _checklist_rows() -> list[list[str]]:
    text = GATE_DOC.read_text(encoding="utf-8")
    section = text.split("## 1. 十四项检查清单", 1)[1].split("\n---", 1)[0]
    rows = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("| G"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        assert len(cells) == 5, f"清单表必须五列（ID/检查项/机械判据/不合格怎么办/归属）：{cells}"
        rows.append(cells)
    return rows


ROWS = _checklist_rows()


def test_checklist_has_at_least_the_documented_twelve_items() -> None:
    assert len(ROWS) >= 12, f"台账 §KG-18 要求至少 12 项，清单里只有 {len(ROWS)} 项"
    ids = [r[0] for r in ROWS]
    assert len(set(ids)) == len(ids), f"检查项 ID 重复：{ids}"
    assert all(re.fullmatch(r"G\d{2}", i) for i in ids), f"ID 必须是 G01 这种两位编号：{ids}"
    assert ids == sorted(ids), "ID 必须连续有序，评审时才数得清漏了哪一项"


def test_the_twelve_named_topics_are_covered() -> None:
    names = "｜".join(r[1] for r in ROWS)
    missing = [topic for topic, needle in NAMED_TOPICS.items() if needle not in names]
    assert not missing, f"方案 §4.7 点名的主题在清单里找不到：{missing}"


def test_every_item_is_mechanically_checkable_or_named_a_person() -> None:
    bad = []
    for cid, name, rule, fallback, owner in ROWS:
        if rule in PLACEHOLDERS or len(rule) < 10:
            bad.append(f"{cid} {name}：机械判据空洞（{rule!r}）")
        if fallback in PLACEHOLDERS or len(fallback) < 6:
            bad.append(f"{cid} {name}：不合格时没人知道怎么办（{fallback!r}）")
        roles = {x.strip() for x in re.split(r"[+＋/、]", owner)}
        if not roles or not roles <= ROLES:
            bad.append(f"{cid} {name}：负责人 {owner!r} 不在角色表里")
    assert not bad, "清单项不合格：\n" + "\n".join(bad)


def test_pr_template_mirrors_the_checklist() -> None:
    """清单长成什么样，PR 模板就得长成什么样——两边对不上就是没人会守。"""
    assert PR_TEMPLATE.is_file(), "PR 模板不存在：.github/pull_request_template.md"
    tpl = PR_TEMPLATE.read_text(encoding="utf-8")
    got = dict(re.findall(r"^- \[ \] (G\d{2}) (.+)$", tpl, re.M))
    want = {r[0]: r[1] for r in ROWS}
    assert set(got) == set(want), (
        f"PR 模板与清单的 ID 集合不一致：模板多 {sorted(set(got) - set(want))}，"
        f"清单多 {sorted(set(want) - set(got))}"
    )
    drift = [(cid, want[cid], got[cid]) for cid in want if got[cid] != want[cid]]
    assert not drift, f"同名 ID 的检查项文字漂了（改一处没改另一处）：{drift}"


def test_gate_doc_points_at_the_upstream_it_depends_on() -> None:
    """清单不许自成一座岛：承诺口径要指回 KG-01，发布口径要指回 D0/生成脚本。"""
    text = GATE_DOC.read_text(encoding="utf-8")
    assert "docs/keyword-claims-audit-2026-11.md" in text, "G13 必须指回 KG-01 审计表"
    assert "build_seo_pages" in text, "G14 必须指回页面生成脚本，否则「可回滚」是空话"
    assert "等待名单" in text or "暂未开放" in text, "G09 要写清没有商店链接时不许做假按钮"
    for rel in (REPO / "docs" / "keyword-claims-audit-2026-11.md",):
        assert rel.is_file(), f"清单引用了不存在的文件：{rel}"


def _page_count() -> int:
    """**内容源**里的页面数（方案 §8.1 的唯一源 `site-content/pages.json`）。

    故意不数 `docs/content/drafts/*.md`：那里现有的 `journal-prompts.en.md`／`.zh.md`
    是 KG-26 反思提示库的导出物（由 `scripts/export_prompts.py` 从
    `backend/content/reflection_prompts.json` 生成），不是面向搜索的公开页，
    没有 canonical/hreflang/sitemap/CTA 可言。把它们当页面会立刻凑出「抽检 2 页」的假交付。
    """
    if not PAGES_JSON.is_file():
        return 0
    return len(re.findall(r'"slug"\s*:', PAGES_JSON.read_text(encoding="utf-8")))


def _sampling_rows() -> list[list[str]]:
    section = GATE_DOC.read_text(encoding="utf-8").split("## 3. 抽检记录", 1)[1].split("\n---", 1)[0]
    rows = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("| 页面") or set(line) <= set("|-: "):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == 5 and cells[0] not in ("（待 KG-10～KG-14 草稿落地后回填，至少 2 页）",):
            rows.append(cells)
    return rows


def test_sampling_covers_two_pages_once_pages_exist() -> None:
    """台账 §KG-18 的「抽检 2 页通过」：页面一旦存在，抽检表就要有真行。"""
    pages = _page_count()
    sampled = _sampling_rows()
    if pages == 0:
        assert not sampled, "还没有页面草稿却有抽检记录——那是编的"
        pytest.skip("内容源尚无页面草稿；KG-10～KG-14 落地后本条自动开始要求 ≥2 页抽检")
    need = min(2, pages)
    assert len(sampled) >= need, f"内容源有 {pages} 页，抽检表只有 {len(sampled)} 行（要 ≥{need}）"
    ids = {r[0] for r in ROWS}
    for page, _date, _who, covered, _issue in sampled:
        hit = set(re.findall(r"G\d{2}", covered))
        assert hit >= ids or "全部" in covered, f"{page} 的抽检没覆盖全部清单项，缺 {sorted(ids - hit)}"
