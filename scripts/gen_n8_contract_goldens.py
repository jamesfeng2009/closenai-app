"""Regenerate go-core/testdata/n8_contract.json (N8 切流域的逐字契约基线).

为什么另起一份而不扩 `gen_go_http_goldens.py`：N1 那份只录「校验错误面 + /health」，
全部不碰数据库；N8 的验收判据是「每域切流前后响应契约 diff 为空」，**成功面**（真读
users/mood_checkins/journey_events 的那些域）必须有同一批行、同一次进程内的真实字节做基准，
所以这里带 seed 播种，并把结果写成 Go 侧可直接回放的夹具。

口径：
- 同一个 TEST_DATABASE_URL：Python 侧走 `psycopg_url()`，Go 侧走 `dsn.Postgres()`（N4 方言归一）。
- `cases[].group`：`validation` 的响应不进 store（Go 测不需库），`seeded` 的必须先播种，
  `curriculum` 与 `journey` 的除播种外还要把 Go 的时钟钉在夹具的 `recorded_at`
  ——批 3 的 journey 组里有一条 POST（acknowledge）会真写库，顺序同样是契约。
- volatile 字段只有两样，两侧各按**同一条正则**替换后再逐字比：
  `generated_at`（值＝请求当下的 utc_now()）⇒ `"<generated_at>"`；
  `journey_events` 的**uuid4 主键**（批 3 的 INSERT 支现生成）⇒ `"<event_id>"`，
  并且每条用例记下掩到的次数 `id_masks`，Go 侧必须掩到同样多次——否则「diff 为空」
  就成了空话。其余字节一个都不许掩。
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
# 必须匹配 Python `str(uuid4())` 的**形状**（8-4-4-4-12 小写十六进制），而且要带
# 前面的 `"id":"`——`"trace_id":"00000000-…"` 与 refs 里的 `"memory_id":"…"` 都以
# 下划线接 id，不会被这条误掩。Go 侧用同一条正则、同一个替换串。
EVENT_ID_RE = re.compile(r'"id":"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"')
MASKED_EVENT_ID = '"id":"<event_id>"'
# 批 4（onboarding）新增的两样易变值。都**不按组无条件掩**，而是只在「这个值本次运行
# 里真的是现算出来的」那几条用例上掩（判据见 record() 里的 run_created 追踪）：
#   - referral_link：惰性生成的推荐码是随机的，两侧永远不同；但种子账号**已带码**时
#     share_url 全串可逐字比，那正是「已有码原样复用」（user_repository.py:373）的证明。
#     字符集＝backend/core/referral.py 的 29 字符（A-Z 去 I/L/O ＋ 2-7），这里写成
#     [A-Z2-7]{8} 是它的**超集**：宁可多认也不漏认，掩错由 code_masks 计数暴露。
#   - created_at：onboarding_profiles.created_at 是 server_default=func.now()，
#     INSERT 支不写值 ⇒ 落库落的是**库钟**，录制与回放必然差几秒。
#     种子行给的是绝对时刻 ⇒ 那一侧不掩，逐字比反而证的是 `+00:00` 序列化格式。
REFERRAL_LINK_RE = re.compile(r"/r/[A-Z2-7]{8}")
MASKED_REFERRAL_LINK = "/r/<code>"
CREATED_AT_RE = re.compile(r'"created_at":"[^"]*"')
MASKED_CREATED_AT = '"created_at":"<created_at>"'
# 需要真库才能回放的组（这些组里 404 必须是信封体，见 _group）
# 批 5 的 mood / reflection 两组：**组名不等于域**，指的是「回放时要不要冻时钟」——
# 两组的响应里都有 created_at / %m-%d 日历字段，都按绝对时刻播种，所以都必须钉在
# recorded_at（Go 侧 srv.now、Python 侧 mood_repository.utc_now）。
DB_GROUPS = frozenset({"curriculum", "journey", "onboarding", "mood", "reflection"})
# 会现生成 uuid 主键、因而需要按形状掩两组 id 的组（批 3 的预写 INSERT、批 4 的 profile INSERT）。
ID_MASK_GROUPS = frozenset({"journey", "onboarding"})


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

def ago(days: int, hour: int | None = None, minute: int | None = None,
        second: int | None = None) -> str:
    """ANCHOR 往前 days 天的**绝对** UTC 时刻（给了 hour/minute/second 就把该位钉死）。

    为什么绝对而不是像 mood_checkins 那样按 days_ago 相对回放当下：journey 的响应里有
    scheduled_at，而它由「基准日的**本地日历日**」推出。相对口径下回放只要跨过用户本地
    午夜，day0 就换了一天，六行的字节全体平移——那不再是契约 diff，是两次不同的预写。

    批 5 把这条口径**也**用到了 mood_checkins 上（见 `M()`）：`GET /mood/trend` 的
    labels 是 `%m-%d` 日历字段，沿用相对播种会让标签随重放日期整批漂移。
    """
    at = ANCHOR - dt_timedelta(days=days)
    if hour is not None or minute is not None or second is not None:
        at = at.replace(hour=hour or 0, minute=minute or 0, second=second or 0,
                        microsecond=0)
    return at.isoformat()


def at_offset(days: int, seconds: int = 0) -> str:
    """ANCHOR − days*86400s − seconds：**逐秒**可指的绝对时刻。

    只为一格存在：`count_by_day` 的窗口是 `created_at >= utc_now() − days*86400`
    （`mood_repository.py:194`，闭区间）。要钉住这个 `>=`，必须有一行**正好落在**
    边界瞬间上（`ago(14)`＝ANCHOR−14d，与冻钟后的 utc_now−14d 同一瞬）＋
    一行差一秒落在窗外（本函数 days=14、seconds=1）。
    """
    return (ANCHOR - dt_timedelta(days=days, seconds=seconds)).isoformat()


def M(created_at: str, emoji: str, label: object = None,
      ai: object = None) -> dict:
    """一行**绝对时刻**的签到（批 5）。

    与 `rows: [(days_ago, emoji)]` 的批 1 口径并存、互不影响：那批行的响应里只有
    7/14 天窗的**计数与均值**，换了播种口径就会改动批 1 已结案用例的字节。
    `label`/`ai` 给的是**列原文**：None⇒SQL NULL、`""`⇒空串——`get_history` 的
    `r.mood_label or ""`(:315) 对这两者的响应相同，但列里不同，夹具要两种都种。
    """
    return {"created_at": created_at, "mood_emoji": emoji,
            "mood_label": label, "ai_interpretation": ai}


def G(created_at: str, record_type: str, content: str,
      refs: object = None) -> dict:
    """一行成长记录（批 5 的 `GET /reflections` 读 growth_records）。

    `refs` 给**列原文**（None⇒SQL NULL、""⇒空串、","⇒两个空 ID、"a,,b"⇒三段），
    因为 `reflection_service.py:136-138` 是 `列.split(",") if 列 else None`——
    假值那一支把 NULL 与空串并成 None，而真值那一支的段数完全由逗号位置决定。
    """
    return {"created_at": created_at, "record_type": record_type,
            "content": content, "related_memory_ids": refs}


def first_read_column(assertions: list[str], summary: str, closer: str, *,
                      locale: str = "en", persona: str | None = None,
                      share_url: str = "https://closenai.app/r/AB2CD3EF",
                      generated_at: str = "2026-10-05T00:00:00+00:00") -> str:
    """按 submit 落库的形状折出 `onboarding_profiles.first_read` 的**列原文**。

    为什么种子行要按这个形状写而不是随手给个 dict：restore 是 `json.loads(列)` 之后
    `dict(card["card_payload"])` 再 `update(六键)`——**响应里的键序就是列里的键序**
    （Python 的 dict 保插入序、json.loads 按文本序落）。所以列原文写歪一格，
    录到的就是另一条契约。分隔符同 `pyDumpsSpaced`（`", "` / `": "`）、ensure_ascii=False，
    与 onboarding_repository.upsert 写出去的那一串逐字同形。
    """
    card_payload = {
        "card_type": "first_read",
        "language": locale,
        "persona_type": persona,
        "assertions": assertions,
        "summary": summary,
        "closer": closer,
        "answer_count": len(assertions),
        "share_url": share_url,
        "generated_at": generated_at,
    }
    outer = {"assertions": assertions, "summary": summary, "closer": closer,
             "card_payload": card_payload}
    return json.dumps(outer, ensure_ascii=False)


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
    # ── 批 3（journey）───────────────────────────────────────────────────────
    # push=users.push_time 的**列原文**（None⇒NULL，与 "" 同一条 `or "09:00"` 分支）；
    # created=users.created_at 的**绝对时刻**（剧本基准日由它推出，相对口径会让
    # 跨午夜的回放整条剧本平移一天，见上面 ANCHOR 的注释）；
    # profile=onboarding_profiles.created_at（"null"⇒插一行但列为 NULL）。
    {"slug": "j-full", "analytics": True, "language": "en", "push": "09:00",
     "created": ago(4)},
    {"slug": "j-null-push", "analytics": True, "language": "en", "push": None,
     "created": ago(1)},
    {"slug": "j-empty-push", "analytics": True, "language": "en", "push": "",
     "created": ago(1)},
    # "24:99" 五字符、两个分段都是 \d{2} ⇒ **今天就能 PATCH 进列**，clamp 支不是历史脏值专属
    {"slug": "j-clamp", "analytics": True, "language": "en", "push": "24:99",
     "created": ago(3)},
    # 全角数字同一条 PATCH 路径可达（schemas.py:400 的 \d 是 Unicode 感知，列按字符计宽）
    {"slug": "j-fullwidth", "analytics": True, "language": "en",
     "push": "１２:３４", "created": ago(2)},
    # 单段「7」过不了 PATCH ⇒ 只能当**历史脏值**播（_push_moment 的 except 支）
    {"slug": "j-legacy-dirty", "analytics": True, "language": "en", "push": "7",
     "created": ago(2)},
    # 本地今天 ≠ UTC 今天的一支：created 取 UTC 18:00 ⇒ +08:00 已是次日，
    # day0 因此比同刻的 UTC 用户晚一天（拿 now 的 UTC 日期当基准日就是这里错）。
    {"slug": "j-sh", "analytics": True, "language": "zh", "push": "23:59",
     "created": ago(2, hour=18), "timezone": "Asia/Shanghai"},
    # 春季拨快：纽约 2026-03-08 的 02:00–02:59 不存在，push="02:30" 的落点是
    # Python fold=0 的**切换前**偏移——Go 的裸 time.Date 在这条上差整整一小时。
    {"slug": "j-gap-ny", "analytics": True, "language": "en", "push": "02:30",
     "created": "2026-03-08T15:00:00+00:00", "timezone": "America/New_York"},
    # 秋季回拨：2026-11-01 的 01:30 出现两次，Python 取第一次（EDT⇒05:30Z）。
    # 基准日刻意在未来（ANCHOR 是 10 月）⇒ 六行全不 Past，skip 支一步都不许走。
    {"slug": "j-repeat-ny", "analytics": True, "language": "en", "push": "01:30",
     "created": "2026-11-01T15:00:00+00:00", "timezone": "America/New_York"},
    # 切换点就在本地午夜附近的区（智利 2026-09-06 跳过 00:00–00:59）：
    # push="00:30" 严格落在缺位里，而这一支与批 2 修掉的「本地零点折算」同族。
    {"slug": "j-gap-cl", "analytics": True, "language": "en", "push": "00:30",
     "created": "2026-09-06T05:00:00+00:00", "timezone": "America/Santiago"},
    # (kind, day_no) 对不上：六行全走 INSERT 支（id＝uuid4，两侧各掩同一条正则）
    {"slug": "j-dedup-key", "analytics": True, "language": "en", "push": "09:00",
     "created": ago(2)},
    {"slug": "j-no-rows", "analytics": True, "language": "en", "push": "09:00",
     "created": ago(2)},
    {"slug": "j-deleted", "analytics": True, "language": "en", "push": "09:00",
     "created": ago(2), "status": "deleted"},
    # 51 行 + 预写插入的 6 行 = 57 行，钉 LIMIT 50 与 ORDER BY (day_no, scheduled_at)
    {"slug": "j-limit", "analytics": True, "language": "en", "push": "09:00",
     "created": ago(2)},
    # 文本列坏值：预写命中的行只能验 memory_refs（payload 必被覆盖），
    # payload 的坏形另挂六行 kind 不匹配的行走「不被碰」那条路。
    {"slug": "j-badcols", "analytics": True, "language": "en", "push": "09:00",
     "created": ago(2)},
    {"slug": "j-lang-empty", "analytics": True, "language": "", "push": "09:00",
     "created": ago(2)},
    {"slug": "j-lang-nonascii", "analytics": True, "language": "ｚｈ-CN", "push": "09:00",
     "created": ago(2)},
    # profile 的 created_at 胜出 user 的：基准日 9 天前 ⇒ 六行全 Past
    {"slug": "j-profile", "analytics": True, "language": "en", "push": "09:00",
     "created": ago(2), "profile": ago(9)},
    # profile 在但 created_at 为 NULL ⇒ 退回 user.created_at（`profile and profile.created_at`）
    {"slug": "j-profile-null", "analytics": True, "language": "en", "push": "09:00",
     "created": ago(2), "profile": "null"},
    # 基准日在未来（列允许，且 schedule_trial_d5 那种「绝对历史时刻」同形）⇒ 全不 Past
    {"slug": "j-future", "analytics": True, "language": "en", "push": "09:00",
     "created": "2027-01-05T12:00:00+00:00"},
    # ── 批 4（onboarding 两条端点）────────────────────────────────────────────
    # referral＝users.referral_code 的**列原文**（不给⇒SQL NULL⇒POST 现算随机码）。
    # 已带码的那几条是「已有码原样复用」的逐字证明：share_url 全串不掩。
    # profile 给 dict⇒四列全给原文（None⇒SQL NULL），给 str/`"null"`⇒批 3 的旧口径。
    {"slug": "o-code-zh", "analytics": True, "language": "zh", "push": "09:00",
     "created": ago(3), "referral": "QQ22WW33"},
    {"slug": "o-nocode", "analytics": True, "language": "en", "push": "09:00",
     "created": ago(1)},
    # 已注销：get_user 过滤⇒user is None⇒只写 profile，share_url 停在 app_store_url
    {"slug": "o-deleted", "analytics": True, "language": "en", "push": "09:00",
     "created": ago(1), "status": "deleted", "referral": "ZZ22XX33"},
    # 恢复用的四列全给真值：language 存的是**请求原文**（这里给 "ｚｈ-CN"，
    # 与 card_payload 里的 locale "en" 不同形⇒restore 的 outer update 必须看到列原文）
    {"slug": "o-restore-full", "analytics": True, "language": "zh", "push": "09:00",
     "created": ago(5), "referral": "AB2CD3EF",
     "profile": {"persona_type": "INTJ",
                 "answers": '[{"q": "conflict", "option": "speak_now"}]',
                 "first_read": first_read_column(
                     ["在意的事你当场说出来——你不愿意让误会过夜。",
                      "You say more to yourself than you ever say out loud."],
                     "你把自己归为 INTJ：先把事情想透再开口。", "",
                     locale="en", persona=None),
                 "language": "ｚｈ-CN"}},
    # first_read 列是坏 JSON ⇒ restore 走 JSONDecodeError ⇒ 40402（与「没有行」同码不同因）
    {"slug": "o-restore-broken", "analytics": True, "language": "en", "push": "09:00",
     "created": ago(5), "profile": {"persona_type": None, "answers": "",
                                    "first_read": "{oops", "language": "en"}},
    # first_read 列 NULL ⇒ `profile.first_read or "{}"`；language 列 NULL ⇒ "language":null
    {"slug": "o-restore-null", "analytics": True, "language": "en", "push": "09:00",
     "created": ago(5), "profile": {"persona_type": None, "answers": None,
                                    "first_read": None, "language": None}},
    # first_read 列是 JSON **标量**（D-8 的无观测点一支：Python 的 card.get 会 AttributeError）
    # 这里给 "5"，restore 走 `dict(card.get(...) or {})`⇒AttributeError⇒未处理 500，
    # 录不进夹具（录制客户端直接抛）。所以**不设这条用例**，只登记。
    {"slug": "o-restore-array", "analytics": True, "language": "en", "push": "09:00",
     "created": ago(5), "profile": {"persona_type": None, "answers": "[]",
                                    "first_read": "[]", "language": ""}},
    # locale 归一的四支：""⇒en、"ja"⇒en、"ｚｈ-CN"⇒en（全角不 startswith "zh"）、"ZH"⇒zh
    {"slug": "o-lang-empty", "analytics": True, "language": "", "push": "09:00",
     "created": ago(1)},
    {"slug": "o-lang-ja", "analytics": True, "language": "ja", "push": "09:00",
     "created": ago(1)},
    {"slug": "o-lang-fullwidth", "analytics": True, "language": "ｚｈ-CN",
     "push": "09:00", "created": ago(1)},
    {"slug": "o-lang-upper-zh", "analytics": True, "language": "ZH", "push": "09:00",
     # 码必须与 o-code-zh **不同**：users.referral_code 上有 UNIQUE
     # （uq_users_referral_code，播种时实测撞出来）——这条约束也正是
     # ensure_referral_code 撞码要重生成那一支存在的原因。
     "created": ago(1), "referral": "TT55KK77"},
    # PyUpper 的正证两条：SSSS（1→2 展开凑满四位）、FINT（连字 ﬁ→FI）。
    # 都不给种子码 ⇒ share_url 的 /r/<code> 必掩，见 record() 的 runtime_code。
    {"slug": "o-persona-sharp", "analytics": True, "language": "zh", "push": "09:00",
     "created": ago(1)},
    {"slug": "o-persona-ligature", "analytics": True, "language": "en", "push": "09:00",
     "created": ago(1)},
    # ── 批 5（mood / reflections 三条纯读）─────────────────────────────────────
    # 这批全部用 `abs_rows`/`growth`（绝对时刻）而不是 `rows`（相对当下）：
    # `GET /mood/trend` 的 labels 是 `%m-%d` 日历字段，`GET /mood/history` 与
    # `GET /reflections` 的 created_at 直接进响应——相对播种 + 回放跨天＝整批行漂移，
    # 逐字比就变成比两次不同的数据集。代价是这两组用例必须冻时钟（见 freeze_clock）。
    # 零行用户：history 的 `checkins: []`、trend 的 `labels/values: []` 与
    # summary 走 `mood.trend.empty`——**空数组不是 null** 这一格只能由它取证书。
    {"slug": "mood-empty", "analytics": True, "language": "en"},
    # 列原文的三种假值与一条含引号/反斜杠/换行的正文：钉 `or ""` 与 JSON 转义
    {"slug": "mood-nulls", "analytics": True, "language": "en", "abs_rows": [
        M(ago(1, hour=3), "😊"),
        M(ago(2, hour=3), "😌", "", ""),
        M(ago(3, hour=3), "😰", "焦虑", '含"引号"与\\反斜杠\n换行'),
        M(ago(4, hour=3), "😢", "难过", None),
    ]},
    # 35 行：钉 `get_recent(limit=30)` 的截断**与** DESC 序（第 1、2 行必须被切掉）
    {"slug": "mood-many", "analytics": True, "language": "en",
     "abs_rows": [M(ago(k, hour=5), ["😊", "😌", "😰", "😢", "😡", "😴"][k % 6])
                  for k in range(1, 36)]},
    # 已注销用户：`get_history` 不查 users 表 ⇒ 仍回 200 带行（与 discovery 的 40401 分叉）
    {"slug": "mood-deleted", "analytics": True, "language": "en", "status": "deleted",
     "abs_rows": [M(ago(1, hour=6), "😊"), M(ago(2, hour=6), "😌")]},
    # 趋势四档阈值：avg>=4 ⇒ high、>=3 ⇒ mid、else low、空 ⇒ empty。
    # [5,4]⇒4.5 high；[3,3,4]⇒3.333 mid（浮点除法，整数均值会算成 3 也落 mid，
    # 所以另给 [4,2]⇒3.0 那一格钉「>=3 取 mid」而不是「>3」）；[2,1]⇒1.5 low。
    {"slug": "mood-t-high", "analytics": True, "language": "en",
     "abs_rows": [M(ago(1, hour=8), "😊"), M(ago(2, hour=8), "😌")]},
    {"slug": "mood-t-mid", "analytics": True, "language": "en",
     "abs_rows": [M(ago(1, hour=8), "😴"), M(ago(2, hour=8), "😴"),
                  M(ago(3, hour=8), "😊")]},
    # 恰好 3.0：钉 `avg >= 3` 的等号侧
    {"slug": "mood-t-exactly3", "analytics": True, "language": "en",
     "abs_rows": [M(ago(1, hour=8), "😊"), M(ago(2, hour=8), "😰")]},
    {"slug": "mood-t-low", "analytics": True, "language": "en",
     "abs_rows": [M(ago(1, hour=8), "😰"), M(ago(2, hour=8), "😢")]},
    # 未知 emoji 😎 ⇒ `MOOD_MAP.get(emoji,(None,3))[1]`＝3；与已知值混排看均值
    {"slug": "mood-t-unknown", "analytics": True, "language": "en",
     "abs_rows": [M(ago(1, hour=8), "😎"), M(ago(2, hour=8), "😎")]},
    # 窗口边界：一行正好落在 `utc_now() − 14d`（闭区间 ⇒ 进窗），一行差一秒（出窗）。
    # 冻钟后两侧的比较瞬间都是 ANCHOR，这一格才是「`>=` 而不是 `>`」的证据。
    {"slug": "mood-t-edge", "analytics": True, "language": "en",
     "abs_rows": [M(at_offset(14), "😊"), M(at_offset(14, 1), "😢"),
                  M(at_offset(13, 1), "😌")]},
    # Asia/Shanghai(UTC+8)：UTC 23:00 那一行落在**次一个**本地日 ⇒ labels 与 UTC 口径不同
    {"slug": "mood-t-sh", "analytics": True, "language": "zh",
     "timezone": "Asia/Shanghai",
     "abs_rows": [M(ago(1, hour=23), "😊"), M(ago(2, hour=23), "😰"),
                  M(ago(3, hour=1), "😌")]},
    # 非法时区名 ⇒ safe_zoneinfo 回退 UTC（与 Go 的 LoadLocation 失败支同口径）
    {"slug": "mood-t-badtz", "analytics": True, "language": "en",
     "timezone": "Not/AZone",
     "abs_rows": [M(ago(1, hour=23), "😊"), M(ago(2, hour=23), "😰"),
                  M(ago(3, hour=1), "😌")]},
    # 同一本地日两行（01:00 与 05:00 UTC，都在 10-13）⇒ `if key not in day_map`
    # 取升序**首行**的 emoji（😊=5 而不是 😢=1）。这是那条判据唯一的可达格。
    {"slug": "mood-t-sameday", "analytics": True, "language": "en",
     "abs_rows": [M(ago(1, hour=1), "😊"), M(ago(1, hour=5), "😢")]},
    # 已注销用户也走一遍 trend：`get_user`⇒None ⇒ tz=None ⇒ UTC，**不是** 40401
    {"slug": "mood-t-deleted", "analytics": True, "language": "en", "status": "deleted",
     "timezone": "Asia/Shanghai",
     "abs_rows": [M(ago(1, hour=23), "😊"), M(ago(2, hour=23), "😊")]},
    # reflections：零行 ⇒ `data: []`
    {"slug": "refl-empty", "analytics": True, "language": "en"},
    # 25 行 ⇒ 钉 `limit` 默认 20 与 DESC 序（前 5 行必须被切掉）
    {"slug": "refl-many", "analytics": True, "language": "en",
     "growth": [G(ago(k, hour=9), "reflection", f"第 {k} 篇反思") for k in range(1, 26)]},
    # related_memory_ids 的四种列原文 + 正文里的转义素材
    {"slug": "refl-nulls", "analytics": True, "language": "en",
     "growth": [
         G(ago(1, hour=9), "reflection", "无关联", None),
         G(ago(2, hour=9), "reflection", "空串关联", ""),
         G(ago(3, hour=9), "reflection", '逗号"与\\斜杠', ","),
         G(ago(4, hour=9), "reflection", "三段", "a,,b"),
         G(ago(5, hour=9), "reflection", "单个", "only"),
     ]},
    # 三种 record_type 混排 ⇒ 只有 reflection 进响应
    {"slug": "refl-types", "analytics": True, "language": "en",
     "growth": [
         G(ago(1, hour=9), "reflection", "反思一"),
         G(ago(2, hour=9), "daily_insight", "洞察不该出现"),
         G(ago(3, hour=9), "weekly_summary", "周报不该出现"),
         G(ago(4, hour=9), "reflection", "反思二"),
         G(ago(5, hour=9), "Reflection", "大小写不同也算另一类"),
     ]},
    # 已注销用户：`reflection_service.list` 同样不查 users ⇒ 200 带回行
    {"slug": "refl-deleted", "analytics": True, "language": "en", "status": "deleted",
     "growth": [G(ago(1, hour=9), "reflection", "注销前的反思")]},
]

COLUMNS = ("id, timezone, streak_count, relationship_level, chat_count, "
           "daily_quota_used, tier, language, analytics_consent, ai_consent, status, "
           "push_time, created_at, referral_code")
INSERT_USER = (f"INSERT INTO users ({COLUMNS}) VALUES (%s, %s, 0, 'L1', 0, 0, 'free', "
               f"%s, %s, %s, %s, %s, %s, %s)")
INSERT_MOOD = (
    "INSERT INTO mood_checkins (id, user_id, mood_emoji, created_at) "
    "VALUES (%s, %s, %s, now() - (%s || ' days')::interval)"
)
# status/sent_at/acknowledged 三个列批 1/批 2 靠 server_default（'pending'/NULL/false），
# 批 3 必须显式写：skip 的判据是「**落库前** status 仍为 pending」，count_unseen 只数
# status='sent' 且 acknowledged 为假——这三列不播种就永远只有一条路径可证。
# 默认值原样传进去（curriculum 行的字节因此不变）。
INSERT_EVENT = (
    "INSERT INTO journey_events (id, user_id, event_kind, day_no, scheduled_at, "
    "payload, memory_refs, status, sent_at, acknowledged) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
# 批 3 只用得到 created_at，所以 persona/answers/first_read/language 四列走四个字面量。
# 批 4 的 restore 把四列全读出来（language 直接进响应、first_read 决定键序），
# 于是 INSERT 改成六列显式给值：**批 3 那几行传的还是同四个字面量**，字节不变。
INSERT_PROFILE = (
    "INSERT INTO onboarding_profiles (id, user_id, persona_type, answers, first_read, "
    "language, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)"
)
# 批 3 旧口径（profile 给 str 或 "null"）落的四列字面量
LEGACY_PROFILE_COLUMNS = ("D01", "[]", "{}", "en")
# 批 5：同一张表的**绝对时刻**通道（见上面 `M()` 的注释）。
# 带 mood_label / ai_interpretation 两列——`get_history` 把两列原样读进响应
# （`mood_service.py:315-316` 的 `or ""`），所以 NULL 与空串必须都能种。
INSERT_MOOD_ABS = (
    "INSERT INTO mood_checkins (id, user_id, mood_emoji, mood_label, "
    "ai_interpretation, created_at) VALUES (%s, %s, %s, %s, %s, %s)"
)
# 批 5：`GET /reflections` 读的是 growth_records（record_type='reflection'）。
# tags/metadata_json/follow_up_sent/generation_run_id 四列留给 server_default，
# 本域的响应面不读它们（读了就是另一批契约）。
INSERT_GROWTH = (
    "INSERT INTO growth_records (id, user_id, record_type, content, "
    "related_memory_ids, created_at) VALUES (%s, %s, %s, %s, %s, %s)"
)


def profile_row(user: dict) -> dict | None:
    """把 `user["profile"]` 换算成一行 onboarding_profiles 播种计划；没有该键⇒None。

    两种形态刻意并存，不改批 3 那六条用例的字节：
      - str `"null"` ⇒ 只插行、created_at 为 SQL NULL；其它 str ⇒ created_at 是**绝对时刻**；
      - dict ⇒ 四列全给**列原文**（None⇒SQL NULL，空串就是空串），created_at 可选。

    dict 分支的默认值刻意**不等于 ANCHOR**：restore 响应里的 created_at 取的是
    `profile.created_at` 列，而回放两侧都把时钟冻在 ANCHOR。若种子也种成 ANCHOR，
    「从列取」和「从钟算」会算出同一串字节，这条支路就被巧合蒙过了。
    """
    spec = user.get("profile")
    if spec is None:
        return None
    uid = f"{SEED_PREFIX}{user['slug']}"
    if isinstance(spec, dict):
        persona, answers, first_read, language = (
            spec.get("persona_type", LEGACY_PROFILE_COLUMNS[0]),
            spec.get("answers", LEGACY_PROFILE_COLUMNS[1]),
            spec.get("first_read", LEGACY_PROFILE_COLUMNS[2]),
            spec.get("language", LEGACY_PROFILE_COLUMNS[3]))
        created = spec.get("created_at", ago(4))
    else:
        persona, answers, first_read, language = LEGACY_PROFILE_COLUMNS
        created = None if spec == "null" else spec
    return {"id": f"{uid}-p", "user_id": uid, "persona_type": persona,
            "answers": answers, "first_read": first_read, "language": language,
            "created_at": created}


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


def column_text(value: object) -> str | None:
    """dict/list 按 json.dumps 成 TEXT 列原文；str **原样入库**（坏 JSON、空串只能这么造）；
    None ⇒ 该列为 SQL NULL（parse_json 的空值分支）。"""
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def I(i: int, day_no: int, payload: object, refs: object = None,
      hour: int = 0, kind: str = "curriculum") -> dict:
    """一条 journey_events 播种计划：i＝本地周内第几天（0＝周一）。

    payload/refs 给 dict/list 时按 json.dumps 成文本列；给 str 时**原样入库**（坏 JSON、
    空串这些形状只能这么造）；给 None 时该列为 NULL（parse_json 的空值分支）。
    """
    return {"i": i, "day_no": day_no, "payload": payload, "refs": refs,
            "hour": hour, "kind": kind,
            # 批 2 的三条默认值＝当时的 server_default，curriculum 的字节因此不变。
            "status": "pending", "sent_at": None, "acknowledged": False}


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
    """把某个用户的行计划换算成**绝对** scheduled_at（按该用户的时区算本地零点）。

    批 2 用它铺一周的主题卡（i＝本地周内第几天）；批 3 的 journey 行只把 i 固定为 0、
    用 hour 逐行错开——预写**必改** scheduled_at，所以播种值只要与预写算出来的落点不同
    就够（响应里出现播种值＝预写没走到，这正是 j-deleted 那条用例的判据）。
    """
    tz = timezone_of(next(u for u in USERS if u["slug"] == slug))
    monday = local_today_at(tz) - dt_timedelta(days=local_today_at(tz).weekday())

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
            "status": item["status"],
            "sent_at": item["sent_at"],
            "acknowledged": item["acknowledged"],
        }
        for n, item in enumerate(items)
    ]


def seeded_events() -> list[dict]:
    """全部 journey_events 播种行（Go 侧按同一份夹具插，两侧读同一批行）。"""
    out: list[dict] = []
    for slug, items in EVENT_PLAN.items():
        out += mk(slug, items)
    for slug, items in JOURNEY_PLAN.items():
        out += mk(slug, items)
    return out


# ── 批 3（journey）的预播种口径 ───────────────────────────────────────────────
# GET /journey/events 是**惰性写入者**：ensure_prefilled 先按 (user_id, event_kind,
# day_no) 查重，命中就改 scheduled_at/payload（**不改 status、不写 memory_refs**），
# 没命中就插一行 uuid4 主键。于是夹具分两条路：
#   - 六行全预播种 ⇒ 恒走 UPDATE 支 ⇒ 主键稳定（n8b1-…-eN），响应可逐字比；
#   - 只在「要证 INSERT 支」的两个用户（j-dedup-key / j-no-rows）上留空，
#     那两例的 uuid 主键由两侧同一条正则掩成 <event_id>，并核对**掩到的次数**。
# 哨兵值是让 UPDATE 支可证的关键：payload 必须消失、memory_refs 必须留下。
from backend.services.journey_service import SCRIPT_EVENTS  # noqa: E402


def J(day_no: int, kind: str, payload: object, refs: object, status: str = "pending",
      sent_at: str | None = None, acknowledged: bool = False, hour: int = 0) -> dict:
    return {"i": 0, "day_no": day_no, "payload": payload, "refs": refs, "hour": hour,
            "kind": kind, "status": status, "sent_at": sent_at,
            "acknowledged": acknowledged}


def j_matching(overrides: dict[int, dict] | None = None) -> list[dict]:
    """六行与 SCRIPT_EVENTS 逐字对齐（kind 与 day_no 同源，不手抄）⇒ 全走 UPDATE 支。

    overrides 按 day_no 换某行的 status/sent_at/acknowledged/payload/refs——
    scheduled_at 不给，因为预写必改它，播种值只是占位。
    """
    out: list[dict] = []
    for n, (kind, offset) in enumerate(SCRIPT_EVENTS):
        spec = {"payload": {"sentinel": offset}, "refs": [{"keep": True}], "hour": n}
        spec.update((overrides or {}).get(offset, {}))
        out.append(J(offset, kind, **spec))
    return out


JOURNEY_PLAN: dict[str, list[dict]] = {
    # 六行各占一条 status 支：pending(将 Past⇒skipped)、pending、sent(已 Past 也不动)、
    # seen+ack、sent(未来，unseen 计数)、skipped(未来，不被 skip 改) ⇒ 一次用例钉住
    # 「skip 只改 status=='pending'」与 count_unseen 的两个谓词。
    "j-full": j_matching({
        2: {"status": "sent", "sent_at": "2026-10-01T03:04:05.123456+00:00"},
        3: {"status": "seen", "acknowledged": True,
            "sent_at": "2026-10-02T03:04:05+00:00"},
        5: {"status": "sent", "sent_at": "2026-10-03T03:04:05.100000+00:00"},
        7: {"status": "skipped"},
    }),
    "j-null-push": j_matching(),
    "j-empty-push": j_matching(),
    "j-clamp": j_matching(),
    "j-fullwidth": j_matching(),
    "j-legacy-dirty": j_matching(),
    "j-sh": j_matching(),
    "j-gap-ny": j_matching(),
    "j-repeat-ny": j_matching(),
    "j-gap-cl": j_matching(),
    "j-lang-empty": j_matching(),
    "j-lang-nonascii": j_matching(),
    "j-profile": j_matching(),
    "j-profile-null": j_matching(),
    "j-future": j_matching(),
    # kind 对但 day_no 全是 9x ⇒ get_by_kind 不命中 ⇒ 六行走 INSERT 支
    "j-dedup-key": [J(90 + o, k, None, None, hour=n)
                    for n, (k, o) in enumerate(SCRIPT_EVENTS)],
    "j-no-rows": [],
    # 注销用户：预写在 get_user 那一格静默返回 ⇒ 这两行的哨兵 scheduled_at 必须**原样**出现
    "j-deleted": [J(0, "day0_read", {"sentinel": 0}, [{"keep": True}]),
                  J(7, "day7_report", {"sentinel": 7}, [{"keep": True}])],
    # 51 行 level_reward（kind 永不匹配）＋预写插入的 6 行 = 57 行 ⇒ LIMIT 50 截掉
    # day_no 44..50 那七行；同 day_no 上的先后由 scheduled_at 单键决定，无并列。
    "j-limit": [J(n, "level_reward", {"n": n}, None, hour=n) for n in range(51)],
    # 预写命中的六行只能验 memory_refs（payload 必被覆盖）；payload 的坏形挂在
    # kind 不匹配的行上走「不被碰」那条路。parse_json **没有** isinstance 守卫。
    "j-badcols": (j_matching({o: {"refs": r} for o, r in zip(
        (0, 1, 2, 3, 5, 7),
        (None, "", "{oops", '[{"memory_id":"m"}]', '"字符串"', "null"))})
        + [J(20 + n, "level_reward", p, None, hour=100 + n) for n, p in enumerate(
            (None, "", "{oops", "[1,2]", '"s"', "5", "null"))]),
}


def _module(name: str):
    """按**模块名**取模块对象，而不是按包属性。

    2026-10-15 实测到的坑：`backend/repositories/__init__.py:26` 有
    `from backend.repositories.mood_repository import ... mood_repository`，
    这把包属性 `backend.repositories.mood_repository` 从**子模块**换成了
    **MoodRepository 单例实例**。于是 `from backend.repositories import mood_repository`
    拿到的是实例，`实例.utc_now = lambda: ANCHOR` 只是给对象加了个函数全局永远读不到的
    属性——时钟钉了个空，录制用的还是真实当下（第一批就靠这个 bug 把 trend-edge-14
    的边界行录成了「窗口外」）。`importlib.import_module` 读的是 sys.modules，拿到的是
    真模块对象；`import a.b.c as m` 与 `from a.b import c` 都会走属性查找，都躲不开。
    """
    import importlib
    import types

    mod = importlib.import_module(name)
    if not isinstance(mod, types.ModuleType):
        raise SystemExit(f"{name} 取到的不是模块而是 {type(mod)}——包再导出把模块名遮蔽了")
    return mod


def freeze_clock() -> None:
    """把 curriculum/journey/first_read 服务与 mood 仓库读到的「当下」钉在 ANCHOR（见上面 ANCHOR 的注释）。

    curriculum：get_week 用 local_today 取今天、mark_read 用 utc_now 写 read_at。
    journey：prefill 的 `today = local_today(user.timezone)` 决定哪一行的 Past；
    它的 base_day 由**绝对**播种的 created_at 推出，所以不钉 local_today 的话，
    回放一旦跨过用户本地午夜，六行的 status 就会整批错位（pending vs skipped）。
    窗口端点走的 local_day_start_utc(tz, day) 收的是**显式日期**，不碰时钟。
    first_read（批 4）：`card_payload["generated_at"] = utc_now().isoformat()`(:163)
    **直接进响应字节**，不钉就会差录制与回放之间那几秒；`submit` 的
    `prefill(..., base_day=local_today(user.timezone))`(:195) 决定六行的 day_no，
    回放跨过本地午夜就会让 POST 预写的那批行整体挪一天（Go 侧对应 Server.now）。
    mood（批 5）：`count_by_day` 的窗口起点是 `utc_now() − days*86400`(:194)，而
    `mood-t-edge` 那三行是**按 ANCHOR** 种的绝对时刻（其中一行正好压在边界上）；
    不钉这一支，回放晚于录制一秒就会把边界行读进/读出窗口。
    `local_today`/`safe_zoneinfo` 在 mood 侧不需要钉：trend 的分组只做
    `astimezone(zone).date()`，没有「今天」这个概念；bedtime 才用
    local_today（那批另立）。

    下面**逐行点名**写，不用 `for mod in modules.values()`：批 2/批 3 的冻钟闸
    （test_recorder_freezes_python_clock_for_curriculum / …_the_journey_clock）
    按「行首 + `<x>_module.<名字> =`」匹配，循环体里那句 `mod.utc_now` 会让这两条
    判据同时失去锚点，而变异 #72（撤掉冻 utc_now 那一行）与 #112（钉到 time_utils）
    证的恰恰是「这一行能被单独写坏」。
    """
    curriculum_module = _module("backend.services.curriculum_service")
    journey_module = _module("backend.services.journey_service")
    first_read_module = _module("backend.services.first_read_service")
    mood_module = _module("backend.repositories.mood_repository")

    modules = {
        "curriculum": curriculum_module,
        "journey": journey_module,
        "first_read": first_read_module,
        "mood": mood_module,
    }

    curriculum_module.utc_now = lambda: ANCHOR
    journey_module.utc_now = lambda: ANCHOR
    first_read_module.utc_now = lambda: ANCHOR
    mood_module.utc_now = lambda: ANCHOR
    curriculum_module.local_today = local_today_at
    journey_module.local_today = local_today_at
    first_read_module.local_today = local_today_at

    # 钉完**自证**一次：上面那四个模块的 utc_now 必须真的返回 ANCHOR。
    # 这条不是装饰——批次 2–4 至今全绿，正是因为它们的判据离边界够远；
    # 只有批 5 那行压边界的种子把「钉了个空」变成了可证的字节差。
    # 如果哪天再导出换了形状，这里会在录制**之前**就停下，而不是录出一份错基准。
    for name, mod in modules.items():
        if mod.utc_now() != ANCHOR:
            raise SystemExit(
                f"{name} 的 utc_now 没能钉住 ANCHOR（实得 {mod.utc_now()}）——"
                f"补丁落在了读不到的名字上，这份夹具不作数")
    for name in ("curriculum", "journey", "first_read"):
        if modules[name].local_today("UTC") != ANCHOR.date():
            raise SystemExit(
                f"{name} 的 local_today 没能钉住 ANCHOR（实得 {modules[name].local_today('UTC')}）")



def seed() -> None:
    # psycopg 直连只认 libpq 形态的串（+psycopg 段是 SQLAlchemy 的驱动声明）
    dsn = psycopg.connect(libpq_url(RAW_TEST_URL), autocommit=True)
    with dsn.cursor() as cur:
        # journey_events.user_id 没有外键，但同一批 id 的旧行会撞主键，先删。
        # **按 user_id 一起删**：批 3 的预写支会插出带随机 uuid 主键的行，
        # 只按 id LIKE 清会把它们留在库里，下一轮两侧读到的行数就不同了。
        cur.execute("DELETE FROM mood_checkins WHERE id LIKE %s OR user_id LIKE %s",
                    (f"{SEED_PREFIX}%", f"{SEED_PREFIX}%"))
        cur.execute("DELETE FROM journey_events WHERE id LIKE %s OR user_id LIKE %s",
                    (f"{SEED_PREFIX}%", f"{SEED_PREFIX}%"))
        cur.execute("DELETE FROM onboarding_profiles WHERE id LIKE %s OR user_id LIKE %s",
                    (f"{SEED_PREFIX}%", f"{SEED_PREFIX}%"))
        # 批 5 的 reflections 读这张表；GET /mood/* 与 /reflections 都是纯读，
        # 但**必须先清**——上一轮遗留的行会让 limit 截断落在不同行上。
        cur.execute("DELETE FROM growth_records WHERE id LIKE %s OR user_id LIKE %s",
                    (f"{SEED_PREFIX}%", f"{SEED_PREFIX}%"))
        cur.execute("DELETE FROM users WHERE id LIKE %s", (f"{SEED_PREFIX}%",))
        for user in USERS:
            uid = f"{SEED_PREFIX}{user['slug']}"
            cur.execute(INSERT_USER, (uid, timezone_of(user), user["language"],
                                      user["analytics"], user.get("ai", False),
                                      user.get("status", "active"), user.get("push"),
                                      user.get("created", ANCHOR.isoformat()),
                                      # referral_code 不给⇒SQL NULL（POST 走惰性生成那一支）
                                      user.get("referral")))
            for i, (days_ago, emoji) in enumerate(user.get("rows", [])):
                cur.execute(INSERT_MOOD, (f"{uid}-m{i}", uid, emoji, str(days_ago)))
            # 批 5 的绝对时刻通道：与上面那条 INSERT 只差在 created_at 是列原文，
            # 行主键沿用同一个 `-m{i}` 编号规则（两个键不会同时出现在一个用户上）。
            for i, row in enumerate(user.get("abs_rows", [])):
                cur.execute(INSERT_MOOD_ABS, (f"{uid}-m{i}", uid, row["mood_emoji"],
                                              row["mood_label"], row["ai_interpretation"],
                                              row["created_at"]))
            for i, row in enumerate(user.get("growth", [])):
                cur.execute(INSERT_GROWTH, (f"{uid}-g{i}", uid, row["record_type"],
                                            row["content"], row["related_memory_ids"],
                                            row["created_at"]))
            row = profile_row(user)
            if row:
                cur.execute(INSERT_PROFILE, (row["id"], row["user_id"],
                                             row["persona_type"], row["answers"],
                                             row["first_read"], row["language"],
                                             row["created_at"]))
        for event in seeded_events():
            cur.execute(INSERT_EVENT, (event["id"], event["user_id"], event["event_kind"],
                                       event["day_no"], event["scheduled_at"],
                                       event["payload"], event["memory_refs"],
                                       event["status"], event["sent_at"],
                                       event["acknowledged"]))
    dsn.close()


def seeded_profiles() -> list[dict]:
    return [row for row in (profile_row(user) for user in USERS) if row]


def seed_spec() -> dict:
    """把播种口径原样交给 Go 侧，两侧读同一份数字。"""
    return {
        "users": [
            {"id": f"{SEED_PREFIX}{u['slug']}", "timezone": timezone_of(u),
             "language": u["language"], "analytics_consent": u["analytics"],
             "ai_consent": u.get("ai", False), "status": u.get("status", "active"),
             # push_time 为 None ⇒ SQL NULL（与 "" 同一条 `or "09:00"` 分支，但列上不同）
             "push_time": u.get("push"),
             "created_at": u.get("created", ANCHOR.isoformat()),
             # referral_code 的**列原文**（None⇒NULL⇒POST 现算随机码，见掩码注释）
             "referral_code": u.get("referral")}
            for u in USERS
        ],
        "mood_checkins": [
            {"id": f"{SEED_PREFIX}{u['slug']}-m{i}", "user_id": f"{SEED_PREFIX}{u['slug']}",
             "mood_emoji": emoji, "days_ago": days_ago}
            for u in USERS
            for i, (days_ago, emoji) in enumerate(u.get("rows", []))
        ] + [
            # 批 5：带 `created_at`（绝对）⇒ Go 回放测走另一条 INSERT 分支，
            # 且 `days_ago` 必须**缺席**而不是 0（0＝今天，会让相对分支插出错行）。
            {"id": f"{SEED_PREFIX}{u['slug']}-m{i}", "user_id": f"{SEED_PREFIX}{u['slug']}",
             "mood_emoji": row["mood_emoji"], "mood_label": row["mood_label"],
             "ai_interpretation": row["ai_interpretation"],
             "created_at": row["created_at"]}
            for u in USERS
            for i, row in enumerate(u.get("abs_rows", []))
        ],
        "growth_records": [
            {"id": f"{SEED_PREFIX}{u['slug']}-g{i}", "user_id": f"{SEED_PREFIX}{u['slug']}",
             "record_type": row["record_type"], "content": row["content"],
             "related_memory_ids": row["related_memory_ids"],
             "created_at": row["created_at"]}
            for u in USERS
            for i, row in enumerate(u.get("growth", []))
        ],
        "journey_events": seeded_events(),
        "onboarding_profiles": seeded_profiles(),
    }



# ── 用例面 ────────────────────────────────────────────────────────────────────
# (name, group, method, path, user slug | "" (匿名), headers, body)
EV = {"event": "screen_view", "properties": {"screen": "home"},
      "occurred_at": "2026-10-01T00:00:00+00:00"}
CURR = "/api/v1/curriculum"
JOURNEY = "/api/v1/journey"


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
        # ── 批 3：journey 域 ──────────────────────────────────────────────────
        # GET /journey/events 先补写再读，所以**每一条 GET 都是一次写请求**：
        # 用例顺序就是回放顺序，两侧都从同一份 seed 起跑（见 Go 侧 TestN8JourneyContractBytes）。
        ("events-ghost-user", "journey", "GET", f"{JOURNEY}/events", "ghost-user", {}, None),
        ("events-anonymous", "journey", "GET", f"{JOURNEY}/events", "", {}, None),
        # 六行全预播种 ⇒ UPDATE 支：payload 换成就负载、memory_refs 留哨兵、
        # status 只有原来是 pending 且已 Past 才变 skipped。
        ("events-full", "journey", "GET", f"{JOURNEY}/events", "j-full", {}, None),
        # push_time 的 NULL 与 ""：同一条 `or "09:00"`，但列上是两种值
        ("events-null-push", "journey", "GET", f"{JOURNEY}/events", "j-null-push", {}, None),
        ("events-empty-push", "journey", "GET", f"{JOURNEY}/events", "j-empty-push", {}, None),
        # "24:99" 过得了 PATCH（\d{2}:\d{2} 不看范围）⇒ min(max(int,0),23) 双侧各夹
        ("events-push-clamp", "journey", "GET", f"{JOURNEY}/events", "j-clamp", {}, None),
        # 全角数字也过得了 PATCH ⇒ int() 的 Nd 支不是历史脏值专属（Go 只认 ASCII 就红）
        ("events-push-fullwidth", "journey", "GET", f"{JOURNEY}/events", "j-fullwidth", {}, None),
        # 单段"7"（PATCH 到不了，属历史脏值）⇒ 解包 ValueError ⇒ 9:00
        ("events-push-legacy-dirty", "journey", "GET", f"{JOURNEY}/events",
         "j-legacy-dirty", {}, None),
        # 本地今天 ≠ UTC 今天：Asia/Shanghai 且 UTC 18:00 的基准日 ⇒ day0 比 UTC 晚一天
        ("events-shanghai-base", "journey", "GET", f"{JOURNEY}/events", "j-sh", {}, None),
        # 春季拨快：纽约 02:30 不存在，落点必须是切换**前**偏移（Python fold=0）
        ("events-gap-newyork", "journey", "GET", f"{JOURNEY}/events", "j-gap-ny", {}, None),
        # 秋季回拨：01:30 出现两次，取第一次（EDT）；基准日在未来 ⇒ 六行全不 Past
        ("events-repeat-newyork", "journey", "GET", f"{JOURNEY}/events", "j-repeat-ny", {}, None),
        # 切换点就在本地午夜的区（智利 00:00–00:59 缺位），push="00:30" 严格落在缺位里
        ("events-gap-santiago", "journey", "GET", f"{JOURNEY}/events", "j-gap-cl", {}, None),
        # (kind,day_no) 对不上 ⇒ 六行走 INSERT 支（uuid 主键按形状掩，见 id_masks）
        ("events-insert-branch", "journey", "GET", f"{JOURNEY}/events", "j-dedup-key", {}, None),
        # 紧接着重放：幂等 ⇒ 仍是 7 行且不新增（少这一步，「重放不产生第二条」不可证）
        ("events-insert-branch-again", "journey", "GET", f"{JOURNEY}/events",
         "j-dedup-key", {}, None),
        ("events-no-rows", "journey", "GET", f"{JOURNEY}/events", "j-no-rows", {}, None),
        ("events-no-rows-again", "journey", "GET", f"{JOURNEY}/events", "j-no-rows", {}, None),
        # 注销用户：ensure_prefilled 在 get_user 那格**静默返回**（不升 40401），
        # 六行照列，且 scheduled_at/payload 仍是播种哨兵＝预写一步都没走。
        ("events-deleted-user", "journey", "GET", f"{JOURNEY}/events", "j-deleted", {}, None),
        # 51 行 + 预写 6 行 = 57 行 ⇒ LIMIT 50 与 ORDER BY (day_no, scheduled_at) 的合成
        ("events-limit-50", "journey", "GET", f"{JOURNEY}/events", "j-limit", {}, None),
        # 文本列坏值：命中的行验 memory_refs 的六形，payload 的七形挂在 kind 不匹配的行上
        ("events-bad-columns", "journey", "GET", f"{JOURNEY}/events", "j-badcols", {}, None),
        # users.language="" ⇒ payload 的 language 是 "en"（`or "en"`）
        ("events-language-empty", "journey", "GET", f"{JOURNEY}/events", "j-lang-empty", {}, None),
        # language="ｚｈ-CN" 原样进负载：这一条**不做**查表归一（与批 1 的语言解析不同）
        ("events-language-nonascii", "journey", "GET", f"{JOURNEY}/events",
         "j-lang-nonascii", {}, None),
        # 基准日取 onboarding_profiles.created_at（9 天前）而不是 users.created_at
        ("events-profile-base", "journey", "GET", f"{JOURNEY}/events", "j-profile", {}, None),
        # profile 在但 created_at 为 NULL ⇒ 退回 user.created_at（`profile and .created_at`）
        ("events-profile-null", "journey", "GET", f"{JOURNEY}/events",
         "j-profile-null", {}, None),
        ("events-future-base", "journey", "GET", f"{JOURNEY}/events", "j-future", {}, None),
        # 未声明的查询参数不是校验面：FastAPI 直接忽略，体与不带时逐字相同
        ("events-unknown-query-param", "journey", "GET", f"{JOURNEY}/events?limit=3",
         "j-clamp", {}, None),
        # ── acknowledge：200 / 40402 的归属面 ──
        ("ack-sent-event", "journey", "POST", f"{JOURNEY}/events/acknowledge", "j-full", {},
         {"event_id": f"{SEED_PREFIX}j-full-e2"}),
        # 写完立刻读：status 变 seen、acknowledged 变 true、unseen_count 少一
        # （CommitBeforeResponseRoute 的「提交在响应之前」就在这条上可证）
        ("events-full-after-ack", "journey", "GET", f"{JOURNEY}/events", "j-full", {}, None),
        ("ack-sent-event-again", "journey", "POST", f"{JOURNEY}/events/acknowledge", "j-full",
         {}, {"event_id": f"{SEED_PREFIX}j-full-e2"}),
        # 别人的事件与不存在的事件合成同一条 40402（不泄露他人 ID 有效性）
        ("ack-cross-user", "journey", "POST", f"{JOURNEY}/events/acknowledge", "j-clamp", {},
         {"event_id": f"{SEED_PREFIX}j-full-e0"}),
        ("ack-missing-event", "journey", "POST", f"{JOURNEY}/events/acknowledge", "j-full", {},
         {"event_id": f"{SEED_PREFIX}no-such-event"}),
        ("ack-anonymous", "journey", "POST", f"{JOURNEY}/events/acknowledge", "", {},
         {"event_id": f"{SEED_PREFIX}j-full-e1"}),
        # 注销用户名下的事件：acknowledge 这条链**不过滤 status**（get_user 压根没被调用）
        # ⇒ 200，与 GET 侧「不预写但照样列行」是两个不同分支，别顺手统一成 404。
        ("ack-deleted-owner", "journey", "POST", f"{JOURNEY}/events/acknowledge", "j-deleted",
         {}, {"event_id": f"{SEED_PREFIX}j-deleted-e0"}),
        ("events-deleted-after-ack", "journey", "GET", f"{JOURNEY}/events", "j-deleted", {}, None),
        # ── JourneyAckRequest 的校验面（pydantic 的 loc/msg/input 逐字）──
        ("ack-no-body", "journey", "POST", f"{JOURNEY}/events/acknowledge", "j-full", {}, None),
        ("ack-empty-object", "journey", "POST", f"{JOURNEY}/events/acknowledge", "j-full",
         {}, {}),
        ("ack-event-id-null", "journey", "POST", f"{JOURNEY}/events/acknowledge", "j-full",
         {}, {"event_id": None}),
        ("ack-event-id-number", "journey", "POST", f"{JOURNEY}/events/acknowledge", "j-full",
         {}, {"event_id": 42}),
        ("ack-event-id-list", "journey", "POST", f"{JOURNEY}/events/acknowledge", "j-full",
         {}, {"event_id": ["x"]}),
        # max_length=36 数的是**字符**不是字节：37 个 ASCII 与 37 个全角字符都该被拒，
        # 但 Go 若按 len(string) 判，后者（111 字节）会在完全不同的位置上红。
        ("ack-event-id-37-ascii", "journey", "POST", f"{JOURNEY}/events/acknowledge", "j-full",
         {}, {"event_id": "x" * 37}),
        ("ack-event-id-37-nonascii", "journey", "POST", f"{JOURNEY}/events/acknowledge",
         "j-full", {}, {"event_id": "字" * 37}),
        ("ack-event-id-36-ok", "journey", "POST", f"{JOURNEY}/events/acknowledge", "j-full",
         {}, {"event_id": "x" * 36}),
        ("ack-extra-key", "journey", "POST", f"{JOURNEY}/events/acknowledge", "j-full",
         {}, {"event_id": "e", "unexpected": 1}),
        ("ack-json-array-body", "journey", "POST", f"{JOURNEY}/events/acknowledge", "j-full",
         {}, "raw:[]"),
        ("ack-json-bad", "journey", "POST", f"{JOURNEY}/events/acknowledge", "j-full",
         {}, "raw:{oops"),
        ("ack-json-empty", "journey", "POST", f"{JOURNEY}/events/acknowledge", "j-full",
         {}, "raw:"),
        ("ack-content-type-text", "journey", "POST", f"{JOURNEY}/events/acknowledge", "j-full",
         {"Content-Type": "text/plain"}, "raw:not json"),
    ]
    out += onboarding_cases()
    return out


# ── 批 4（onboarding 两条端点）的用例面 ───────────────────────────────────────
# 两条路径都在同一个前缀下，POST 现算的三处写要按**真实顺序**录：
# POST⇒GET⇒再 POST⇒再 GET，中间不换用户，这样 restore 读到的才是同一次运行写下的列。
ONB = "/api/v1/onboarding/first-read"
ANS_ZH = [{"q": "conflict", "option": "speak_now"},
          {"q": "uncertainty", "option": "list_out"},
          {"q": "night", "option": "replay"}]
ANS_EN = [{"q": "conflict", "option": "hold_in"},
          {"q": "uncertainty", "option": "talk_out"}]


def onboarding_cases() -> list[tuple]:
    # (name, group, method, path, user slug, headers, body)
    return [
        # ── 校验面（validation 组：Go 侧无库回放，读到 store 即红）──
        # 这四条钉的是 FirstReadRequest/FirstReadAnswer 的**错误累积顺序**与 loc 形状，
        # 全部在 pydantic 层失败，Python 一次库都没碰（routers/onboarding.py:26 的
        # `request: FirstReadRequest` 在 Depends(get_db) 之前已由 FastAPI 解完）。
        ("onb-no-body", "validation", "POST", ONB, "o-code-zh", {}, None),
        ("onb-answers-not-list", "validation", "POST", ONB, "o-code-zh", {},
         {"answers": "x"}),
        ("onb-answers-null", "validation", "POST", ONB, "o-code-zh", {}, {"answers": None}),
        ("onb-answer-item-not-dict", "validation", "POST", ONB, "o-code-zh", {},
         {"answers": ["x"]}),
        ("onb-answer-missing-option", "validation", "POST", ONB, "o-code-zh", {},
         {"answers": [{"q": "conflict"}]}),
        ("onb-answer-missing-q", "validation", "POST", ONB, "o-code-zh", {},
         {"answers": [{"option": "speak_now"}]}),
        # 上面两条各缺一个字段，看不出**同一条 item 内**的错误顺序；只有两个同时缺
        # 才能钉住「按 schemas.py 声明序 q → option」这一条判据。
        ("onb-answer-both-missing", "validation", "POST", ONB, "o-code-zh", {},
         {"answers": [{}]}),
        ("onb-answer-q-not-string", "validation", "POST", ONB, "o-code-zh", {},
         {"answers": [{"q": 5, "option": "speak_now"}]}),
        # max_length 数的是**码点**：31 个 ASCII 与 31 个全角都越界，但字节数差三倍。
        ("onb-answer-q-31-ascii", "validation", "POST", ONB, "o-code-zh", {},
         {"answers": [{"q": "x" * 31, "option": "speak_now"}]}),
        ("onb-answer-q-31-nonascii", "validation", "POST", ONB, "o-code-zh", {},
         {"answers": [{"q": "字" * 31, "option": "speak_now"}]}),
        ("onb-answer-option-41", "validation", "POST", ONB, "o-code-zh", {},
         {"answers": [{"q": "conflict", "option": "x" * 41}]}),
        # answers 的 max_length=10（schemas.py:502）⇒ 11 条才越界（3×3+2）。
        ("onb-answers-11", "validation", "POST", ONB, "o-code-zh", {},
         {"answers": ANS_ZH * 3 + [{"q": "night", "option": "sharpest"},
                                   {"q": "extra", "option": "entry"}]}),
        # persona_type 的 ValueError 文案带**原值**（不是 strip/upper 后的值），
        # 所以 "  int  " 报的必须是含空格的那一串——Go 若先清洗再报错就红。
        ("onb-persona-three", "validation", "POST", ONB, "o-code-zh", {},
         {"persona_type": "INT"}),
        ("onb-persona-spaces-then-short", "validation", "POST", ONB, "o-code-zh", {},
         {"persona_type": "  int  "}),
        ("onb-persona-five", "validation", "POST", ONB, "o-code-zh", {},
         {"persona_type": "INTJJ"}),
        ("onb-persona-digit", "validation", "POST", ONB, "o-code-zh", {},
         {"persona_type": "INT1"}),
        # 全角 iｎｔｊ 的 .upper() 得到**全角** ＩＮＴＪ ⇒ 过不了 [A-Z]{4}；
        # 这条钉的是「Go 的 PyUpper 不许把非 ASCII 字母折成 ASCII」。
        ("onb-persona-fullwidth", "validation", "POST", ONB, "o-code-zh", {},
         {"persona_type": "ｉｎｔｊ"}),
        # 实测（2026-10-15，.venv 的 pydantic 现场跑）：CPython 的 `.upper()` 做**全案映射**，
        # ß→SS 是**每个码位各展开一次**，所以 "ßß"⇒"SSSS"（合法！）、"ßßßß"⇒"SSSSSS"（越界）。
        # 这条取越界那一支：400 的文案回显**原值** "ßßßß"，两侧同形；
        # 正证（响应里真的出现 "SSSS"）在下面 onboarding 组的 o-persona-sharp。
        # Go 若只写 strings.ToUpper（对 ß 恒等）会在 o-persona-sharp 那条变 400。
        ("onb-persona-ss-6-upper", "validation", "POST", ONB, "o-code-zh", {},
         {"persona_type": "ßßßß"}),
        ("onb-language-not-string", "validation", "POST", ONB, "o-code-zh", {},
         {"language": 5}),
        ("onb-language-11", "validation", "POST", ONB, "o-code-zh", {},
         {"language": "x" * 11}),
        ("onb-language-11-nonascii", "validation", "POST", ONB, "o-code-zh", {},
         {"language": "字" * 11}),
        # 同一请求里三个字段各错一次 ⇒ 累积顺序＝**字段声明序**（answers, persona_type,
        # language），不是「谁先校验谁先报错」的猜测。
        ("onb-three-field-errors", "validation", "POST", ONB, "o-code-zh", {},
         {"answers": "x", "persona_type": "BAD", "language": 5}),
        ("onb-json-array-body", "validation", "POST", ONB, "o-code-zh", {}, "raw:[]"),
        ("onb-json-bad", "validation", "POST", ONB, "o-code-zh", {}, "raw:{oops"),
        ("onb-json-empty", "validation", "POST", ONB, "o-code-zh", {}, "raw:"),
        ("onb-content-type-text", "validation", "POST", ONB, "o-code-zh",
         {"Content-Type": "text/plain"}, "raw:not json"),
        # 重复键：Python 的 json.loads 取**最后一个**（后写覆盖前写）。
        # 这里把合法值放前面、非法值放后面 ⇒ 只有「取后」才会报 400。
        # 于是这条 400 体本身就是 last-wins 的证明：Go 若取前，会写成 200 而红。
        ("onb-duplicate-keys", "validation", "POST", ONB, "o-code-zh", {},
         'raw:{"answers": [{"q": "conflict", "option": "speak_now"}], "answers": "x"}'),
        # GET 没有 body、没有查询参数面：带上的话 FastAPI 直接忽略（与批 3 同判据）。
        ("onb-get-unknown-query", "onboarding", "GET", f"{ONB}?limit=3",
         "o-restore-full", {}, None),
        # ── 成功面（onboarding 组：真库 + 冻时钟，顺序就是契约）──
        # 还没有行 ⇒ 40402。放在这个位置是因为下面第一条就是它的 POST。
        ("onb-restore-before-submit", "onboarding", "GET", ONB, "o-code-zh", {}, None),
        # 种子账号**已带码** ⇒ share_url 全串逐字可比（惰性生成分支被跳过）
        ("onb-submit-zh", "onboarding", "POST", ONB, "o-code-zh", {},
         {"answers": ANS_ZH, "persona_type": "INTJ", "language": "zh"}),
        ("onb-restore-zh", "onboarding", "GET", ONB, "o-code-zh", {}, None),
        # 第二次 POST 换一份答案 ⇒ upsert 走 UPDATE 支（id 不变、content 以最后一次为准）
        ("onb-submit-zh-update", "onboarding", "POST", ONB, "o-code-zh", {},
         {"answers": ANS_EN, "persona_type": None, "language": "zh"}),
        ("onb-restore-zh-update", "onboarding", "GET", ONB, "o-code-zh", {}, None),
        # 无码账号 ⇒ 码随机 ⇒ share_url 的 /r/<code> 被掩（code_masks=1）。
        # answers 里混一支未知 q、一支未知 option ⇒ 只剩一支真断言，fillers 补到 2。
        ("onb-submit-generate-code", "onboarding", "POST", ONB, "o-nocode", {},
         {"answers": [{"q": "no-such-q", "option": "speak_now"},
                      {"q": "conflict", "option": "no-such-option"},
                      {"q": "night", "option": "sharpest"}],
          "language": "en"}),
        # 第二次 POST：**复用**第一次现算的码（掩掉后字节仍应相同，且 code_masks 仍是 1）
        ("onb-submit-code-reused", "onboarding", "POST", ONB, "o-nocode", {},
         {"answers": ANS_EN, "persona_type": "inqt", "language": "en"}),
        ("onb-restore-generated", "onboarding", "GET", ONB, "o-nocode", {}, None),
        # 已注销账号：get_user 过滤 ⇒ user is None ⇒ 只写 profile，
        # share_url 停在 **app_store_url 原文**（逐字，不掩），且一行剧本都不写。
        ("onb-submit-deleted", "onboarding", "POST", ONB, "o-deleted", {},
         {"answers": ANS_ZH, "persona_type": "ENTJ", "language": "zh"}),
        ("onb-restore-deleted", "onboarding", "GET", ONB, "o-deleted", {}, None),
        # 匿名（dev 无 X-User-Id）⇒ user_id="anonymous"，库里没这账号的行，
        # 但 restore 只 SELECT 不 INSERT ⇒ 40402（POST 那条走不了：profile.user_id 有外键，
        # Python 会得到 IntegrityError 的**非信封 500**，属 D-8 登记的无观测点一支）。
        ("onb-restore-anonymous", "onboarding", "GET", ONB, "", {}, None),
        ("onb-restore-ghost", "onboarding", "GET", ONB, "ghost-user", {}, None),
        # 播种行的四列全真值：id 与 created_at 都**不掩**（固定值），
        # 所以这一条同时钉住「restore 的 id 来自列」＋「created_at 的 +00:00 形状」。
        ("onb-restore-seeded-full", "onboarding", "GET", ONB, "o-restore-full", {}, None),
        # first_read 列是坏 JSON ⇒ JSONDecodeError ⇒ 与「没有行」同码不同因
        ("onb-restore-broken-json", "onboarding", "GET", ONB, "o-restore-broken", {}, None),
        # first_read 列 NULL ⇒ `or "{}"` ⇒ 只剩 update 的六键；language 列 NULL ⇒ null
        ("onb-restore-null-columns", "onboarding", "GET", ONB, "o-restore-null", {}, None),
        # "ßß" ⇒ .upper()＝"SSSS"（每个 ß 各展开成 SS）⇒ 合法，且**响应里的
        # persona_type 就是 "SSSS"**。这是 PyUpper 那 8 条 ASCII 展开表的正证：
        # strings.ToUpper 对 ß 恒等，少了这张表这一条就变 400（同一请求的字节差异）。
        ("onb-submit-persona-sharp-s", "onboarding", "POST", ONB, "o-persona-sharp", {},
         {"answers": ANS_ZH[:2], "persona_type": "ßß", "language": "zh"}),
        # 紧随其后的 GET：restore 是从 **first_read 列**复现的，所以这一条证的是
        # 「列原文里的 persona_type 也写成了 SSSS」（列用 `", "` 分隔、响应用紧凑形，
        # 两侧各自的写法只要与对方逐字同形才可证）。
        ("onb-restore-persona-sharp-s", "onboarding", "GET", ONB, "o-persona-sharp", {}, None),
        # 连字 ﬁ（1 码位）⇒ "FI" ⇒ "ﬁnt" 四位输入折成 FINT 合法（1→2 展开凑满长度）
        ("onb-submit-persona-ligature", "onboarding", "POST", ONB, "o-persona-ligature", {},
         {"answers": ANS_EN, "persona_type": "ﬁnt", "language": "en"}),
        ("onb-restore-persona-ligature", "onboarding", "GET", ONB, "o-persona-ligature", {}, None),
        # locale 归一：""、"ja"、"ｚｈ-CN" 全落 en；"ZH" 落 zh（(language or "").lower()）
        ("onb-submit-lang-empty", "onboarding", "POST", ONB, "o-lang-empty", {},
         {"answers": ANS_ZH + [{"q": "night", "option": "slow_down"}],
          "language": ""}),
        ("onb-restore-lang-empty", "onboarding", "GET", ONB, "o-lang-empty", {}, None),
        ("onb-submit-lang-ja", "onboarding", "POST", ONB, "o-lang-ja", {},
         {"language": "ja"}),
        ("onb-restore-lang-ja", "onboarding", "GET", ONB, "o-lang-ja", {}, None),
        ("onb-submit-lang-fullwidth", "onboarding", "POST", ONB, "o-lang-fullwidth", {},
         {"answers": [ANS_EN[0], ANS_EN[0]], "language": "ｚｈ-CN"}),
        ("onb-restore-lang-fullwidth", "onboarding", "GET", ONB, "o-lang-fullwidth", {}, None),
        # "ZH" ⇒ locale zh（大写被 lower 折掉），但**列存请求原文 "ZH"** ⇒
        # restore 的 outer update 用列原文，于是同一个键两侧值不同（POST 回 zh、GET 回 ZH）
        ("onb-submit-lang-upper-zh", "onboarding", "POST", ONB, "o-lang-upper-zh", {},
         {"answers": ANS_ZH[:2], "persona_type": "  INFP  ", "language": "ZH"}),
        ("onb-restore-lang-upper-zh", "onboarding", "GET", ONB, "o-lang-upper-zh", {}, None),
        # ── 批 5：mood 域两条纯读 + reflections 一条纯读 ────────────────────────
        # 组名 "mood"/"reflection" 的含义是「回放要冻时钟＋按绝对时刻播种」，
        # 不是「域」——域那一面在 Ingress/ConfigMap 那边登记（见 tasks.md 批 5 的 D-5）。
        # validation 组那批全部带 `X-Language: en`：`get_request_language`(language.py:44-46)
        # 在 header 有效时**提前 return、一次库都不读**，这样它们才能落进「无库回放」组，
        # 由 n8ForbiddenStore 当场钉住「FastAPI 的参数校验先于服务层」这条依赖顺序。
        # 反过来说，若哪天 Python 改成「先查库再校验参数」，这些用例会变成读库派生的字节、
        # 而 _group 的 200-却标-validation 那道 tripwire 会先炸。
        ("history-anonymous", "mood", "GET", "/api/v1/mood/history", "", {}, None),
        ("history-empty", "mood", "GET", "/api/v1/mood/history", "mood-empty", {}, None),
        ("history-nulls", "mood", "GET", "/api/v1/mood/history", "mood-nulls", {}, None),
        # 35 行 ⇒ 只回 30 条：钉 `get_recent(limit=30)` 的截断与 DESC 序
        ("history-many", "mood", "GET", "/api/v1/mood/history", "mood-many", {}, None),
        # 已注销用户仍回 200 带行（get_history 不查 users）；ghost id 同理回空数组
        ("history-deleted-user", "mood", "GET", "/api/v1/mood/history",
         "mood-deleted", {}, None),
        ("history-ghost-user", "mood", "GET", "/api/v1/mood/history", "ghost-user", {}, None),
        # history 面**不接受** query 与 X-Language（多给一律忽略）：这两格钉的是
        # 「同一路径带参数不改字节」，而不是「参数被读进逻辑」
        ("history-ignores-query", "mood", "GET", "/api/v1/mood/history?days=3",
         "mood-nulls", {}, None),
        ("history-ignores-language", "mood", "GET", "/api/v1/mood/history",
         "mood-nulls", {"X-Language": "th"}, None),
        ("trend-default", "mood", "GET", "/api/v1/mood/trend", "mood-t-high", {}, None),
        ("trend-explicit-14", "mood", "GET", "/api/v1/mood/trend?days=14",
         "mood-t-high", {}, None),
        ("trend-empty", "mood", "GET", "/api/v1/mood/trend", "mood-empty", {}, None),
        ("trend-mid", "mood", "GET", "/api/v1/mood/trend", "mood-t-mid", {}, None),
        # 恰好 3.0 ⇒ mid：钉 `avg >= 3` 的等号侧（写成 >3 就翻成 low）
        ("trend-exactly3", "mood", "GET", "/api/v1/mood/trend", "mood-t-exactly3", {}, None),
        ("trend-low", "mood", "GET", "/api/v1/mood/trend", "mood-t-low", {}, None),
        ("trend-unknown-emoji", "mood", "GET", "/api/v1/mood/trend",
         "mood-t-unknown", {}, None),
        # 窗口边界：days=14 时「正好落在 start 那一瞬」的行进窗、差一秒的出窗（`>=`）
        ("trend-edge-14", "mood", "GET", "/api/v1/mood/trend?days=14", "mood-t-edge", {}, None),
        ("trend-edge-13", "mood", "GET", "/api/v1/mood/trend?days=13", "mood-t-edge", {}, None),
        ("trend-days-1", "mood", "GET", "/api/v1/mood/trend?days=1", "mood-t-high", {}, None),
        ("trend-days-90", "mood", "GET", "/api/v1/mood/trend?days=90", "mood-t-edge", {}, None),
        # 同一本地日两行 ⇒ 取升序首行的 emoji（😊=5 而不是 😢=1）
        ("trend-sameday", "mood", "GET", "/api/v1/mood/trend", "mood-t-sameday", {}, None),
        # Asia/Shanghai：UTC 23:00 那行属**次一个**本地日 ⇒ labels 与 UTC 口径不同
        ("trend-sh-14", "mood", "GET", "/api/v1/mood/trend?days=14", "mood-t-sh", {}, None),
        ("trend-sh-3", "mood", "GET", "/api/v1/mood/trend?days=3", "mood-t-sh", {}, None),
        # 非法时区名 ⇒ UTC 回退，同样三行、labels 与上面不同（钉 safe_zoneinfo）
        ("trend-badtz-3", "mood", "GET", "/api/v1/mood/trend?days=3",
         "mood-t-badtz", {}, None),
        # 已注销用户 + Shanghai 种子：get_user⇒None ⇒ tz=None ⇒ 走 UTC，不是 40401
        ("trend-deleted-3", "mood", "GET", "/api/v1/mood/trend?days=3",
         "mood-t-deleted", {}, None),
        ("trend-anonymous", "mood", "GET", "/api/v1/mood/trend", "", {}, None),
        # 四档判据 × 若干语言：summary 文案（其余 60 条闭包走 mood_copy_corpus）
        ("trend-high-zh-header", "mood", "GET", "/api/v1/mood/trend",
         "mood-t-high", {"X-Language": "zh"}, None),
        ("trend-mid-th-header", "mood", "GET", "/api/v1/mood/trend",
         "mood-t-mid", {"X-Language": "th"}, None),
        ("trend-low-km-header", "mood", "GET", "/api/v1/mood/trend",
         "mood-t-low", {"X-Language": "km"}, None),
        ("trend-empty-de-header", "mood", "GET", "/api/v1/mood/trend",
         "mood-empty", {"X-Language": "de"}, None),
        ("trend-exactly3-ja-header", "mood", "GET", "/api/v1/mood/trend",
         "mood-t-exactly3", {"X-Language": "ja"}, None),
        ("trend-low-ru-header", "mood", "GET", "/api/v1/mood/trend",
         "mood-t-low", {"X-Language": "ru"}, None),
        # users.language=zh（无 header）⇒ 走第②级读库；非法码 xx 同样落第②级
        ("trend-zh-db-preference", "mood", "GET", "/api/v1/mood/trend",
         "mood-t-sh", {}, None),
        ("trend-header-uppercase-zh", "mood", "GET", "/api/v1/mood/trend",
         "mood-empty", {"X-Language": "ZH"}, None),
        ("trend-header-region-zh", "mood", "GET", "/api/v1/mood/trend",
         "mood-empty", {"X-Language": "zh-CN"}, None),
        ("trend-header-unsupported-falls-back-db", "mood", "GET", "/api/v1/mood/trend",
         "mood-t-sh", {"X-Language": "xx"}, None),
        ("trend-header-blank-db-zh", "mood", "GET", "/api/v1/mood/trend",
         "mood-t-sh", {"X-Language": " "}, None),
        # ?days=+14：int 侧能不能吃加号由录制说了算（标 mood 组＝不假设它一定 400）
        ("trend-days-plus-14", "mood", "GET", "/api/v1/mood/trend?days=%2B14",
         "mood-t-high", {"X-Language": "en"}, None),
        ("trend-days-duplicate", "mood", "GET", "/api/v1/mood/trend?days=1&days=14",
         "mood-t-high", {"X-Language": "en"}, None),
        ("trend-days-empty-then-valid", "mood", "GET", "/api/v1/mood/trend?days=&days=3",
         "mood-t-edge", {"X-Language": "en"}, None),
        ("trend-days-abc", "validation", "GET", "/api/v1/mood/trend?days=abc",
         "mood-t-high", {"X-Language": "en"}, None),
        ("trend-days-float", "validation", "GET", "/api/v1/mood/trend?days=1.5",
         "mood-t-high", {"X-Language": "en"}, None),
        ("trend-days-blank", "validation", "GET", "/api/v1/mood/trend?days=",
         "mood-t-high", {"X-Language": "en"}, None),
        ("trend-days-zero", "validation", "GET", "/api/v1/mood/trend?days=0",
         "mood-t-high", {"X-Language": "en"}, None),
        ("trend-days-91", "validation", "GET", "/api/v1/mood/trend?days=91",
         "mood-t-high", {"X-Language": "en"}, None),
        ("trend-days-negative", "validation", "GET", "/api/v1/mood/trend?days=-1",
         "mood-t-high", {"X-Language": "en"}, None),
        ("trend-days-space", "validation", "GET", "/api/v1/mood/trend?days=%20",
         "mood-t-high", {"X-Language": "en"}, None),
        ("trend-days-fullwidth", "validation",
         "GET", "/api/v1/mood/trend?days=\uff11\uff14", "mood-t-high",
         {"X-Language": "en"}, None),
        ("trend-days-two-errors-order", "validation",
         "GET", "/api/v1/mood/trend?days=abc&days=\u00b1", "mood-t-high",
         {"X-Language": "en"}, None),
        # /mood/trend 不认 limit：多余键被忽略 ⇒ 200，所以它**不能**标 validation
        # （_group 里那道「200 却标 validation」的 tripwire 会先炸）
        ("trend-limit-ignored", "mood", "GET", "/api/v1/mood/trend?limit=0",
         "mood-t-high", {"X-Language": "en"}, None),
        ("reflections-anonymous", "reflection", "GET", "/api/v1/reflections", "", {}, None),
        ("reflections-empty", "reflection", "GET", "/api/v1/reflections",
         "refl-empty", {}, None),
        # 25 行 ⇒ 默认 limit 20 的截断与 DESC 序
        ("reflections-many", "reflection", "GET", "/api/v1/reflections",
         "refl-many", {}, None),
        # related_memory_ids 的 NULL / 空串 / "," / "a,,b" / "only" 五种列原文
        ("reflections-nulls", "reflection", "GET", "/api/v1/reflections",
         "refl-nulls", {}, None),
        # 三种 record_type 混排 ⇒ 只有 'reflection' 进响应（大小写不同也算另一类）
        ("reflections-by-type", "reflection", "GET", "/api/v1/reflections",
         "refl-types", {}, None),
        ("reflections-deleted-user", "reflection", "GET", "/api/v1/reflections",
         "refl-deleted", {}, None),
        ("reflections-ghost-user", "reflection", "GET", "/api/v1/reflections",
         "ghost-user", {}, None),
        ("reflections-limit-1", "reflection", "GET", "/api/v1/reflections?limit=1",
         "refl-many", {}, None),
        ("reflections-limit-100", "reflection", "GET", "/api/v1/reflections?limit=100",
         "refl-many", {}, None),
        ("reflections-limit-duplicate", "reflection",
         "GET", "/api/v1/reflections?limit=2&limit=1", "refl-many", {"X-Language": "en"}, None),
        ("reflections-limit-abc", "validation", "GET", "/api/v1/reflections?limit=abc",
         "refl-many", {"X-Language": "en"}, None),
        ("reflections-limit-zero", "validation", "GET", "/api/v1/reflections?limit=0",
         "refl-many", {"X-Language": "en"}, None),
        ("reflections-limit-101", "validation", "GET", "/api/v1/reflections?limit=101",
         "refl-many", {"X-Language": "en"}, None),
        ("reflections-limit-float", "validation", "GET", "/api/v1/reflections?limit=2.5",
         "refl-many", {"X-Language": "en"}, None),
        ("reflections-limit-blank", "validation", "GET", "/api/v1/reflections?limit=",
         "refl-many", {"X-Language": "en"}, None),
        ("reflections-limit-negative", "validation", "GET", "/api/v1/reflections?limit=-1",
         "refl-many", {"X-Language": "en"}, None),
        # days 参数不该影响 reflections（多余的键被 Starlette 忽略、不进校验）⇒ 200
        ("reflections-ignores-days", "reflection", "GET", "/api/v1/reflections?days=abc",
         "refl-many", {"X-Language": "en"}, None),
    ]


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
    if declared in DB_GROUPS and status == 404 and not text.startswith('{"code"'):
        # 业务码 40401 也推导成 HTTP 404，所以 404 本身不是错——**体不是信封**才是：
        # 那是 FastAPI 的路由没匹配（{"detail": "Not Found"}），录进夹具就是把
        # 「我们两边都没实现的那条路径」当成契约。
        raise SystemExit(f"{name} 得到非信封 404（路由没匹配）：{text}")
    return declared


async def record() -> tuple[list[dict], list[str]]:
    recorded = []
    generated_at_samples = []
    # 批 4 的两样易变值**按用例判**，不按组无脑掩（判据见上面两条正则的注释）。
    # 两张表各自只回答一个问题，而且**都不能按「有没有种子码」推**：
    #   runtime_code   —— 这次响应里的 share_url 是不是**本次运行现算**的码。
    #     种子已带码的账号 ⇒ 全串逐字可比，那正是「已有码原样复用」(user_repository.py:373)
    #     的证据；无码账号 ⇒ 随机 ⇒ 只能掩，并用 code_masks 计数。
    #   run_profiles   —— 这次响应的 profile 行是不是**本次运行才 INSERT** 的。
    #     是 ⇒ created_at 走库钟（server_default now()，录制与回放必然差几秒）⇒ 掩；
    #     种子行的 created_at 是夹具给的绝对时刻 ⇒ 不掩，逐字比钉的是 `+00:00` 形状。
    # 两张表都只由「POST 且 200」推进：账号是否 active、有没有种子码都与之无关
    # （o-code-zh 有种子码但没种子 profile ⇒ share_url 可比、created_at 必须掩；
    #  o-deleted 不写 journey 但照样写 profile ⇒ created_at 也必须掩）。
    active_slugs = {u["slug"] for u in USERS if u.get("status", "active") == "active"}
    seeded_referral = {u["slug"]: u.get("referral") for u in USERS}
    seeded_profile_slugs = {u["slug"] for u in USERS if "profile" in u}
    run_profiles: set[str] = set()
    runtime_code: set[str] = set()
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
            group = _group(name, path, group, res.status_code, text)
            is_onb = group == "onboarding"
            # 先登记后掩码：POST 自己那条响应里就同时含现算的码与新行的 id，
            # 若把登记放在掩码之后，生成码那一条会以「未掩的随机串」进夹具。
            if is_onb and method == "POST" and res.status_code == 200:
                if user_slug not in seeded_profile_slugs:
                    run_profiles.add(user_slug)
                if user_slug in active_slugs and seeded_referral.get(user_slug) is None:
                    runtime_code.add(user_slug)
            # 预写的 INSERT 支现生成 uuid4 主键：两侧各自掩成同一条占位串，
            # 并把**掩到的次数**记进夹具——Go 侧必须掩到同样多次，否则「行数变了」
            # 这种最要紧的发散会被掩码本身吃掉（幂等重放那条用例就靠这个数）。
            id_masks = 0
            if group in ID_MASK_GROUPS:
                text, id_masks = EVENT_ID_RE.subn(MASKED_EVENT_ID, text)
            code_masks = created_at_masks = 0
            if is_onb and user_slug in runtime_code:
                text, code_masks = REFERRAL_LINK_RE.subn(MASKED_REFERRAL_LINK, text)
            if is_onb and user_slug in run_profiles:
                text, created_at_masks = CREATED_AT_RE.subn(MASKED_CREATED_AT, text)
            recorded.append({
                "name": name,
                "group": group,
                "method": method,
                "path": path,
                "headers": headers,
                "request_raw": content.decode() if content is not None else None,
                "status": res.status_code,
                "id_masks": id_masks,
                "code_masks": code_masks,
                "created_at_masks": created_at_masks,
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


def push_moment_corpus() -> list[dict]:
    """`_push_moment` 的跨语言语料：给定 (push_time 列原文, 时区名, 剧本日)，
    Python **现行实现**折出的 UTC 瞬间是哪一串字节。

    为什么不能只靠 journey 那几十条 HTTP 用例：录制与回放只隔几分钟，用例里的剧本日
    永远落在 ANCHOR 前后那一周——「春季拨快缺位的 02:30」「回拨后重复的 01:30」
    「切换点恰好压在本地午夜的区（智利/伊朗/查塔姆）」「整天不存在（阿皮亚 2011-12-30）」
    一次都走不到。这些形状 Go 的裸 `time.Date(loc)` 与 Python 的 fold=0 **必然分家**
    （实测：纽约 2026-03-08 02:30，Go 给 06:30Z、Python 给 07:30Z），而 scheduled_at
    是直接进响应字节的列。批 3① 在 journey_test.go 里写下的「届时把口径宣称换成可指回的
    出处」就是这条：下面每条 want 都由 `journey_service._push_moment` 现算。

    want 用 `isoformat()` 原样存（µs=0 时是 "+00:00"，不是 "Z"）——与
    list_events 那条无 response_model 的序列化路径同形（pyISOOffset）。
    """
    from types import SimpleNamespace

    from backend.services.journey_service import journey_service

    # push_time 的取值面：NULL 与 "" 走同一条 `or`，脏段数走 ValueError，
    # clamp 两侧各一次，Nd 全类与下划线/空白是 int() 的字符集面。
    pushes: list[object] = [
        None, "", "09:00", "07:15", "00:00", "00:30", "01:30", "02:00", "02:30", "02:59",
        "23:59", "24:99", "25:70", "-3:-9", "7", "9:", ":30", "abc:def", " 7 : 30 ",
        "+9:00", "\u00a09:30", "١٢:٣٠", "１２:３４", "𝟙𝟚:𝟘𝟛", "1_0:00", "1__0:00",
        "\x1c9:30", "9:30:00", "99999999999999999999:00", "9:30:15",
    ]
    zones = [
        "UTC", "Asia/Shanghai", "Asia/Kolkata", "America/New_York", "Europe/London",
        "Australia/Sydney", "America/Santiago", "Pacific/Chatham", "Australia/Lord_Howe",
        "Pacific/Apia", "Pacific/Kiritimati", "Pacific/Niue", "Asia/Tehran",
        "America/Asuncion", "Etc/GMT+5", "Not/AZone", "",
    ]
    # 异常日：每个区**向前跳**（缺位）与**向后拨**（重复）各取一天，再补几个
    # 切换点不在 02:00 的异形区（阿皮亚整天不存在、德黑兰/智利跳在午夜、查塔姆 45 分偏移）。
    hand_picked = {
        "Pacific/Apia": [dt_date(2011, 12, 30), dt_date(2011, 12, 31)],
        "Asia/Tehran": [dt_date(2016, 3, 21)],
        "Pacific/Chatham": [dt_date(2026, 9, 27), dt_date(2026, 4, 5)],
        "Australia/Lord_Howe": [dt_date(2026, 10, 4), dt_date(2026, 4, 5)],
        "America/Santiago": [dt_date(2026, 9, 6), dt_date(2026, 4, 5)],
    }

    def offset_at(tz: str, day: dt_date) -> int:
        return int(dt_datetime(day.year, day.month, day.day, 12, 0,
                               tzinfo=safe_zoneinfo(tz)).utcoffset().total_seconds())

    def anomaly_days(tz: str) -> list[dt_date]:
        days = hand_picked.get(tz, [])
        forward: dt_date | None = None
        backward: dt_date | None = None
        day = dt_date(2024, 1, 2)
        while day < dt_date(2031, 1, 1):
            if offset_at(tz, day) != offset_at(tz, day - dt_timedelta(days=1)):
                delta = offset_at(tz, day) - offset_at(tz, day - dt_timedelta(days=1))
                if delta > 0 and forward is None:
                    forward = day
                elif delta < 0 and backward is None:
                    backward = day
            if forward and backward:
                break
            day += dt_timedelta(days=1)
        return [d for d in ([forward, backward] + days) if d is not None]

    out: list[dict] = []
    normal_days = [dt_date(2026, 3, 9), dt_date(2026, 10, 14), dt_date(2027, 1, 5)]
    seen: set[tuple] = set()

    def add(push: object, tz: str, day: dt_date, transition: bool) -> None:
        key = (repr(push), tz, day, transition)
        if key in seen:
            return
        seen.add(key)
        # day 是**日历日**，与 Python 一样不带时区；折出来的瞬间按 utc 记。
        moment = journey_service._push_moment(
            SimpleNamespace(push_time=push, timezone=tz), day)
        out.append({
            "tz": tz,
            "push_time": push,
            "day": day.isoformat(),
            "want": moment.astimezone(dt_timezone.utc).isoformat(),
            # transition=true ⇒ 这一天在该区有 DST 跳变（缺位或重复），
            # Go 侧断言它不为零：这一族样本是本轮实现里最贵的一支。
            "transition": transition,
        })

    # ① 全覆盖：所有 push 值 × 6 个代表区 × 3 个正常日（int()/clamp/字符集面）
    for push in pushes:
        for tz in ["UTC", "Asia/Shanghai", "America/New_York", "Europe/London",
                   "Not/AZone", ""]:
            for day in normal_days:
                add(push, tz, day, False)
    # ② 异常日：每个区各取缺位/重复日，push 只留能压到跳变窗口的那些
    key_pushes = ["", "00:00", "00:30", "01:30", "02:00", "02:30", "23:59"]
    for tz in zones:
        for day in anomaly_days(tz):
            for push in key_pushes:
                add(push, tz, day, True)
    # ③ 字符集面 × 异常日的交叉（Nd 数字与下划线在缺位日上的落点）
    for tz in ["America/New_York", "Pacific/Chatham", "Asia/Tehran"]:
        for day in anomaly_days(tz):
            for push in ["١٢:٣٠", "1_0:00", "+9:00", " 7 : 30 ", None]:
                add(push, tz, day, True)
    return out


def template_corpus() -> list[dict]:
    """措辞闸的**闭包**语料：两份模板能产出的每一条候选文案，逐条现算 `filter_template`。

    为什么不是采样（D-2）：本域所有模板串实测**一条都不该被闸掉**，采样只能证「没掉」，
    不能证「两侧判据同源」。把候选串全列出来比**输出文本**才是闭包——闸若在 Go 侧
    吞掉某条断言，那张卡就静默少一句（响应面看不出错，只有逐字比抓得到）。
    候选集合＝{pattern.format(option=选项文本)}（每题 3 条断言形 × 同题 3 个选项文本，
    含**不可达**的交叉格，多给不亏）∪ fillers ∪ closer ∪ {summary_frame.format(每个 persona)}。
    `reachable` 标出真实流程走得到的那些：交叉格只是保险，不是可达性声明。

    闭包之外还有一组 `hit-*` / `safe-*` 探针（kind 前缀），理由见下面那段注释：
    闭包 92 条实测全部放行，「命中即吞」那一支在闭包里没有观测点。
    探针不是模板产出的文本（reachable=False），它们只喂 `filter_template` 这一支函数。
    """
    from backend.services.first_read_service import load_template
    from backend.services.safety_service import safety_service

    out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(locale: str, kind: str, raw: str, reachable: bool) -> None:
        key = (locale, raw)
        if key in seen:
            return
        seen.add(key)
        out.append({"locale": locale, "kind": kind, "raw": raw, "reachable": reachable,
                    "out": safety_service.filter_template(raw, locale)})

    for locale in ("zh", "en"):
        template = load_template(locale)
        questions = {q.get("id"): q for q in template.get("questions") or []}
        for q_id, by_option in (template.get("assertions") or {}).items():
            texts = [opt.get("text") for opt in (questions.get(q_id, {}).get("options") or [])]
            for opt_id, pattern in by_option.items():
                for fill_id, fill_text in zip(
                        [o.get("id") for o in (questions.get(q_id, {}).get("options") or [])],
                        texts):
                    add(locale, "assertion", pattern.format(option=fill_text),
                        opt_id == fill_id)
        for filler in template.get("fillers") or []:
            add(locale, "filler", filler, True)
        add(locale, "closer", template.get("closer") or "", True)
        frame = template.get("summary_frame") or ""
        for persona, trait in (template.get("personas") or {}).items():
            add(locale, "summary", frame.format(persona=persona, trait=trait), True)

    # ── 对抗面 ────────────────────────────────────────────────────────────────
    # 上面那 92 条实测**一条都不被闸掉**，于是「命中即吞」这一整支在闭包里根本没有观测点：
    # 把 Go 的 bannedHit/attributionHit 整条删掉，措辞闸语料照样全绿。批 2 已经立过这条规矩
    # （看不见的判据就补夹具，不登记成「已证」），所以这里按**词表本身**造探针。
    #
    # 词条一律从 safety_service 的表里现取，脚本不另抄一份：
    #   hit-*   必须以「被闸掉」收（want==""）
    #   safe-*  必须以「原样放行」收（want==raw）
    # 方向不符就直接 raise——那是探针文本自己撞上了别的词，改探针，不改闸。
    def probe(loc: str, kind: str, raw: str) -> None:
        verdict = safety_service.filter_template(raw, loc)
        if kind.startswith("hit-") and verdict != "":
            raise AssertionError(f"探针 {loc}/{kind} 本该被闸掉却放行了：{raw!r}")
        if kind.startswith("safe-") and verdict != raw:
            raise AssertionError(f"探针 {loc}/{kind} 本该放行却被闸掉了：{raw!r} ⇒ {verdict!r}")
        add(loc, kind, raw, False)

    def plain(text: str) -> bool:
        return not any(ch in text for ch in "\\[]()?*+|^$.{}")

    def group_word(patterns) -> str:
        """取一条**能直接拼进文本**的指纹：优先纯字面量（zh 全是），
        其次剥掉 `\\b` 后的单词（en 的 third/causal 几乎都带边界断言）。
        剥出来的词在探针文本里两侧都带空格 ⇒ `\b` 是否还在都不影响这一格的方向。
        """
        for pattern in patterns:
            if plain(pattern):
                return pattern
        for pattern in patterns:
            word = pattern.replace("\\b", "")
            if plain(word) and word:
                return word
        raise AssertionError("这一组指纹拼不出可用探针")

    bases = {"zh": "这段话只讲你自己的选择", "en": "This line is about your own choice"}
    for locale in ("zh", "en"):
        sep = "" if locale == "zh" else " "
        banned = safety_service.BANNED_TERMS[locale]
        markers = safety_service.ATTRIBUTION_MARKERS[locale]
        label = group_word(markers["label"])
        causal = group_word(markers["causal"])
        term = next((t for t in banned if plain(t) and " " not in t), None)
        if term is None:
            raise AssertionError(f"{locale} 的黑名单没有可拼的词")
        # 黑名单命中：把词条贴在模板句尾。zh 走 `term in text`（纯子串），en 走
        # `\b{term}`（**只有前边界**，Python 的 rf"\b{re.escape(term)}" 没有 \b 收尾）。
        probe(locale, "hit-banned", f"{bases[locale]}{sep}{term}")
        if locale == "zh":
            # 子串判据的另一半：词条中间插一个字就不算命中。
            probe("zh", "safe-broken-term", f"{bases['zh']}{term[0]}了{term[1:]}")
        else:
            # 同一支判据的两个方向：前面粘住字母 ⇒ 不是词首 ⇒ 放行；
            # 后面接东西 ⇒ 因为没有后边界，照样命中。
            # 后者是「把 Python 的 \`\\b{term}\` 误写成 \`\\b{term}\\b\`」的唯一观测点——
            # 那种写法会把 "healing" 从 "heal" 里放出来，是改判据不是复刻。
            probe("en", "safe-boundary-pre", f"{bases['en']} re{term}")
            probe("en", "hit-boundary-post", f"{bases['en']} {term}ing")
        # 越界：third 是**必需**的一支。label∧causal 而没有 third 刻意不算越界
        # （safety_service.py:341-346 的注释写明了这条口径），所以它必须是 safe-*。
        if locale == "zh":
            third = group_word(markers["third"])
            probe("zh", "hit-label-third", f"{bases['zh']}{third}{label}")
            probe("zh", "hit-causal-third", f"{bases['zh']}{third}{causal}")
            probe("zh", "safe-no-third", f"{bases['zh']}{label}{causal}")
            # 后视断言 `(?<![其这那每])他` 的三格：行首、前一个字不在禁用集、前一个字在集内。
            # RE2 不支持后视，Go 是按定义手写的（见 domain/first_read_gate.go 的 markerMatcher），
            # 这三格就是那支手写等价的全部可证面。
            probe("zh", "hit-third-at-start", f"他说这是{label}")
            probe("zh", "hit-third-after-verb", f"说他是{label}")
            probe("zh", "safe-third-lookbehind", f"和其他{label}有关")
        else:
            third = group_word(markers["third"])
            probe("en", "hit-label-third", f"{bases['en']} {third} {label}")
            probe("en", "hit-causal-third", f"{bases['en']} {third} {causal}")
            probe("en", "safe-no-third", f"{bases['en']} {label} {causal}")
    return out


def persona_upper_corpus() -> list[dict]:
    """`persona_type` 的 `.upper()` **闭包**：整个 BMP 里所有「upper 会变样」的码位。

    为什么必须扫全 BMP 而不是手挑（D-4/可达性）：校验器的判据是 `^[A-Z]{4}$`，
    于是「一个非 ASCII 字符能不能拼出合法的 INTJ」是**响应面**的问题，不是风格问题。
    CPython 的 str.upper() 有三类输出：单码位恒等、单码位变 ASCII（i→I）、
    **多码位展开**（ß→SS、ﬁ→FI）。Go 的 strings.ToUpper 第三类是恒等，
    第一类对非 ASCII 拉丁字母（ß 之外的 ǳ、ŉ…）也不同——只有把整张表录下来逐条比，
    才说得上「Go 复刻的是 CPython 的 upper，不是 ASCII 的 upper」。
    每行记 raw=码位×4、upper=Python 的结果、ascii4=是否因此成为**合法** persona。
    Go 侧不只比这几条：`TestN8PersonaUpperCorpusIsClosed` 用 Go 自己扫一遍 BMP，
    要求集合等于「这张表的 ascii4 码位 ∪ A-Z」——`'A'.upper()` 恒等，按上面的筛选
    条件它不在表里，但四位重复后照样合法，所以那一半由 Go 按定义补齐。
    """
    out: list[dict] = []
    for cp in range(0x20, 0x10000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        char = chr(cp)
        upper = char.upper()
        if upper == char:
            continue
        if not (upper.isascii() or len(upper) != 1):
            continue
        four = next((char * k for k in (1, 2, 3, 4)
                     if re.fullmatch(r"[A-Z]{4}", (char * k).upper())), char * 4)
        out.append({"raw": four, "upper": four.upper(),
                    "ascii4": bool(re.fullmatch(r"[A-Z]{4}", four.upper()))})
    return out


def referral_link_corpus() -> list[dict]:
    """`referral_link(site_url, code)` 的输入面（backend/core/referral.py:34-37）。

    钉的是 `site_url.rstrip('/')`：剥的是**一串**斜杠而不是一个，所以
    "https://a//" 也得 https 站点单斜杠。空串与 "/" 这两格是配置写歪时的形状——
    Go 若写成 TrimSuffix，"https://a//" 会留下双斜杠，深链就指错地方。
    """
    from backend.core.referral import referral_link

    variants = [
        "https://closenai.app", "https://closenai.app/", "https://closenai.app//",
        "https://closenai.app///", "http://localhost:8080/", "https://a/b",
        "https://a/b/", "", "/", "///", "  https://a  ",
    ]
    return [{"site_url": v, "link": referral_link(v, "QQ22WW33")} for v in variants]


def share_url_inputs() -> dict:
    """夹具带出的两个设置原文：Go 回放测据此构造 cfg，比的是**主机部分逐字**。

    为什么带出来而不是在 Go 侧写死默认值：share_url 会**落进 first_read 列**，
    restore 按列复现、不回查设置。两侧默认值不同＝同一个账号切流前后拿到不同深链。
    """
    from backend.config import settings

    return {"site_url": settings.site_url, "app_store_url": settings.app_store_url}


def mood_copy_corpus() -> list[dict]:
    """15 语言 × 4 判据 = 60 条 trend summary 的**闭包**（不是采样）。

    为什么必须闭包：`GET /mood/trend` 的 HTTP 用例只能各命中一格判据，
    而 Go 侧那 60 条是**逐字节副本**——副本与 `backend/data/copy/*.json` 一旦分叉，
    只有被命中的那一格会红，其余 59 格静默错。整张表列出来才是闭包。
    取的是 `copy.t()` 的**返回值**（含它自己的两级回退），不是 JSON 里的原始字面量：
    这样「目标语言缺键回退 en」那条链也一并进证据面（本域实测零缺键，所以两者相同，
    但将来谁删了一格，这里会立刻显出回退值而不是消失）。
    """
    from backend.core import copy as copy_module

    return [{"locale": loc, "key": f"trend.{k}",
             "out": copy_module.t("mood", f"trend.{k}", loc)}
            for loc in copy_module.SUPPORTED_LOCALES
            for k in ("empty", "high", "mid", "low")]


def language_resolve_corpus() -> list[dict]:
    """`resolve_request_language` 的归一闭包：原始 header 值 → 归一码（或 None）。

    直接调**真函数**（它只读 `request.headers.get("X-Language","")`，所以一个
    带 headers dict 的简单对象就够），Go 侧由 `domain.ResolveRequestLanguage` 就地重算。
    这一格存在的理由是摸底发现的两支分叉：`language.py:30` 用 `split("-")[0].lower()`
    ＋精确成员判定，`copy.py:35` 用 `lower().strip()`＋前缀匹配——同一个原始值
    两边可以给出不同结论，而 `/mood/trend` 的 summary 恰好**两级都走**。
    """
    from types import SimpleNamespace

    from backend.core.language import resolve_request_language

    raws = ["en", "zh", "ZH", "zh-CN", "zh-Hans", "zh_CN", "en-GB", "pt-BR",
            " ", "  es  ", "", "xx", "portuguese", "ﬁnt", "１４", "de-AT",
            "km", "km-KH", "tw", "cn", "zh-TW", "es-419", "--"]
    out = []
    for raw in raws:
        resolved = resolve_request_language(SimpleNamespace(headers={"X-Language": raw}))
        out.append({"raw": raw, "want": resolved})
    return out


def main() -> None:
    seed()
    freeze_clock()
    recorded, samples = asyncio.run(record())
    corpus = datetime_corpus()
    templates = template_corpus()
    personae = persona_upper_corpus()
    payload = {
        "_comment": "generated by scripts/gen_n8_contract_goldens.py; do not hand-edit",
        "trace_id": TRACE,
        # curriculum 组的回放必须把 Server.now 钉在这一刻（见 ANCHOR 的注释）。
        "recorded_at": ANCHOR.isoformat(),
        # 四条掩码都是**易变值**，不是「难比对的字段」：
        #   generated_at      —— 录制与回放必然差一个时刻
        #   journey_event_id  —— uuid4 主键，预写行数只能靠**次数**证
        #   referral_link     —— 码由惰性生成；种子已带码的用例**不掩**（逐字比就是
        #                        「已有码不许被换」的证据，见 record() 的 code_masks）
        #   created_at        —— 同上：种子 profile 的绝对时刻留着比 `+00:00` 形状，
        #                        只有本次运行新写的行才掩
        "masked_fields": ["generated_at", "journey_event_id", "referral_link", "created_at"],
        "generated_at_samples": samples[:4],
        # Go 侧 cfg 的 SiteURL/AppStoreURL 从这里取，不写死默认值（见 share_url_inputs 注释）
        "share_url_inputs": share_url_inputs(),
        "seed": seed_spec(),
        "occurred_at_corpus": corpus,
        "week_window_corpus": week_window_corpus(),
        "push_moment_corpus": push_moment_corpus(),
        # 批 4 的三块**核**语料：措辞闸闭包、CPython upper 闭包、深链拼接。
        # 它们都不是 HTTP 响应面能取证的——前者的命中会让一张卡静默少一句、
        # 后者只在改模板/换配置时才显现。
        "template_corpus": templates,
        "persona_upper_corpus": personae,
        "referral_corpus": referral_link_corpus(),
        # 批 5 的两块核：60 条文案闭包＋语言归一闭包。
        # 都不是 HTTP 用例能覆盖的——前者只有被命中的那一格会红，
        # 后者两支归一函数的分叉在 200 响应里根本显不出来。
        "mood_copy_corpus": mood_copy_corpus(),
        "language_resolve_corpus": language_resolve_corpus(),
        "cases": recorded,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    counts = {g: sum(1 for c in recorded if c["group"] == g)
              for g in ("validation", "seeded", "curriculum", "journey", "onboarding",
                        "mood", "reflection")}
    accepted = sum(1 for c in corpus if c["ok"])
    print(f"wrote {OUT}: {len(recorded)} cases "
          f"(validation {counts['validation']} / seeded {counts['seeded']} / "
          f"curriculum {counts['curriculum']} / journey {counts['journey']} / "
          f"onboarding {counts['onboarding']} / mood {counts['mood']} / "
          f"reflection {counts['reflection']})")
    print(f"recorded_at={ANCHOR.isoformat()} journey_events={len(seed_spec()['journey_events'])} "
          f"week_window_corpus={len(payload['week_window_corpus'])} "
          f"push_moment_corpus={len(payload['push_moment_corpus'])} "
          f"id_masks={sum(c['id_masks'] for c in recorded)} "
          f"code_masks={sum(c['code_masks'] for c in recorded)} "
          f"created_at_masks={sum(c['created_at_masks'] for c in recorded)}")
    print(f"occurred_at corpus: {len(corpus)} entries ({accepted} accepted / "
          f"{len(corpus) - accepted} rejected)")
    probe_rows = [t for t in templates if t["kind"].startswith(("hit-", "safe-"))]
    print(f"template_corpus={len(templates)} "
          f"(closure={len(templates) - len(probe_rows)} dropped="
          f"{sum(1 for t in templates if t['out'] == '' and t not in probe_rows)} "
          f"probes={len(probe_rows)}) "
          f"persona_upper_corpus={len(personae)} "
          f"legal_after_upper={sum(1 for p in personae if p['ascii4'])} "
          f"referral_corpus={len(payload['referral_corpus'])}")
    print(f"mood_copy_corpus={len(payload['mood_copy_corpus'])} "
          f"distinct={len({row['out'] for row in payload['mood_copy_corpus']})} "
          f"language_resolve_corpus={len(payload['language_resolve_corpus'])} "
          f"resolved={sum(1 for row in payload['language_resolve_corpus'] if row['want'])}")
    print(f"share_url_inputs={payload['share_url_inputs']}")
    print(f"generated_at samples: {samples[:3]}")


if __name__ == "__main__":
    main()
