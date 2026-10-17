"""批1 G1 调度覆盖闸：防「端点写好了、调度那一刀没人落」再复发。

被验对象两个，都现场推导、不抄名单：
- `vercel.json` 的 `crons`（现役生产的调度源；Vercel Cron 只发 GET），
- `backend.main` 里挂在 `/api/v1/cron/` 下的真实路由。

根因（2026-10-17 实测）：`dispatch_journey_events` 的唯一调用方是
`GET /api/v1/cron/journey`，而清单里 11 条 cron 恰好漏了它 ⇒ 代码全对、
从调度面不可达，第一周剧本 D0–D7 永不出货，pending 事件永不清零
（连带把每日主动消息也一起按住了）。这比「没实现」更难发现，所以闸立在这里。

判据：
1. 每个 GET `/cron/*` 必须被排程，或在 `EXEMPT` 里带理由登记；
2. `vercel.json` 每条 cron 的 path 必须是真实存在的 GET 路由（改名后静默 404）；
3. 端点 docstring 宣称的节律（「Vercel Cron 每小时/每日/每周…」）必须与 cron 表达式同形。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from backend.main import app

ROOT = Path(__file__).resolve().parents[2]
VERCEL_JSON = ROOT / "vercel.json"
CRON_PREFIX = "/api/v1/cron/"

# 不排程的端点必须在这里带理由登记（理由为空即打红）。
# 两条口径分开：POST 收不了 Vercel Cron 的 GET；GET 不排程就是本批那类漏项。
EXEMPT: dict[str, str] = {
    "POST /api/v1/cron/process-user": "Vercel Cron 只发 GET；本端点是 QStash 分块回调，调度在 cron_service 投递侧",
    "POST /api/v1/cron/follow-up": "Vercel Cron 只发 GET；由 cron_service.register_follow_up_schedule 经 QStash 每日调度",
    "POST /api/v1/cron/purge-user": "Vercel Cron 只发 GET；注销后由 QStash 异步回调，兜底见 GET /cron/purge-deleted-users",
    "GET /api/v1/cron/health": "纯保热、无生产副作用；未排程（settings.health_check_interval 无消费方，归 tasks.md T 桶）",
}

CRON_FIELD_RE = re.compile(r"^\*(/\d+)?$|^\d+(-\d+)?(/\d+)?(,\d+(-\d+)?(/\d+)?)*$")
CADENCE_WORDS = ("每小时", "每日", "每周")
WEEKLY_RE = re.compile(r"每周")
# 「Vercel Cron 每小时触发」＝宣称；「Vercel Cron 只发 GET」＝解释，不算宣称。
CRON_CLAIM_RE = re.compile(r"Vercel Cron[^\n。】]{0,20}?(?:触发|调用)")


def _flat_routes() -> list[tuple[str, set[str], str]]:
    """把 app 路由表摊平成 (完整 path, methods, endpoint docstring)。

    FastAPI 0.141 把 include_router 包成 `_IncludedRouter`，`route.path` 只剩
    router 内的相对路径，前缀在 `include_context.prefix` 里——不拼回来就一条
    `/cron/*` 也找不到（闸会空转到全绿，所以这里显式拼且下面断言非空）。
    """
    out: list[tuple[str, set[str], str]] = []

    def walk(routes, prefix: str) -> None:
        for route in routes:
            context = getattr(route, "include_context", None)
            nested = getattr(route, "original_router", None) or getattr(route, "routes", None)
            children = getattr(nested, "routes", nested) if nested is not None else None
            if context is not None or (children is not None and not hasattr(route, "methods")):
                own = getattr(context, "prefix", "") or ""
                walk(list(children or []), prefix + own)
                continue
            path = getattr(route, "path", "")
            methods = getattr(route, "methods", None)
            if path and methods:
                out.append((prefix + path, {m.upper() for m in methods}, route.endpoint.__doc__ or ""))

    walk(app.routes, "")
    return out


def cron_entries() -> list[tuple[str, set[str], str]]:
    return [e for e in _flat_routes() if e[0].startswith(CRON_PREFIX)]


def cron_routes() -> dict[str, set[str]]:
    """从 app 路由表推导 `/cron/*` 的 path → methods。"""
    out = {path: methods for path, methods, _doc in cron_entries()}
    assert out, "路由表里一个 /cron/* 端点都没有，闸本身失效"
    return out


def endpoint_docs() -> dict[str, str]:
    """path → 首个处理函数的 docstring（节律宣称的证据源）。"""
    return {path: doc for path, _methods, doc in cron_entries()}


def crons() -> list[dict]:
    manifest = json.loads(VERCEL_JSON.read_text(encoding="utf-8"))
    entries = manifest.get("crons")
    assert isinstance(entries, list), "vercel.json 里没有 crons 数组"
    return entries


def schedule_of(path: str) -> list[str]:
    return [e["schedule"] for e in crons() if e.get("path") == path]


def fields(schedule: str) -> list[str]:
    parts = schedule.split()
    assert len(parts) == 5, f"cron 表达式必须 5 段，实际 {schedule!r}"
    return parts


def claimed_cadence(doc: str) -> str | None:
    """取「Vercel Cron」之后出现的第一个节律词。

    只看这个词之后：`每日主动消息生成（Vercel Cron 触发）` 里的「每日」是业务语义
    （按最佳发送小时分桶 ⇒ 确实每小时跑一次），不是调度宣称，不能拿它打红。
    """
    marker = doc.find("Vercel Cron")
    if marker < 0:
        return None
    tail = doc[marker + len("Vercel Cron") :]
    hits = [(tail.find(word), word) for word in CADENCE_WORDS if word in tail]
    if not hits:
        return None
    return min(hits)[1]


def test_route_derivation_covers_the_whole_cron_router():
    """闸自身的完整性：摊平路由必须数得齐 cron.py 里的装饰器。

    少一条就是「某类路由从推导里掉了」——那种情况下上面几条判据会全绿空转。
    """
    source = (ROOT / "backend" / "routers" / "cron.py").read_text(encoding="utf-8")
    decorators = len(re.findall(r'^@router\.(?:get|post)\("/cron', source, re.M))
    derived = len(cron_entries())
    assert derived == decorators, f"装饰器 {decorators} 条、推导只拿到 {derived} 条，路由摊平逻辑漏了"


def test_every_get_cron_route_is_scheduled_or_exempt():
    """判据 1：GET 定时端点要么排程、要么带理由豁免——journey 当初就漏在这格。"""
    unscheduled = []
    for path, methods in sorted(cron_routes().items()):
        if "GET" not in methods:
            continue
        key = f"GET {path}"
        if key in EXEMPT:
            continue
        if not schedule_of(path):
            unscheduled.append(path)
    assert not unscheduled, (
        "以下 GET 定时端点没有任何调度源，等于从生产不可达："
        f"{unscheduled}；确需不排程的写进 EXEMPT 并说明理由"
    )


def test_scheduled_paths_are_live_get_routes():
    """判据 2：清单指向不存在的路径 = 每小时 404，而 cron 侧只会静默重试。"""
    routes = cron_routes()
    bad = []
    for entry in crons():
        path = entry.get("path", "")
        if "GET" not in routes.get(path, set()):
            bad.append(f"{path} -> {entry.get('schedule')!r}")
    assert not bad, f"vercel.json 里排程了不存在的 GET 路由：{bad}"


def test_cron_expressions_are_valid():
    """判据 2 附：表达式逐段合法且无重复 path（重复的那条 Vercel 会照单全收）。"""
    seen: list[str] = []
    for entry in crons():
        path = entry.get("path", "")
        assert path.startswith(CRON_PREFIX), f"cron path 越界：{path}"
        assert path not in seen, f"vercel.json 重复排程 {path}"
        seen.append(path)
        for name, value in zip(
            ("minute", "hour", "day-of-month", "month", "day-of-week"),
            fields(entry.get("schedule", "")),
        ):
            assert CRON_FIELD_RE.match(value), f"{path} 的 {name} 段非法：{value!r}"


def claims_cron_trigger(doc: str) -> bool:
    """docstring 是否在宣称「本端点由 Vercel Cron 触发/调用」。

    必须跟触发类动词才算宣称：`Vercel Cron 只发 GET，所以走 QStash` 这种解释性
    提法不该把豁免条目打红。
    """
    return bool(CRON_CLAIM_RE.search(doc))


def test_docstring_cadence_claim_matches_manifest():
    """判据 3：docstring 说「每小时」而清单是每天一次，就是本批那类谎话登记。"""
    mismatches = []
    for path, doc in sorted(endpoint_docs().items()):
        if not claims_cron_trigger(doc):
            continue
        schedules = schedule_of(path)
        if not schedules:
            mismatches.append(f"{path} 宣称被 Vercel Cron 触发，清单里却没排程")
            continue
        claim = claimed_cadence(doc)
        if claim is None:
            continue
        for schedule in schedules:
            minute, hour, _dom, _month, dow = fields(schedule)
            fixed_minute = minute.isdigit()
            if claim == "每小时":
                ok = fixed_minute and hour == "*" and dow == "*"
            elif claim == "每日":
                ok = fixed_minute and hour.isdigit() and dow == "*"
            else:
                ok = fixed_minute and hour.isdigit() and dow.isdigit()
            if not ok:
                mismatches.append(f"{path} 宣称「{claim}」，清单却是 {schedule!r}")
    assert not mismatches, "调度宣称与实际排程不符：" + "；".join(mismatches)


def test_weekly_endpoints_are_actually_weekly():
    """docstring 说「每周…」的端点必须落在周字段固定的表达式上（每日跑会重复生成）。"""
    routes = cron_routes()
    offenders = []
    for path, doc in sorted(endpoint_docs().items()):
        if "GET" not in routes[path] or not WEEKLY_RE.search(doc):
            continue
        for schedule in schedule_of(path):
            if fields(schedule)[4] == "*":
                offenders.append(f"{path} 说每周、清单却是 {schedule!r}")
    assert not offenders, "周节律端点被排成了每天：" + "；".join(offenders)


def test_exempt_table_is_not_rotten():
    """豁免表也要能失效：端点被删/改名，或理由写成空，都算这条登记作废。"""
    routes = cron_routes()
    stale = []
    for key, reason in EXEMPT.items():
        method, path = key.split(" ", 1)
        assert reason.strip(), f"{key} 的豁免没写理由"
        if method not in routes.get(path, set()):
            stale.append(key)
    assert not stale, f"EXEMPT 登记了不存在的路由：{stale}"


def test_journey_dispatch_is_scheduled_hourly():
    """G1 的回归证人：剧本调度必须在清单里，且是每小时一次。

    漏掉这一格时 D0–D7 永不出货，而 `has_pending_due` 一直为真会把每日主动
    消息一起按住——症状出现在别的端点上，最难归因，所以单独立一条。
    """
    schedules = schedule_of("/api/v1/cron/journey")
    assert len(schedules) == 1, f"剧本调度应恰好一条，实际 {schedules}"
    minute, hour, _dom, _month, dow = fields(schedules[0])
    assert minute.isdigit() and hour == "*" and dow == "*", (
        f"剧本调度必须是每小时一次（端点 docstring 就这么宣称），实际 {schedules[0]!r}"
    )
