"""N8 批 1（analytics + discovery）的跨语言逐字闸。

被验对象：`go-core/internal/{httpapi,domain,config}` 里从 Python 移植过来的**常量与表**，
以及 `contracts/openapi.json` 里这两个域的 deprecated 标记。

为什么单开一份而不塞进 test_contract_freeze.py：冻结表守的是「路由集合没变」，
而这里守的是「Go 侧把口径抄错了没」——这两类漂移的修法完全不同：
前者改路由要重跑导出脚本，后者只要任一侧改一个数字就会让逐字节契约测（
`go-core/testdata/n8_contract.json` 回放）在**下一条**用例上红，但那口红是 Go 的测试，
Python 的 CI 里看不到。本文件把它提前一格搬到 Python 侧，改的人当场知道是哪一侧。

口径来源（每条都点名到行）：
- supported_languages        ← backend/config.py:77  ↔  httpapi/batch1.go::SupportedLanguages
- ALLOWED_PROPERTY_KEYS      ← analytics_service.py:33 ↔  domain/analytics.go::AllowedPropertyKeys
- events max_length=100      ← models/schemas.py:689  ↔  httpapi/analytics_batch.go::maxAnalyticsEvents
- 7/14 天窗口                ← discovery_service.py:47-49 ↔ httpapi/discovery_daily.go::discoveryWeek*
- posthog_host 默认值         ← config.py:160          ↔  config/config.go 的 envString 默认
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from backend.config import settings
from backend.main import app
from backend.services.analytics_service import ALLOWED_PROPERTY_KEYS

ROOT = Path(__file__).resolve().parents[2]
GO = ROOT / "go-core"
FIXTURE = GO / "testdata" / "n8_contract.json"

BATCH1_DOMAINS = ("analytics", "discovery")
BATCH1_PATHS = {"/api/v1/analytics/batch", "/api/v1/discovery/daily"}
# 夹具里的分组：批 2 的 curriculum 组自带一套判据，不混进本文件。
BATCH1_GROUPS = ("validation", "seeded")


def _go(rel: str) -> str:
    return (GO / rel).read_text(encoding="utf-8")


def _string_slice(name: str, source: str) -> list[str]:
    """取 Go 的 `var <name> = []string{...}` / `map[string]bool{...}` 里的键字面量。"""
    block = re.search(rf"{name}\s*=\s*(?:\[\]string|map\[string\]bool)\{{(.*?)\}}",
                      source, re.S)
    assert block, f"Go 侧找不到 {name} 的字面量——改名或换写法了，闸要跟着改"
    keys = re.findall(r'"((?:[^"\\]|\\.)*)"', block.group(1))
    assert keys, f"{name} 解析出空表——正则吃错了范围"
    return keys


def test_only_batch1_paths_are_deprecated():
    """N8 第④步的边界：批 1 这两条**必须**被标上，且路由本体留着。

    「不得提前标废弃未切流的域」这条反向判据不在这里了——批 2 起夹具与 deprecated 集合
    都会长大，逐批改这里等于把闸写死在第一轮；全量集合由
    backend/tests/test_n8_batch2_contract.py::test_deprecated_set_is_exactly_the_cut_domains 守。
    """
    paths = app.openapi()["paths"]
    deprecated = {
        p
        for p, item in paths.items()
        for op in item.values()
        if isinstance(op, dict) and op.get("deprecated")
    }
    assert BATCH1_PATHS <= deprecated, (
        f"deprecated 集合 = {sorted(deprecated)}，缺 {sorted(BATCH1_PATHS - deprecated)}；"
        "＝第④步没做"
    )


def test_deprecated_paths_are_still_routable_for_rollback():
    """回滚＝把灰度改回 0，前提是 Python 还在答这两条路——所以**只标 deprecated，不删路由**。"""
    registered = {
        p for p, item in app.openapi()["paths"].items()
        for method in item
    }
    assert BATCH1_PATHS <= registered, f"路由本体被删了：{sorted(BATCH1_PATHS - registered)}"


def test_supported_languages_match_go_both_directions():
    """两级语言优先查的是同一张表：一侧多一个码，另一侧就会把该语言的文案发错。"""
    go_codes = _string_slice("SupportedLanguages", _go("internal/httpapi/batch1.go"))
    py_codes = list(settings.supported_languages)
    assert set(go_codes) == set(py_codes), (
        f"仅 Go 有={sorted(set(go_codes) - set(py_codes))} "
        f"仅 Python 有={sorted(set(py_codes) - set(go_codes))}"
    )
    assert len(go_codes) == len(set(go_codes)), "Go 侧表里有重复码"
    # 顺序也一致：两侧都按这张表做 membership，顺序不进契约，但漂移通常意味着一侧是手抄的。
    assert go_codes == py_codes, f"顺序漂移：Go={go_codes} Python={py_codes}"


def test_allowed_property_keys_match_go_literal():
    """匿名化白名单是隐私红线：一侧多放行一个键＝给用户数据开第二条出口。"""
    go_keys = _string_slice("AllowedPropertyKeys", _go("internal/domain/analytics.go"))
    assert set(go_keys) == set(ALLOWED_PROPERTY_KEYS), (
        f"仅 Go 有={sorted(set(go_keys) - set(ALLOWED_PROPERTY_KEYS))} "
        f"仅 Python 有={sorted(set(ALLOWED_PROPERTY_KEYS) - set(go_keys))}"
    )
    # 内容类字段必须**不在**表里（这条是白名单存在的理由，防止有人顺手加）。
    for forbidden in ("reflection_text", "memory_content", "message", "mood_emoji"):
        assert forbidden not in ALLOWED_PROPERTY_KEYS, f"{forbidden} 属内容字段，不得出网"
        assert forbidden not in go_keys, f"{forbidden} 在 Go 侧被放行了"


def test_max_events_limit_matches_go():
    """100 这个数同时决定 too_long 的报错文案，抄错就是 400 体逐字不同。"""
    go_src = _go("internal/httpapi/analytics_batch.go")
    match = re.search(r"maxAnalyticsEvents\s*=\s*(\d+)", go_src)
    assert match, "Go 侧找不到 maxAnalyticsEvents 常量"
    field = app.openapi()["components"]["schemas"]["AnalyticsBatchRequest"]["properties"][
        "events"
    ]
    assert field["maxItems"] == int(match.group(1)) == 100, field


def test_discovery_week_windows_match_go():
    """7/14 天窗口两侧必须同一个数：窗口边界落在哪一侧是响应内容本身。"""
    go_src = _go("internal/httpapi/discovery_daily.go")
    windows = {
        int(n)
        for n in re.findall(
            r"discovery(?:Week|Fortnight)\s*=\s*(\d+)\s*\*\s*24\s*\*\s*time\.Hour", go_src
        )
    }
    days = {int(n) for n in re.findall(r"days=(\d+)", _py("services/discovery_service.py"))}
    assert windows == {7, 14} == days, f"Go={sorted(windows)} Python={sorted(days)}"
    cutoff = re.search(r"now\s*-\s*timedelta\(days=(\d+)\)",
                       _py("services/discovery_service.py"))
    assert cutoff and int(cutoff.group(1)) == 7, "cutoff 的 7 天与 Go 的 discoveryWeek 不同源"


def _py(rel: str) -> str:
    return (ROOT / "backend" / rel).read_text(encoding="utf-8")


def test_posthog_host_default_matches_across_languages():
    """两侧打到同一个收件箱：Go 的默认值若落回 posthog-python 的 app.posthog.com，
    灰度期同一批事件会分叉进两个项目。"""
    match = re.search(r'envString\("POSTHOG_HOST",\s*"([^"]+)"\)', _go("internal/config/config.go"))
    assert match, "Go 侧没有 POSTHOG_HOST 的默认值"
    assert match.group(1) == settings.posthog_host, (
        f"Go={match.group(1)} Python={settings.posthog_host}"
    )


def test_go_contract_fixture_covers_both_domains():
    """Go 侧的回放闸不能悄悄变空：每域都要有**成功面**用例，掩码只许登记过的字段。

    只看 batch1 的两组（validation/seeded）：批 2 起夹具多了 curriculum 组，
    那一组的覆盖面由 test_n8_batch2_contract.py 负责，两边各自钉住自己的批。
    掩码白名单也逐批增长（批 3 加了 journey 的 uuid 主键，那条由
    test_n8_batch3_contract.py 钉），所以这里两边都卡：批 1 的 generated_at 必须在里面，
    而整份名单里不许冒出没在任一批准过形的第三样——新名字必须先被某一登记面接住。
    """
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert "generated_at" in fixture["masked_fields"], fixture["masked_fields"]
    assert set(fixture["masked_fields"]) <= {
        # 批 1 generated_at ／批 3 journey 的 uuid 主键
        # ／批 4 惰性生成的推荐码、本次新写 profile 的 created_at
        "generated_at", "journey_event_id", "referral_link", "created_at",
    }, fixture["masked_fields"]
    # validation 组是**跨域共用**的（批 4 往同一组里放了 onboarding 的 26 条 400 面），
    # 所以这一节按路径先收成本批两个域的用例，再数覆盖面——不是「夹具里不许有别的域」。
    cases = [c for c in fixture["cases"]
             if c["group"] in BATCH1_GROUPS
             and any(c["path"].startswith(f"/api/v1/{d}") for d in BATCH1_DOMAINS)]
    by_domain: dict[str, dict[str, int]] = {}
    for case in cases:
        domain = next((d for d in BATCH1_DOMAINS if case["path"].startswith(f"/api/v1/{d}")), None)
        assert domain, f"用例路径不在批 1 两个域里：{case['path']}"
        bucket = by_domain.setdefault(domain, {})
        bucket[case["group"]] = bucket.get(case["group"], 0) + 1
    for domain in BATCH1_DOMAINS:
        assert by_domain.get(domain, {}).get("seeded"), f"{domain} 没有真读库的成功面用例"
    assert by_domain["analytics"].get("validation"), "analytics 的 400 面没进夹具"

    # seeded 组必须**真的**覆盖了 200 与 404 两种形态（discovery 的 require_user 分支）。
    statuses = {c["status"] for c in cases if c["group"] == "seeded"}
    assert {200, 404} <= statuses, statuses
    assert len(cases) >= 110, f"批 1 用例数掉到 {len(cases)}，覆盖面在缩"


def test_fixture_seed_users_cover_the_decisions_they_prove():
    """播种面必须含「同意/不同意/无行/两个窗口都有行」四类，否则逐字节回放只在测同一件事。"""
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    users = fixture["seed"]["users"]
    ids = {u["id"] for u in users}
    assert {"n8b1-consent-on", "n8b1-consent-off", "n8b1-no-rows"} <= ids
    assert any(not u["analytics_consent"] for u in users), "没有未同意的用户⇒丢弃分支没测"
    moods = fixture["seed"]["mood_checkins"]
    per_user: dict[str, list[float]] = {}
    for row in moods:
        per_user.setdefault(row["user_id"], []).append(row["days_ago"])
    # 上周窗口（7<days_ago<=14）必须有行，否则 last_week 分支全空。
    assert any(any(7 < d <= 14 for d in days) for days in per_user.values()), \
        "没有任何用户在上一周窗口里有签到，对比分支没被走到"
    # 距窗口边界至少留 1.5 天：回放比录制晚几小时也不能翻边。
    borderline = [d for days in per_user.values() for d in days
                  if min(abs(d - 7), abs(d - 14)) < 1.5]
    assert not borderline, f"这些行贴着窗口边界，回放时刻一变就会翻边：{sorted(borderline)}"
