"""批 4 · D-G15／D-G16／D-G20 的口径闸：把「话说准」变成可执行判据。

本批正题是**口径失真**（工作单 §四），交付形状是第五种：改文字，并钉住那句文字。
这种形状最容易假绿，所以判据照 §六.1 定死两条——
① 那句失真措辞的**字面量在源里归零**；② 替进去的实测口径**带日期**。
「文件被改过」不算证据，所以每条反失真断言都配一条**正向证人**（同一把 grep 必须
在别处命中），否则「0 命中」可能只是我的扫描根写错了。

三组靶（逐字实测于 2026-10-17，出处指回源）：

- **D-G15** `问题.md:6` 自称「PRD 未实现功能已全部实现」，而同文件 §三 的遗留说明
  第 3 条自陈 `coexist_with`／TTL 冷存储未实现 ⇒ 一句话内部自相矛盾。
  今天实测：`coexist_with`、`cold_storage|memory_ttl|ttl_days` 在 `backend/`＋`go-core/`
  各 0 命中；`anniversary` 在 `services/push/`、`cron_service.py`、`message_generator.py`
  各 0 命中（唯一出现处是 `share_service.py` 的记忆卡文案键）；
  `exportLifeArchive` 在 `frontend/` 只剩定义行。
- **D-G16** `go-core/internal/domain/proactive.go:209` 的注释宣称有 `DayNo` 字段，
  而 `ProactiveCandidate`（:189-222）没有这个字段、`decodeProactive` 也不赋值。
  `DayNo` 是 curriculum/journey 那一族的字段（`domain/curriculum.go:37`），不是候选的。
- **D-G20** `backend/main.py:172` 的 `/health` docstring 自称由 Vercel Cron 按分钟保热，
  而 `vercel.json` 的 12 条排程全是 `/api/v1/cron/*`，一条 `/health` 都没有；
  `backend/routers/cron.py:4` 的模块头又把所有 cron 说成同一节奏、且清单漏了 `curriculum`。
  `settings.health_check_interval`（`config.py:177`）按 D-G20 的默认值**只登记不删**，
  留 tasks.md 的 T 桶——所以这里钉的是「它没有代码消费方」，不是「它该消失」。

两套判据**分文件、分函数**（§六.2）：本文件是口径闸，只看注释/文档面；
剥掉注释后判代码面的那条（`health_check_interval` 的消费方）单独走 `ast`，不混进字符串比对。
失真字面量在这里**拼出来写**（`"每" + " 5 分钟"`）：本文件一旦把原句整句抄进去，
自己的扫描就会命中自己，闸会变成永久红或靠排除自己来糊过去——两种都比没闸更糟。
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ISSUE_MD = ROOT / "问题.md"
PROACTIVE_GO = ROOT / "go-core" / "internal" / "domain" / "proactive.go"
CURRICULUM_GO = ROOT / "go-core" / "internal" / "domain" / "curriculum.go"
CRON_PY = ROOT / "backend" / "routers" / "cron.py"
MAIN_PY = ROOT / "backend" / "main.py"
VERCEL = ROOT / "vercel.json"
OPENAPI = ROOT / "contracts" / "openapi.json"

REVIEW_DATE = "2026-10-17"

# 三句被收掉的失真措辞——拼出来写，理由见模块 docstring 末段。
LIE_PRD_ALL_DONE = "PRD 未实现功能已全部" + "实现"
LIE_SECTION_ALL_DONE = "（✅ 全部" + "已实现"
LIE_FIVE_MINUTE = "每" + " 5 分钟"
LIE_ALL_HOURLY = "通过 Vercel Cron 每小时" + " GET 触发"

SKIP_PARTS = {"gen", "__pycache__", "node_modules", "testdata", ".git", "dist", "build"}


def _in_tree(base: Path, needle: str, *, impl_only: bool = False) -> list[str]:
    """返回包含 `needle` 的仓库相对路径（跳过生成物/依赖目录；`base` 可以是单个文件）。

    `impl_only` 把范围收到**实现面**：`*.py` 不算 `backend/tests/**`、`*.go` 不算 `*_test.go`。
    登记的「0 命中」说的是没人实现它，而本文件自己就要写出这些标识符才能把它们数一遍——
    不限范围，闸会因为自己的存在而永久红（那种红只会教人删闸）。
    """
    files = [base] if base.is_file() else sorted(base.rglob("*"))
    hits: list[str] = []
    for path in files:
        if path.is_dir() or SKIP_PARTS.intersection(path.parts) or not path.is_file():
            continue
        if impl_only:
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith("backend/tests/") or rel.endswith("_test.go"):
                continue
        if path.suffix not in {".py", ".go", ".ts", ".tsx", ".json", ".md", ".yaml", ".html"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if needle in text:
            hits.append(path.relative_to(ROOT).as_posix())
    return hits


# ── D-G15：`问题.md` 的「全部已实现」 ──────────────────────────────────

def test_prd_all_done_claim_is_gone_and_replaced_by_dated_review():
    text = ISSUE_MD.read_text(encoding="utf-8")
    for lie in (LIE_PRD_ALL_DONE, LIE_SECTION_ALL_DONE):
        assert lie not in text, f"失真原句又回到 问题.md：{lie!r}（D-G15 的靶）"
    head = "\n".join(text.splitlines()[:8])
    assert REVIEW_DATE in head, (
        f"问题.md 前 8 行（审查结论横幅）没有 {REVIEW_DATE} 的复核日期 ⇒ "
        "改措辞必须带日期，否则下一个人无从判断这句是哪一天的实测"
    )


def test_review_block_names_the_four_zero_items():
    text = ISSUE_MD.read_text(encoding="utf-8")
    start = text.index("## 三、")
    block = text[start:text.index("### 遗留说明", start)]
    assert REVIEW_DATE in block.splitlines()[0], "§三 的标题没带实测日期"
    for name in ("coexist_with", "ttl_days", "anniversary", "exportLifeArchive"):
        assert name in block, f"§三 的复核块没点名 {name} ⇒ 「四项仍零实现」这句没有可核对的对象"


def test_the_four_reviewed_zero_claims_still_match_the_code():
    """文档说「0 实现」，代码必须真的是 0——这条防的是文档跑在代码前面（或后面）。

    命中了就要回来撤掉 `问题.md` 的对应条目：闸不许让「已实现」和「登记为未实现」并存。
    """
    for needle, bases in (
        ("coexist_with", (ROOT / "backend", ROOT / "go-core")),
        ("cold_storage", (ROOT / "backend", ROOT / "go-core")),
        ("memory_ttl", (ROOT / "backend", ROOT / "go-core")),
        ("ttl_days", (ROOT / "backend", ROOT / "go-core")),
    ):
        hits = [h for b in bases for h in _in_tree(b, needle, impl_only=True)]
        assert not hits, f"{needle} 现在有实现痕迹：{hits}——代码已变，D-G15 的登记要改写"

    push_places = (
        ROOT / "backend" / "services" / "push",
        ROOT / "backend" / "services" / "cron_service.py",
        ROOT / "backend" / "services" / "message_generator.py",
    )
    for place in push_places:
        where = place.relative_to(ROOT).as_posix()
        assert not _in_tree(place, "anniversary", impl_only=True) and not _in_tree(
            place, "Anniversary", impl_only=True
        ), f"{where} 里出现了 anniversary ⇒ 周年推送已落地，D-G15 第 3 项要撤掉"
    # 正向证人：同一把 grep 必须在他处命中，否则「0 命中」只是扫描根写错了。
    assert _in_tree(ROOT / "backend" / "services", "anniversary", impl_only=True), (
        "整个 backend/services 都搜不到 anniversary：多半是我改错了 grep 而不是它没实现"
    )

    frontend_hits = _in_tree(ROOT / "frontend" / "src", "exportLifeArchive", impl_only=True)
    assert frontend_hits == ["frontend/src/services/api.ts"], (
        f"exportLifeArchive 的消费者集合变了：{frontend_hits}——"
        "已接线就回去改 D-G15 第 4 项，未接线也不许把它挪出 api.ts 后不再登记"
    )
    assert len(_in_tree(ROOT / "frontend" / "src", "getLifeArchivePdf")) >= 2, (
        "getLifeArchivePdf 只剩定义行 ⇒ 批 1 G2 接的 PDF 领取口又被摘掉了，"
        "D-G15 第 4 项的「已兑现一半」这句要跟着改"
    )


# ── D-G16：`proactive.go` 的 DayNo 残留注释 ────────────────────────────

def test_proactive_candidate_no_longer_advertises_a_dayno_field():
    src = PROACTIVE_GO.read_text(encoding="utf-8")
    assert "DayNo" not in src, (
        "proactive.go 又出现了 DayNo——这个字段在 ProactiveCandidate 里不存在，"
        "注释宣称它存在就是让下一个人去找一个不存在的列（D-G16）"
    )
    block = re.search(r"type ProactiveCandidate struct \{(.*?)\n\}", src, re.S)
    assert block, "找不到 ProactiveCandidate 的字段块：结构体改名了，本闸要跟着改"
    assert "DayNo" not in block.group(1), "字段块里真有了 DayNo ⇒ D-G16 的判据反过来，注释该留"
    # 正向证人：`DayNo` 在本仓是**别的类型**的真实字段，不是拼错的空 grep。
    assert "DayNo" in CURRICULUM_GO.read_text(encoding="utf-8"), (
        "curriculum.go 里没有 DayNo ⇒ 上面的「0 命中」不再说明任何问题"
    )


# ── D-G20：健康检查的节律宣称 ──────────────────────────────────────────

def _vercel_crons() -> list[dict]:
    entries = json.loads(VERCEL.read_text(encoding="utf-8")).get("crons")
    assert isinstance(entries, list) and entries, "vercel.json 的 crons 数组空了，本闸失去靶"
    return entries


def test_health_endpoint_stops_claiming_a_minutely_cron():
    for path in (MAIN_PY, CRON_PY):
        assert LIE_FIVE_MINUTE not in path.read_text(encoding="utf-8"), (
            f"{path.relative_to(ROOT).as_posix()} 又写了分钟级保热——"
            "vercel.json 里没有 /health 这条排程（下面那条闸逐条数过）"
        )
    rel = OPENAPI.read_text(encoding="utf-8")
    assert LIE_FIVE_MINUTE not in rel, (
        "生成的契约里还留着那句谎 ⇒ 改完 docstring 要跑 scripts/export_contract.py"
        "（openapi.json 不可手改，这条按整份文件扫，免得把断言绑在某个 JSON 路径上）"
    )


def test_vercel_schedule_list_has_no_health_and_no_minutely_entry():
    """把「那句措辞为什么不成立」写成可重算的判据，而不是让人回去读 vercel.json。"""
    entries = _vercel_crons()
    paths = [e.get("path", "") for e in entries]
    assert not [p for p in paths if p.rstrip("/").endswith("/health")], (
        f"vercel.json 现在真的排程了 /health：{paths} ⇒ 那句措辞不再是谎，"
        "本闸与 main.py 的 docstring 一起改写"
    )
    assert not [e for e in entries if e.get("schedule", "").split()[0].startswith("*/")], (
        "出现分钟步长排程 ⇒ D-G20 的前提（Vercel Cron 无分钟级条目）变了"
    )


def test_cron_module_header_states_the_measured_cadence_and_lists_every_route():
    """模块头注释两处失真：把 12 条排程说成一个节奏、清单漏一条端点。"""
    from backend.main import app

    header = ast.get_docstring(ast.parse(CRON_PY.read_text(encoding="utf-8"))) or ""
    assert LIE_ALL_HOURLY not in header, "cron.py 头注释又把全部 cron 说成同一个节奏"
    assert REVIEW_DATE in header, "头注释的实测口径没带日期"

    entries = _vercel_crons()
    hourly = [e for e in entries if e["schedule"] == "0 * * * *"]
    assert len(entries) == 12 and len(hourly) == 7, (
        f"vercel.json 的排程形状已变（共 {len(entries)} 条，整点每小时 {len(hourly)} 条），"
        "头注释里那两个数要跟着改"
    )

    # 清单以**冻结契约**为源（98 条里 `/cron/*` 的 16 条），不靠手数装饰器。
    registered = sorted(p for p in app.openapi()["paths"] if "/cron/" in p)
    assert registered, "契约里一条 /cron/* 路径都没有，本闸空转"
    names = [p.split("/cron/", 1)[1] for p in registered]
    missing = [n for n in names if f"/cron/{n}" not in header]
    assert not missing, f"cron.py 头注释的端点清单漏了：{missing}（curriculum 就是这么漏掉的）"


def test_health_check_interval_has_no_consumer_and_stays_registered():
    """代码面判据（剥掉注释后）：这个键只声明、没人读。

    D-G20 的默认值是「只登记不顺手删」⇒ 这里不许它悄悄变成旋钮，也不许被顺手删掉后
    让 ConfigMap/口径文档指着一个不存在的字段。
    """
    from backend.config import settings

    readers: list[str] = []
    for path in sorted((ROOT / "backend").rglob("*.py")):
        if SKIP_PARTS.intersection(path.parts) or path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "health_check_interval"
                and isinstance(node.value, ast.Name)
                and node.value.id == "settings"
            ):
                readers.append(path.relative_to(ROOT).as_posix())
    assert not readers, f"settings.health_check_interval 被这些地方读走了：{readers}"
    assert settings.health_check_interval == 5, (
        "这个键按 T 桶保留原值（默认 5 分钟），删字段或改默认都要先结案 T 桶那条登记"
    )
