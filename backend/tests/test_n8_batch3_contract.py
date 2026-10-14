"""N8 批 3（journey 域）的跨语言逐字闸。

被验对象：`go-core/internal/{domain,httpapi,store,config}` 里从 Python 移植的 journey 口径，
`contracts/openapi.json` 里这两条端点的 deprecated 标记，以及夹具 journey 组（46 条）与
push_moment_corpus（736 条）自身的覆盖面。

为什么这一批的闸和批 1/批 2 长得不一样（三条，每条都在本文件里钉住）：
1. **读接口会写库**。`GET /journey/events` 第一件事是 `ensure_prefilled`（journey_service.py:887
   → :189-201），命中幂等键缺口时插 6 行。于是「惰性写入者的沉默」本身成了契约：
   用户读不到（含注销）时 Python **静默返回**再照样列行，回 200 而不是 40401。
   Go 把它「修正」成 40401 更合理，但那是改契约——灰度期同一账号两侧状态码不同。
2. **两行 uuid 主键进响应**。批 1/2 的响应没有 journey_events.id，这一批有，且预写行的主键
   是 `str(uuid4())`（journey_repository.py 的 schedule）⇒ 录制与回放的 uuid 必然不同。
   所以逐字比之前要按一条**两侧同源的正则**把它掩掉，并核对掩码次数（id_masks）。
   正则各写各的，就会有「Go 少掩一处 ⇒ 常红」或「多掩一处 ⇒ 把真发散掩成绿」。
3. **墙钟不可逆的两种形状**。推送窗口由「用户本地偏好时刻 → UTC 瞬间」折算，本地零点/时刻
   落在 DST 缺位（春季拨快）或重复（秋季回拨）时，两种语言、两个库都给不同答案。
   批 2 的 899 条 week_window 只撞见缺位，重复那一类靠这里点名。

口径来源（每条点名到行）：
- SCRIPT_EVENTS           ← journey_service.py:62-69        ↔ domain/journey.go::ScriptEvents
- limit=50                ← journey_repository.py:96-108 + journey_service.py:888 ↔ domain.JourneyListLimit
- `or "09:00"` / 9,0      ← journey_service.py:237-240      ↔ domain/journey.go::defaultPushHour/Minute
- 「事件不存在 / Event not found」 ← journey_service.py:911  ↔ httpapi/journey_ack.go::forbiddenJourneyMessage
- max_length=36           ← models/schemas.py:532-534       ↔ httpapi/pybody.go::maxJourneyEventID
- count_unseen 三谓词     ← journey_repository.py:156-166   ↔ store/legacy_batch3.go::countJourneyUnseen
- acknowledge 两列赋值    ← journey_repository.py:208-213   ↔ store/legacy_batch3.go::acknowledgeJourneyEvent
- 40402                   ← core/errors.py:58               ↔ apierr/apierr.go::CodeResourceNotFound
- CLOSEN_CUTOVER_JOURNEY  ← config/config.go:66             ↔ deploy/k8s/10-config.yaml
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from backend.main import app

ROOT = Path(__file__).resolve().parents[2]
GO = ROOT / "go-core"
FIXTURE = GO / "testdata" / "n8_contract.json"
MANIFESTS = ROOT / "deploy" / "k8s"

BATCH3_DOMAIN = "journey"
BATCH3_PATHS = {"/api/v1/journey/events", "/api/v1/journey/events/acknowledge"}

# 与 go-core/internal/httpapi/n8_replay_test.go 的 n8EventIDRe **同一串字面量**。
# 这条正则是 uuid 掩码的定义：两侧必须逐字相同，所以这里比文本而不是「各自都写成能跑通的样子」。
GO_EVENT_ID_PATTERN = (
    r'"id":"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"'
)
GO_EVENT_ID_MASK = '"id":"<event_id>"'


def _go(rel: str) -> str:
    return (GO / rel).read_text(encoding="utf-8")


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _go_value(name: str, rel: str) -> str:
    src = _go(rel)
    match = re.search(rf"{name}\s*=\s*([^=\n]+?)\n", src)
    assert match, f"Go 侧 {rel} 找不到 {name}——改名或换写法了，闸要跟着改"
    return match.group(1).strip().strip('"')


def _py_script_events() -> list[tuple[str, int]]:
    src = (ROOT / "backend" / "services" / "journey_service.py").read_text(encoding="utf-8")
    block = re.search(r"SCRIPT_EVENTS[^=]*=\s*\((.*?)\n\)", src, re.S)
    assert block, "SCRIPT_EVENTS 的写法变了，闸要跟着改"
    return [(k, int(v)) for k, v in re.findall(r'\("([^"]+)",\s*(\d+)\)', block.group(1))]


def _go_script_events() -> list[tuple[str, int]]:
    src = _go("internal/domain/journey.go")
    block = re.search(r"var ScriptEvents = \[\]ScriptEvent\{(.*?)\n\}", src, re.S)
    assert block, "Go 侧 ScriptEvents 的写法变了，闸要跟着改"
    return [(k, int(v)) for k, v in re.findall(r'\{"([^"]+)", (\d+)\}', block.group(1))]


def _journey_cases() -> list[dict]:
    return [c for c in _fixture()["cases"] if c["group"] == BATCH3_DOMAIN]


def test_both_journey_routes_deprecated_yet_still_routable():
    """第④步：只标废弃，**不删路由**——回滚＝把 CLOSEN_CUTOVER_JOURNEY 改回 0，
    前提是 Python 还答得出这两条。

    `route_class=CommitBeforeResponseRoute` 也在这一条里守：GET 会写 6 行，
    「响应发出前提交」是它的提交时机；换成默认 APIRoute 就变成响应后再提交，
    同一用户下一次请求在两侧读到的行数可能不同。
    """
    src = (ROOT / "backend" / "routers" / "journey.py").read_text(encoding="utf-8")
    assert 'router = APIRouter(route_class=CommitBeforeResponseRoute)' in src
    for deco in (
        '@router.get("/journey/events", deprecated=True)',
        '@router.post("/journey/events/acknowledge", deprecated=True)',
    ):
        assert deco in src, f"路由装饰器少了 deprecated：{deco}"

    paths = app.openapi()["paths"]
    assert BATCH3_PATHS <= set(paths), sorted(BATCH3_PATHS - set(paths))
    for p, op in (("/api/v1/journey/events", "get"), ("/api/v1/journey/events/acknowledge", "post")):
        assert paths[p][op].get("deprecated"), f"{p} 的 {op} 没标废弃"


def test_go_registers_both_journey_routes_under_the_batch3_switch():
    """Ingress 一刀把 /api/v1/journey 指到 go-api ⇒ Go 必须有这两条**带方法**的注册，
    且都在 batch3 接线开关下（机制没接好时 Go 不该抢先回答这一域）。

    两个 handler 各调一次 `Gate.Delegate(…, "journey", userID)`：域名字符串错了
    不是编译错误，而是「这一域永远 0%、永远只多一跳代理」——放量时才被发现。
    """
    src = _go("internal/httpapi/server.go")
    assert 's.mux.HandleFunc("GET /api/v1/journey/events"' in src
    assert 's.mux.HandleFunc("POST /api/v1/journey/events/acknowledge"' in src
    guard = re.search(r"if s\.batch3 != nil \{(.*?)\n\t\}", src, re.S)
    assert guard and guard.group(1).count("journey") == 2, "两条 journey 路由没同时挂在批 3 开关下"

    for rel in ("internal/httpapi/journey_events.go", "internal/httpapi/journey_ack.go"):
        assert _go(rel).count('Gate.Delegate(w, r, "journey", userID)') == 1, (
            f"{rel} 的转交判定缺失或域名写错"
        )


def test_script_events_table_matches_go_row_for_row():
    """六行 (kind, offset) 逐字 + **顺序**：Python 按 SCRIPT_EVENTS 的顺序在同一事务里写，
    Go 的 JourneyPrefillPlan 按 ScriptEvents 的顺序产出计划。行序变了 ⇒ 响应数组的字节序变了，
    而 D4/D6 天然没有事件（PRD §5.19.3），少一行或多一行都是改内容契约。"""
    py = _py_script_events()
    assert py == [
        ("day0_read", 0),
        ("day1_checkin_echo", 1),
        ("day2_first_outreach", 2),
        ("day3_weak_assert", 3),
        ("day5_stat", 5),
        ("day7_report", 7),
    ], "Python 侧时刻表本身变了：先确认 Go 是不是也该跟着变，再改这条断言"
    assert _go_script_events() == py, "Go 的 ScriptEvents 与 Python 不同字面量或不同顺序"


def test_list_limit_and_push_fallback_literals_match_go():
    """两个数字决定「列几行」和「没有偏好时刻时算几点」。

    limit 有**两处**要一致：仓库默认值（journey_repository.py:101）与 service 的显式实参
    （journey_service.py:888）——只改一处，Python 自己就会在不同调用点给不同行数，
    而 Go 只有一个常量，切流后必然与其中一侧不符。

    推送兜底是 `user.push_time or "09:00"` 与 `except …: hour, minute = 9, 0` 两条不同的路，
    Go 用 defaultPushHour/Minute 同时复刻；把它们改成 8 或 10 会让每一行的 scheduled_at 平移。
    """
    assert int(_go_value("JourneyListLimit", "internal/domain/journey.go")) == 50
    repo = (ROOT / "backend" / "repositories" / "journey_repository.py").read_text(encoding="utf-8")
    default_limit = re.search(r"async def list_for_user\(.*?limit: int = (\d+)", repo, re.S)
    assert default_limit and int(default_limit.group(1)) == 50, "仓库默认 limit 变了"
    svc = (ROOT / "backend" / "services" / "journey_service.py").read_text(encoding="utf-8")
    assert re.search(r"list_for_user\(db, user_id, limit=50\)", svc), "service 的显式 limit 变了"

    assert (_go_value("defaultPushHour", "internal/domain/journey.go"),
            _go_value("defaultPushMinute", "internal/domain/journey.go")) == ("9", "0")
    body = re.search(r"def _push_moment.*?(?=\n    def )", svc, re.S).group(0)
    assert 'user.push_time or "09:00"' in body, "NULL/空串的兜底字面量变了"
    assert "hour, minute = 9, 0" in body, "解析失败分支的兜底时刻变了"
    assert "min(max(int(hh), 0), 23), min(max(int(mm), 0), 59)" in body, "钳位写法变了"


def test_ack_contract_literals_match_go():
    """越权与不存在合成**同一条** 40402 文案（不泄露他人 ID 有效性），
    且响应回显的是**入参** event_id 而不是列值；长度上限 36 两侧同源。

    四样都是字面量级契约：文案差一个字符、码值差一位、max_length 差一个数、
    回显取错来源，回放就红——而前三样红得莫名其妙，所以在这里点名出处。
    """
    svc = (ROOT / "backend" / "services" / "journey_service.py").read_text(encoding="utf-8")
    ack = re.search(r"async def acknowledge.*?journey_repository\.acknowledge", svc, re.S).group(0)
    assert "event is None or event.user_id != user_id" in ack
    assert ack.count("AppException(ErrorCode.RESOURCE_NOT_FOUND") == 1, "404 与越权被拆成两条了"

    assert _go_value("forbiddenJourneyMessage", "internal/httpapi/journey_ack.go") == (
        "事件不存在 / Event not found"
    )
    assert '"事件不存在 / Event not found"' in ack

    from backend.core.errors import ErrorCode

    go_codes = _go("internal/apierr/apierr.go")
    match = re.search(r"CodeResourceNotFound\s*=\s*(\d+)", go_codes)
    assert match and int(match.group(1)) == int(ErrorCode.RESOURCE_NOT_FOUND) == 40402

    schemas = (ROOT / "backend" / "models" / "schemas.py").read_text(encoding="utf-8")
    assert re.search(r"class JourneyAckRequest.*?event_id: str = Field\(\.\.\., max_length=36\)",
                     schemas, re.S), "请求模型的长度约束写法变了"
    assert int(_go_value("maxJourneyEventID", "internal/httpapi/pybody.go")) == 36

    router = (ROOT / "backend" / "routers" / "journey.py").read_text(encoding="utf-8")
    assert '{"acknowledged": request.event_id}' in router, "Python 的回显来源不再是入参"
    assert 'out.set("acknowledged", pyStringValue(eventID))' in _go(
        "internal/httpapi/journey_ack.go"), "Go 的回显来源不再是入参"


def test_lazy_writer_silence_is_not_promoted_to_an_error():
    """本批最容易「顺手修好」的一格，也是唯一能在这里证它的形状。

    journey_service.ensure_prefilled（:194-200）有两个 `return`：
      - `user = await user_repository.get_user(...)`，get_user 过滤 status='active'
        （user_repository.py:83）⇒ 注销用户读不到 ⇒ 静默返回；
      - `base = profile.created_at if … else user.created_at`，`if base is None: return`。
    返回后 list_events 照样查 journey_events（list_for_user **不带** status 过滤，
    journey_repository.py:99-107）⇒ 已注销用户若有历史行，这条端点回 200 + 那些行。

    Go 侧对应的形状是 `if !user.Found { return nil }` 与 `if base == nil { return nil }`，
    且整个 ensurePrefilled **不许出现** CodeUserNotFound：升成 40401 看着更合理，
    但那是同一账号在灰度期两侧状态码不同。
    """
    svc = (ROOT / "backend" / "services" / "journey_service.py").read_text(encoding="utf-8")
    body = re.search(r"async def ensure_prefilled.*?(?=\n    async def )", svc, re.S).group(0)
    assert "user_repository.get_user(db, user_id)" in body
    assert body.count("\n            return\n") + body.count("\n        return\n") >= 2, (
        "ensure_prefilled 的静默返回少了一格"
    )
    assert "AppException" not in body, "静默返回被改成抛错了"

    ur = (ROOT / "backend" / "repositories" / "user_repository.py").read_text(encoding="utf-8")
    assert re.search(r"async def get_user\(.*?User\.status == \"active\"", ur, re.S), (
        "get_user 不再过滤 active：静默分支的触发条件变了，Go 的 JourneyUser 查询要跟着改"
    )

    go = _go("internal/httpapi/batch3.go")
    impl = go[go.index("func (s *Server) ensurePrefilled"):]
    assert impl.count("return nil") >= 2, "Go 侧的静默返回少了一格"
    assert "CodeUserNotFound" not in impl, "Go 把「读不到用户」升成了 40401：改了契约"

    cases = {c["name"]: c["status"] for c in _journey_cases()}
    assert cases["events-deleted-user"] == 200, cases["events-deleted-user"]
    # acknowledge 的归属链（journey_service.py:909 走 repository.get）不过滤 status，
    # 所以注销用户名下的事件回 200——与上面那条是**两条不同的**判据，都得留。
    assert cases["ack-deleted-owner"] == 200, cases["ack-deleted-owner"]


def test_count_unseen_predicate_and_call_order_match_go():
    """未读数的三个谓词与它在响应组装里的**位置**。

    谓词：`status == "sent"` 且 `acknowledged.is_(False)`（journey_repository.py:163-164）。
    `.is_(False)` 与 SQL 的 `acknowledged IS FALSE` 同形，而不是 `= false`——后者在 NULL 行上
    结果不同，历史脏值要认（Go 侧 store/legacy_batch3.go:140 就是这个写法）。
    顺序：Python 在 dict 字面量里现算 unseen_count，看到的是**刚被预写过**的那批行；
    Go 的 handler 必须同样是 预写 ⇒ 列表 ⇒ 计数。
    """
    repo = (ROOT / "backend" / "repositories" / "journey_repository.py").read_text(encoding="utf-8")
    body = re.search(r"async def count_unseen.*?return int", repo, re.S).group(0)
    assert 'JourneyEvent.status == "sent"' in body
    assert "JourneyEvent.acknowledged.is_(False)" in body

    go = _go("internal/store/legacy_batch3.go")
    assert "AND status = 'sent' AND acknowledged IS FALSE" in go

    handler = _go("internal/httpapi/journey_events.go")
    order = [handler.index(x) for x in
             ("s.ensurePrefilled(", "s.batch3.Store.JourneyEvents(", "s.batch3.Store.JourneyUnseenCount(")]
    assert order == sorted(order), "预写/列表/计数的顺序变了：计数看到的行数不再与 Python 同刻"

    # 列表查询不带 status 过滤（注销用户的行照样列出来）。
    assert "ORDER BY day_no ASC, scheduled_at ASC" in go, (
        "排序键变了：批 2 的周视图按 scheduled_at 单键，两处不能抄混"
    )


def test_prefill_is_one_transaction_on_both_sides():
    """六组「查重⇒改/插⇒可能 skip」必须**原子**。

    Python 跑在请求事务里，由 CommitBeforeResponseRoute 在响应前一次提交，任何一步抛异常整批回滚；
    Go 用 pool.Begin + tx 上的三次 Exec + 末尾 Commit。逐条 autocommit 会留下
    「前三行已写、后三行失败」，而 Python 那边是「一行都没有」——同一用户切流前后行数不同。
    """
    go = _go("internal/store/legacy_batch3.go")
    impl = go[go.index("func (p *PG) PrefillJourney"):]
    assert "p.pool.Begin(ctx)" in impl and "tx.Commit(ctx)" in impl
    for stmt in ("selectJourneyKindDay", "insertJourneyEvent", "updateJourneySchedule",
                 "skipJourneyEvent"):
        assert stmt in impl, f"预写里少了 {stmt}"
    assert "p.pool.Exec" not in impl, "预写里有绕过事务的单条 autocommit"
    assert impl.count("tx.Exec") == 3 and impl.count("tx.QueryRow") == 1, (
        "事务内的语句次数变了：查重/改/插/跳过四步的接线不再是那一个事务"
    )
    # skip 的判据读的是**本次落库前**的 status（既有行不被 schedule 改），新行恒 pending。
    assert 'if row.Past && status == "pending"' in impl

    repo = (ROOT / "backend" / "repositories" / "journey_repository.py").read_text(encoding="utf-8")
    sched = re.search(r"async def schedule.*?return event", repo, re.S).group(0)
    assert "existing.scheduled_at = scheduled_at" in sched, "命中既有行的赋值项变了"
    assert 'status="pending"' in sched, "新行的 status 不再显式写 pending"
    skip = re.search(r"async def skip\(.*?(?=\n    async def )", repo, re.S)
    assert skip and "delete(" not in skip.group(0), "skip 里出现了删行：永不删行是 §5.19.3 的铁律"


def test_ack_update_sets_two_columns_without_owner_predicate():
    """一条 UPDATE 同时改 status 与 acknowledged，且 WHERE 只有 id（归属判定在 service）。

    把 user_id 加进 SQL 会让「越权」变成 0 行受影响而不是 40402，错误形状就换了；
    只改一列则红点清不掉。acknowledged 为 NULL 的历史行两侧都算 False
    （Python `bool(e.acknowledged)` ↔ Go `ack != nil && *ack`）。
    """
    repo = (ROOT / "backend" / "repositories" / "journey_repository.py").read_text(encoding="utf-8")
    body = re.search(r"async def acknowledge\(.*?\n\n", repo, re.S).group(0)
    assert '.where(JourneyEvent.id == event_id)' in body
    assert '.values(status="seen", acknowledged=True)' in body

    go = _go("internal/store/legacy_batch3.go")
    sql = re.search(r"const acknowledgeJourneyEvent = `([^`]*)`", go).group(1)
    assert "SET status = 'seen', acknowledged = true" in sql
    assert "WHERE id = $1" in sql and "user_id" not in sql
    assert "e.Acknowledged = ack != nil && *ack" in go
    assert "bool(e.acknowledged)" in (
        ROOT / "backend" / "services" / "journey_service.py").read_text(encoding="utf-8")


def test_journey_datetimes_serialize_with_offset_not_z():
    """两条序列化路径的分界，批 3 起必须钉住（否则有人会把两边「统一」成一种）。

    journey 的路由**没有** response_model/返回类型注解 ⇒ FastAPI 走 serialize_response 的
    `field is None` 分支 ⇒ jsonable_encoder ⇒ `ENCODERS_BY_TYPE[datetime] = isoformat` ⇒ "+00:00"。
    批 1 的 discovery 有 pydantic 模型，所以那边是 pydantic-core 的 "Z"。
    这里用夹具的录制体自证：journey 组凡带 datetime 的响应体全是 +00:00，一条 Z 也没有。
    """
    bodies = [c["response_text"] for c in _journey_cases() if re.search(r"\d{2}:\d{2}:\d{2}", c["response_text"])]
    assert len(bodies) >= 20, f"journey 组只剩 {len(bodies)} 条带 datetime 的响应体，测不到序列化路径"
    for b in bodies:
        assert "+00:00" in b, b[:120]
        assert not re.search(r'T\d{2}:\d{2}:\d{2}Z', b), "响应体里出现了 Z 形：两侧序列化路径被统一了"

    handler = _go("internal/httpapi/journey_events.go")
    assert handler.count("pyISOOffset(") == 2, "两列 datetime 少用/多用了偏移编码器"
    assert "pyISO(" not in handler.replace("pyISOOffset(", ""), (
        "journey handler 里出现了批 1 的 Z 形编码器"
    )


def test_event_id_mask_definition_is_the_same_on_both_sides():
    """uuid 掩码的定义只能有一条，且要能被逐字核对。

    预写行的主键是 `str(uuid4())`，录制与回放必然不同 ⇒ 「逐字节相同」只在掩码后成立。
    掩码写成两条各扫各的正则时，发散有两种安静的死法：Go 少掩一处 ⇒ 常红，
    有人于是把整组用例降级成「只比状态码」；Go 多掩一处 ⇒ 把真的内容发散掩成绿。
    所以这里要求 Go 源里的正则与掩码串**逐字等于**本文件的常量，
    并要求 masked_fields 里必须有这两样（名单逐批增长：批 4 加了推荐码与 created_at，
    那两条的定义由 test_n8_batch4_contract.py 逐字核对）。
    """
    replay = _go("internal/httpapi/n8_replay_test.go")
    assert GO_EVENT_ID_PATTERN in replay, "Go 侧的 uuid 掩码正则与本文件不同字面量"
    assert f'const n8MaskedEventID = `{GO_EVENT_ID_MASK}`' in replay, "Go 侧的掩码串不同"

    fixture = _fixture()
    assert {"generated_at", "journey_event_id"} <= set(fixture["masked_fields"]), (
        fixture["masked_fields"]
    )
    py_pattern = re.compile(GO_EVENT_ID_PATTERN)  # 本侧正则可用，且与 Go 的 RE2 串同源
    cases = _journey_cases()
    masked = [c for c in cases if c["id_masks"] > 0]
    assert masked, "journey 组没有任何掩码：预写分支（uuid 主键进响应）没被走过"
    assert sum(c["id_masks"] for c in cases) == 30, sum(c["id_masks"] for c in cases)
    for c in masked:
        assert GO_EVENT_ID_MASK in c["response_text"], c["name"]
        assert not re.search(GO_EVENT_ID_PATTERN, c["response_text"]), (
            f'{c["name"]} 记了 id_masks 但体里还有未掩的 uuid'
        )


def test_push_moment_corpus_does_not_degenerate():
    """日历核的语料面（Go 侧 n8_pushmoment_test.go 逐条比对 736 条 want）。

    反向守的理由与批 2 同：录制脚本若把「每 10 天扫一遍 + 压住两次 DST 跳变」缩成
    「只扫 UTC 午夜」，行数骤减但 Go 的逐条比照样 100% 通过，而 `_push_moment` 的
    本地折算没人测了。这里额外要求**重复墙钟**与「墙钟不可逆」两类样本都在——
    批 2 的 week_window 只撞见缺位（春季），重复（秋季回拨）是 2026-10-14 靠这支语料
    才暴露出 Go≠Python 的（London/Sydney/Lord_Howe 三条，见 tasks.md 批 3 勘误）。
    """
    rows = _fixture()["push_moment_corpus"]
    assert len(rows) >= 700, f"语料只剩 {len(rows)} 行，覆盖面在缩"
    zones = {r["tz"] for r in rows}
    assert len(zones) >= 10, f"只剩 {len(zones)} 个时区名：{sorted(zones)}"
    assert {"", "Not/AZone", "Asia/Shanghai", "Pacific/Apia",
            "Australia/Lord_Howe", "America/Santiago"} <= zones, sorted(zones)
    assert len({(r["tz"], r["push_time"]) for r in rows}) >= 100, "(时区, 偏好时刻) 组合在缩"
    assert any(r["push_time"] is None for r in rows), "没有 NULL 偏好时刻：or 分支的列值形状没了"
    assert any(r["push_time"] == "" for r in rows), "没有空串偏好时刻：同分支的另一种列值没了"
    assert len({r["day"] for r in rows}) >= 10, "日历日在缩"
    assert any(r["transition"] for r in rows), "没有落在 DST 跳变日的样本"

    # 「墙钟不可逆」＝ want 那一瞬间换算回本地后，墙钟不等于输入的 (day, push_time)。
    # 这里**独立重算**一遍而不是信 transition 位：语料若被人手改标签，Go 侧的逐条比
    # 仍会全绿（它只比 want），而这一格证的正是「换算不是简单相加」这件事还在被走过。
    irreversible = 0
    for r in rows:
        try:
            zone = ZoneInfo(r["tz"])
        except Exception:  # noqa: BLE001 — ""、Not/AZone 走 safe_zoneinfo 的 UTC 回退，不参与此判据
            continue
        # 兜底与 _push_moment 同一条：脏值（"7"、"9:30:00"、非数字）都落 9,0。
        try:
            hh, mm = (r["push_time"] or "09:00").split(":")
            hour, minute = min(max(int(hh), 0), 23), min(max(int(mm), 0), 59)
        except (ValueError, IndexError):
            hour, minute = 9, 0
        wall = datetime.strptime(r["day"], "%Y-%m-%d").replace(
            hour=hour, minute=minute, tzinfo=zone
        )
        want = datetime.strptime(r["want"], "%Y-%m-%dT%H:%M:%S+00:00").replace(tzinfo=timezone.utc)
        if want.astimezone(zone).replace(tzinfo=None) != wall.replace(tzinfo=None):
            irreversible += 1
    assert irreversible > 0, "语料里没有任何墙钟不可逆的行：本批最容易写歪的换算没被走过"


def test_seed_face_has_the_journey_row_shapes():
    """播种面必须含「兜底分支的每一种输入 + 四种 status + 基准日的三种来源」，
    否则逐字节回放只在测同一条 happy path。
    """
    fixture = _fixture()
    users = {u["id"].removeprefix("n8b1-"): u for u in fixture["seed"]["users"]}
    for slug, why in (("j-null-push", "push_time 为 NULL ⇒ or 分支"),
                      ("j-empty-push", "push_time 为空串 ⇒ 同一条 or 分支，不是解析失败"),
                      ("j-legacy-dirty", 'push_time="7" ⇒ split 只有一段'),
                      ("j-clamp", 'push_time="24:99" ⇒ min(max(…)) 钳位'),
                      ("j-fullwidth", "push_time 全角数字 ⇒ CPython int() 认、strconv.Atoi 不认"),
                      ("j-gap-ny", "本地 02:30 落在春季缺位"),
                      ("j-repeat-ny", "本地 01:30 落在秋季重复"),
                      ("j-lang-empty", 'language="" ⇒ or "en"'),
                      ("j-lang-nonascii", "language 非 ASCII ⇒ payload 逐字"),
                      ("j-deleted", "status=deleted ⇒ 静默跳过预写但照样列行")):
        assert slug in users, f"缺 {slug} 用户：{why} 这条分支没测"
    assert users["j-null-push"]["push_time"] is None
    assert users["j-empty-push"]["push_time"] == ""
    assert users["j-legacy-dirty"]["push_time"] == "7"
    assert users["j-clamp"]["push_time"] == "24:99"
    assert users["j-fullwidth"]["push_time"] == "１２:３４"
    assert users["j-gap-ny"]["push_time"] == "02:30"
    assert users["j-repeat-ny"]["push_time"] == "01:30"
    assert users["j-deleted"]["status"] == "deleted"

    profiles = {p["user_id"].removeprefix("n8b1-"): p for p in fixture["seed"]["onboarding_profiles"]}
    assert profiles["j-profile"]["created_at"], "基准日走 profile.created_at 的那一格没有样本"
    assert profiles["j-profile-null"]["created_at"] is None, (
        "缺 created_at 为 NULL 的档案 ⇒ `if base is None: return` 与 falsy 判定没测"
    )

    statuses = {e["status"] for e in fixture["seed"]["journey_events"]}
    assert {"pending", "sent", "seen", "skipped"} <= statuses, statuses


def test_journey_group_cases_cover_the_lazy_writer_branches():
    """回放闸自身的覆盖面：两个端点、成对的幂等、空表、上限截断、越权与校验面。

    「成对」是这一组的重点而不是重复：`events-insert-branch` 之后紧跟 `-again`，
    第二次必须回**同样字节**（幂等由 (user_id, event_kind, day_no) 查重保证）。
    没有 -again，Go 侧「每次都插 6 行」的实现也能全绿——因为录制只发生过一次。
    """
    cases = _journey_cases()
    assert len(cases) == 46, len(cases)
    assert {c["path"].split("?")[0] for c in cases} == {
        "/api/v1/journey/events", "/api/v1/journey/events/acknowledge"
    }
    assert {c["status"] for c in cases} == {200, 400, 404}, {c["status"] for c in cases}

    names = {c["name"] for c in cases}
    for required in ("events-insert-branch", "events-insert-branch-again",
                     "events-no-rows", "events-no-rows-again", "events-limit-50",
                     "events-deleted-user", "events-push-fullwidth",
                     "ack-deleted-owner", "ack-event-id-37-nonascii", "ack-event-id-36-ok"):
        assert required in names, f"惰性写入者的分支 {required} 不在用例里"

    order = [c["name"] for c in cases]
    for first, again in (("events-insert-branch", "events-insert-branch-again"),
                         ("events-no-rows", "events-no-rows-again")):
        assert order.index(again) == order.index(first) + 1, f"{first} 的第二次访问没紧跟其后"

    # 写过的账号后面必须有「重读」用例：unseen_count 由**被 ack 之后**的行读出。
    assert "events-full-after-ack" in names and "ack-sent-event-again" in names


def test_recorder_freezes_the_journey_clock():
    """冻时钟得真的冻到 journey_service 的模块全局上。

    prefill 的 `today = local_today(user.timezone)` 决定哪几行被置 skipped，
    `utc_now` 出现在触达判定里；这两个名字在 get_week 里是晚绑定，所以覆盖模块全局即可。
    Go 侧把 Server.now 钉在 recorded_at（n8_replay_test.go），两侧必须同一个锚点，
    否则跨过本地午夜时「逐字比」变成比两个不同日期的响应。
    """
    script = (ROOT / "scripts" / "gen_n8_contract_goldens.py").read_text(encoding="utf-8")
    # 行首锚点：被注释掉的一行不算冻钟（#7 勘误里那种「机制存在」不能靠文本里出现过就算）。
    assert re.search(r"^\s*journey_module\.local_today\s*=", script, re.M), (
        "录制侧没冻 journey 的 local_today"
    )
    assert re.search(r"^\s*journey_module\.utc_now\s*=", script, re.M), (
        "录制侧没冻 journey 的 utc_now"
    )

    fixture = _fixture()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", fixture["recorded_at"]), (
        fixture.get("recorded_at")
    )
    # 播种行的 scheduled_at 必须是绝对时刻（相对当下会让两侧读到不同一天）。
    assert all(e["scheduled_at"].endswith("+00:00")
               for e in fixture["seed"]["journey_events"]), "journey 播种行有相对时间"


def test_cutover_journey_switch_exists_in_go_and_manifest_not_python():
    """CLOSEN_CUTOVER_JOURNEY 是 Go-only 的灰度面：Python 读了它就会出现
    「两侧都以为自己在答」的分裂状态；ConfigMap 里缺它则这一批无法回滚到 0。"""
    cfg = _go("internal/config/config.go")
    assert 'envInt("CLOSEN_CUTOVER_JOURNEY", 0)' in cfg
    data = next(d for d in yaml.safe_load_all(
        (MANIFESTS / "10-config.yaml").read_text(encoding="utf-8"))
        if d and d["kind"] == "ConfigMap")["data"]
    assert data["CLOSEN_CUTOVER_JOURNEY"] == "0", "这一批落地必须是 0%，放量是一次独立上线动作"
    py_src = (ROOT / "backend" / "config.py").read_text(encoding="utf-8")
    assert "CLOSEN_CUTOVER" not in py_src, "Python 侧读了 Go 的灰度开关"
