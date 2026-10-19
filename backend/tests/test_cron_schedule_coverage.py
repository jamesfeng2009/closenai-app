"""调度覆盖闸：防「端点写好了、调度那一刀没人落」再复发（2026-10-18 起唯一调度源＝`deploy/schedule/crontab`）。

被验对象两个，都现场推导、不抄名单：
- `deploy/schedule/crontab` 的排程行（T0-02 起这是唯一调度声明源；
  `vercel.json` 同日退役——批 1 时代它才是现役源，闸若不改靶就会继续「保护」一个不存在的文件），
- `backend.main` 里挂在 `/api/v1/cron/` 下的真实路由。

根因（2026-10-17 立项时实测）：`dispatch_journey_events` 的唯一调用方是
`GET /api/v1/cron/journey`，而当时清单恰好漏了它 ⇒ 代码全对、从调度面不可达，
第一周剧本 D0–D7 永不出货（连带把每日主动消息也按住了）。迁移到宿主 crontab 时
这条形状原样保留：15 条排程（7 条整点每小时、4 条按日、2 条按周、2 条 */5——
/cron/health 保热与 T1-01 的 /cron/export-jobs 队列认领；T1-04 的 /cron/delivery-audit
是每日那条 0 4 * * *），
3 条 POST 是 QStash 回调、不排程、带理由豁免。

判据：
1. 每个 GET `/cron/*` 必须被排程，或在 `EXEMPT` 里带理由登记；
2. crontab 每行必须能打「5 字段＋curl 本机 URL」，path 必须是真实存在的 GET 路由，
   且 `vercel.json` 不许复活（双源＝同一条消息可能被触发两次）；
3. 端点 docstring 宣称的节律（「宿主 crontab 每小时/每日/每周/每 5 分钟…」）
   必须与 crontab 表达式同形。
"""
from __future__ import annotations

import re
from pathlib import Path

from backend.main import app

ROOT = Path(__file__).resolve().parents[2]
CRONTAB = ROOT / "deploy" / "schedule" / "crontab"
CRON_PREFIX = "/api/v1/cron/"

# 不排程的端点必须在这里带理由登记（理由为空即打红）。
# 三条 POST 的共同形状：QStash 公网回调（验签 Upstash-Signature），调度在投递侧，
# 不是宿主 crontab——`/cron/health` 从 T0-02 起已被排程，不再是豁免条目。
EXEMPT: dict[str, str] = {
    "POST /api/v1/cron/process-user": "宿主 crontab 不排 POST；本端点是 QStash 分块回调（验签），调度在 cron_service 投递侧",
    "POST /api/v1/cron/follow-up": "宿主 crontab 不排 POST；由 cron_service.register_follow_up_schedule 经 QStash 每日调度（验签）",
    "POST /api/v1/cron/purge-user": "宿主 crontab 不排 POST；注销后由 QStash 异步回调（验签），兜底见 GET /cron/purge-deleted-users",
}

CRON_FIELD_RE = re.compile(r"^\*(/\d+)?$|^\d+(-\d+)?(/\d+)?(,\d+(-\d+)?(/\d+)?)*$")
CADENCE_WORDS = ("每小时", "每日", "每周", "每 5 分钟")
WEEKLY_RE = re.compile(r"每周")
# 「宿主 crontab 每小时触发」＝宣称；「三条 POST 不在宿主 crontab 里」＝解释，不算宣称。
CRON_CLAIM_RE = re.compile(r"crontab[^\n。】]{0,20}?(?:触发|调用)")
ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
CMD_URL_RE = re.compile(r"http://127\.0\.0\.1:8000(/[^\s]+)")


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
    """解析宿主 crontab：跳过注释/env 赋值行，命令行取前 5 字段＋curl 的本机 path。

    解析形状本身就是判据——「5 字段＋一个敲得进 `127.0.0.1:8000/api/v1/cron/*` 的 URL」，
    写成别的形式（比如 URL 用变量拼）闸就瞎了，所以这里 assert 而不是正则宽容匹配。
    """
    entries: list[dict] = []
    for raw in CRONTAB.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ENV_ASSIGN_RE.match(line):
            continue
        parts = line.split(None, 5)
        assert len(parts) >= 6, f"crontab 行不是「5 字段＋命令」：{raw!r}"
        match = CMD_URL_RE.search(line)
        assert match, f"crontab 命令行里敲不出本机 API URL：{raw!r}"
        entries.append({"path": match.group(1), "schedule": " ".join(parts[:5])})
    assert entries, "deploy/schedule/crontab 一条排程都没有，闸空转"
    return entries


