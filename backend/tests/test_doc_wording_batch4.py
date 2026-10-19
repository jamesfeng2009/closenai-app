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
  ↳ 2026-10-18 追记：批 T1-03 把这条零调用封装删掉了（连同 `LifeArchiveResult`），
  端点转为「服务端能力＋登记理由」，所以下面那条对账的期望从「只剩 api.ts 一处定义」
  改成「`frontend/src` 零命中」——登记不许因为代码没了就一起消失。
- **D-G16** `go-core/internal/domain/proactive.go:209` 的注释宣称有 `DayNo` 字段，
  而 `ProactiveCandidate`（:189-222）没有这个字段、`decodeProactive` 也不赋值。
  `DayNo` 是 curriculum/journey 那一族的字段（`domain/curriculum.go:37`），不是候选的。
- **D-G20** `backend/main.py:172` 的 `/health` docstring 宣称有 Vercel Cron 按分钟保热，
  而当时 `vercel.json` 的 12 条排程全是 `/api/v1/cron/*`，一条 `/health` 都没有；
  `backend/routers/cron.py:4` 的模块头又把所有 cron 说成同一节奏、且清单漏了 `curriculum`。
  ↳ **2026-10-18 T0-02 改判**：调度源已迁到 `deploy/schedule/crontab`（迁移当时 13 条，
  T1-01 起 14 条、T1-04 起 15 条（每日交付审计 `0 4 * * *`），`/cron/health` 与 `/cron/export-jobs` 两条都是 `*/5`），`vercel.json` 同日退役——
  「每 5 分钟」从谎话变成实测，本文件 D-G20 那组闸随之从「把措辞归零」翻成「**宣称必须由排程付账**」（宣称侧与排程侧
  共存亡）。`settings.health_check_interval`（`config.py:177`）按 D-G20 的默认值
  **只登记不删**，留 tasks.md 的 T 桶——这里钉的仍是「它没有代码消费方」，不是「它该消失」。

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
VERCEL = ROOT / "vercel.json"  # T0-02 起这个路径必须不存在（单源判据在下面）
CRONTAB = ROOT / "deploy" / "schedule" / "crontab"
OPENAPI = ROOT / "contracts" / "openapi.json"

REVIEW_DATE = "2026-10-17"
MIGRATION_DATE = "2026-10-18"

