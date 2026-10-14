"""Regenerate go-core/testdata/n8_contract.json (N8 切流域的逐字契约基线).

为什么另起一份而不扩 `gen_go_http_goldens.py`：N1 那份只录「校验错误面 + /health」，
全部不碰数据库；N8 的验收判据是「每域切流前后响应契约 diff 为空」，**成功面**（真读
users/mood_checkins/journey_events 的那些域）必须有同一批行、同一次进程内的真实字节做基准，
所以这里带 seed 播种，并把结果写成 Go 侧可直接回放的夹具。

口径：
- 同一个 TEST_DATABASE_URL：Python 侧走 `psycopg_url()`，Go 侧走 `dsn.Postgres()`（N4 方言归一）。
- `cases[].group`：`validation` 的响应不进 store（Go 测不需库），`seeded` 的必须先播种，
  `curriculum` 的除播种外还要把 Go 的时钟钉在夹具的 `recorded_at`。
- volatile 字段（`generated_at`，值＝请求当下的 utc_now()）在两侧都按同一条正则替换成
  `"<generated_at>"` 后再逐字比——其余字节一个都不许掩码，否则「diff 为空」就成了空话。
- 播种行用 `days_ago`（相对当下）而不是绝对时间戳：回放可能比录制晚几分钟到几小时，
  窗口边界（7/14 天）两侧必须落在同一侧，所以每行距边界至少留 1.5 天。
  批 2 的 curriculum 例外：它的响应里有**日历字段**，必须冻时钟＋绝对 scheduled_at，
  详见下面 ANCHOR 的注释。

Run:
    TEST_DATABASE_URL=postgresql://postgres:…@127.0.0.1:55432/closenai \
        .venv/bin/python scripts/gen_n8_contract_goldens.py
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import sys
from datetime import date as dt_date
from datetime import datetime as dt_datetime, time as dt_time
from datetime import timedelta as dt_timedelta
from datetime import timezone as dt_timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TRACE = "00000000-0000-0000-0000-000000000001"
OUT = ROOT / "go-core" / "testdata" / "n8_contract.json"
SEED_PREFIX = "n8b1-"
MASKED = "<generated_at>"
EPOCH_UTC = dt_datetime(1970, 1, 1, tzinfo=dt_timezone.utc)
GENERATED_AT_RE = re.compile(r'"generated_at":"[^"]*"')


def _test_db_url() -> str:
    raw = os.environ.get("TEST_DATABASE_URL", "")
    if not raw:
        raise SystemExit(
            "TEST_DATABASE_URL 未设置——这份夹具的成功面用例必须真读 PG，"
            "缺库时录出来的字节不作数"
        )
    return raw


# 必须在 import backend 之前落定：settings 读 env 一次，engine 在 import 期就建好了。
os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = _test_db_url()

from backend.core.pg_scheme import libpq_url, psycopg_url  # noqa: E402

RAW_TEST_URL = os.environ["TEST_DATABASE_URL"]
os.environ["DATABASE_URL"] = psycopg_url(RAW_TEST_URL)

import psycopg  # noqa: E402
from backend.core.time_utils import local_day_start_utc, safe_zoneinfo  # noqa: E402
from backend.main import app  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

# ── 播种面 ────────────────────────────────────────────────────────────────────
# ANCHOR＝本轮录制**冻结**的当下。
#
# 为什么批 2 要冻时钟而批 1 不用：curriculum 的响应字节里有 week_start、days[].date、
# revealed——三者都由「用户本地今天」推出。录制与回放之间只要跨过本地午夜（更常见的是
# 跨过周一），两侧算出的就是**不同的一周**，逐字比立刻失去意义。于是 curriculum 组：
# Python 侧把 curriculum_service 的 local_today/utc_now 换成 ANCHOR，Go 侧把 Server.now
# 钉成夹具里的 recorded_at，journey_events 播种行写**绝对** scheduled_at。
# mood_checkins 仍按 days_ago 相对回放当下播种：批 1 的响应里没有日历字段，只有 7/14 天
# 窗口，两侧各用**自己那一侧的真实当下**读同一批行即可。
ANCHOR = dt_datetime.now(dt_timezone.utc).replace(microsecond=0)

# 播种用户：slug / analytics_consent / language / rows（距今天数, emoji）
#   ai_consent（批 2 curriculum 的前置门）/ timezone（本地周视图的窗口函数）/ status
USERS: list[dict] = [
    {"slug": "consent-off", "analytics": False, "language": "en"},
    {"slug": "consent-on", "analytics": True, "language": "en"},
    {"slug": "no-rows", "analytics": True, "language": "en"},
    {"slug": "newcomer", "analytics": True, "language": "en", "rows": [(1.5, "😊"), (2.5, "😌")]},
    {"slug": "break", "analytics": True, "language": "en", "rows": [(9.0, "😊"), (11.0, "😌")]},
    {"slug": "freq2", "analytics": True, "language": "en", "rows": [(1.0, "😊"), (2.0, "😌"), (3.0, "😌"),
                           (4.0, "😴"), (9.0, "😌"), (11.0, "😴")]},
    {"slug": "freq3", "analytics": True, "language": "en", "rows": [(1.0, "😊"), (2.0, "😌"), (3.0, "😌"),
                           (4.0, "😴"), (5.0, "😰"), (9.0, "😌"), (11.0, "😴")]},
    {"slug": "up-high", "analytics": True, "language": "en", "rows": [(1.0, "😊"), (2.0, "😊"),
                           (9.0, "😰"), (11.0, "😰")]},
    {"slug": "down-high", "analytics": True, "language": "en", "rows": [(1.0, "😢"), (2.0, "😢"),
                             (9.0, "😊"), (11.0, "😊")]},
    {"slug": "drift-low", "analytics": True, "language": "en", "rows": [(1.0, "😌"), (2.0, "😌"),
                             (9.0, "😌"), (11.0, "😴")]},
    {"slug": "flat", "analytics": True, "language": "en", "rows": [(1.0, "😌"), (2.0, "😴"),
                       (9.0, "😰"), (11.0, "😊")]},
    # 😎 不在 MOOD_MAP 里：走 `MOOD_MAP.get(emoji, (None, 3))[1]` 的默认分值 3。
    {"slug": "unknown-emoji", "analytics": True, "language": "en", "rows": [(1.0, "😎"), (2.0, "😎"),
                                 (9.0, "😌"), (11.0, "😴")]},
    # 首周里程碑 + X-Language: zh 走中文文案；同一用户也证明 header 优先于 users.language=en
    {"slug": "zh-header", "analytics": True, "language": "en", "rows": [(1.5, "😊"), (2.5, "😌")]},
    # users.language=zh 且不带 X-Language ⇒ 走 get_request_language 的第②级（读库偏好）
    {"slug": "zh-db-pref", "analytics": True, "language": "zh", "rows": [(1.5, "😊"), (2.5, "😌")]},
    # users.language=zh 但 header 给了受支持的 es ⇒ header 赢，文案回英文
    {"slug": "es-header-over-zh-db", "analytics": True, "language": "zh", "rows": [(1.5, "😊"), (2.5, "😌")]},
    # users.language='zh-CN'（带区域后缀）不在 supported_languages 里 ⇒ 整条落回 en。
    {"slug": "zh-region", "analytics": True, "language": "zh-CN", "rows": [(1.5, "😊"), (2.5, "😌")]},
    # ── 批 2（curriculum）─────────────────────────────────────────────────────
    # ai_consent=False 且有卡：证明同意门在**碰事件表之前**（不是「查了个空周」）
    {"slug": "curr-off", "analytics": True, "language": "en", "ai": False},
    # 已同意、零卡：未生成的周返回 days=[]（空数组，不是 null——前端两样都不认）
    {"slug": "curr-none", "analytics": True, "language": "en", "ai": True},
    {"slug": "curr-full", "analytics": True, "language": "en", "ai": True},
    {"slug": "curr-gaps", "analytics": True, "language": "en", "ai": True},
    {"slug": "curr-falsy", "analytics": True, "language": "en", "ai": True},
    {"slug": "curr-badjson", "analytics": True, "language": "en", "ai": True},
    # 52 行落在同一周：钉 LIMIT 50 + ORDER BY scheduled_at + 同格后写覆盖先写
    {"slug": "curr-over", "analytics": True, "language": "en", "ai": True},
    # 非 UTC 时区：窗口的 UTC 端点必须跟着本地零点走（Asia/Shanghai 无 DST，可逐字比）
    {"slug": "curr-sh", "analytics": True, "language": "zh", "ai": True, "timezone": "Asia/Shanghai"},
    # 非法时区名 ⇒ safe_zoneinfo 回退 UTC（与 Go 的 LoadLocation 失败分支同口径）
    {"slug": "curr-badtz", "analytics": True, "language": "en", "ai": True, "timezone": "Not/AZone"},
    # 卡的主人（供 curr-full 跨用户去标已读 ⇒ 403）
    {"slug": "curr-other", "analytics": True, "language": "en", "ai": True},
    # 行存在但 event_kind 不是 curriculum ⇒ 与「不存在」「别人的」同一条 403
    {"slug": "curr-kind", "analytics": True, "language": "en", "ai": True},
    # 已注销（status='deleted'）：get_user 过滤 ⇒ 40401，不是 40303
    {"slug": "curr-deleted", "analytics": True, "language": "en", "ai": True, "status": "deleted"},
]

COLUMNS = ("id, timezone, streak_count, relationship_level, chat_count, "
           "daily_quota_used, tier, language, analytics_consent, ai_consent, status")
INSERT_USER = f"INSERT INTO users ({COLUMNS}) VALUES (%s, %s, 0, 'L1', 0, 0, 'free', %s, %s, %s, %s)"
INSERT_MOOD = (
    "INSERT INTO mood_checkins (id, user_id, mood_emoji, created_at) "
    "VALUES (%s, %s, %s, now() - (%s || ' days')::interval)"
)
INSERT_EVENT = (
    "INSERT INTO journey_events (id, user_id, event_kind, day_no, scheduled_at, "
    "payload, memory_refs) VALUES (%s, %s, %s, %s, %s, %s, %s)"
)


def timezone_of(user: dict) -> str:
    return user.get("timezone", "UTC")


def local_today_at(tz: str) -> dt_date:
    """ANCHOR 在 tz 里的本地日历日（口径同 time_utils.local_today）。"""
    return ANCHOR.astimezone(safe_zoneinfo(tz)).date()


def CARD(title: object, assertion: object = None, question: object = None,
         read_at: object = None, **extra: object) -> dict:
    """主题卡 payload 的形状（curriculum_service 生成时是 title/assertion/question/read_at）。"""
    card = {"title": title, "assertion": assertion, "question": question, "read_at": read_at}
    card.update(extra)
    return card


def I(i: int, day_no: int, payload: object, refs: object = None,
      hour: int = 0, kind: str = "curriculum") -> dict:
    """一条 journey_events 播种计划：i＝本地周内第几天（0＝周一）。

    payload/refs 给 dict/list 时按 json.dumps 成文本列；给 str 时**原样入库**（坏 JSON、
    空串这些形状只能这么造）；给 None 时该列为 NULL（parse_json 的空值分支）。
    """
    return {"i": i, "day_no": day_no, "payload": payload, "refs": refs,
            "hour": hour, "kind": kind}


# 每张卡的计划（slug → 卡列表）。窗口的绝对端点由 mk() 按各自时区算出来。
EVENT_PLAN: dict[str, list[dict]] = {
    "curr-off": [I(0, 0, CARD("不该被读到")), I(3, 3, CARD("也不该被读到"))],
    "curr-full": [
        I(0, 0, CARD("周一的标题", "断言：你这周第一次记录情绪", "那天到底发生了什么？"),
          [{"memory_id": "m1", "excerpt": "原话引用"}]),
        I(1, 1, CARD("已读的卡", "A1", "Q1", read_at="2026-10-05T09:00:00+00:00")),
        # read_at="" 是 Python 的假值 ⇒ 未读。判空（!= None）会把这张卡读成已读。
        I(2, 2, CARD("空串 read_at", "A2", "Q2", read_at="")),
        # payload 里多余的键**不进响应**（get_week 只取三个内容键）
        I(3, 3, CARD("带多余键", "A3", "Q3", unexpected={"a": [1, 2]},
                     memory_refs=[{"in_payload": "不算数"}])),
        # 没有 question 键 ⇒ null（不是省略这个键）；read_at 是**非空数组** ⇒ 真值
        I(4, 4, {"title": "缺 question", "assertion": "A4", "read_at": [{"m": 1}]}),
        # 转义面：响应里这些字符必须按 Python 的集合出（不转义 < > & /，非 ASCII 原样）
        I(5, 5, CARD("标题里的 <>&\"\\/ 与 😊 日本", "A5\nB5\t制表", "Q5\u0001\u007f")),
        I(6, 6, CARD("零值对象 read_at", "A6", "Q6", read_at={}), refs=[]),
    ],
    # 缺格不出卡（视图长度不是 7）+ 周序编码的 day_no（5*7+i ⇒ 槽位 i）
    "curr-gaps": [
        I(0, 35, CARD("只有三天有卡", "A", "Q")),
        I(2, 37, CARD("第三天", "A", "Q"), [{"memory_id": "m2", "n": 3, "中文": "值"}]),
        # 负 day_no：Python 的 -1 % 7 == 6，Go 的 -1 % 7 == -1（会永远不落格）
        I(6, -1, CARD("负 day_no 落最后一格", "A", "Q")),
    ],
    # 七个格子各放一种 read_at 假值/真值：钉的是一条 `bool(payload.get("read_at"))`
    "curr-falsy": [
        I(0, 0, {"title": "T0", "read_at": None}),
        I(1, 1, {"title": "T1", "read_at": False}),
        I(2, 2, {"title": "T2", "read_at": 0}),
        I(3, 3, {"title": "T3", "read_at": 0.0}),
        I(4, 4, {"title": "T4", "read_at": []}),
        I(5, 5, {"title": "T5", "read_at": {}}),
        # 缺 read_at 键（生成流程以外写进去的行）⇒ None ⇒ 未读
        I(6, 6, {"title": "T6"}),
    ],
    # 文本列的坏值：parse_json 全部回退默认值，视图不能抛
    "curr-badjson": [
        I(0, 0, None, None),                       # 两列都 NULL
        I(1, 1, "", ""),                           # 两列空串
        I(2, 2, "{oops", "[oops"),                 # 两列坏 JSON
        I(3, 3, "[1,2]", "{\"a\":1}"),             # payload 是数组、refs 是对象
        I(4, 4, "\"字符串\"", "null"),             # payload 是字符串、refs 是 JSON null
        I(5, 5, "5", "true"),                      # 数字/布尔
        I(6, 6, "null", "[{\"memory_id\":\"ok\"}]"),
    ],
    # 52 行落在同一周：每天 8 行、同格 hour 递增 ⇒ scheduled_at 正序的最后一条赢；
    # 第 51、52 条（i=6, hour=2/3）被 LIMIT 50 截掉 ⇒ 第 6 格显示 n=49。
    "curr-over": [I(n // 8, n // 8, CARD(f"n{n}", f"a{n}", f"q{n}"), hour=n % 8)
                  for n in range(52)],
    "curr-sh": [
        I(0, 0, CARD("上海的周一", "A", "Q")),
        I(3, 3, CARD("周中", "A", "Q")),
        I(6, 6, CARD("周日", "A", "Q")),
        # 下周一的行：窗口右端是**开区间**，多播一行也不能漏进本周
        I(7, 7, CARD("下周的不该出现", "A", "Q")),
    ],
    "curr-badtz": [
        I(0, 0, CARD("非法时区按 UTC", "A", "Q")),
        I(4, 4, CARD("非法时区按 UTC 的第二张", "A", "Q")),
    ],
    "curr-other": [I(2, 2, CARD("别人的卡", "A", "Q"))],
    "curr-kind": [
        I(1, 1, CARD("不是主题卡"), kind="day3_weak_assert"),
        I(2, 2, CARD("同用户的主题卡")),
    ],
}


def mk(slug: str, items: list[dict]) -> list[dict]:
    """把某个用户的卡计划换算成绝对行（scheduled_at 用**该用户的时区**算本地零点）。"""
    tz = timezone_of(next(u for u in USERS if u["slug"] == slug))
    monday = local_today_at(tz) - dt_timedelta(days=local_today_at(tz).weekday())

    def column_text(value: object) -> str | None:
        if value is None or isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    return [
        {
            "id": f"{SEED_PREFIX}{slug}-e{n}",
            "user_id": f"{SEED_PREFIX}{slug}",
            "event_kind": item["kind"],
            "day_no": item["day_no"],
            "scheduled_at": (local_day_start_utc(tz, monday + dt_timedelta(days=item["i"]))
                             + dt_timedelta(hours=item["hour"])).isoformat(),
            "payload": column_text(item["payload"]),
            "memory_refs": column_text(item["refs"]),
        }
        for n, item in enumerate(items)
    ]


def seeded_events() -> list[dict]:
    """全部 curriculum 播种行（Go 侧按同一份夹具插，两侧读同一批行）。"""
    out: list[dict] = []
    for slug, items in EVENT_PLAN.items():
        out += mk(slug, items)
    return out


def freeze_clock() -> None:
    """把 curriculum 服务读到的「当下」钉在 ANCHOR（见上面 ANCHOR 的注释）。

    只钉这两个名字：get_week 用 local_today 取今天、mark_read 用 utc_now 写 read_at，
    窗口端点走 local_day_start_utc(tz, day)——它收的是**显式日期**，不碰时钟。
    """
    from backend.services import curriculum_service as curriculum_module

    curriculum_module.local_today = local_today_at
    curriculum_module.utc_now = lambda: ANCHOR


def seed() -> None:
    # psycopg 直连只认 libpq 形态的串（+psycopg 段是 SQLAlchemy 的驱动声明）
    dsn = psycopg.connect(libpq_url(RAW_TEST_URL), autocommit=True)
    with dsn.cursor() as cur:
        # journey_events.user_id 没有外键，但同一批 id 的旧行会撞主键，先删。
        cur.execute("DELETE FROM mood_checkins WHERE id LIKE %s", (f"{SEED_PREFIX}%",))
        cur.execute("DELETE FROM journey_events WHERE id LIKE %s", (f"{SEED_PREFIX}%",))
        cur.execute("DELETE FROM users WHERE id LIKE %s", (f"{SEED_PREFIX}%",))
        for user in USERS:
            uid = f"{SEED_PREFIX}{user['slug']}"
            cur.execute(INSERT_USER, (uid, timezone_of(user), user["language"],
                                      user["analytics"], user.get("ai", False),
                                      user.get("status", "active")))
            for i, (days_ago, emoji) in enumerate(user.get("rows", [])):
                cur.execute(INSERT_MOOD, (f"{uid}-m{i}", uid, emoji, str(days_ago)))
        for event in seeded_events():
            cur.execute(INSERT_EVENT, (event["id"], event["user_id"], event["event_kind"],
                                       event["day_no"], event["scheduled_at"],
                                       event["payload"], event["memory_refs"]))
    dsn.close()


def seed_spec() -> dict:
    """把播种口径原样交给 Go 侧，两侧读同一份数字。"""
    return {
        "users": [
            {"id": f"{SEED_PREFIX}{u['slug']}", "timezone": timezone_of(u),
             "language": u["language"], "analytics_consent": u["analytics"],
             "ai_consent": u.get("ai", False), "status": u.get("status", "active")}
            for u in USERS
        ],
        "mood_checkins": [
            {"id": f"{SEED_PREFIX}{u['slug']}-m{i}", "user_id": f"{SEED_PREFIX}{u['slug']}",
             "mood_emoji": emoji, "days_ago": days_ago}
            for u in USERS
            for i, (days_ago, emoji) in enumerate(u.get("rows", []))
        ],
        "journey_events": seeded_events(),
    }



# ── 用例面 ────────────────────────────────────────────────────────────────────
# (name, group, method, path, user slug | "" (匿名), headers, body)
EV = {"event": "screen_view", "properties": {"screen": "home"},
      "occurred_at": "2026-10-01T00:00:00+00:00"}
CURR = "/api/v1/curriculum"


def cases() -> list[tuple]:
    out: list[tuple] = [
        # A 组：请求体校验面（pydantic 的 loc/msg/input 逐字）
        ("batch-no-body", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, None),
        ("batch-empty-object", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {}),
        ("batch-events-missing-null", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": None}),
        ("batch-events-not-list", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": {"a": 1}}),
        ("batch-events-string-items", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": ["x"]}),
        ("batch-item-missing-event", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {},
         {"events": [{"occurred_at": "2026-10-01T00:00:00+00:00"}]}),
        ("batch-item-event-not-string", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": 5, "occurred_at": EV["occurred_at"]}]}),
        ("batch-item-occurred-at-missing", "validation", "POST",
         "/api/v1/analytics/batch", "consent-on", {}, {"events": [{"event": "e"}]}),
        ("batch-item-occurred-at-bad-string", "validation", "POST",
         "/api/v1/analytics/batch", "consent-on", {},
         {"events": [{"event": "e", "occurred_at": "not-a-date"}]}),
        ("batch-item-occurred-at-epoch-int", "validation", "POST",
         "/api/v1/analytics/batch", "consent-on", {},
         {"events": [{"event": "e", "occurred_at": 1767225600}]}),
        ("batch-item-properties-not-dict", "validation", "POST",
         "/api/v1/analytics/batch", "consent-on", {},
         {"events": [{"event": "e", "occurred_at": EV["occurred_at"], "properties": "s"}]}),
        ("batch-two-items-two-errors", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": "e"}, {"occurred_at": EV["occurred_at"]}]}),
        ("batch-101-items", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [EV] * 101}),
        # too_long 与 item 错误的**相对顺序**、以及 actual_length 到底数的是
        # 「原文条数」还是「校验通过的条数」——这两点在文档里都没有，只能实测。
        # Go 侧的实现是「item 错误在前、too_long 在后、actual_length=原文条数」，
        # 这条用例就是那句话的证明（错了就该红）。
        ("batch-101-items-with-bad-item", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": "e"}] + [EV] * 100}),
        # 同一个 item 里三个字段全错 ⇒ 字段声明序（event/properties/occurred_at）
        ("batch-item-three-field-errors", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": 5, "properties": "s",
                                       "occurred_at": "not-a-date"}]}),
        # event 给 JSON null / properties 给数组：都归 string_type、dict_type
        ("batch-item-event-null", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": None, "occurred_at": EV["occurred_at"]}]}),
        ("batch-item-properties-array", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": "e", "properties": [1],
                                       "occurred_at": EV["occurred_at"]}]}),
        # 非 JSON 的 Content-Type：FastAPI 走 form 解析而不是 json.loads，
        # 于是同一串 body 字节会得到**另一种**错误。Go 侧目前忽略 Content-Type，
        # 这条用例就是「忽略到底对不对」的判据。
        ("batch-content-type-text-plain", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {"Content-Type": "text/plain"}, 'raw:{"events": []}'),
        # 2026-10-14 批 1 登记的欠账（「改这两支打不出红——不是闸门，是没有观测点」）：
        # repr(bytes) 的两个分支各补一条，让 `pyBytesRepr` 的引号选择与
        # 非 ASCII→`\xNN` 第一次有真字节可比。
        #   ① 非 ASCII：CPython 的 bytes repr 一律把 >=0x7f 转成 `\xNN`（逐字节，
        #      不看 UTF-8 边界），Go 的 `c >= 0x7f` 那一支必须在多字节序列上逐字节对得上。
        ("batch-content-type-text-plain-nonascii", "validation", "POST",
         "/api/v1/analytics/batch", "consent-on", {"Content-Type": "text/plain"},
         'raw:{"事件": "你好"}'),
        #   ② 引号选择：body 含单引号而不含双引号时 Python 换成 `b"…"`，
        #      上面那条 `{"events": []}` 恰好相反（含双引号 ⇒ 留在 b'…'），两条一起
        #      才是那条 if 的两侧。
        ("batch-content-type-text-plain-single-quote", "validation", "POST",
         "/api/v1/analytics/batch", "consent-on", {"Content-Type": "text/plain"},
         "raw:it's fine"),
        # 批 1 另一条欠账：`isJSONContentType` 是对 `email.message` 解析的**等价实现**
        # 而不是同一个库，畸形串（同一值重复两遍）两侧可能判不同——补一条把它变成可证的。
        ("batch-content-type-duplicate-json", "validation", "POST",
         "/api/v1/analytics/batch", "consent-on",
         {"Content-Type": "application/json, application/json"}, "raw:nope"),
        # 上面那条只证明「非 form 的 Content-Type ⇒ 走 bytes 分支」。另外三条把
        # 剩下的分支补齐：form 类型走的是另一条码路、带 charset 后缀的 JSON 仍算 JSON、
        # 空 body 在 bytes 分支里是 missing 还是 model_attributes_type。
        ("batch-content-type-form", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {"Content-Type": "application/x-www-form-urlencoded"},
         'raw:events=%5B%5D&a=1'),
        ("batch-content-type-json-charset", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {"Content-Type": "application/json; charset=utf-8"}, "raw:nope"),
        ("batch-content-type-text-plain-empty", "validation", "POST",
         "/api/v1/analytics/batch", "consent-on", {"Content-Type": "text/plain"}, "raw:"),
        # 空 Content-Type 头（值为空串）在 starlette 里就是「没给」⇒ 与缺头同一条 bytes 分支。
        ("batch-content-type-empty-header", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {"Content-Type": ""}, 'raw:{"events": []}'),
        # 只有空白的 body：FastAPI 的判据是 `if body_bytes:`（**不 strip**），
        # 所以三个空格走 JSON 解析并报 json_invalid，而不是 missing。
        ("batch-body-whitespace-only", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, "raw:   "),
        # 主类型对但子类型是 +json：email.message 的口径是 subtype=="json" 或 endswith("+json")。
        ("batch-content-type-vnd-json", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {"Content-Type": "application/vnd.api+json"}, "raw:nope"),
        ("batch-not-json-raw", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, "raw:not json"),
        ("batch-trailing-garbage", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, 'raw:{"events":[]} x'),
        # 请求体本身不是合法 JSON 时，loc 末位与 ctx.error 来自 **标准库 json** 的
        # JSONDecodeError（starlette 解析失败后包成 json_invalid）。Go 要复刻的是
        # CPython 扫描器的报错文案与 char 位置，不是 Go encoding/json 的。
        ("batch-json-empty-body", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, "raw:"),
        ("batch-json-bare-word", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, "raw:nope"),
        ("batch-json-single-quoted", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, "raw:{'events':[]}"),
        ("batch-json-unquoted-key", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, "raw:{events: []}"),
        ("batch-json-missing-comma", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, 'raw:{"a": 1 "b": 2}'),
        ("batch-json-missing-colon", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, 'raw:{"a" 1}'),
        ("batch-json-trailing-comma", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, 'raw:{"events": [],}'),
        ("batch-json-unterminated-string", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, 'raw:{"events": "abc'),
        ("batch-json-bad-escape", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, 'raw:{"events": "a\\qb"}'),
        ("batch-json-bad-unicode-escape", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, 'raw:{"events": "a\\u12"}'),
        ("batch-json-leading-newline", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, 'raw:\n\n  {"events": []} x'),
        # 注：标准库 json 还接受 NaN/Infinity 这类非标准字面量，pydantic 会把 nan 当作
        # list_type 的 input 回显，再由 JSONResponse(allow_nan=False) 抛 ValueError ⇒
        # 真实部署里那是一条 500。本夹具走 ASGITransport，异常直接冒到客户端、录不到
        # 500 响应体，所以不在此列设例；该发散点按日期登记进 tasks.md，不假装两侧一致。
        ("batch-json-number-only", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, "raw:42"),
        ("batch-json-array-body", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, "raw:[]"),
        # 字符串转义面：Python 的 json.dumps(ensure_ascii=False) 只转义 " \\ 与控制字符
        # （\b \t \n \f \r 用短写、其余 \\u00XX），非 ASCII 原样输出，且 **不**转义 `/`；
        # Go 的 encoding/json 默认还会转义 < > &。这里用回显把它钉住。
        ("batch-json-control-char", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, 'raw:{"events": "a\nb"}'),
        # 字符串转义面：Python 的 json.dumps(ensure_ascii=False) 只转义 " \\ 与控制字符
        # （\b \t \n \f \r 用短写、其余 \\u00XX），非 ASCII 原样输出，且 **不**转义 `/`；
        # Go 的 encoding/json 默认还会转义 < > &。这里用回显把它钉住。
        ("batch-json-echo-string-escapes", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": "e", "occurred_at": EV["occurred_at"],
                                        "properties": '日本／<>&"\\\t\n\r\x01\x7f'}]}),
        ("batch-json-echo-unicode-escape", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": "e", "occurred_at": EV["occurred_at"],
                                        "properties": "\\u00e9😀"}]}),
        ("batch-json-duplicate-keys", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, 'raw:{"events":{"a":1,"b":2,"a":{"c":[1,2]}}}'),
        ("batch-json-null-body", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, "raw:null"),
        # input 回显走 `json.loads(json.dumps(errors))` ⇒ 数字被 CPython repr 重排：
        # 1e16 写成 1e+16、9999999999999998 写成 9999999999999998.0、-0.0 写成 -0.0。
        # Go 的 %v 阈值不同，不钉住就是逐字差异。
        *[(f"batch-float-echo-{tag}", "validation", "POST", "/api/v1/analytics/batch",
           "consent-on", {}, {"events": [{"event": value,
                                          "occurred_at": EV["occurred_at"]}]})
          for tag, value in [("plain", 1.5), ("tenths", 0.1), ("one", 1.0),
                             ("e16", 1e16), ("just-under-e16", 9999999999999998.0),
                             ("tiny", 1e-5), ("neg-zero", -0.0), ("e20", 1e20),
                             ("big-int", 10**30), ("neg-int", -7),
                             ("exp-form", 2.5e-8), ("three-quarters", 0.75)]],
        # occurred_at 的取值面：pydantic v2 的 datetime 解析器接受 unix 秒（int/float）
        # 与 date-only，拒绝的范围错误各带不同 ctx.error。Go 侧要逐字复刻，
        # 「猜一个 RFC3339 校验器」会在这些字节上红。
        ("batch-occurred-at-true", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": "e", "occurred_at": True}]}),
        ("batch-occurred-at-false", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": "e", "occurred_at": False}]}),
        ("batch-occurred-at-negative-epoch", "validation", "POST",
         "/api/v1/analytics/batch", "consent-on", {},
         {"events": [{"event": "e", "occurred_at": -1}]}),
        ("batch-occurred-at-float-epoch", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": "e", "occurred_at": 1767225600.5}]}),
        ("batch-occurred-at-epoch-string", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": "e", "occurred_at": "1767225600"}]}),
        ("batch-occurred-at-list", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": "e", "occurred_at": []}]}),
        ("batch-occurred-at-object", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": "e", "occurred_at": {"y": 2026}}]}),
        ("batch-occurred-at-date-only", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": "e", "occurred_at": "2026-10-01"}]}),
        ("batch-occurred-at-basic-date", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": "e", "occurred_at": "20261001"}]}),
        ("batch-occurred-at-space-sep", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": "e", "occurred_at": "2026-10-01 08:00:00"}]}),
        ("batch-occurred-at-naive-iso", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": "e", "occurred_at": "2026-10-01T08:00:00"}]}),
        ("batch-occurred-at-offset", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {},
         {"events": [{"event": "e", "occurred_at": "2026-10-01T08:00:00+08:00"}]}),
        ("batch-occurred-at-month-13", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": "e", "occurred_at": "2026-13-01"}]}),
        ("batch-occurred-at-day-32", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": "e", "occurred_at": "2026-10-32"}]}),
        ("batch-occurred-at-hour-25", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": "e", "occurred_at": "2026-10-01T25:00:00"}]}),
        ("batch-occurred-at-truncated-t", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": "e", "occurred_at": "2026-10-01T"}]}),
        ("batch-occurred-at-empty", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": "e", "occurred_at": ""}]}),
        # event 空串、properties 缺省/空/为 null、多带未知键：这些都合法通过，
        # 因此会进 store（由 _group 自动改标 seeded）。
        ("batch-event-empty-string", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": "", "occurred_at": EV["occurred_at"]}]}),
        ("batch-properties-empty-dict", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": "e", "occurred_at": EV["occurred_at"],
                                        "properties": {}}]}),
        ("batch-properties-null", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": "e", "occurred_at": EV["occurred_at"],
                                        "properties": None}]}),
        ("batch-properties-non-whitelist-only", "validation", "POST",
         "/api/v1/analytics/batch", "consent-on", {},
         {"events": [{"event": "e", "occurred_at": EV["occurred_at"],
                      "properties": {"reflection_text": "不该入库的原文",
                                     "memory_content": "x", "message": "y"}}]}),
        ("batch-properties-nested-and-typed", "validation", "POST",
         "/api/v1/analytics/batch", "consent-on", {},
         {"events": [{"event": "e", "occurred_at": EV["occurred_at"],
                      "properties": {"screen": "home", "count": 3, "duration_ms": 12.5,
                                     "is_new_user": True, "nested": {"a": [1, "两"]},
                                     "unknown_key": "drop"}}]}),
        ("batch-item-extra-key", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [{"event": "e", "occurred_at": EV["occurred_at"],
                                        "unexpected": 1}]}),
        ("batch-body-extra-key", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [EV], "unexpected": [1, 2]}),
        ("batch-events-exactly-100", "validation", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [EV] * 100}),
        # 匿名（dev 模式无 X-User-Id）user_id 落成 "anonymous"，仍要查库 ⇒ 属 B 组
        ("batch-anonymous-drops-all", "seeded", "POST", "/api/v1/analytics/batch",
         "", {}, {"events": [EV]}),
        # B 组：成功面（真读 users.analytics_consent / mood_checkins）
        ("batch-consent-off-drops-all", "seeded", "POST", "/api/v1/analytics/batch",
         "consent-off", {}, {"events": [EV, EV, EV]}),
        ("batch-consent-on-relays-all", "seeded", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [EV, EV]}),
        ("batch-empty-list", "seeded", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": []}),
        ("batch-content-fields-only", "seeded", "POST", "/api/v1/analytics/batch",
         "consent-on", {}, {"events": [
             {"event": "reflection_saved",
              "properties": {"reflection_text": "内容不入分析", "screen": "reflect",
                             "mood_emoji": None, "count": 3, "tier": "free"},
              "occurred_at": "2026-10-02T03:04:05+00:00"}]}),
        ("batch-user-not-found", "seeded", "POST", "/api/v1/analytics/batch",
         "ghost-user", {}, {"events": [EV]}),
        ("discovery-anonymous", "seeded", "GET", "/api/v1/discovery/daily", "", {}, None),
        ("discovery-user-not-found", "seeded", "GET", "/api/v1/discovery/daily",
         "ghost-user", {}, None),
        ("discovery-no-rows", "seeded", "GET", "/api/v1/discovery/daily", "no-rows", {}, None),
        ("discovery-newcomer-milestone", "seeded", "GET", "/api/v1/discovery/daily",
         "newcomer", {}, None),
        ("discovery-week-break", "seeded", "GET", "/api/v1/discovery/daily", "break", {}, None),
        ("discovery-freq-diff-2-medium", "seeded", "GET", "/api/v1/discovery/daily",
         "freq2", {}, None),
        ("discovery-freq-diff-3-high", "seeded", "GET", "/api/v1/discovery/daily",
         "freq3", {}, None),
        ("discovery-mood-up-high", "seeded", "GET", "/api/v1/discovery/daily",
         "up-high", {}, None),
        ("discovery-mood-down-high", "seeded", "GET", "/api/v1/discovery/daily",
         "down-high", {}, None),
        ("discovery-mood-drift-low", "seeded", "GET", "/api/v1/discovery/daily",
         "drift-low", {}, None),
        ("discovery-mood-flat-no-discovery", "seeded", "GET", "/api/v1/discovery/daily",
         "flat", {}, None),
        ("discovery-unknown-emoji-default-3", "seeded", "GET", "/api/v1/discovery/daily",
         "unknown-emoji", {}, None),
        ("discovery-zh-via-header", "seeded", "GET", "/api/v1/discovery/daily",
         "zh-header", {"X-Language": "zh-CN"}, None),
        ("discovery-zh-via-db-preference", "seeded", "GET", "/api/v1/discovery/daily",
         "zh-db-pref", {}, None),
        ("discovery-es-header-overrides-zh-db", "seeded", "GET", "/api/v1/discovery/daily",
         "es-header-over-zh-db", {"X-Language": "es"}, None),
        ("discovery-unsupported-header-falls-back-db", "seeded", "GET",
         "/api/v1/discovery/daily", "zh-db-pref", {"X-Language": "xx"}, None),
        ("discovery-bad-limit-is-not-a-param", "seeded", "GET",
         "/api/v1/discovery/daily?limit=3", "no-rows", {}, None),
        # users.language='zh-CN' 不在受支持表里 ⇒ 第②级整条落回 en（不是归一化成 zh）；
        # header 侧的 zh-CN 却会归一化成 zh。两级口径不同，必须分开取证书。
        ("discovery-zh-region-db-falls-back-en", "seeded", "GET",
         "/api/v1/discovery/daily", "zh-region", {}, None),
        ("discovery-header-bare-zh", "seeded", "GET", "/api/v1/discovery/daily",
         "zh-header", {"X-Language": "zh"}, None),
        ("discovery-header-uppercase-zh", "seeded", "GET", "/api/v1/discovery/daily",
         "zh-header", {"X-Language": "ZH-CN"}, None),
        # 只有空白值的 header：raw.strip() 为空 ⇒ 视为缺失，继续走库偏好
        ("discovery-header-blank", "seeded", "GET", "/api/v1/discovery/daily",
         "zh-db-pref", {"X-Language": " "}, None),
        # 带区域后缀但主码不受支持（de-AT）⇒ 归一后仍不在表里 ⇒ 落回库偏好 zh
        ("discovery-header-unsupported-region", "seeded", "GET", "/api/v1/discovery/daily",
         "zh-db-pref", {"X-Language": "de-AT"}, None),
        # en 用户给 zh：文案随 header 变，说明语言不参与数据查询
        ("discovery-en-user-zh-header", "seeded", "GET", "/api/v1/discovery/daily",
         "up-high", {"X-Language": "zh"}, None),
        # ── 批 2：curriculum 域（组名 curriculum＝回放时把 Server.now 钉成 recorded_at）──
        # 前置门先于事件表：注销用户 40401、未同意 40303 都不该出现「空周」。
        ("week-anonymous", "curriculum", "GET", f"{CURR}/week", "", {}, None),
        ("week-user-not-found", "curriculum", "GET", f"{CURR}/week", "ghost-user", {}, None),
        ("week-deleted-user", "curriculum", "GET", f"{CURR}/week", "curr-deleted", {}, None),
        ("week-ai-consent-off", "curriculum", "GET", f"{CURR}/week", "curr-off", {}, None),
        # 未生成周：days 是空数组 []，不是 null
        ("week-empty", "curriculum", "GET", f"{CURR}/week", "curr-none", {}, None),
        ("week-full", "curriculum", "GET", f"{CURR}/week", "curr-full", {}, None),
        # 缺格不出卡 + 周序编码的 day_no + 负 day_no 的 floor-mod 槽位
        ("week-gaps", "curriculum", "GET", f"{CURR}/week", "curr-gaps", {}, None),
        # 七个格子各一种 read_at 假值：钉 `bool(payload.get("read_at"))` 而不是判空
        ("week-falsy", "curriculum", "GET", f"{CURR}/week", "curr-falsy", {}, None),
        # 文本列的 NULL/空串/坏 JSON/类型不符：parse_json 的回退分支
        ("week-bad-text-columns", "curriculum", "GET", f"{CURR}/week", "curr-badjson", {}, None),
        # 52 行同周：LIMIT 50 与 ORDER BY scheduled_at 的合成效果（第 6 格该显示 n49）
        ("week-over-limit", "curriculum", "GET", f"{CURR}/week", "curr-over", {}, None),
        # 本地零点折算 UTC：窗口的两个端点都要跟着时区走
        ("week-timezone-shanghai", "curriculum", "GET", f"{CURR}/week", "curr-sh", {}, None),
        # 非法时区名 ⇒ safe_zoneinfo 回退 UTC
        ("week-timezone-invalid", "curriculum", "GET", f"{CURR}/week", "curr-badtz", {}, None),
        # 同用户混了别的 event_kind：只出 curriculum 行
        ("week-kind-filter", "curriculum", "GET", f"{CURR}/week", "curr-kind", {}, None),
        # 越权三情形合成同一条 403（行不存在 / 别人的 / kind 不对）
        ("read-cross-user", "curriculum", "POST", f"{CURR}/{SEED_PREFIX}curr-other-e0/read",
         "curr-full", {}, None),
        ("read-missing-event", "curriculum", "POST", f"{CURR}/n8b1-no-such-event/read",
         "curr-full", {}, None),
        ("read-wrong-kind", "curriculum", "POST", f"{CURR}/{SEED_PREFIX}curr-kind-e0/read",
         "curr-kind", {}, None),
        ("read-ai-consent-off", "curriculum", "POST", f"{CURR}/{SEED_PREFIX}curr-off-e0/read",
         "curr-off", {}, None),
        # 真写库的一条 + 紧接着的幂等重放：第二次不再写，回同样的字节
        ("read-card", "curriculum", "POST", f"{CURR}/{SEED_PREFIX}curr-full-e0/read",
         "curr-full", {}, None),
        ("read-card-again", "curriculum", "POST", f"{CURR}/{SEED_PREFIX}curr-full-e0/read",
         "curr-full", {}, None),
        # 写完之后同一张卡必须变成已读、unread_count 少一（「提交在响应之前」的判据）
        ("week-full-after-read", "curriculum", "GET", f"{CURR}/week", "curr-full", {}, None),
        # payload 列为 NULL 的行被标已读：写回的是整份 {"read_at": "..."}
        ("read-null-payload", "curriculum", "POST", f"{CURR}/{SEED_PREFIX}curr-badjson-e0/read",
         "curr-badjson", {}, None),
        # payload 是数组/坏 JSON/字符串的行同样能标已读（_payload 回退 {} 后再写）
        ("read-array-payload", "curriculum", "POST", f"{CURR}/{SEED_PREFIX}curr-badjson-e3/read",
         "curr-badjson", {}, None),
        ("read-broken-payload", "curriculum", "POST", f"{CURR}/{SEED_PREFIX}curr-badjson-e2/read",
         "curr-badjson", {}, None),
        ("read-string-payload", "curriculum", "POST", f"{CURR}/{SEED_PREFIX}curr-badjson-e4/read",
         "curr-badjson", {}, None),
        ("read-null-json-payload", "curriculum", "POST", f"{CURR}/{SEED_PREFIX}curr-badjson-e6/read",
         "curr-badjson", {}, None),
        ("week-bad-columns-after-read", "curriculum", "GET", f"{CURR}/week",
         "curr-badjson", {}, None),
        # 上海时区用户标已读：写回的 read_at 与 UTC 侧同一时刻（钉的是 utc_now 不是本地钟）
        ("read-shanghai-card", "curriculum", "POST", f"{CURR}/{SEED_PREFIX}curr-sh-e1/read",
         "curr-sh", {}, None),
        ("week-shanghai-after-read", "curriculum", "GET", f"{CURR}/week", "curr-sh", {}, None),
    ]
    return out


def _group(name: str, path: str, declared: str, status: int, text: str) -> str:
    """组别按「请求有没有真的进 store」派生，不靠手标。

    `validation` 组是 Go 侧**无库**回放的那批；一旦某条其实读了 users 却被标成
    validation，无库回放就会拿库派生的字节去比，红得没有信息量。/analytics/batch
    只有校验全过（HTTP 200）才会走到 `relay_batch` ⇒ 用状态码判，比人盯可靠。
    """
    if path.startswith("/api/v1/analytics/batch") and status == 200:
        return "seeded"
    if declared == "validation" and status == 200:
        raise SystemExit(f"{path} 返回 200 却标为 validation：无库回放组会被污染")
    if declared == "curriculum" and status == 404 and not text.startswith('{"code"'):
        # 业务码 40401 也推导成 HTTP 404，所以 404 本身不是错——**体不是信封**才是：
        # 那是 FastAPI 的路由没匹配（{"detail": "Not Found"}），录进夹具就是把
        # 「我们两边都没实现的那条路径」当成契约。
        raise SystemExit(f"{name} 得到非信封 404（路由没匹配）：{text}")
    return declared


async def record() -> list[dict]:
    recorded = []
    generated_at_samples = []
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for name, group, method, path, user_slug, extra_headers, body in cases():
            headers = {"X-Trace-Id": TRACE}
            headers.update(extra_headers)
            if user_slug:
                headers["X-User-Id"] = f"{SEED_PREFIX}{user_slug}"
            content = None
            wants_json = "Content-Type" not in extra_headers
            if isinstance(body, str) and body.startswith("raw:"):
                content = body[4:].encode()
                if wants_json:
                    headers["Content-Type"] = "application/json"
            elif body is not None:
                content = json.dumps(body, ensure_ascii=False).encode()
                if wants_json:
                    headers["Content-Type"] = "application/json"
            res = await client.request(method, path, headers=headers, content=content)
            text = res.text
            sample = GENERATED_AT_RE.search(text)
            if sample:
                generated_at_samples.append(sample.group(0)[len('"generated_at":"'):-1])
                text = GENERATED_AT_RE.sub(f'"generated_at":"{MASKED}"', text)
            recorded.append({
                "name": name,
                "group": _group(name, path, group, res.status_code, text),
                "method": method,
                "path": path,
                "headers": headers,
                "request_raw": content.decode() if content is not None else None,
                "status": res.status_code,
                "response_text": text,
            })
    return recorded, generated_at_samples


def datetime_corpus() -> list[dict]:
    """`occurred_at` 的取值面：把 pydantic-core/speedate 的解析空间录成表，供 Go 逐条回放。

    为什么不只靠 `cases()` 里那几十条 HTTP 用例：错误词表只有 7 类，但触达它们的
    字符串形状有几百个（截断、单字符替换、月/日/时/秒越界、分隔符、小数位、时区）。
    用例面只能证明「我想起的那几个输入两侧一致」，语料面才证明解析器等价；
    前者是抽查，后者是把状态机钉住。
    """
    from backend.models.schemas import AnalyticsEvent

    canonical = "2026-10-01T08:09:10.123456+07:00"
    inputs: list[object] = [
        None, True, False, 0, 1, -1, 1767225600, 1767225600.5, -1767225600,
        253402300800, -62135596800, 10**18, 1.7976931348623157e308,
        [], [1], {}, {"y": 2026},
    ]
    for cut in range(len(canonical) + 1):
        inputs.append(canonical[:cut])
        inputs.append(canonical[:cut].replace("T", " "))
    for ch in ("a", "9", "-", ".", ":", " ", "T", "+"):
        for pos in range(len(canonical)):
            inputs.append(canonical[:pos] + ch + canonical[pos + 1:])
    inputs += [
        canonical + "x", canonical + " ", canonical + "Z", canonical + "0",
        "2026-10-01", "20261001", "2026101", "2026-10-1", "26-10-01",
        "2026/10/01", "2026_10_01", "2026 10 01", "10000-01-01", "999-01-01",
        "0000-01-01", "0000-12-31", "9999-12-31T23:59:59.999999",
        "2026-10-01t08:09:10", "2026-10-01 08:09:10+07:00",
        "2026-10-01T8:09:10", "2026-10-01T08:9:10", "2026-10-01T08:09:1",
        "1767225600", "-1", "0.5", "1e3", "+1", "9" * 20, " ", "",
        "2026-10-01T08:09:10.1", "2026-10-01T08:09:10.12", "2026-10-01T08:09:10.123",
        "2026-10-01T08:09:10.1234567", "2026-10-01T08:09:10,25",
        "2026-10-01T08:09:10Z", "2026-10-01T08:09:10z", "2026-10-01T08:09:10+00:00",
        "2026-10-01T08:09:10-00:00", "2026-10-01T08:09:10+0800", "2026-10-01T08:09:10+8",
        "2026-10-01T08:09:10+08:60", "2026-10-01T08:09:10+24:00", "2026-10-01T08:09:10-14:00",
        "2026-10-01T08:09:10 UTC", "2026-10-01T08:09:10.123+07:00",
    ]
    for month in range(0, 14):
        inputs.append(f"2026-{month:02d}-01")
    for day in range(0, 33):
        inputs.append(f"2026-02-{day:02d}")
        inputs.append(f"2026-04-{day:02d}")
    inputs += ["2024-02-29", "2023-02-29", "1900-02-29", "2000-02-29", "2100-02-29"]
    for hour in (0, 1, 9, 10, 23, 24, 25, 99):
        inputs.append(f"2026-10-01T{hour:02d}:09:10")
    for minute in (0, 59, 60, 61):
        inputs.append(f"2026-10-01T08:{minute:02d}:10")
    for second in (0, 59, 60, 61):
        inputs.append(f"2026-10-01T08:09:{second:02d}")
    # 数字面：speedate 只有在「整串都是数字」时才按 unix 秒解析，且 |ts| > 2e10
    # 自动按毫秒解释；非 ASCII 数字、指数写法、前导零、'.' 悬空都要有观测点。
    inputs += [
        "1234567890123", "19999999999", "20000000000", "20000000001",
        "0", "00", "007", "+1.5", "-0.000001", "1.", ".5", "1e5", "-1.",
        "١٢٣", " 1", "1 ", "99999999999999999999999",
        0.0000005, 1.0000005, -0.0000005, 2603, 2604,
        # 0 年附近：日期串走 datetime_parsing（year 0 is out of range），
        # 时间戳走另一条越界口径，两侧类型名不同，必须有观测点。
        -62167219200, -62167219201, 62167219200, "-62167219200", "0000-01-01T00:00:00",
    ]
    # 第二轮补的**语法面**（2026-10-14）：第一轮证明的是「量级/舍入」，
    # 这一轮钉的是时间部与偏移部的**状态机边界**——个位字段、偏移位数、
    # 尾随垃圾、以及 speedate 与 Python 的 i64 溢出分界。
    # 为什么必须单独取证据而不是"看起来合理就实现"：`9223372036854775807` 走数字、
    # `9223372036854775808` 落回日期解析（报 separator），而浮点负分数时间戳
    # 有一条 `second=floor / microsecond=|round(frac)|` 的**符号怪癖**，
    # 毫秒路径却是精确互补归一——两侧口径不统一，只能逐条钉。
    inputs += [
        # 时间部分隔符与个位字段
        "2026-10-01T08a09:10", "2026-10-01T08:09a10", "2026-10-01T08:09:10a",
        "2026-10-01T08:09+07:00", "2026-10-01T08:09.5", "2026-10-01T08:09.5+07:00",
        "2026-10-01T08:09:60.5", "2026-10-01T08:09:10.123456789",
        "2026-10-01T08:09:10,1234567", "2026-10-01Q08:09:10", "2026-10-01\t08:09:10",
        # 偏移位数与尾随垃圾
        "2026-10-01T08:09:10+070", "2026-10-01T08:09:10+07000", "2026-10-01T08:09:10+070000",
        "2026-10-01T08:09:10+7:00", "2026-10-01T08:09:10+07:0", "2026-10-01T08:09:10+07:000",
        "2026-10-01T08:09:10+07:00Z", "2026-10-01T08:09:10Z ", "2026-10-01T08:09:10Zx",
        "2026-10-01T08:09:10+07:0 ", "2026-10-01T08:09:10-24:00", "2026-10-01T08:09:10+23:59",
        "2026-10-01T08:09:10-23:59", "2026-10-01T08:09:10+07:60", "2026-10-01T08:09:10-07:60",
        "9999-12-31T23:59:59.999999+07:00", "0001-01-01T00:00:00", "0001-01-01",
        # 数字串的 i64 分界、半微秒舍入、以及负毫秒
        "9223372036854775", "9223372036854775807", "9223372036854775808",
        "922337203685477580.7", "92233720368547758.7", "0.0000005", "0.0000015",
        "-0.0000005", "-0.0000015", "253402300800000", "-62135596800000", "-62135596800000000",
        "+", "-", ".", "-.5", "+.5", "1.2.3", "--1", "1-", "0x10", "1_000", "01", "-0", "+0",
        "-20000000000.5", "-100000000000000",
        # 同量级的**浮点**输入：与数字串走不同代码路径，负号口径必须分开取证
        -62167219200.0, -62167219200.5, 62167219200.5, -0.999999, -0.5, -1.5, -2.5, -1000.5,
        -1.0, 253402300800.0, -62135596800.0, 253402300799, 10**30, -20000000001.0,
    ]

    # 同一输入只录一次（截断/替换会撞车，语料去重后仍然覆盖各分支）
    seen: set[str] = set()
    corpus: list[dict] = []
    for value in inputs:
        key = json.dumps(value, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        entry: dict = {"input": value}
        try:
            parsed = AnalyticsEvent(event="e", occurred_at=value).occurred_at
        except Exception as exc:  # noqa: BLE001  只取第一条错误即可（本字段只有一个）
            err = exc.errors()[0]
            entry.update(ok=False, type=err["type"],
                         error=(err.get("ctx") or {}).get("error"))
        else:
            as_utc = parsed if parsed.tzinfo else parsed.replace(
                tzinfo=dt_timezone.utc)
            # 必须是**整数**减法：`int(ts.timestamp() * 1e6)` 在 253402300799.999999
            # 上回绕成 253402300800000000（多一微秒），Go 侧按精确历法算就会对不上。
            entry.update(
                ok=True,
                iso=parsed.isoformat(),
                utc_micros=(as_utc - EPOCH_UTC) // dt_timedelta(microseconds=1),
            )
        corpus.append(entry)
    return corpus


def week_window_corpus() -> list[dict]:
    """本地周窗口的跨语言语料（无需 HTTP、无需库）——批 2 的日历核就靠这份钉住。

    为什么不能只靠 HTTP 用例：录制与回放只隔几分钟，用例里的 week_start 永远落在
    **同一个星期**内；「本地今天与 UTC 今天不同周」「DST 缺位的本地午夜」「周日 23:59:59」
    这些形状只有拿**固定瞬间**当输入才可证。每个瞬间的四个值都由 Python 现算并记下来，
    Go 侧逐条比（见 internal/httpapi/n8_window_test.go）。
    """
    zones = [
        "UTC", "Asia/Shanghai", "Asia/Kolkata", "America/New_York", "Europe/London",
        "Australia/Sydney", "America/Santiago", "Pacific/Chatham", "Etc/GMT+5",
        "Pacific/Kiritimati", "Pacific/Niue",
        # 这三个的切换点在**本地午夜**（00:00→01:00 之类），于是「本地零点」这个
        # 墙钟时刻本身就要么不存在要么有两个含义——批 2 修掉的真缺陷就是这一类。
        "Asia/Tehran", "America/Asuncion", "Africa/Cairo",
        "Not/AZone", "",
    ]
    moments = [
        # 周边界：UTC 的周一 00:00:00 与前一刻；Kiritimati(+14)/Niue(-11) 在这里会
        # 落到**与 UTC 不同的一周**，正是 CurriculumWindow 用本地日历日的理由。
        "2026-10-11T23:59:59+00:00", "2026-10-12T00:00:00+00:00",
        "2026-10-11T11:00:00+00:00", "2026-10-12T10:00:00+00:00",
        # 年边界与 ISO 周序跨年
        "2026-01-01T00:00:00+00:00", "2026-12-31T23:59:59+00:00",
        "2027-01-04T00:00:00+00:00",
        # 2026 年的 DST 切换时刻（北半球春秋分前后、澳/智的南半球方向）
        "2026-03-08T07:00:00+00:00", "2026-03-08T07:30:00+00:00",
        "2026-03-29T01:00:00+00:00", "2026-03-29T01:30:00+00:00",
        "2026-10-25T00:59:59+00:00", "2026-10-25T01:00:00+00:00",
        "2026-04-05T13:00:00+00:00", "2026-10-04T13:00:00+00:00",
        "2026-09-06T04:00:00+00:00", "2026-04-05T03:00:00+00:00",
    ]
    # 扫描：2026 全年每 10 天一个瞬间，小时数轮转，保证每个时区都撞到自己的
    # 切换日、周边界与本地午夜（上面的手选点只覆盖了我想到的形状）。
    base = dt_datetime(2026, 1, 1, tzinfo=dt_timezone.utc)
    moments += [(base + dt_timedelta(days=10 * n, hours=(n * 5) % 24)).isoformat()
                for n in range(37)]

    out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(tz: str, at: dt_datetime, gap: bool = False) -> None:
        key = (tz, at.isoformat())
        if key in seen:
            return
        seen.add(key)
        # 这四行的顺序就是 get_week 里的算法（curriculum_service.py:167-171），
        # 换成本仓库的 helper 会让语料测的是**我的复刻**而不是现行实现。
        today = at.astimezone(safe_zoneinfo(tz)).date()
        monday = today - dt_timedelta(days=today.weekday())
        out.append({
            "tz": tz,
            "at": at.isoformat(),
            "monday": monday.isoformat(),
            "today": today.isoformat(),
            "start": local_day_start_utc(tz, monday).isoformat(),
            "end": local_day_start_utc(tz, monday + dt_timedelta(days=7)).isoformat(),
            # gap=true ＝这一行的**本地今天**的零点不唯一确定一个瞬间（被跳过或被重复）；
            # 若今天是周一，窗口的左端点就落在这个不存在/有两个含义的时刻上。
            # 语料里最贵的一类样本，所以标出来让 Go 侧能断言它没被去重扫掉。
            "gap": gap,
        })

    # 先跑这一轮：扫 2015–2035 每个时区**所有本地零点异常的日子**。
    # 判据不能用 fold：PEP 495 规定 fold 只对**重复**（秋季回拨）有意义，缺位（春季
    # 拨快）时两个 fold 给出同一个瞬间——上一版就是因此扫出 0 条，把最贵的一类样本
    # 悄悄丢了。缺位要靠「换算到 UTC 再换回本地，墙钟回不到 00:00」来认。
    def midnight_anomaly(tz: str, day: dt_date) -> bool:
        zone = safe_zoneinfo(tz)
        wall = dt_datetime.combine(day, dt_time())
        low = wall.replace(tzinfo=zone)
        if low.astimezone(dt_timezone.utc).astimezone(zone).replace(tzinfo=None) != wall:
            return True
        return low.utcoffset() != low.replace(fold=1).utcoffset()

    def noon_utc(tz: str, d: dt_date) -> dt_datetime:
        """当天**本地正午**对应的 UTC 瞬间：正午离切换点最远，today 一定等于 d。"""
        return dt_datetime(d.year, d.month, d.day, 12, 0,
                           tzinfo=safe_zoneinfo(tz)).astimezone(dt_timezone.utc)

    day = dt_date(2015, 1, 1)
    while day.year < 2035:
        for tz in zones:
            if not midnight_anomaly(tz, day):
                continue
            # 今天异常 ⇒ 覆盖 today/monday 的折算；今天是周一 ⇒ 额外补一条周日正午，
            # 那一行的窗口**右**端点正好压在这个异常午夜上。
            add(tz, noon_utc(tz, day), gap=True)
            if day.weekday() == 0:
                add(tz, noon_utc(tz, day - dt_timedelta(days=1)), gap=True)
        day += dt_timedelta(days=1)

    for tz in zones:
        for raw in moments:
            add(tz, dt_datetime.fromisoformat(raw).astimezone(dt_timezone.utc))
    return out


def main() -> None:
    seed()
    freeze_clock()
    recorded, samples = asyncio.run(record())
    corpus = datetime_corpus()
    payload = {
        "_comment": "generated by scripts/gen_n8_contract_goldens.py; do not hand-edit",
        "trace_id": TRACE,
        # curriculum 组的回放必须把 Server.now 钉在这一刻（见 ANCHOR 的注释）。
        "recorded_at": ANCHOR.isoformat(),
        "masked_fields": ["generated_at"],
        "generated_at_samples": samples[:4],
        "seed": seed_spec(),
        "occurred_at_corpus": corpus,
        "week_window_corpus": week_window_corpus(),
        "cases": recorded,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    counts = {g: sum(1 for c in recorded if c["group"] == g)
              for g in ("validation", "seeded", "curriculum")}
    accepted = sum(1 for c in corpus if c["ok"])
    print(f"wrote {OUT}: {len(recorded)} cases "
          f"(validation {counts['validation']} / seeded {counts['seeded']} / "
          f"curriculum {counts['curriculum']})")
    print(f"recorded_at={ANCHOR.isoformat()} journey_events={len(seed_spec()['journey_events'])} "
          f"week_window_corpus={len(payload['week_window_corpus'])}")
    print(f"occurred_at corpus: {len(corpus)} entries ({accepted} accepted / "
          f"{len(corpus) - accepted} rejected)")
    print(f"generated_at samples: {samples[:3]}")


if __name__ == "__main__":
    main()
