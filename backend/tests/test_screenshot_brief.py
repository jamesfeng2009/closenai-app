"""KG-08 的六帧截图闸：每帧必须指到**真存在的界面与真代码行**，首帧不许复活绝对化承诺。

**为什么截图简报也要一张闸**：截图是商店页上唯一「不点进 App 就看到的承诺」，
而本项目刚刚在 KG-01 审计里发现 `backend/data/copy/*.json` 的水印（＝截图取景位置）
落后于 App 内文案一整批。所以本文件把六帧的「界面源」写成 `路径:行`，
让闸逐行打开文件核对 needle——**指不到那行代码，就不算一帧**，比人眼核对便宜。

**判据复用**：禁语口径不重抄一遍，直接 `import` KG-04 校验器里的 `BANNED_PATTERNS`
（`scripts/check_aso_copy.py`）。商店文案与截图文案共用同一把尺子，才不会一处严一处松。
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "aso" / "screenshot-brief-2026-11.md"
AUDIT = ROOT / "docs" / "keyword-claims-audit-2026-11.md"
SCRIPT = ROOT / "scripts" / "check_aso_copy.py"

_spec = importlib.util.spec_from_file_location("check_aso_copy", SCRIPT)
assert _spec and _spec.loader
aso = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aso)

TEXT = DOC.read_text(encoding="utf-8")

COLUMNS = ["帧", "主张（图上那句话）", "对应真实界面", "界面源（仓内路径:行）",
           "取图条件（拍之前必须成立）", "证据与合规标注"]
#: 主张的可读性上限：超过就要在 6.7 英寸屏上换行，换行会吃掉画面。
CLAIM_MAX = 40

ROWS: list[list[str]] = []
for line in TEXT.splitlines():
    if not re.match(r"^\| [1-6] \|", line):
        continue
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if len(cells) == len(COLUMNS):
        ROWS.append(cells)

#: 六帧的界面源 needle：这一行必须真的画着那块界面，否则「指到真界面」是空的。
SCREEN_ANCHORS: dict[str, str] = {
    "frontend/src/screens/MemoriesScreen.tsx:163": "quote_text",
    "frontend/src/screens/HomeScreen.tsx:85": "buildHeatCells",
    "frontend/src/screens/HomeScreen.tsx:32": "moodCheckinRepo",
    "frontend/src/screens/InsightsScreen.tsx:210": "first_evidence_insight",
    "frontend/src/screens/GrowthRecordsScreen.tsx:267": "first_outcome",
    "frontend/src/screens/GrowthRecordsScreen.tsx:69": "结果如何",
    "frontend/src/screens/RelationshipTimelineScreen.tsx:79": "timelineRes",
    "frontend/src/screens/PrivacySecurityScreen.tsx:81": "latestExport",
    "frontend/src/screens/PrivacySecurityScreen.tsx:261": "deletionRow",
    "backend/tests/test_absolute_memory_claims.py:139": "step1Tagline",
}


def test_six_frames_and_full_columns() -> None:
    """台账 §KG-08 点名 6 帧；少一帧或某格空着都不算交付。"""
    assert len(ROWS) == 6, f"只解析到 {len(ROWS)} 帧，验收原文要 6 帧"
    header = f"| {' | '.join(COLUMNS)} |"
    assert header in TEXT, "§1 表头与列契约不一致"
    for cells in ROWS:
        for col, value in zip(COLUMNS, cells):
            if value in {"", "—", "-", "待补", "待定", "TBD", "todo"}:
                pytest.fail(f"第 {cells[0]} 帧的「{col}」是空的或占位")


def test_claims_fit_the_readable_length() -> None:
    for cells in ROWS:
        claim = cells[1].strip("`")
        assert len(claim) <= CLAIM_MAX, f"第 {cells[0]} 帧主张 {len(claim)} 字符，超 {CLAIM_MAX} 要换行：{claim!r}"
        assert claim.strip(), "主张不能是空串"


def test_first_frame_is_not_an_absolute_memory_claim() -> None:
    """首帧验收原文：不用 `remembers everything`——并且用 KG-04 的同一条禁语尺子扫六帧。"""
    for cells in ROWS:
        claim = cells[1].strip("`")
        for term, pat in aso.BANNED_PATTERNS:
            assert not pat.search(claim), f"第 {cells[0]} 帧主张命中禁语 {term!r}：{claim!r}"
    first = ROWS[0][1].strip("`")
    assert "choose" in first or "选择" in first, f"首帧必须是限定式（你选择分享的）：{first!r}"


def test_one_frame_is_the_outcome_follow_up() -> None:
    """台账点名「至少一帧讲结果回访」——这是本品的差异化，缺了等于六帧都在讲通用日记。"""
    # 只看**主张与界面**两列：证据列里出现「结果回访」不算这帧在讲结果回访。
    blob = " ".join(c[1] + " " + c[2] for c in ROWS)
    assert re.search(r"回访|How did it go", blob), "没有任何一帧落在结果回访上"
    frame = next((c for c in ROWS if re.search(r"回访|How did it go", c[1] + " " + c[2])), None)
    assert frame is not None and "GrowthRecordsScreen.tsx" in frame[3], (
        "结果回访那帧没指到真代码（要指 GrowthRecordsScreen 的行动—结果界面）"
    )


@pytest.mark.parametrize("cite,needle", sorted(SCREEN_ANCHORS.items()))
def test_screen_anchor_points_at_the_claimed_code(cite: str, needle: str) -> None:
    """引用即自证：`路径:行` 必须存在、行号不越界，且那一行真含所声称的代码/文案。"""
    rel, line_s = cite.rsplit(":", 1)
    path = ROOT / rel
    assert path.is_file(), f"{cite} 的文件不存在"
    lines = path.read_text(encoding="utf-8").splitlines()
    n = int(line_s)
    assert 1 <= n <= len(lines), f"{cite} 行号越界（{len(lines)} 行）"
    assert needle in lines[n - 1], f"{cite} 那一行不含 {needle!r}，实际是 {lines[n-1]!r}"
    assert cite in TEXT, f"{cite} 写在闸里但文档没引用它（两边漂了）"


def test_every_screen_source_in_the_table_exists() -> None:
    """六帧的「界面源」列逐个解析：路径必须存在，行号必须在范围内。"""
    for cells in ROWS:
        cites = re.findall(r"`([^`]+?):(\d+)`", cells[3])
        assert cites, f"第 {cells[0]} 帧没有指到 路径:行"
        for rel, n in cites:
            path = ROOT / rel
            assert path.is_file(), f"第 {cells[0]} 帧指了不存在的文件 {rel}"
            lines = path.read_text(encoding="utf-8").splitlines()
            assert 1 <= int(n) <= len(lines), f"{rel}:{n} 越界（{len(lines)} 行）"


def test_repo_paths_elsewhere_in_the_doc_exist() -> None:
    """文档其余位置引用的仓内路径（如导出服务）也必须真存在——防「能力是想象出来的」。"""
    for rel in re.findall(r"`((?:backend|frontend|docs|scripts|web|site)/[^`:\s]+)`", TEXT):
        assert (ROOT / rel).exists() or (ROOT / rel).is_dir(), f"引用了不存在的路径：{rel}"


def test_doc_declares_no_screenshots_taken_yet() -> None:
    """状态诚实：本机取不到真机截图，文档必须明写「未取」，闸还要盯着这句话。"""
    assert "截图未取" in TEXT, "简报必须声明截图未取（否则会被当成已交付素材）"
    assert "未取任何真机截图" in TEXT, "§5 的未做清单必须有真机截图这一条"
    assert "不用占位截图上线" in TEXT, "要引用台账 §KG-08 的「不用占位截图上线」这条验收"


def test_preview_video_deferral_is_writable() -> None:
    """§4 不做预览视频：必须给原因＋可检查的重开条件，不能只写「以后再说」。"""
    section = TEXT.split("## 4. 预览视频", 1)[1].split("\n## ", 1)[0]
    assert "重开条件" in section, "不做预览视频要写重开条件"
    assert len(section.strip()) > 120, "§4 太薄，说不清为什么本轮不做"


def test_audit_row_count_cited_in_the_brief_is_live() -> None:
    """简报开头写「KG-01，N 条承诺审计」——N 由审计表现算，改了表就得同步。"""
    rows = [l for l in AUDIT.read_text(encoding="utf-8").splitlines() if l.startswith("| `")]
    assert f"（KG-01，{len(rows)} 条承诺审计）" in TEXT, (
        f"简报里的审计条数与表里的 {len(rows)} 条不符（闸两侧对拉，防过期数字）"
    )


def test_first_frame_claim_differs_from_the_disputed_subtitle() -> None:
    """首帧刻意不用副标题 A 那句：A 与 KG-01 判定冲突、已挂 `待法务`。

    两侧不同源：另一侧从 KG-04 的 JSON 包里读，不是把文案抄进本文件。
    """
    pack = json.loads((ROOT / "docs" / "aso" / "en-US-2026-11.json").read_text(encoding="utf-8"))
    subtitle_a = pack["fields"]["subtitle_variants"][0]["text"]
    first = ROWS[0][1].strip("`")
    assert first != subtitle_a, "首帧直接复制了待法务的副标题 A"
    assert "docs/keyword-claims-audit-2026-11.md:96" in TEXT, "必须指回那条 `待法务` 登记行"