def schedule_of(path: str) -> list[str]:
    return [e["schedule"] for e in crons() if e.get("path") == path]


def fields(schedule: str) -> list[str]:
    parts = schedule.split()
    assert len(parts) == 5, f"cron 表达式必须 5 段，实际 {schedule!r}"
    return parts


def claimed_cadence(doc: str) -> str | None:
    """取「crontab」之后出现的第一个节律词。

    只看这个词之后：`每日主动消息生成（宿主 crontab 每小时触发）` 里的「每日」是业务语义
    （按最佳发送小时分桶 ⇒ 确实每小时跑一次），不是调度宣称，不能拿它打红。
    """
    marker = doc.find("crontab")
    if marker < 0:
        return None
    tail = doc[marker + len("crontab") :]
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


def test_vercel_manifest_stays_retired():
    """单一调度源：`vercel.json` 若复活（哪怕带一条 cron），同一条消息就有双触发形状。

    T0-02（B-02 默认删除）之后仓库里不该再有这份「第二调度声明」；
    真要回滚 Vercel 排程，必须连本闸与 crontab 一起改判，不许悄悄并存两份清单。
    """
    assert not (ROOT / "vercel.json").exists(), (
        "vercel.json 又回到仓库根——它已被 T0-02 退役，现役唯一调度源是 "
        "deploy/schedule/crontab；双源并存＝消息可能被触发两次"
    )


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
    """判据 2：清单指向不存在的路径 = 每小时静默 404，cron 侧只会看到非零退出码。"""
    routes = cron_routes()
    bad = []
    for entry in crons():
        path = entry.get("path", "")
        if "GET" not in routes.get(path, set()):
            bad.append(f"{path} -> {entry.get('schedule')!r}")
    assert not bad, f"crontab 里排程了不存在的 GET 路由：{bad}"


def test_cron_expressions_are_valid():
    """判据 2 附：表达式逐段合法、命令都带 CRON_SECRET、无重复 path。"""
    seen: list[str] = []
    raw_lines = CRONTAB.read_text(encoding="utf-8").splitlines()
    for entry in crons():
        path = entry.get("path", "")
        assert path.startswith(CRON_PREFIX), f"cron path 越界：{path}"
        assert path not in seen, f"crontab 重复排程 {path}"
        seen.append(path)
        for name, value in zip(
            ("minute", "hour", "day-of-month", "month", "day-of-week"),
            fields(entry.get("schedule", "")),
        ):
            assert CRON_FIELD_RE.match(value), f"{path} 的 {name} 段非法：{value!r}"
    command_lines = [
        line
        for line in raw_lines
        if line.strip()
        and not line.strip().startswith("#")
        and not ENV_ASSIGN_RE.match(line.strip())
    ]
    assert command_lines and all("$CRON_SECRET" in line for line in command_lines), (
        "crontab 命令行必须都从宿主 env 读 CRON_SECRET（密钥不写进 crontab 本身）"
    )


def claims_cron_trigger(doc: str) -> bool:
    """docstring 是否在宣称「本端点由宿主 crontab 触发/调用」。

    必须跟触发类动词才算宣称：`宿主 crontab 不排 POST，所以走 QStash` 这种解释性
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
            mismatches.append(f"{path} 宣称被宿主 crontab 触发，清单里却没排程")
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
            elif claim == "每 5 分钟":
                ok = minute == "*/5"
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