# 三句被收掉的失真措辞——拼出来写，理由见模块 docstring 末段。
LIE_PRD_ALL_DONE = "PRD 未实现功能已全部" + "实现"
LIE_SECTION_ALL_DONE = "（✅ 全部" + "已实现"
LIE_FIVE_MINUTE = "每" + " 5 分钟"  # T0-02 起这句成立——闸翻成正向证人（见下）
VERCEL_CLAIM = "Vercel" + " Cron"  # 调度源已退役，不许在 backend 运行时面当现役口径

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

    # T1-03（2026-10-18）之后这里必须是**零命中**：封装删掉了，登记搬到契约的「前端调用」列。
    # 反向闸配正向证人——同一把 grep 必须在契约与脚本两侧命中，否则「0 命中」只是扫错了根。
    frontend_hits = _in_tree(ROOT / "frontend" / "src", "exportLifeArchive", impl_only=True)
    assert frontend_hits == [], (
        f"exportLifeArchive 又回到前端实现面：{frontend_hits}——"
        "已接线就回去改 D-G15 第 4 项与契约登记；只是注释里带上这个名字也不行，"
        "这条闸扫的是整个 frontend/src，注释会让「零命中」变成永远数不清的软判据"
    )
    assert _in_tree(ROOT / "contracts", "exportLifeArchive"), (
        "契约台账里没有这个名字 ⇒ T1-03 的「服务端能力＋登记理由」被抹掉了，"
        "这条端点会退回成看起来像漏扫的裸 `—`"
    )
    assert _in_tree(ROOT / "scripts", "exportLifeArchive"), (
        "scripts/ 里的登记表 SERVER_ONLY_REASONS 没有这条 ⇒ 台账与登记脚本脱钩"
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

def _crontab_entries() -> list[tuple[str, str]]:
    """crontab 行的迷你解析：(path, 5 字段表达式)。

    与 `test_cron_schedule_coverage.py` 同形但**独立实现**——§六.2 要求口径闸与覆盖闸
    分文件分函数，两处不许共用一把解析尺，否则一次解析写坏会让两条闸一起变绿。
    """
    out: list[tuple[str, str]] = []
    for raw in CRONTAB.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", line):
            continue
        parts = line.split(None, 5)
        assert len(parts) >= 6, f"crontab 行不是「5 字段＋命令」：{raw!r}"
        m = re.search(r"http://127\.0\.0\.1:8000(/[^\s]+)", line)
        assert m, f"crontab 命令行里敲不出本机 API URL：{raw!r}"
        out.append((m.group(1), " ".join(parts[:5])))
    assert out, "deploy/schedule/crontab 一条排程都没有，本闸失去靶"
    return out


def test_vercel_manifest_is_retired_and_pyproject_follows():
    """T0-02 单一调度源：`vercel.json` 回来就是「双源⇒双触发」形状。

    存在性判据的正主是覆盖闸（`test_vercel_manifest_stays_retired`）；这里钉的是**配套面**：
    `pyproject.toml` 的 `[tool.vercel]` 段必须一并清掉，免得下一个人按它找第二个运行入口。
    """
    assert not VERCEL.exists(), (
        "vercel.json 又回到仓库根 ⇒ 双调度源形状回来了；要么删掉它，要么两条闸的口径一起改判"
    )
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.vercel]" not in pyproject, "pyproject.toml 里还挂着 [tool.vercel] 段"


# 现役配置面（2026-10-18 用户拍板「不走 Vercel」后逐条清过）——这些文件里再出现
# vercel 就是配置回流，不是历史记述。历史面（crontab/README/工作单）的「vercel.json 已退役」
# 勘误记述是取证证据，不归本闸管，所以下面用正向证人而不是全仓归零。
ACTIVE_VERCEL_FREE_FILES = [
    ROOT / "pyproject.toml",
    ROOT / "backend" / ".env.example",
    ROOT / ".github" / "CODEOWNERS",
    ROOT / "deploy" / "Dockerfile.py-api",
    ROOT / "go-core" / "Dockerfile",
    ROOT / "Makefile",
]


def test_active_config_surface_has_no_vercel_platform_left():
    """Vercel 下马口径的配置面判据：现役装包/部署/归属配置里不许再出现 vercel。

    反向归零闸配正向证人：同一把小写 grep 必须还能在历史文档里命中
    （`AI进程面工作单-2026-10-16.md` 记着退役前的事实），否则「0 命中」分不清
    是配置真清干净了还是我把扫描清单写空了。
    """
    for path in ACTIVE_VERCEL_FREE_FILES:
        text = path.read_text(encoding="utf-8").lower()
        assert "vercel" not in text, (
            f"{path.relative_to(ROOT)} 又出现 vercel——2026-10-18 拍板不走 Vercel，"
            "现役配置面要清出去（历史勘误记述请写在文档面，不写进这些文件）"
        )
    witness = (ROOT / "AI进程面工作单-2026-10-16.md").read_text(encoding="utf-8").lower()
    assert "vercel" in witness, "正向证人失联：历史工作单里都搜不到 vercel ⇒ 扫描口径可疑"


