"""N8 批 5（mood + reflections 两个域）的跨语言逐字闸——每域四步的第④步。

被验对象：`go-core/internal/domain/{mood,pyquery,pytime}.go`（分值表、按本地日历日聚合、
四档阈值、15 语言文案副本、query 参数形状）、`internal/httpapi/{mood_history,mood_trend,
reflections_list,batch5}.go`（对外形状）、`internal/store/legacy_batch5.go`（四条 SQL）、
`internal/config/config.go` 与 `cmd/go-api/main.go`（两条灰度键）、
`deploy/k8s/{10-config,50-ingress}.yaml`（放量面）、`contracts/openapi.json` 里这两条 GET
的 deprecated，以及夹具的 mood 组（42，含 M-G 的两格「同刻三行」证人）／reflection 组
（13，含 D-A 的「同刻两行」两格）＋ mood_copy_corpus（60）／language_resolve_corpus（23）
两块语料。

口径与批 1–4 相同（三段式，缺一段就有空转的格子）：
1. Python 侧现算——本文件；
2. 夹具存着 Python 当时的结果——由 `scripts/gen_n8_contract_goldens.py` 从真 FastAPI＋真 PG 录出；
3. Go 侧就地重算并与夹具比——`internal/httpapi/n8_replay_test.go` 的
   `TestN8MoodContractBytes`／`TestN8ReflectionContractBytes`／两块语料闭包测。
⇒ 本文件证「夹具 == Python」，Go 测证「Go == 夹具」，两段的输入是同一份夹具，
   合起来就是「Go == Python」。本文件因此额外钉住两侧的**源码字面量**：一旦有人在任一侧
   改了分值表、阈值、SQL 的 WHERE 或文案副本而语料碰不到那一格，行号级的文本比对先红。

为什么批 5 的闸比前四批更依赖「闭包」而不是「采样」：这一批三条端点全是读，
一次 HTTP 用例只能命中四档阈值里的一档、15 语言里的一种；而 Go 侧嵌的是**整张**
`templates/copy_mood.json` 副本。副本与 `backend/data/copy/*.json` 一旦分叉，
只有被命中的那一格会红，其余 59 格静默错——所以下面第 1 条按 15×4 全枚举比对。

口径来源（逐条点名到行）：
- MOOD_MAP / 未知表情取 3   ← mood_service.py:39-46,50-58 ↔ domain/mood.go:29-39
- limit=30、streak_updated=0 ← mood_service.py:309,317     ↔ domain/mood.go:44 / mood_history.go:47
- days=Query(14,ge=1,le=90)  ← routers/mood.py:69          ↔ domain/mood.go:48-52
- avg>=4 / avg>=3            ← mood_service.py:344-348     ↔ domain/mood.go:162-169
- 15 语言文案四键            ← core/copy.py:26-28,33-41,64-75 ↔ domain/mood.go:175-225 + templates/copy_mood.json
- 两支语言归一不合并         ← core/language.py:20-34 ↔ httpapi/batch1.go:53-73；core/copy.py:35 ↔ domain/mood.go:184-195
- 四条 SQL 的 WHERE/ORDER    ← mood_repository.py:155-172,194-215 / user_repository.py:83 / growth_repository.py:86-96
                              ↔ store/legacy_batch5.go:39-44,71-75,99-102,123-128
- falsy 的 related_memory_ids ← reflection_service.py:137  ↔ domain/mood.go:231-236
- limit=Query(20,ge=1,le=100) ← routers/reflection.py:64   ↔ domain/mood.go:76-81
- 两条灰度键                 ← config/config.go:83-84,100-101 ↔ 10-config.yaml ↔ main.go:100 ↔ 50-ingress.yaml
- 决策点 D-10（Ingress 的 path 匹配不看方法）← 50-ingress.yaml 的批 5 段 ↔ 本文件第 11 条
- mood 时钟钉在真模块         ← scripts/gen_n8_contract_goldens.py 的 _module/freeze_clock
                              ↔ 本文件第 12 条（2026-10-15 的空转勘误）
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import yaml

from backend.config import settings
from backend.core import copy as copy_module
from backend.core.language import resolve_request_language
from backend.main import app
from backend.models.schemas import (
    MoodCheckinResponse,
    MoodHistoryResponse,
    MoodTrendResponse,
    ReflectionResponse,
)
from backend.services.mood_service import MOOD_MAP

ROOT = Path(__file__).resolve().parents[2]
GO = ROOT / "go-core"
FIXTURE = GO / "testdata" / "n8_contract.json"
MANIFESTS = ROOT / "deploy" / "k8s"

DOMAIN_MOOD = GO / "internal" / "domain" / "mood.go"
STORE_BATCH5 = GO / "internal" / "store" / "legacy_batch5.go"
HTTPAPI = GO / "internal" / "httpapi"
COPY_MOOD = GO / "internal" / "domain" / "templates" / "copy_mood.json"

TREND_KEYS = ("empty", "high", "mid", "low")


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _cases(group: str) -> list[dict]:
    return [c for c in _fixture()["cases"] if c["group"] == group]


def _go(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _go_int_const(name: str, src: str, where: str) -> int:
    match = re.search(rf"{name}\s*=\s*(\d+)", src)
    assert match, f"Go 侧 {where} 找不到 {name} 常量——改名或换写法了，闸要跟着改"
    return int(match.group(1))


def _go_map_block(name: str, src: str) -> dict[str, int]:
    """抓 `var X = map[string]int{ ... }` 的字面量块。"""
    match = re.search(rf"var {name} = map\[string\]int\{{(.*?)\n\}}", src, re.S)
    assert match, f"Go 侧抓不到 var {name} = map[string]int{{...}}，写法变了"
    pairs = re.findall(r'"([^"]+)"\s*:\s*(\d+)', match.group(1))
    assert pairs, f"var {name} 块里一行都没解析出来"
    return {emoji: int(value) for emoji, value in pairs}


def _json_field_order(struct_name: str, src: str) -> list[str]:
    """按声明序取出 Go struct 的 json tag；字段序就是响应体的键序，这条不能靠推断。"""
    match = re.search(rf"type {struct_name} struct \{{(.*?)\n\}}", src, re.S)
    assert match, f"Go 侧抓不到 type {struct_name} struct"
    return re.findall(r'`json:"([^"]+)"', match.group(1))


def _go_sql(name: str) -> str:
    match = re.search(rf"const {name} = `(.*?)`", _go(STORE_BATCH5), re.S)
    assert match, f"store/legacy_batch5.go 抓不到 const {name}"
    return " ".join(match.group(1).split())


def _manifest(kind: str) -> dict:
    docs = yaml.safe_load_all((MANIFESTS / "10-config.yaml").read_text(encoding="utf-8"))
    if kind != "Ingress":
        return next(d for d in docs if d and d["kind"] == kind)
    docs = yaml.safe_load_all((MANIFESTS / "50-ingress.yaml").read_text(encoding="utf-8"))
    return next(d for d in docs if d and d["kind"] == kind)


def _go_api_paths() -> list[dict]:
    ing = _manifest("Ingress")
    return [
        p
        for rule in ing["spec"]["rules"]
        for p in rule["http"]["paths"]
        if p["backend"]["service"]["name"] == "go-api"
    ]


def test_mood_copy_corpus_is_a_sixty_cell_closure_on_three_sides():
    """60 格 = 15 locale × 4 判据，三个来源逐字节相等：文案库源文件、Go 内嵌副本、Python 现算。

    为什么全枚举（前四批的语料是采样，这批不是）：Go 侧嵌的是**整张** copy_mood.json，
    而 `/mood/trend` 的 HTTP 用例每档判据只命中一种语言。采样会让「副本里其余 59 格
    与源文件分叉」这件事静默存活到放量之后。
    取 `copy.t()` 的返回值而不是 JSON 原始字面量：「目标语言缺键→回退 en→回退键名」
    这条链（copy.py:64-75）也因此进证据面——本域实测零缺键，所以两者相同，
    但将来谁删了一格，这里显出的是回退值而不是消失。
    """
    cells = _fixture()["mood_copy_corpus"]
    go_copy = json.loads(COPY_MOOD.read_text(encoding="utf-8"))

    assert len(cells) == len(copy_module.SUPPORTED_LOCALES) * len(TREND_KEYS) == 60, (
        f"闭包条数 {len(cells)}，期望 15×4"
    )
    seen = {(c["locale"], c["key"]) for c in cells}
    assert len(seen) == len(cells), "语料里有重复格，闭包是假的"
    assert seen == {
        (loc, f"trend.{k}") for loc in copy_module.SUPPORTED_LOCALES for k in TREND_KEYS
    }, "语料的 locale 或键集漂了"

    for cell in cells:
        locale, dotted = cell["locale"], cell["key"]
        short = dotted.split(".", 1)[1]
        assert copy_module.t("mood", dotted, locale) == cell["out"], (
            f"Python 现算 != 夹具（{locale}/{dotted}）：夹具该重录了"
        )
        source = json.loads(
            (ROOT / "backend" / "data" / "copy" / f"{locale}.json").read_text(encoding="utf-8")
        )
        assert source["mood"]["trend"][short] == cell["out"], (
            f"文案库源文件 != 夹具（{locale}/{dotted}）"
        )
        assert go_copy[locale][short] == cell["out"], (
            f"Go 内嵌副本 != 夹具（{locale}/{dotted}）——副本与源文件分叉了"
        )

    # 双向差集：副本多一格＝白嵌，少一格＝那格走 moodCopyString 的回退链（响应里出现
    # "mood.trend.high" 这种键名字面量），两种都不该在无人登记时发生。
    assert set(go_copy) == set(copy_module.SUPPORTED_LOCALES), (
        f"副本的 locale 集合 != SUPPORTED_LOCALES：{sorted(set(go_copy) ^ set(copy_module.SUPPORTED_LOCALES))}"
    )
    for locale in copy_module.SUPPORTED_LOCALES:
        assert set(go_copy[locale]) == set(TREND_KEYS), (
            f"副本 {locale} 的键集 != mood.trend 四键：{sorted(go_copy[locale])}"
        )


def test_mood_levels_table_is_one_table_on_both_sides():
    """分值表（emoji→1..5）与「未命中取 3」：这张表决定 values 与四档判据的输入。

    ↳ 顺带钉住一条**已登记的重复**：mood_service.py:39-46 与 :50-57 把 MOOD_MAP
    逐字声明了两遍（内容完全相同，第二遍覆盖第一遍）。Go 侧只有一张表，所以
    「只改其中一遍」会让 Python 的现算与 Go 分叉而看不出原因——这里比的是
    **生效的那张**（模块属性），同时断言两处字面量互为副本，改一处就先红在这里。
    """
    mood_src = ROOT / "backend" / "services" / "mood_service.py"
    src = mood_src.read_text(encoding="utf-8")
    blocks = re.findall(
        r"MOOD_MAP = \{(.*?)\n\}", mood_src.read_text(encoding="utf-8"), re.S
    )
    assert len(blocks) == 2, f"MOOD_MAP 声明了 {len(blocks)} 遍，批 5 的登记按两遍写的"
    norm = [re.sub(r"\s+", "", b) for b in blocks]
    assert norm[0] == norm[1], "两处 MOOD_MAP 已经不再互为副本——生效的是第二处，先核对再放量"

    go_levels = _go_map_block("MoodLevels", _go(DOMAIN_MOOD))
    live = {emoji: value for emoji, (_label, value) in MOOD_MAP.items()}
    assert go_levels == live, f"分值表两侧不等：Go {go_levels} vs Python {live}"

    assert _go_int_const("MoodLevelDefault", _go(DOMAIN_MOOD), "domain/mood.go") == 3
    assert "MOOD_MAP.get(emoji, (None, 3))[1]" in src, (
        "Python 侧未命中表情的默认元组改了写法——3 这个默认值是判据，不是美化"
    )


def test_history_limit_hardcoded_streak_and_key_order_match_go():
    """GET /mood/history 的四件事：limit=30、streak_updated 恒 0、无查询参数、键序。

    第 2、3 条是「不许顺手修正」的那两类：30 与 0 都写死在 service 层，
    端点根本没有 days/limit 参数（所以 `?days=3` 与不带参数逐字同形，夹具
    history-ignores-query 钉的就是这条）。
    """
    src = ROOT / "backend" / "services" / "mood_service.py"
    text = src.read_text(encoding="utf-8")
    history_body = re.search(
        r"async def get_history.*?\n    async def get_trend", text, re.S
    )
    assert history_body, "get_history 的写法变了，这条闸要跟着改"
    assert "mood_repository.get_recent(db, user_id, limit=30)" in history_body.group(0)
    assert _go_int_const("MoodHistoryLimit", _go(DOMAIN_MOOD), "domain/mood.go") == 30
    assert _go_int_const("StreakUpdatedInHistory", _go(DOMAIN_MOOD), "domain/mood.go") == 0
    assert "streak_updated=0," in history_body.group(0)

    router = (ROOT / "backend" / "routers" / "mood.py").read_text(encoding="utf-8")
    history_route = re.search(
        r'@router\.get\("/mood/history".*?\nasync def history\((.*?)\):', router, re.S
    )
    assert history_route, "history 路由的声明形状变了"
    assert "Query" not in history_route.group(1), (
        "/mood/history 多了查询参数＝对外契约变了，客户端零改动这条前提作废"
    )

    go_src = _go(HTTPAPI / "mood_history.go")
    assert _json_field_order("moodHistoryPayload", go_src) == list(
        MoodHistoryResponse.model_fields
    )
    assert _json_field_order("moodCheckinPayload", go_src) == list(
        MoodCheckinResponse.model_fields
    ), "行内键序 != MoodCheckinResponse 声明序（含两个恒 null 的 Optional）"


def test_trend_query_spec_thresholds_and_window_match_go():
    """days 三件套、四档阈值、窗口下界与 labels/values/summary 键序。

    阈值那两行（`avg >= 4` / `avg >= 3`）用浮点比较，4.0 归 high、3.0 归 mid；
    夹具 trend-exactly3 特意种成 [2,5] 让均值**恰好**等于 3，就是为了钉住 `>=` 而不是 `>`。
    窗口是 `utc_now() - timedelta(days=days)`——天数加法是 n*86400 秒，不做日历加法，
    Go 侧写成 `time.Duration(days) * 24 * time.Hour`，两条必须同形。
    """
    router = (ROOT / "backend" / "routers" / "mood.py").read_text(encoding="utf-8")
    days = re.search(r"days: int = Query\((\d+), ge=(\d+), le=(\d+)\)", router)
    assert days, "/mood/trend 的 days 参数写法变了，闸要跟着改"
    py_spec = tuple(int(g) for g in days.groups())
    go_src = _go(DOMAIN_MOOD)
    assert py_spec == (
        _go_int_const("MoodTrendDefaultDays", go_src, "domain/mood.go"),
        _go_int_const("MoodTrendMinDays", go_src, "domain/mood.go"),
        _go_int_const("MoodTrendMaxDays", go_src, "domain/mood.go"),
    ), f"days 三件套不等：Python {py_spec} vs Go"

    summary = re.search(
        r"def _trend_summary.*?(?=\n    async def |\nmood_service)",
        (ROOT / "backend" / "services" / "mood_service.py").read_text(encoding="utf-8"),
        re.S,
    )
    assert summary, "_trend_summary 写法变了"
    assert re.findall(r"if avg >= (\d+):", summary.group(0)) == ["4", "3"], (
        "四档判据的两个阈值漂了——Go 侧 switch 的两条 case 必须同步改"
    )
    assert 'copy.t("mood", "trend.' in summary.group(0)
    for key in TREND_KEYS:
        assert f'"trend.{key}"' in summary.group(0), f"四档里少了 {key}"

    trend_go = _go(HTTPAPI / "mood_trend.go")
    assert "time.Duration(days) * 24 * time.Hour" in trend_go, (
        "窗口不再是 n*86400 秒：改成日历加法就会与 Python 的 timedelta(days=) 分叉"
    )
    assert _json_field_order("moodTrendPayload", trend_go) == list(
        MoodTrendResponse.model_fields
    )


def test_two_language_branches_stay_two_functions():
    """两支语言归一不能合并：`language.py` 走「split→精确成员」，`copy.py` 走「lower/strip→前缀匹配」。

    同一个原始 header 两支可以给出不同结论（实测 `zh_CN`：第一支 None、第二支 zh），
    而 `/mood/trend` 的 summary 恰好**两级都走**，所以这里既比夹具闭包（23 格），
    也钉住「Go 侧没有把它们合成一支」——两支在 Go 里分属两个包：
    `httpapi.normalizeLanguageCode` 与 `domain.CopyLocaleFor`。
    """
    cells = _fixture()["language_resolve_corpus"]
    assert len(cells) == 23, f"归一闭包 {len(cells)} 格，期望 23"
    accepted = sum(1 for c in cells if c["want"])
    assert accepted and accepted < len(cells), (
        f"23 格里 accepted={accepted}：全接受或全拒绝都说明这支只剩一半"
    )
    for cell in cells:
        got = resolve_request_language(SimpleNamespace(headers={"X-Language": cell["raw"]}))
        assert got == cell["want"], (
            f"resolve_request_language({cell['raw']!r}) = {got!r}，夹具记的是 {cell['want']!r}"
        )

    # 两支在同一批原始值上确实分叉：合并成一支就会在这一格静默改变 summary 的语言。
    diffs = {
        c["raw"]: (c["want"], copy_module.locale_for(c["raw"]))
        for c in cells
        if (c["want"] or None) != copy_module.locale_for(c["raw"])
    }
    assert diffs, "两支语言归一在本批 23 个输入上完全同解——那说明有一支被改宽了"
    assert diffs["zh_CN"] == (None, "zh"), f"zh_CN 的分叉格变了：{diffs.get('zh_CN')}"

    httpapi_batch1 = _go(HTTPAPI / "batch1.go")
    domain_mood = _go(DOMAIN_MOOD)
    assert "func normalizeLanguageCode" in httpapi_batch1
    assert "func CopyLocaleFor" not in httpapi_batch1, (
        "两支被合进了 httpapi：domain.CopyLocaleFor 才是 core/copy.py 的那一支"
    )
    assert "func CopyLocaleFor" in domain_mood
    assert "func normalizeLanguageCode" not in domain_mood

    # 两张表的**顺序**都是判据：locale_for 做前缀匹配（zh 在前），
    # resolve 做成员判定（en 在前）。同集合换序就会改变 zh* 串的归属。
    go_supported = re.search(
        r"var SupportedLanguages = \[\]string\{(.*?)\}", httpapi_batch1, re.S
    )
    assert go_supported, "httpapi 抓不到 SupportedLanguages"
    assert tuple(re.findall(r'"([^"]+)"', go_supported.group(1))) == tuple(
        settings.supported_languages
    ), "supported_languages 表或顺序与 Go 不等"
    go_locales = re.search(
        r"var CopySupportedLocales = \[\]string\{(.*?)\}", domain_mood, re.S
    )
    assert go_locales, "domain 抓不到 CopySupportedLocales"
    assert tuple(re.findall(r'"([^"]+)"', go_locales.group(1))) == copy_module.SUPPORTED_LOCALES, (
        "SUPPORTED_LOCALES 表或顺序与 Go 不等（前缀匹配吃顺序）"
    )
    assert tuple(settings.supported_languages) != copy_module.SUPPORTED_LOCALES, (
        "两张表顺序一样＝有人把它们合成了同一张，两支归一就此并成一支"
    )


def test_batch5_sql_shapes_are_one_to_one():
    """四条 SQL 只收缩列，不动 WHERE 与 ORDER BY——灰度期两侧读同一批行才有可比之处。

    逐条对应：get_recent（倒序＋`id DESC`＋LIMIT）、count_by_day（窗口下界＋**升序**＋`id ASC`，
    「当日取首个」靠这个顺序）、get_user（`status='active'`，deleted 查不到⇒时区 None⇒UTC，
    **不是** 40401）、list_by_type（record_type **精确**等值）。
    两个 COALESCE 对应 service 层的 `or ""` 与 reflection 侧的 falsy 判据。
    """
    repo = (ROOT / "backend" / "repositories" / "mood_repository.py").read_text(encoding="utf-8")
    get_recent = re.search(r"async def get_recent.*?return list", repo, re.S)
    assert get_recent, "get_recent 写法变了"
    assert ".where(MoodCheckin.user_id == user_id)" in get_recent.group(0)
    # M-G：两条都必须带 id 次级键、方向与 created_at 一致。同刻行没有它时
    # 「谁进 LIMIT 30」「当日首个 emoji 是谁」由查询计划决定，两侧同形才是可复现的
    # （夹具 mood-h-tie／mood-t-tie 各三行同刻是证人，覆盖面闸按播种序判它不空转）。
    assert (".order_by(MoodCheckin.created_at.desc(), MoodCheckin.id.desc())"
            in get_recent.group(0)), "get_recent 的 id 次级键没了或方向反了"
    assert ".limit(limit)" in get_recent.group(0)
    history_sql = _go_sql("selectMoodHistory").upper()
    assert "WHERE USER_ID = $1" in history_sql
    assert "ORDER BY CREATED_AT DESC, ID DESC" in history_sql, (
        "Go 侧的次级键缺失或方向与 Python 不一致"
    )
    assert "LIMIT $2" in history_sql
    assert "COALESCE(MOOD_LABEL, '')" in history_sql and "COALESCE(AI_INTERPRETATION, '')" in history_sql

    count_by_day = re.search(r"async def count_by_day.*?return result", repo, re.S)
    assert count_by_day, "count_by_day 写法变了"
    assert "start = utc_now() - timedelta(days=days)" in count_by_day.group(0)
    assert "MoodCheckin.created_at >= start" in count_by_day.group(0)
    assert (".order_by(MoodCheckin.created_at.asc(), MoodCheckin.id.asc())"
            in count_by_day.group(0)), "count_by_day 的 id 次级键没了或方向反了"
    trend_sql = _go_sql("selectMoodTrendRows").upper()
    assert "AND CREATED_AT >= $2" in trend_sql
    assert "ORDER BY CREATED_AT ASC, ID ASC" in trend_sql, (
        "窗口下界或升序＋id 升序没了：当日取首个的行就会变成另一行"
    )
    assert "DATE_TRUNC" not in trend_sql, (
        "Go 侧不许在 SQL 里按日历日聚合——时区折算必须留在应用层，与 Python 同一条回退链"
    )

    users_repo = (ROOT / "backend" / "repositories" / "user_repository.py").read_text(
        encoding="utf-8"
    )
    assert 'stmt = select(User).where(User.id == user_id, User.status == "active")' in users_repo
    assert "WHERE ID = $1 AND STATUS = 'ACTIVE'" in _go_sql("selectMoodUserTimezone").upper()

    growth_repo = (ROOT / "backend" / "repositories" / "growth_repository.py").read_text(
        encoding="utf-8"
    )
    by_type = re.search(r"async def list_by_type.*?return list", growth_repo, re.S)
    assert by_type, "list_by_type 写法变了"
    assert (
        "GrowthRecord.user_id == user_id, GrowthRecord.record_type == record_type"
        in by_type.group(0)
    )
    # 次级排序键两侧**同方向**才算同形：只有一侧带 `id` 时，夹具 refl-tie 的
    # 「同刻两行」就会两侧给出相反顺序（D-A；重录字节里那条用例是它的证人）。
    assert (".order_by(GrowthRecord.created_at.desc(), GrowthRecord.id.desc())"
            in by_type.group(0)), "list_by_type 的 id 次级键没了"
    refl_sql = _go_sql("selectReflections").upper()
    assert "AND RECORD_TYPE = $2" in refl_sql
    assert "ORDER BY CREATED_AT DESC, ID DESC" in refl_sql, (
        "Go 侧的次级键缺失或方向与 Python 不一致"
    )
    assert "LIMIT $3" in refl_sql and "COALESCE(RELATED_MEMORY_IDS, '')" in refl_sql


def test_reflections_falsy_split_exact_type_and_limit_match_go():
    """GET /reflections 的三处「不许顺手修正」：falsy 判据、精确 record_type、limit 三件套。

    `r.related_memory_ids.split(",") if r.related_memory_ids else None` 判的是 **falsy**
    而不是「列是否 NULL」：所以 NULL 与空串都⇒null，而 `","`⇒`["",""]`、`"a,,b"`⇒`["a","","b"]`
    （夹具 reflections-nulls 五格）。record_type 若做成 lower 或前缀匹配，
    夹具里那条 `Reflection`（首字母大写）就会被错误读进来。
    """
    service = (ROOT / "backend" / "services" / "reflection_service.py").read_text(encoding="utf-8")
    listing = re.search(r"async def list\(.*?(?=\n\nmood|\n\nreflection_service)", service, re.S)
    assert listing, "reflection_service.list 写法变了"
    assert 'growth_repository.list_by_type(db, user_id, "reflection", limit=limit)' in listing.group(0)
    assert (
        'r.related_memory_ids.split(",") if r.related_memory_ids else None' in listing.group(0)
    ), "falsy 判据被改成 is-not-None 之类——空串那一格就会从 null 变成 [\"\"]"

    domain_src = _go(DOMAIN_MOOD)
    split = re.search(r"func SplitMemoryIDs\(raw string\) \[\]string \{(.*?)\n\}", domain_src, re.S)
    assert split and 'if raw == ""' in split.group(1) and "strings.Split(raw, \",\")" in split.group(1), (
        "Go 侧的 falsy 形状变了（NULL 与空串必须同解，且 split 至少给一个元素）"
    )
    assert re.search(r'ReflectionRecordType\s*=\s*"reflection"', domain_src), (
        "record_type 字面量漂了：夹具里那条 `Reflection` 就是专门用来打红宽松匹配的"
    )

    router = (ROOT / "backend" / "routers" / "reflection.py").read_text(encoding="utf-8")
    limit = re.search(r"limit: int = Query\((\d+), ge=(\d+), le=(\d+)\)", router)
    assert limit, "GET /reflections 的 limit 参数写法变了"
    assert tuple(int(g) for g in limit.groups()) == (
        _go_int_const("ReflectionsDefaultLimit", domain_src, "domain/mood.go"),
        _go_int_const("ReflectionsMinLimit", domain_src, "domain/mood.go"),
        _go_int_const("ReflectionsMaxLimit", domain_src, "domain/mood.go"),
    )

    go_src = _go(HTTPAPI / "reflections_list.go")
    assert _json_field_order("reflectionPayload", go_src) == list(
        ReflectionResponse.model_fields
    )


def test_batch5_groups_are_mask_free_and_purely_read():
    """两组用例的掩码计数必须恒为 0，且组内全是 200——这两件合起来才叫「纯读逐字比」。

    前三批靠四条掩码正则（uuid / 推荐码 / created_at / occurred_at）把易变值折成占位串；
    批 5 三条端点**一个都不需要**：id 由夹具钉死、没有生成码、created_at 由播种时刻决定。
    ⇒ 一旦出现非零计数，说明有人开始用正则掩盖真实差异，那这组绿灯不证明任何事。
    全 200 的另一面：400 面（days/limit 越界、类型错）全在 validation 组，
    那 15 条批 5 用例走的是「零库读」支（夹具语言子依赖先短路），两侧同形。
    """
    for group, want_cases in (("mood", 40), ("reflection", 13)):
        cases = _cases(group)
        assert len(cases) >= want_cases, f"{group} 组只剩 {len(cases)} 条，覆盖面在缩"
        for c in cases:
            for field in ("id_masks", "code_masks", "created_at_masks"):
                assert c[field] == 0, f"{c['name']} 的 {field}={c[field]}：批 5 不许有掩码"
            assert c["status"] == 200, f"{c['name']} 不是 200：4xx 面该进 validation 组"

    # 绝对播种与相对播种互斥：同一行既给 days_ago 又给 created_at 时，
    # Go 侧的 seeding 会先按绝对写、再由相对分支覆盖，边界行的位置就此不确定。
    rows = _fixture()["seed"]["mood_checkins"]
    both = [r["id"] for r in rows if "days_ago" in r and "created_at" in r]
    assert not both, f"这些行同时给了两种时刻：{both}"
    assert any("created_at" in r for r in rows), "绝对播种通道没了：trend 的边界格就无法钉死"
    assert any("days_ago" in r for r in rows), "相对播种通道没了：批 1 的窗口对比格会翻边"
    assert all(r["id"].startswith("n8b1-") for r in rows), "播种行的 id 前缀漂了，清理 SQL 会漏删"
    growth = _fixture()["seed"]["growth_records"]
    assert growth and all(r["user_id"].startswith("n8b1-") for r in growth)
    # D-A 的观测点自己也要有闸：夹具里必须**留着**「同一用户、同一 created_at」的两行，
    # 否则次级排序键又退回只有注释、没有字节的状态。
    instants = [(r["user_id"], r["created_at"]) for r in growth if r["created_at"]]
    same_instant = {k for k in instants if instants.count(k) > 1}
    assert same_instant, (
        "夹具里没有任何同一用户、同一时刻的两行 growth_records："
        "refl-tie 那一格被删了，两侧 ORDER BY 的同形就此失去唯一的证人"
    )
    # M-G 把同一条判据扩到 mood_checkins：补次级键之前夹具刻意「每行不同瞬」来绕开这条缺陷，
    # 补了之后必须**留着**同刻行，否则 mood 那两条的 id 键又回到只有注释、没有字节。
    # 更关键的是**同刻组要有三行**：两行时数据库没有次级键给出的序要么是插入序要么是它的
    # 逆序，而 `id DESC` 恰好会命中其中一支（2026-10-15 实测就是命中了 ⇒ 那条变异打不红）。
    # 三行且把 id 最小的那行放在中间插，两支才都不等于主键序 ⇒ 「撤掉次级键」必翻字节。
    # 这一条判据在这里**直接由夹具的播种序算出来**，不靠变异撞运气。
    mood_rows = _fixture()["seed"]["mood_checkins"]
    by_user: dict[str, dict[str, list[str]]] = {}
    for r in mood_rows:
        if r["user_id"].endswith(("mood-h-tie", "mood-t-tie")):
            by_user.setdefault(r["user_id"], {}).setdefault(r["created_at"], []).append(r["id"])
    assert sorted(by_user) == ["n8b1-mood-h-tie", "n8b1-mood-t-tie"], (
        f"同刻组用户不全（实得 {sorted(by_user)}）：证人被改名就没人在守这两条 ORDER BY"
    )
    for user, instants in sorted(by_user.items()):
        tie_instants = {k: v for k, v in instants.items() if len(v) > 1}
        assert len(tie_instants) == 1, (
            f"{user} 的同刻组数量是 {len(tie_instants)}（全部时刻 {dict(instants)}）："
            "mood-h-tie／mood-t-tie 各只有**一组**同瞬行，这组是这两条 ORDER BY 唯一的字节证人"
        )
        tie_at, ids = next(iter(tie_instants.items()))
        assert len(ids) == 3, (
            f"{user} 的同刻组有 {len(ids)} 行，不是 3 行：两行时数据库给的序（插入序或逆插入序）"
            "必有一支与 id 序重合，撤掉次级键就不翻字节 ⇒ 空转"
        )
        # mood-h-tie 的第四行是「次级键不越权」证人：时刻必须**更早**，
        # 否则它挤进同刻组或跑到组后面，证的就是 LIMIT 而不是 tiebreak。
        for at, others in instants.items():
            if at != tie_at:
                assert at < tie_at, f"{user} 的额外行 {at} 不比同刻组 {tie_at} 早"
        suffixes = ["-".join(i.rsplit("-", 2)[1:]) for i in ids]
        assert sorted(suffixes) == ["tie-a", "tie-m", "tie-z"], (
            f"{user} 的同刻组主键后缀是 {suffixes}，不是 tie-a/tie-m/tie-z："
            "改回播种序号＝三支同序，撤掉次级键也全绿，那格就此空转"
        )
        insertion = list(ids)                       # 夹具数组序＝真插入序（录侧与 Go 回放同序播种）
        for name, order in (("升", sorted(ids)), ("降", sorted(ids, reverse=True))):
            assert order not in (insertion, insertion[::-1]), (
                f"{user} 的同刻组按 id {name}序（{order}）与插入序或其逆序重合："
                "数据库没有次级键时给的正是这一支，撤键不翻字节 ⇒ 观测点空转"
            )


def test_created_at_bytes_are_z_shaped_because_these_routes_have_models():
    """created_at 的字节形（`Z` 还是 `+00:00`）由「这条路由的 data 是不是 pydantic 模型」决定。

    批 3/批 4 是 `+00:00`（handler 直接 `success(dict)`，落 `jsonable_encoder`→`isoformat()`）；
    批 5 三条的 data 全是模型（MoodHistoryResponse / MoodTrendResponse / List[ReflectionResponse]），
    走 pydantic-core 的 json mode ⇒ `Z`。这条分界写在 Go 侧注释里，但它必须可被机器反驳：
    这里拿夹具的真实字节比，而不是拿注释。
    """
    dated = [c for c in _cases("mood") + _cases("reflection") if '"created_at"' in c["response_text"]]
    assert dated, "两组里没有任何带 created_at 的用例：这条分界没东西可证"
    stamps = re.findall(r'"created_at":\s*"([^"]+)"', "".join(c["response_text"] for c in dated))
    assert stamps, "response_text 里有 created_at 字样却没解析出值"
    assert all(s.endswith("Z") for s in stamps), (
        f"这批的 created_at 里出现了非 Z 形：{[s for s in stamps if not s.endswith('Z')][:3]}"
    )
    assert "+00:00" not in "".join(stamps)

    # 比的是**调用**而不是标识符：三个文件的包注释都在解释这条分界，
    # 只出现 `pyISO` 字样不代表它真在用（trend 的响应里根本没有 datetime 列）。
    for rel in ("mood_history.go", "reflections_list.go"):
        src = _go(HTTPAPI / rel)
        assert "pyISO(" in src, f"{rel} 不再调用 pyISO：与夹具字节的 Z 形对不上"
        assert "pyISOOffset(" not in src, f"{rel} 混进了 +00:00 那一支"
    # trend 的响应里没有 datetime 字段，所以它不参与这条分界——反过来钉一次，
    # 免得将来给它加了列却忘了选哪一支。
    assert "pyISO(" not in _go(HTTPAPI / "mood_trend.go")


def test_only_the_two_cut_gets_are_deprecated():
    """方法级的废弃面：path 级的那条闸（test_n8_batch2_contract.py）看不出 POST 被误标。

    标废弃＝告诉客户端「这条路要没了」。本批只有两条 GET 的对外形状归 Go，
    POST /mood/checkin（LLM＋配额＋写）与 /api/v1/reflections 的 GET/POST 都还整条由
    Python 回答，因此它们**不许**带 deprecated；同时路由本体一律留着（回滚＝改灰度）。
    """
    paths = app.openapi()["paths"]
    marked = {
        (method.upper(), p)
        for p, item in paths.items()
        for method, op in item.items()
        if isinstance(op, dict) and op.get("deprecated")
    }
    assert {
        ("GET", "/api/v1/mood/history"),
        ("GET", "/api/v1/mood/trend"),
    } <= marked
    for op in (("POST", "/api/v1/mood/checkin"), ("GET", "/api/v1/reflections"),
               ("POST", "/api/v1/reflections"), ("POST", "/api/v1/reflections/generate")):
        assert op not in marked, f"{op} 被标了废弃但 Go 还没接管它的入口"
    registered = {
        (method.upper(), p) for p, item in paths.items() for method in item
    }
    for op in (("GET", "/api/v1/mood/history"), ("GET", "/api/v1/mood/trend"),
               ("POST", "/api/v1/mood/checkin"), ("GET", "/api/v1/reflections")):
        assert op in registered, f"{op} 的路由本体被删了：灰度回滚没有落点"


def test_cutover_keys_exist_only_in_go_and_manifests_and_both_are_zero():
    """灰度键的三面：Python 不读、ConfigMap 给 0、Go 侧 config＋gate map 都接上。

    ↳ 第二面在本批多了一条**不对称**（决策点 D-10）：`CLOSEN_CUTOVER_REFLECTIONS` 有键、
    有 Go 读取，但**没有** Ingress path——Ingress 的 path 匹配不看 HTTP 方法，
    而 `/api/v1/reflections` 这一个 path 上挂着 GET 与 POST 两个操作，
    把 GET 搬走就会让 POST 打在一台没注册它的 mux 上（405）。
    键先声明＝放量那一次不需要再发版；没有 path＝它现在调到多少都不放量，
    失败方向是「没切」。这条不对称必须被机器看见，所以这里点名断言。
    """
    # 口径同 test_n8_batch2_contract.py::test_cutover_switch_exists_in_go_and_manifest_not_python：
    # 「Python 不读」说的是**没有读取站点**，注释里提到键名是必需的（ routers/mood.py 的
    # 废弃说明就得写清楚回滚改的是哪个键）。所以这里扫的是 getenv/environ，
    # 外加 settings 模块本体不许出现这个前缀。
    assert "CLOSEN_CUTOVER" not in (ROOT / "backend" / "config.py").read_text(encoding="utf-8")
    readers = [
        f"{p.relative_to(ROOT)}:{no}: {line.strip()}"
        for p in ROOT.glob("backend/**/*.py")
        if "tests" not in p.parts
        for no, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "CLOSEN_CUTOVER" in line
        and re.search(r"\b(os\.environ|os\.getenv|getenv)\b", line)
    ]
    assert not readers, f"Python 侧读了 Go 的灰度开关：{readers}"

    cm = _manifest("ConfigMap")["data"]
    assert cm["CLOSEN_CUTOVER_MOOD"] == "0"
    assert cm["CLOSEN_CUTOVER_REFLECTIONS"] == "0"

    cfg = _go(GO / "internal" / "config" / "config.go")
    for key in ("CLOSEN_CUTOVER_MOOD", "CLOSEN_CUTOVER_REFLECTIONS"):
        assert f'envInt("{key}", 0)' in cfg, f"config.go 少了 {key} 的读取"
        assert f'"{key}":' in cfg, f"config.go 的范围校验表少了 {key}（越界必须拒绝启动）"
    main = _go(GO / "cmd" / "go-api" / "main.go")
    for domain_key in ('"mood":', '"reflections":'):
        assert domain_key in main, f"main.go 的 gate map 少了 {domain_key}：这个键不会生效"

    paths = {p["path"]: p["pathType"] for p in _go_api_paths()}
    assert paths.get("/api/v1/mood/history") == "Exact", (
        "/mood/history 不是端点级 Exact 或没指 go-api：同域还有一条 POST /mood/checkin 不切"
    )
    assert paths.get("/api/v1/mood/trend") == "Exact"
    assert paths.get("/api/v1/reflections") is None, (
        "D-10 被推翻：有人给 /api/v1/reflections 上了 path，却没带上同一个 path 的 POST"
        "（Ingress 的 path 匹配不看方法，Go 未注册 POST ⇒ 405）"
    )


def test_recorder_pins_the_mood_clock_to_the_real_module():
    """录制侧钉 mood 仓库的 utc_now，而且必须钉在**真模块**上——这条是本批的字节基准。

    2026-10-15 实测到的空转：`backend/repositories/__init__.py:26` 有
    `from backend.repositories.mood_repository import ... mood_repository`，这把包属性
    `backend.repositories.mood_repository` 从**子模块**换成了 **MoodRepository 单例实例**。
    于是 `from backend.repositories import mood_repository` 拿到的是实例，
    `实例.utc_now = lambda: ANCHOR` 只是给对象挂了个全局永远读不到的属性——
    时钟看着钉了、其实没钉，`count_by_day` 的窗口仍按真实当下算。
    批 2–4 全绿是巧合：它们的判据离边界够远。本批的 `mood-t-edge` 里有一行**正好压在
    14 天窗口边界上**（`created_at == ANCHOR − 14*86400`），录制与回放之间只要差一秒，
    这一行就在窗口内外之间换边 ⇒ trend 的 labels/values 少一格 ⇒ 字节差。
    ⇒ 冻钟要有效，得同时满足三件事，下面逐件钉：取到真模块（importlib 而不是包属性）、
    赋值目标写成 `mood_module.utc_now`（行首，注释掉的不算）、钉完自证一次。
    """
    script = (ROOT / "scripts" / "gen_n8_contract_goldens.py").read_text(encoding="utf-8")

    # 取模块那一支：必须是 `_module(...)`（走 sys.modules），不许退回包属性查找。
    assert 'mood_module = _module("backend.repositories.mood_repository")' in script, (
        "录制侧不再用 _module() 取 mood 仓库模块：包再导出会把这个名字遮蔽成单例实例"
    )
    assert not re.search(
        r"^\s*from backend\.repositories import[^\n]*\bmood_repository\b", script, re.M
    ), "录制侧用 from backend.repositories import 取模块——拿到的会是再导出的实例"

    # 冻钟那一行：行首匹配，口径同批 2 的 curriculum 闸与批 3 的 journey 闸。
    assert re.search(r"^\s*mood_module\.utc_now\s*=", script, re.M), (
        "录制侧没冻 mood 仓库的 utc_now：trend 的 14 天边界行会随录制时刻换边"
    )

    # 自证：钉完必须回头读一次，否则「补丁落在读不到的名字上」这种空转再次发生时就只有夹具知道。
    assert "if mod.utc_now() != ANCHOR:" in script, (
        "freeze_clock 的钉后自证被撤了：空转冻钟不会再有任何人报警"
    )

    # 边界行的存在性：上面四条守的是机制，这一条守的是「机制真有观测点」——
    # 若有人把 mood-t-edge 的种子挪离边界，冻钟就重新变成不可证，得先看见这件事。
    edge = [r for r in _fixture()["seed"]["mood_checkins"]
            if r["user_id"] == "n8b1-mood-t-edge"]
    assert len(edge) == 3, f"mood-t-edge 应有 3 行边界种子，实得 {len(edge)}"
    assert any(r["created_at"].endswith("+00:00") for r in edge), (
        "边界行不再是绝对播种：窗口比较会跟着真实钟漂"
    )
