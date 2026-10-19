#!/usr/bin/env python3
"""T0-05 功能状态台账：从四个证据面推导，`docs/feature-status.json` 是唯一事实源。

Baseline §15 要求六态（SPECIFIED / IMPLEMENTED / WIRED / E2E_VERIFIED /
PRODUCTION_ENABLED / VALUE_MEASURED），§16 给了十行验收矩阵。散文式工作单已经出过
「勾了但没生效」的死账，所以这里把「态」变成**可重跑的对账**：每一条声明都要指回
仓库里真实存在的东西，指不到就是错，不靠人记得住。

四个证据面（本脚本自己读，不接受人肉转述）：
- 端点存在性 → `contracts/endpoints.md`（T0-04 重导的那份台账，93 paths）；
- 前端接线 → `frontend/src/services/api.ts` 的导出封装 ＋ `frontend/src` 里除 api.ts
  之外的引用点（「写得进读不出」「封装未接线」就是这一层的历史病）；
- 调度 → `deploy/schedule/crontab`（T0-02 起的唯一调度声明源）；
- 灰度键 → `go-core/internal/config/config.go` 的 `CLOSEN_CUTOVER_*` 与其**默认值**。

判据（写死在 `verify()`，三条故意写错的 state 各红一次）：
1. `api` / `cron` / `tests` / `e2e_tests` / `frontend_calls` / `assets` 声明的东西必须存在——
   拼错一个端点、指一个不存在的测试文件、写一个已改名的封装、点名一个不存在的发布资产，
   都是台账说谎；
2. `WIRED` 要求每条 `frontend_calls` 在 api.ts 之外真的有引用点，且 `frontend_entry`
   里点名的文件存在（§15 末句：有 API 但前端零调用只能算 IMPLEMENTED）；`delivery="static"`
   的功能（§16 行 10「静态页面/链接 → 发布资产」）没有端点可指，改由 `assets` 撑起同一层；
3. `E2E_VERIFIED` 要求 `e2e_tests` 非空、文件里有测试声明、`verified_on` 是合法日期
   且不晚于今天、`verified_by` 带非零「N passed」实测计数；
4. `PRODUCTION_ENABLED` 要求的 cron 必须逐条出现在 `deploy/schedule/crontab`，且切流键
   默认值为 0（灰度 0%）时不许宣称；还要求 `production_evidence` 带日期；
5. `VALUE_MEASURED` 要求 `value_metric` ＋带数字与日期的 `metric_evidence`；
6. 矩阵十行每行至少被一个 feature 覆盖，id 不得重复，state 必须在六态之内；
7. `internal_capabilities`（T6-01 的「内部能力」登记面）状态封顶 `IMPLEMENTED`、impl/tests
   指向真实文件、reason 指回条款、`review_by` 是**未过期**的处置日期且 `review_action` 写明删什么。

用法：
    .venv/bin/python scripts/feature_status.py            # 重新生成 docs/feature-status.md
    .venv/bin/python scripts/feature_status.py --check     # CI 用：判据 ＋ md 是否过期
退出码 0＝台账与证据一致；1＝有判据违例或 md 过期（PR 必须连同 md 一起提）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "docs" / "feature-status.json"
MARKDOWN = ROOT / "docs" / "feature-status.md"
ENDPOINTS = ROOT / "contracts" / "endpoints.md"
CRONTAB = ROOT / "deploy" / "schedule" / "crontab"
GO_CONFIG = ROOT / "go-core" / "internal" / "config" / "config.go"
API_TS = ROOT / "frontend" / "src" / "services" / "api.ts"

STATES = [
    "SPECIFIED",
    "IMPLEMENTED",
    "WIRED",
    "E2E_VERIFIED",
    "PRODUCTION_ENABLED",
    "VALUE_MEASURED",
]
RANK = {name: i for i, name in enumerate(STATES)}

# verified_by 必须带**非零**实测计数（口径同任务板结案行：结论要指回一次真跑）；
# 日期由 verified_on 单独承担（它另有「合法 YYYY-MM-DD 且不晚于今天」的判据），
# production_evidence / metric_evidence 则必须自带日期——那两类是线上状态声明，出处要能对上发布记录。
PASSED_RE = re.compile(r"(?<![\d.])[1-9]\d*\s+passed")
DATED_RE = re.compile(r"20\d{2}-\d{2}-\d{2}")
ENTRY_FILE_RE = re.compile(r"(frontend/[\w./-]+\.(?:tsx|ts))")
# 内部能力的理由必须指回条款（§x.y），口径同任务板「结论必须标出处」；
# 处置动作必须写明「删除」——T6-01 选项 A 卖的就是这条 90 天到期删除的承诺，
# 只登记不写处置＝另一份会过期的死账。
CLAUSE_RE = re.compile(r"§\s*\d")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def contracted_endpoints() -> set[str]:
    """从契约台账行取 `METHOD /path` 全集（形状同 export_contract.py 的输出）。"""
    out: set[str] = set()
    for line in read(ENDPOINTS).splitlines():
        m = re.match(r"^\|\s*\d+\s*\|\s*(GET|POST|PATCH|PUT|DELETE)\s*\|\s*`([^`]+)`", line)
        if m:
            out.add(f"{m.group(1)} {m.group(2)}")
    return out


def contracted_paths() -> set[str]:
    """契约台账里出现过的**裸路径**集合（给 `cron` 用：排程 URL 不带方法，方法由 crontab 决定）。"""
    return {ep.split(" ", 1)[1] for ep in contracted_endpoints()}


def scheduled_crons() -> set[str]:
    """crontab 里真实排程的 cron 路径（不含 QStash 回调那三条 POST）。"""
    out: set[str] = set()
    if not CRONTAB.exists():
        return out
    for line in read(CRONTAB).splitlines():
        if line.strip().startswith("#") or "curl" not in line:
            continue
        for m in re.finditer(r"http://[^\s]*/(api/v1/[^\s]+?)(?:\?|\s|>)", line):
            out.add("/" + m.group(1))
    return out


def cutover_defaults() -> dict[str, int | None]:
    """`CLOSEN_CUTOVER_*` → 默认值；读不到默认值记 None（键存在但形状不认识）。"""
    src = read(GO_CONFIG)
    out: dict[str, int | None] = {}
    for m in re.finditer(r'envInt\("(CLOSEN_[A-Z_]+)",\s*(-?\d+)\)', src):
        out[m.group(1)] = int(m.group(2))
    for m in re.finditer(r'envString\("(CLOSEN_[A-Z_]+)"', src):
        out.setdefault(m.group(1), None)
    return out


def api_wrappers() -> set[str]:
    """api.ts 导出的封装名（`export async function X` / `export function X`）。"""
    return set(re.findall(r"^export (?:async )?function (\w+)", read(API_TS), re.M))


def wired_wrappers() -> dict[str, list[str]]:
    """封装名 → api.ts 之外的引用文件（屏 / hook / service）。

    只认**带点号的调用或导入形态**（`saveReflection(`／`saveReflection,`），
    注释里出现名字不算接线——变异自证 F2 打过的就是「注释喂饱断言」这一口。
    """
    callers: dict[str, list[str]] = {}
    names = api_wrappers()
    for path in sorted((ROOT / "frontend" / "src").rglob("*")):
        if not path.is_file() or path.suffix not in {".ts", ".tsx"}:
            continue
        if path == API_TS:
            continue
        body = read(path)
        code = "\n".join(
            ln for ln in body.splitlines() if not ln.strip().startswith(("//", "*", "/*"))
        )
        for name in names:
            if re.search(rf"\b{name}\b\s*[(,;]", code) or re.search(
                rf"[{{,]\s*{name}\s*[,}}]", code
            ):
                callers.setdefault(name, []).append(str(path.relative_to(ROOT)))
    return callers


def has_tests(path: Path) -> bool:
    """测试文件里真的有测试声明（pytest 的 `def test_`、jest 的 `it(`/`test(`、Go 的 `func Test`）。"""
    if not path.exists():
        return False
    src = read(path)
    if path.suffix == ".py":
        return bool(re.search(r"^(async )?def test_", src, re.M))
    if path.suffix == ".go":
        # T6-01 的内部能力登记表把 Go 侧测试也指进来了：`go test` 只认 `func TestXxx`，
        # 而 jest 那条 `it(`/`test(` 正则在这里会因为 Go 文件里的 `t.Errorf(` 之类误判，
        # 所以按各语言各自的测试声明分别判，不给「指个空文件也算证据」留口子。
        return bool(re.search(r"^func Test\w*\(", src, re.M))
    return bool(re.search(r"\b(it|test)(\.\w+)?\(", src)) and "describe(" in src


def is_valid_date(value: str | None, *, allow_null: bool) -> bool:
    if value is None or value == "":
        return allow_null
    try:
        d = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return False
    return d <= date.today()


def verify_internal_capabilities(ledger: dict) -> list[str]:
    """§15 末句／§21 的「内部能力」登记面（T6-01）。

    §16 的十行是**用户功能**矩阵，把一条没有用户入口的接口实现塞进去会让「十行」这个
    口径失真，所以它住在隔壁这张表里：状态词表共用（同一套六态才可比），但阶梯封顶在
    `IMPLEMENTED`——`WIRED` 的判据是「前端有调用点」，内部能力按定义没有。

    登记的代价是**必须带处置日期**：`review_by` 一旦过期就判红，逼着到期真的去删或改判，
    而不是让「90 天内没调用方就删」这句话永久绿着。
    """
    errors: list[str] = []
    caps = ledger.get("internal_capabilities", [])
    feature_ids = {f["id"] for f in ledger["features"]}
    seen: list[str] = []
    for cap in caps:
        cid = cap.get("id", "")
        seen.append(cid)
        if cid in feature_ids:
            errors.append(f"{cid}: id 与 features 重名（内部能力与用户功能必须分开数）")
        state = cap.get("state")
        if state not in RANK:
            errors.append(f"{cid}: state「{state}」不在 §15 六态之内")
        elif RANK[state] > RANK["IMPLEMENTED"]:
            errors.append(
                f"{cid}: 内部能力封顶 IMPLEMENTED——它没有用户入口，"
                f"标 {state} 就是拿接口实现冒充上线（Baseline §10.4）"
            )
        for group in ("impl", "tests", "probe_tools"):
            rels = cap.get(group, [])
            if group != "probe_tools" and not rels:
                errors.append(f"{cid}: {group} 为空——登记内部能力也要能指回真实代码")
            for rel in rels:
                if not (ROOT / rel).exists():
                    errors.append(f"{cid}: {group} 里 {rel} 不存在")
                elif group == "tests" and not has_tests(ROOT / rel):
                    errors.append(f"{cid}: {rel} 里没有测试声明，不能当证据")
        reason = (cap.get("reason") or "").strip()
        if not CLAUSE_RE.search(reason):
            errors.append(f"{cid}: reason 没指回条款（§x.y）——「谁是生产调用方」这个问题要有出处")
        action = (cap.get("review_action") or "").strip()
        if "删除" not in action:
            errors.append(f"{cid}: review_action 必须写明到期删除什么，当前：{action!r}")
        review = cap.get("review_by")
        try:
            review_date = datetime.strptime(review or "", "%Y-%m-%d").date()
        except ValueError:
            errors.append(f"{cid}: review_by「{review}」不是 YYYY-MM-DD（到期处置日期是这条登记唯一的时限）")
        else:
            if review_date < date.today():
                errors.append(
                    f"{cid}: review_by {review} 已过期 ⇒ 按 review_action 删除，"
                    "或另立裁决并改日期；不许把过期登记留在台账里继续绿"
                )
        if not (cap.get("next_gap") or "").strip():
            errors.append(f"{cid}: next_gap 为空——内部能力也要写清另一条路是什么")
    for dup in sorted({i for i in seen if seen.count(i) > 1}):
        errors.append(f"{dup}: 内部能力 id 重复")
    return errors


def verify(ledger: dict) -> list[str]:
    """返回全部判据违例（空列表＝台账每条声明都被证据撑起）。"""
    errors: list[str] = []
    endpoints = contracted_endpoints()
    paths = contracted_paths()
    crons = scheduled_crons()
    defaults = cutover_defaults()
    wrappers = api_wrappers()
    wired = wired_wrappers()

    rows = ledger["matrix_rows"]
    features = ledger["features"]

    seen_rows = {f["matrix_row"] for f in features}
    for row in rows:
        if row not in seen_rows:
            errors.append(f"§16 矩阵行「{row}」没有任何 feature 覆盖（台账必须覆盖十行）")

    ids = [f["id"] for f in features]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        errors.append(f"feature id 重复：{dup}")

    for f in features:
        fid = f["id"]
        state = f["state"]
        if state not in RANK:
            errors.append(f"{fid}: state「{state}」不在 §15 六态之内")
            continue
        rank = RANK[state]

        # —— 存在性：不分状态，声明了就必须真（拼错即说谎）——
        for ep in f["api"]:
            if not re.match(r"^(GET|POST|PATCH|PUT|DELETE) /", ep):
                errors.append(f"{fid}: api 项形状不是「METHOD /path」：{ep!r}")
            elif ep not in endpoints:
                errors.append(f"{fid}: 契约台账里没有这条端点：{ep}（端点要么改名要么没立项）")
        for group in ("tests", "e2e_tests"):
            for rel in f[group]:
                if not (ROOT / rel).exists():
                    errors.append(f"{fid}: {group} 指向不存在的文件：{rel}")
                elif group == "e2e_tests" and not has_tests(ROOT / rel):
                    errors.append(f"{fid}: {rel} 里没有测试声明，不能当端到端证据")
        for name in f["frontend_calls"]:
            if name not in wrappers:
                errors.append(f"{fid}: frontend_calls 里的 {name} 在 api.ts 已不存在（封装改名或删除）")
        for cp in f["cron"]:
            if not cp.startswith("/"):
                errors.append(f"{fid}: cron 项必须是路径：{cp!r}")
            elif cp not in paths:
                errors.append(f"{fid}: cron 路径 {cp} 不在契约台账里（端点要么没立项要么已改名）")
        for rel in f.get("assets", []):
            if not (ROOT / rel).exists():
                errors.append(f"{fid}: assets 里 {rel} 不存在（发布资产指的是仓库里真实文件）")

        key = f.get("cutover_key")
        if key:
            if key not in defaults:
                errors.append(f"{fid}: 切流键 {key} 在 go-core/internal/config 里不存在")

        delivery = f.get("delivery", "api")
        if delivery not in ("api", "static"):
            errors.append(f"{fid}: delivery「{delivery}」只允许 api 或 static")
        assets = f.get("assets", [])

        # —— 阶梯：态越高，要求越多 ——
        if rank >= RANK["IMPLEMENTED"] and not f["api"] and not f["frontend_files"] and not assets:
            errors.append(f"{fid}: {state} 却既没有端点、也没有前端文件、也没有发布资产，证据为空")

        if rank >= RANK["WIRED"]:
            if delivery == "api":
                if not f["api"]:
                    errors.append(f"{fid}: WIRED 要求至少有 API 链路")
                if not f["frontend_calls"]:
                    errors.append(f"{fid}: WIRED 要求声明前端调用点（frontend_calls 为空）")
                for name in f["frontend_calls"]:
                    if name in wrappers and name not in wired:
                        errors.append(
                            f"{fid}: {name} 只有 api.ts 里的定义、零调用方 ⇒ 按 §15 末句只能标 IMPLEMENTED"
                        )
            else:
                # §16 行 10 的「API/服务链路」写的是「静态页面/链接」、持久化列写「发布资产」：
                # 这一类功能没有端点可指，链路就是「App 链接常量 → 站点页面源文件」这两头，
                # 两头都必须能在仓库里摸到，否则 WIRED 与 static 都无从谈起。
                if not assets:
                    errors.append(f"{fid}: delivery=static 的 WIRED 要声明发布资产（assets 为空）")
            entry_files = ENTRY_FILE_RE.findall(f["frontend_entry"])
            if not entry_files:
                errors.append(f"{fid}: frontend_entry 没点名 frontend/ 下的文件（无法核）")
            for rel in entry_files:
                if not (ROOT / rel).exists():
                    errors.append(f"{fid}: frontend_entry 点名的文件不存在：{rel}")
            for rel in f["frontend_files"]:
                if not (ROOT / rel).exists():
                    errors.append(f"{fid}: frontend_files 里 {rel} 不存在")

        if rank >= RANK["E2E_VERIFIED"]:
            if not f["e2e_tests"]:
                errors.append(f"{fid}: E2E_VERIFIED 却 e2e_tests 为空——端到端结论必须有测试出处")
            if not is_valid_date(f.get("verified_on"), allow_null=False):
                errors.append(f"{fid}: verified_on「{f.get('verified_on')}」不是不晚于今天的 YYYY-MM-DD")
            by = f.get("verified_by") or ""
            if not PASSED_RE.search(by):
                errors.append(f"{fid}: verified_by 必须带实测计数（N passed），当前：{by!r}")

        if rank >= RANK["PRODUCTION_ENABLED"]:
            for cp in f["cron"]:
                if cp not in crons:
                    errors.append(f"{fid}: PRODUCTION_ENABLED 但 {cp} 不在 deploy/schedule/crontab 里")
            if key and defaults.get(key) == 0:
                errors.append(
                    f"{fid}: 切流键 {key} 默认 0（灰度 0%），不能宣称 PRODUCTION_ENABLED"
                )
            ev = f.get("production_evidence") or ""
            if not DATED_RE.search(ev):
                errors.append(f"{fid}: PRODUCTION_ENABLED 需要 production_evidence 带日期的实测出处，当前：{ev!r}")

        if rank >= RANK["VALUE_MEASURED"]:
            if not (f.get("value_metric") or "").strip():
                errors.append(f"{fid}: VALUE_MEASURED 需要 value_metric 写明指标")
            ev = f.get("metric_evidence") or ""
            if not (DATED_RE.search(ev) and re.search(r"\d", ev)):
                errors.append(f"{fid}: VALUE_MEASURED 的 metric_evidence 要带数字与日期，当前：{ev!r}")

        if not (f.get("next_gap") or "").strip():
            errors.append(f"{fid}: next_gap 为空——没到顶的态必须写清还差什么，这是台账存在的意义")

    errors += verify_internal_capabilities(ledger)
    return errors


def render(ledger: dict, errors: list[str]) -> str:
    features = ledger["features"]
    crons = scheduled_crons()
    wired = wired_wrappers()
    lines = [
        "# 功能状态台账（T0-05）",
        "",
        "> 本文件由 `scripts/feature_status.py` 从 `docs/feature-status.json` 生成，**禁止手改**。",
        "> 六态口径来自 Baseline §15，覆盖 §16 十行验收矩阵；每条声明的证据由脚本对账",
        "> （`contracts/endpoints.md`／`frontend/src`／`deploy/schedule/crontab`／`go-core/internal/config`）。",
        f"> 生成时对账结论：{'全部判据通过' if not errors else f'{len(errors)} 条违例（见文末）'}。",
        "",
        "## 状态一览",
        "",
        "| 态 | 功能数 | 功能 |",
        "|---|---|---|",
    ]
    for state in STATES:
        hit = [f for f in features if f["state"] == state]
        names = "、".join(sorted(f["id"] for f in hit)) if hit else "—"
        lines.append(f"| `{state}` | {len(hit)} | {names} |")
    lines += [
        f"| 合计 | **{len(features)}** | 覆盖 §16 十行矩阵（一行可拆多条时按 `matrix_row` 归位） |",
        "",
        "## 逐条对账",
        "",
        "| 功能 | §16 矩阵行 | 态 | 前端入口 | 端点 | 调度（crontab 实测） | 切流键（默认值） | 下一步缺口 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for f in sorted(features, key=lambda x: (rows_order(ledger).index(x["matrix_row"]), x["id"])):
        cron_text = "、".join(
            f"{cp}{'（已排程）' if cp in crons else '（未排程）'}" for cp in f["cron"]
        ) or "—"
        key = f.get("cutover_key")
        key_text = "—" if not key else f"{key}"
        entry = f["frontend_entry"]
        calls = f["frontend_calls"]
        wired_marks = "、".join(
            f"{n}{'✓' if n in wired else '✗零调用'}" for n in calls
        ) or "—"
        assets = f.get("assets", [])
        link_text = (
            f"{len(f['api'])} 条" if f.get("delivery", "api") == "api"
            else f"静态发布（{len(assets)} 份资产）"
        )
        lines.append(
            f"| `{f['id']}` {f['title']} | {f['matrix_row']} | **{f['state']}** | {entry} "
            f"| {link_text} | {cron_text} | {key_text} | {f['next_gap']} |"
        )
        evidence = f"调用点：{wired_marks}"
        if assets:
            evidence += f"｜资产：{'、'.join(assets)}"
        lines.append(
            f"| ↳ 证据 |  |  | {evidence} "
            f"| 测试：{'、'.join(f['tests']) or '—'} "
            f"| 端到端：{'、'.join(f['e2e_tests']) or '—'} "
            f"| 实测：{f.get('verified_by') or '—'} |  |"
        )
    lines += ["", "## 内部能力登记（无用户入口，状态封顶 IMPLEMENTED）", ""]
    caps = ledger.get("internal_capabilities", [])
    if not caps:
        lines.append("—（本仓没有登记任何「接口已实现但无生产调用方」的内部能力）")
    for cap in caps:
        lines += [
            f"- **`{cap['id']}`** {cap.get('title', '')}（**{cap.get('state')}**，"
            f"处置日期 {cap.get('review_by')}）：{cap.get('reason')}",
            f"  - 代码面：{('、' if cap.get('impl') else '—').join(cap.get('impl', []))}",
            f"  - 测试面：{('、' if cap.get('tests') else '—').join(cap.get('tests', []))}",
            f"  - 非生产调用方：{('、' if cap.get('probe_tools') else '—').join(cap.get('probe_tools', []))}",
            f"  - 到期动作：{cap.get('review_action')}",
            f"  - 缺口：{cap.get('next_gap')}",
        ]
    lines += ["", "## 为什么这些态不能更高（口径备忘）", ""]
    for f in sorted(features, key=lambda x: x["id"]):
        lines.append(f"- **`{f['id']}`**（{f['state']}）：{f['next_gap']}")
    if errors:
        lines += ["", "## 判据违例", ""]
        lines += [f"- {e}" for e in errors]
    lines.append("")
    return "\n".join(lines)


_ORDER_CACHE: list[str] | None = None


def rows_order(ledger: dict) -> list[str]:
    """§16 矩阵行顺序（稳定排序依据）。"""
    global _ORDER_CACHE
    if _ORDER_CACHE is None:
        _ORDER_CACHE = ledger["matrix_rows"]
    return _ORDER_CACHE


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只判据＋查 md 是否过期，不写文件")
    args = ap.parse_args()

    ledger = json.loads(read(LEDGER))
    global _ORDER_CACHE
    _ORDER_CACHE = None
    errors = verify(ledger)
    md = render(ledger, errors)

    if args.check:
        stale = MARKDOWN.exists() and read(MARKDOWN) != md
        if errors:
            print(f"功能状态台账判据违例 {len(errors)} 条：")
            for e in errors:
                print(f"  - {e}")
        if stale:
            print("docs/feature-status.md 与 JSON 证据不同源（PR 必须重跑本脚本并把 md 一起提）")
        if errors or stale:
            return 1
        feats = ledger["features"]
        counts = {s: sum(1 for f in feats if f["state"] == s) for s in STATES}
        print(
            f"台账一致：{len(feats)} 条 feature 覆盖 §16 {len(ledger['matrix_rows'])} 行矩阵；"
            + "／".join(f"{s} {n}" for s, n in counts.items() if n)
        )
        return 0

    if errors:
        print("先修判据违例再生成（生成器不写脏台账）：")
        for e in errors:
            print(f"  - {e}")
        return 1
    MARKDOWN.write_text(md, encoding="utf-8")
    print(f"已生成 {MARKDOWN.relative_to(ROOT)}（{len(ledger['features'])} 条 feature）")
    if read(MARKDOWN) != md:  # 幂等自证：写完再读，字节一致
        print("生成物与内存不一致（不应发生）")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