def test_vercel_cron_wording_is_gone_from_backend_runtime_sources():
    """调度口径：backend 运行时面（含注释、剥掉测试面）不许再把 Vercel 时代当现役口径。

    归零闸配正向证人——同一把 grep 必须在历史文档里命中，否则「0 命中」分不清
    是「口径真退役了」还是「我扫描根写错了」。
    """
    hits = _in_tree(ROOT / "backend", VERCEL_CLAIM, impl_only=True)
    assert not hits, f"调度口径又写回 Vercel 时代：{hits}"
    for path in (CRON_PY, MAIN_PY):
        assert VERCEL_CLAIM not in path.read_text(encoding="utf-8")
    witness = [
        h
        for h in _in_tree(ROOT, VERCEL_CLAIM)
        if h.startswith(("closenai-prd/", "tasks.md", ".trellis/")) or h.endswith("工作单-2026-10-17.md")
    ]
    assert witness, f"历史文档里也搜不到 {VERCEL_CLAIM} ⇒ 多半是我改错了 grep，不是口径退役干净"


def test_minutely_health_claim_is_now_backed_by_crontab():
    """D-G20 靶在 T0-02 后的翻案：那句「每 5 分钟」从谎话变成承诺——宣称与排程共存亡。

    排程在、宣称没了 ⇒ 没人替这个承诺说话，闸失去对象（红）；
    宣称在、排程被摘 ⇒ 话又悬空（红）。两个方向都得有证人。
    """
    health = [s for p, s in _crontab_entries() if p.endswith("/cron/health")]
    assert len(health) == 1, f"/cron/health 的保热排程应恰好一条，实际 {health}"
    assert health[0].split()[0] == "*/5", (
        f"crontab 的保热实际是 {health[0]!r}，而 docstring 宣称「每 5 分钟」⇒ 那句又悬空了"
    )
    claims = [
        path.name
        for path in (MAIN_PY, CRON_PY, OPENAPI)
        if LIE_FIVE_MINUTE in path.read_text(encoding="utf-8")
    ]
    assert claims, (
        f"「{LIE_FIVE_MINUTE}」在 main.py/cron.py/契约里都消失了——crontab 还在排 */5，"
        "却没人把这条口径说成话 ⇒ 本闸失去对象，随 docstring 一起改写"
    )


def test_cron_module_header_states_the_measured_cadence_and_lists_every_route():
    """模块头注释两处失真：把所有排程说成一个节奏、清单漏一条端点。

    T0-02 把被数的源换成了 crontab，两个计数随之 12→13、整点每小时仍是 7——钉死它们
    的是本闸，不是我的记性。
    T1-01（2026-10-18）加了 `*/5` 的导出队列认领 ⇒ 总数 13→14、`*/5` 由 1 条变 2 条，
    整点每小时仍是 7；头注释里这三个数与本闸同源。
    T1-04（2026-10-18）加了每日的付费交付审计 ⇒ 总数 14→15、按日由 3 条变 4 条，
    整点每小时与 `*/5` 都没动；「按日」这一档以前只写在头注释里没人头数过，
    所以这一批把它也钉住——否则新增一条每日排程只有总数会变，红的时候说不出是
    「哪一档多了一条」。
    """
    from backend.main import app

    header = ast.get_docstring(ast.parse(CRON_PY.read_text(encoding="utf-8"))) or ""
    assert MIGRATION_DATE in header, "头注释的实测口径没带 2026-10-18 迁移日期"

    entries = _crontab_entries()
    hourly = [s for _p, s in entries if s == "0 * * * *"]
    daily = [s for s in (x[1] for x in entries) if re.fullmatch(r"\d+ \d+ \* \* \*", s)]
    five_minute = [s for _p, s in entries if s == "*/5 * * * *"]
    assert len(entries) == 15 and len(hourly) == 7, (
        f"crontab 的排程形状已变（共 {len(entries)} 条，整点每小时 {len(hourly)} 条），"
        "头注释里那两个数要跟着改"
    )
    assert len(daily) == 4, f"按日排程应是 4 条（daily-insight/trial-reminder/cleanup-tokens/delivery-audit），实得 {len(daily)} 条"
    assert len(five_minute) == 2, f"`*/5` 应是保热＋导出队列两条，实得 {len(five_minute)} 条"

    # 清单以**冻结契约**为源（105 条里 `/cron/*` 的 17 条），不靠手数装饰器。
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
