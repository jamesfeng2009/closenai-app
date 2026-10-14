"""Regenerate go-core/testdata/n8_contract.json (N8 首批切流域的逐字契约基线).

为什么另起一份而不扩 `gen_go_http_goldens.py`：N1 那份只录「校验错误面 + /health」，
全部不碰数据库；N8 的验收判据是「每域切流前后响应契约 diff 为空」，**成功面**（真读
users/mood_checkins 的那两条域）必须有同一批行、同一次进程内的真实字节做基准，
所以这里带 seed 播种，并把结果写成 Go 侧可直接回放的夹具。

口径：
- 同一个 TEST_DATABASE_URL：Python 侧走 `psycopg_url()`，Go 侧走 `dsn.Postgres()`（N4 方言归一）。
- `cases[].group`：`validation` 的响应不进 store（Go 测不需库），`seeded` 的必须先播种。
- volatile 字段（`generated_at`，值＝请求当下的 utc_now()）在两侧都按同一条正则替换成
  `"<generated_at>"` 后再逐字比——其余字节一个都不许掩码，否则「diff 为空」就成了空话。
- 播种行用 `days_ago`（相对当下）而不是绝对时间戳：回放可能比录制晚几分钟到几小时，
  窗口边界（7/14 天）两侧必须落在同一侧，所以每行距边界至少留 1.5 天。

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
from datetime import datetime as dt_datetime, timedelta as dt_timedelta
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
from backend.main import app  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

# ── 播种面 ────────────────────────────────────────────────────────────────────
# (id, analytics_consent, language, [距今天数, emoji])
USERS: list[tuple[str, bool, str, list[tuple[float, str]]]] = [
    ("consent-off", False, "en", []),
    ("consent-on", True, "en", []),
    ("no-rows", True, "en", []),
    ("newcomer", True, "en", [(1.5, "😊"), (2.5, "😌")]),
    ("break", True, "en", [(9.0, "😊"), (11.0, "😌")]),
    ("freq2", True, "en", [(1.0, "😊"), (2.0, "😌"), (3.0, "😌"), (4.0, "😴"),
                           (9.0, "😌"), (11.0, "😴")]),
    ("freq3", True, "en", [(1.0, "😊"), (2.0, "😌"), (3.0, "😌"), (4.0, "😴"), (5.0, "😰"),
                           (9.0, "😌"), (11.0, "😴")]),
    ("up-high", True, "en", [(1.0, "😊"), (2.0, "😊"), (9.0, "😰"), (11.0, "😰")]),
    ("down-high", True, "en", [(1.0, "😢"), (2.0, "😢"), (9.0, "😊"), (11.0, "😊")]),
    ("drift-low", True, "en", [(1.0, "😌"), (2.0, "😌"), (9.0, "😌"), (11.0, "😴")]),
    ("flat", True, "en", [(1.0, "😌"), (2.0, "😴"), (9.0, "😰"), (11.0, "😊")]),
    # 😎 不在 MOOD_MAP 里：走 `MOOD_MAP.get(emoji, (None, 3))[1]` 的默认分值 3。
    ("unknown-emoji", True, "en", [(1.0, "😎"), (2.0, "😎"), (9.0, "😌"), (11.0, "😴")]),
    # 首周里程碑 + X-Language: zh 走中文文案；同一用户也证明 header 优先于 users.language=en
    ("zh-header", True, "en", [(1.5, "😊"), (2.5, "😌")]),
    # users.language=zh 且不带 X-Language ⇒ 走 get_request_language 的第②级（读库偏好）
    ("zh-db-pref", True, "zh", [(1.5, "😊"), (2.5, "😌")]),
    # users.language=zh 但 header 给了受支持的 es ⇒ header 赢，文案回英文
    ("es-header-over-zh-db", True, "zh", [(1.5, "😊"), (2.5, "😌")]),
    # users.language='zh-CN'（带区域后缀）不在 supported_languages 里 ⇒ 整条落回 en。
    # 注册路径确实可能写下这种值（users.language 是 String(10)），两侧必须同判。
    ("zh-region", True, "zh-CN", [(1.5, "😊"), (2.5, "😌")]),
]

COLUMNS = "id, timezone, streak_count, relationship_level, chat_count, " \
          "daily_quota_used, tier, language, analytics_consent"
INSERT_USER = f"INSERT INTO users ({COLUMNS}) VALUES (%s, 'UTC', 0, 'L1', 0, 0, 'free', %s, %s)"
INSERT_MOOD = (
    "INSERT INTO mood_checkins (id, user_id, mood_emoji, created_at) "
    "VALUES (%s, %s, %s, now() - (%s || ' days')::interval)"
)


def seed() -> None:
    # psycopg 直连只认 libpq 形态的串（+psycopg 段是 SQLAlchemy 的驱动声明）
    dsn = psycopg.connect(libpq_url(RAW_TEST_URL), autocommit=True)
    with dsn.cursor() as cur:
        cur.execute("DELETE FROM mood_checkins WHERE id LIKE %s", (f"{SEED_PREFIX}%",))
        cur.execute("DELETE FROM users WHERE id LIKE %s", (f"{SEED_PREFIX}%",))
        for slug, consent, language, rows in USERS:
            uid = f"{SEED_PREFIX}{slug}"
            cur.execute(INSERT_USER, (uid, language, consent))
            for i, (days_ago, emoji) in enumerate(rows):
                cur.execute(INSERT_MOOD, (f"{uid}-m{i}", uid, emoji, str(days_ago)))
    dsn.close()


def seed_spec() -> dict:
    """把播种口径原样交给 Go 侧，两侧读同一份数字。"""
    return {
        "users": [
            {"id": f"{SEED_PREFIX}{slug}", "language": language,
             "analytics_consent": consent}
            for slug, consent, language, _ in USERS
        ],
        "mood_checkins": [
            {"id": f"{SEED_PREFIX}{slug}-m{i}", "user_id": f"{SEED_PREFIX}{slug}",
             "mood_emoji": emoji, "days_ago": days_ago}
            for slug, _, _, rows in USERS
            for i, (days_ago, emoji) in enumerate(rows)
        ],
    }


# ── 用例面 ────────────────────────────────────────────────────────────────────
# (name, group, method, path, user slug | "" (匿名), headers, body)
EV = {"event": "screen_view", "properties": {"screen": "home"},
      "occurred_at": "2026-10-01T00:00:00+00:00"}


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
    ]
    return out


def _group(path: str, declared: str, status: int) -> str:
    """组别按「请求有没有真的进 store」派生，不靠手标。

    `validation` 组是 Go 侧**无库**回放的那批；一旦某条其实读了 users 却被标成
    validation，无库回放就会拿库派生的字节去比，红得没有信息量。/analytics/batch
    只有校验全过（HTTP 200）才会走到 `relay_batch` ⇒ 用状态码判，比人盯可靠。
    """
    if path.startswith("/api/v1/analytics/batch") and status == 200:
        return "seeded"
    if declared == "validation" and status == 200:
        raise SystemExit(f"{path} 返回 200 却标为 validation：无库回放组会被污染")
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
                "group": _group(path, group, res.status_code),
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


def main() -> None:
    seed()
    recorded, samples = asyncio.run(record())
    corpus = datetime_corpus()
    payload = {
        "_comment": "generated by scripts/gen_n8_contract_goldens.py; do not hand-edit",
        "trace_id": TRACE,
        "masked_fields": ["generated_at"],
        "generated_at_samples": samples[:4],
        "seed": seed_spec(),
        "occurred_at_corpus": corpus,
        "cases": recorded,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    validation = sum(1 for c in recorded if c["group"] == "validation")
    seeded = sum(1 for c in recorded if c["group"] == "seeded")
    accepted = sum(1 for c in corpus if c["ok"])
    print(f"wrote {OUT}: {len(recorded)} cases (validation {validation} / seeded {seeded})")
    print(f"occurred_at corpus: {len(corpus)} entries ({accepted} accepted / "
          f"{len(corpus) - accepted} rejected)")
    print(f"generated_at samples: {samples[:3]}")


if __name__ == "__main__":
    main()
