"""T2-06 · Python 生产者面：`DELETE /memories/{id}` ⇒ 提交之后投一条 `source.deleted`。

这条面是四件套里的**入口**那一件。工作单要的判据不是「有个函数会发消息」，而是三件
具体的、各自会坏在现网的事：

1. **顺序**：事件必须排在提交之后。`source_records.status` 是 Go 那边 `live_support`
   的算料，投在提交前＝对面读到的还是翻转前的自己，传播变成空转。
   → `test_delete_endpoint_publishes_only_after_the_commit`（探针是 `in_transaction()`）。
2. **默认关 ⇒ 零副作用**：灰度 0 时现网行为必须与今天逐字相同；「不拨号」比
   「拨了但失败」好解释，而开关开着却没地址时配置本身就是错的。
   → `test_switch_is_off_by_default`／`test_disabled_registers_nothing_and_opens_nothing`。
3. **失败不许改响应**：数据已经提交，回 500 只会让用户重试删除并拿到 404。
   → `test_publish_failure_keeps_response_200` ＋ 报告面的分道。

跨语言那部分（四个串、体里的四个键、码表）不住在这里，住在
`backend/tests/test_ai_mq_contract.py`——那个文件比对 Python ⇄ Go 两侧的字面量，
本文件只判 Python 自己这一侧的行为。真 broker ＋ 真 Go 入站那一段在
`backend/tests/test_v3_evidence_e2e.py`（同一条链的最后一跳）。
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

import backend.services.source_events as source_events
from backend.ai.mq.config import ENV_SOURCE_EVENTS, ENV_URL, SwitchError, load_producer_switch
from backend.ai.mq.contract import (
    CONTENT_TYPE_JSON,
    HEADER_SCHEMA_VERSION,
    HEADER_TRACE_ID,
    HEADER_USER_ID,
    REASON_MISSING_USER_ID,
    REASON_SOURCE_POINTER_INCOMPLETE,
    REASON_SOURCE_TYPE_NOT_ENABLED,
    REASON_TOO_LARGE,
    SOURCE_DELETED_BODY_KEYS,
    SOURCE_DELETED_CONSUMER,
    SOURCE_DELETED_MESSAGE_TYPE,
    SOURCE_DELETED_QUEUE,
    SOURCE_DELETED_ROUTING_KEY,
    ContractError,
    encode_source_deleted,
    source_deleted_message_id,
)
from backend.ai.mq.topology import EXCHANGE_EVENTS
from backend.core.auth import get_current_user
from backend.core.db_route import STATE_ATTR, run_post_commit_hooks
from backend.core.errors import ErrorCode
from backend.core.middleware import register_exception_handlers
from backend.database import get_db
from backend.models.memory import Memory
from backend.repositories import memory_repository
from backend.routers.memories import router

MAX_BODY = 64 * 1024
USER_ID = "u-del"
MEMORY_ID = "11111111-1111-1111-1111-111111111111"


def encode(**kwargs: Any):
    """带默认值的编码入口（测试里少写八个字，判据不变）。"""
    base: dict[str, Any] = {
        "user_id": "user-1",
        "source_id": "mem-9",
        "exchange": EXCHANGE_EVENTS,
        "max_body_bytes": MAX_BODY,
    }
    base.update(kwargs)
    return encode_source_deleted(**base)


# ── ① 编码面：投出去的字节对侧解得进 ─────────────────────


def test_encoded_bytes_are_the_documented_wire_shape():
    """断言两侧不许同源：这里写**字面量**，不 import Go 的常量。

    如果两边都从同一个 Python 常量派生，这条闸就变成「自己比自己」——
    Go 的 `sourceDeletedBody` 改了一个 tag，这里不会红，红的是对面那条契约闸；
    而那条闸在另一个语言侧，漂移动辄一次跨进程联调才被发现。
    """
    msg = encode(user_id="user-1", source_id="mem-9")
    assert json.loads(msg.body) == {
        "schemaVersion": "1",
        "userId": "user-1",
        "sourceTable": "memories",
        "sourceId": "mem-9",
    }, msg.body
    assert msg.routing_key == SOURCE_DELETED_ROUTING_KEY == "source.deleted"
    assert msg.message_type == SOURCE_DELETED_MESSAGE_TYPE == "source_deleted"
    assert msg.content_type == CONTENT_TYPE_JSON
    assert msg.exchange == "closenai.events"
    # message_type＝键把点换成下划线：对侧 `messageTypeFor()` 只有这一条规则。
    assert msg.message_type == msg.routing_key.replace(".", "_")


def test_ownership_headers_are_present_and_authoritative():
    """归属只有 `x-user-id` 一个来源；体里那个 `userId` 只是对账痕迹。

    对侧 `transportGate` 缺这个头就是永久拒绝（`missing_user_id`），
    而生产者写错这一格时，用户看到的表现是「删了，但依据还挂在洞察上」。
    """
    msg = encode(user_id="user-1", trace_id="trace-77")
    assert msg.headers[HEADER_USER_ID] == "user-1"
    assert msg.headers[HEADER_SCHEMA_VERSION] == "1"
    assert msg.headers[HEADER_TRACE_ID] == "trace-77"
    # 空 trace 不占位（与 Go publisher 同口径）：印一条空链路＝给排查留假证人。
    assert HEADER_TRACE_ID not in encode(user_id="user-1", trace_id="").headers


def test_body_keys_are_exactly_the_admitted_four():
    """对侧 `DisallowUnknownFields` ⇒ 多一键少一键都是永久拒绝，不是重投能救的。"""
    assert set(json.loads(encode().body)) == set(SOURCE_DELETED_BODY_KEYS)


def test_queue_and_consumer_name_are_pinned_on_the_producer_side_too():
    """生产者也要知道自己投进的是谁的队列、对面用哪个幂等名认领。

    为什么两侧都钉：`go.source.deleted` 只有 Go 认领，名字写错不会报错，
    只会让这条删除进一个没人绑定的队列然后被 broker 丢弃（exchange 无绑定＝静默扔）。
    `consumer` 与候选面不同值那条理由（inbox 幂等键 `(message_id, consumer)`）
    在生产侧同样成立——这里就是发射点，并串之后第一个受害者也是这里。
    """
    assert SOURCE_DELETED_QUEUE == "go.source.deleted"
    assert SOURCE_DELETED_CONSUMER == "go.core.source"
    assert EXCHANGE_EVENTS == "closenai.events"
    # 正向证人：候选面那个串确实**不**等于事件面这一个。
    from backend.ai.mq import contract  # 候选面的幂等名住在 topology/contract 的对侧，这里取本地值

    assert "go.core.candidate" != SOURCE_DELETED_CONSUMER
    assert contract.SOURCE_DELETED_TABLE == "memories"


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        pytest.param({"user_id": ""}, REASON_MISSING_USER_ID, id="归属为空"),
        pytest.param({"user_id": "   "}, REASON_MISSING_USER_ID, id="只有空白"),
        pytest.param({"source_id": ""}, REASON_SOURCE_POINTER_INCOMPLETE, id="指针少一行"),
        pytest.param({"source_table": ""}, REASON_SOURCE_POINTER_INCOMPLETE, id="指针少表"),
        pytest.param({"source_table": "reflections"}, REASON_SOURCE_TYPE_NOT_ENABLED,
                     id="来源没开（ADR-003）"),
    ],
)
def test_illegal_pointers_are_rejected_before_the_wire(kwargs: dict, reason: str):
    with pytest.raises(ContractError) as got:
        encode(**kwargs)
    assert got.value.reason == reason


def test_oversize_body_is_refused_locally_not_left_for_the_consumer():
    """两侧同一条阈值：超限在入站侧也是永久拒绝，投出去只是把毒消息推上阶梯。"""
    with pytest.raises(ContractError) as got:
        encode(max_body_bytes=16)
    assert got.value.reason == REASON_TOO_LARGE


# ── ② 幂等面：id 由指针派生，不带时间与随机 ──────────────


def test_message_id_is_deterministic_from_the_pointer():
    assert source_deleted_message_id("u", "memories", "m") == \
        source_deleted_message_id("u", "memories", "m")
    assert encode().message_id == encode().message_id
    # 三个维度各自都要能改变 id，否则「同一次删除」的判据会漏。
    assert source_deleted_message_id("u2", "memories", "m") != \
        source_deleted_message_id("u", "memories", "m")
    assert source_deleted_message_id("u", "memories", "m2") != \
        source_deleted_message_id("u", "memories", "m")


def test_pointer_parts_cannot_be_confused_by_concatenation():
    """没有分隔符时 `("ab","c")` 与 `("a","bc")` 会同串＝两次不同删除共享一个幂等键。

    这不是假想形状：`user_id` 与 `source_id` 都是变长文本，只靠拼接顺序分家，
    而撞键的表现是「第二次删除被 Go 的 inbox 判成重复，账面停在翻转前」。
    """
    assert source_deleted_message_id("ab", "memories", "c") != \
        source_deleted_message_id("a", "memories", "bc")


def test_correlation_id_points_at_itself():
    """事件没有上游命令可指（发射点是 HTTP 请求），correlation 只能指向自己。

    写成空串也行，但那样 `x-correlation-id` 在 Loki 里就查不到这条删除，
    而重放工具（T2-05）需要它。
    """
    assert encode().correlation_id == encode().message_id


# ── ③ 开关面：默认关 ⇒ 零副作用 ─────────────────────────


def test_switch_is_off_by_default():
    """现网默认档：没写 env ⇒ 不连 broker。变异靶 M6（把默认值改成 True）。"""
    sw = load_producer_switch({})
    assert sw.enabled is False
    assert sw.url == ""


@pytest.mark.parametrize("raw", ["1", "true", "yes", "on", "ON", " True "])
def test_on_values_recognised(raw: str):
    assert load_producer_switch({ENV_SOURCE_EVENTS: raw, ENV_URL: "amqp://x"}).enabled is True


@pytest.mark.parametrize("raw", ["", "0", "false", "no", "off"])
def test_off_values_recognised(raw: str):
    assert load_producer_switch({ENV_SOURCE_EVENTS: raw, ENV_URL: "amqp://x"}).enabled is False


@pytest.mark.parametrize("raw", ["maybe", "2", "yes-no"])
def test_unrecognised_boolean_refuses_to_start(raw: str):
    """猜成「大概是关」不行：一个手抖的值变成静默关断，比拒绝启动贵得多。"""
    with pytest.raises(SwitchError):
        load_producer_switch({ENV_SOURCE_EVENTS: raw, ENV_URL: "amqp://x"})


def test_enabled_without_url_refuses_to_start():
    with pytest.raises(SwitchError):
        load_producer_switch({ENV_SOURCE_EVENTS: "1"})


def _bare_request() -> Request:
    scope = {"type": "http", "headers": [], "state": {}, "app": None,
             "path": "/", "query_string": b"", "root_path": "", "scheme": "http",
             "http_version": "1.1", "method": "DELETE", "server": ("test", 80),
             "client": ("test", 1), "raw_path": b"/"}
    return Request(scope)


def test_disabled_registers_nothing_and_opens_nothing(monkeypatch):
    """开关关时 `schedule_source_deleted` 不许碰连接，也不许挂钩子。

    判据不是「返回了 False」，而是**没有副作用**：`aio_pika.connect_robust` 换成一个
    会炸的假件——真连了就一定红。
    """
    import aio_pika

    def boom(_url: str):
        raise AssertionError("开关关时不许拨号")

    monkeypatch.setattr(aio_pika, "connect_robust", boom)
    monkeypatch.delenv(ENV_SOURCE_EVENTS, raising=False)
    request = _bare_request()
    assert source_events.schedule_source_deleted(request, user_id="u", source_id="m") == \
        {"scheduled": False, "reason": source_events.REASON_SWITCH_OFF}
    assert getattr(request.state, STATE_ATTR, None) in (None, [])


async def test_publish_reports_switch_off_without_touching_the_wire(monkeypatch):
    monkeypatch.delenv(ENV_SOURCE_EVENTS, raising=False)
    report = await source_events.publish_source_deleted(user_id="u", source_id="m")
    assert report == {"published": False, "reason": "switch_off", "message_id": ""}


async def test_invalid_switch_is_its_own_branch(monkeypatch):
    """开着开关没给地址＝配置错，报告面要能把它与「broker 坏了」分开。"""
    monkeypatch.setenv(ENV_SOURCE_EVENTS, "1")
    monkeypatch.delenv(ENV_URL, raising=False)
    report = await source_events.publish_source_deleted(user_id="u", source_id="m")
    assert report == {"published": False, "reason": source_events.REASON_SWITCH_INVALID,
                      "message_id": ""}
    assert source_events.schedule_source_deleted(_bare_request(), user_id="u", source_id="m") \
        == {"scheduled": False, "reason": source_events.REASON_SWITCH_INVALID}


# ── ④ 报告面：每一支都答「投没投、为什么、哪条 id」 ──────


class _FakeProducer:
    """假生产者保持**两步顺序**与真件同形：先编码，编码过了才叫作出网。

    只记一个「被调用了」会把「非法字节没上网络」这件事糊掉——那一格恰恰是
    `encode_source_deleted` 住在 `SourceEventProducer.publish` 里面的理由。
    """

    def __init__(self) -> None:
        self.error: Exception | None = None
        self.calls: list[dict] = []
        self.wire: list[Any] = []

    async def publish(self, **kwargs: Any):
        self.calls.append(kwargs)
        message = encode(user_id=kwargs["user_id"], source_id=kwargs["source_id"],
                         source_table=kwargs["source_table"], trace_id=kwargs.get("trace_id", ""))
        if self.error is not None:
            raise self.error
        self.wire.append(message)
        return message

    async def close(self) -> None:
        pass


@pytest.fixture
def enabled(monkeypatch):
    """开开关＋换一个假生产者（网络面由 E2E 那一条真跑，不在这里冒充）。"""
    monkeypatch.setenv(ENV_SOURCE_EVENTS, "1")
    monkeypatch.setenv(ENV_URL, "amqp://fake")
    source_events._producer = None
    fake = _FakeProducer()
    monkeypatch.setattr(source_events, "get_producer", lambda sw: fake)
    yield fake
    source_events._producer = None


async def test_published_report_carries_the_message_id(enabled):
    report = await source_events.publish_source_deleted(user_id="u", source_id="m")
    assert report == {"published": True, "reason": "published",
                      "message_id": encode(user_id="u", source_id="m").message_id}
    assert enabled.calls == [{"user_id": "u", "source_id": "m",
                              "source_table": "memories", "trace_id": ""}]
    assert len(enabled.wire) == 1


async def test_topology_missing_is_its_own_branch(enabled):
    """拓扑没建起来（Go 没先起）必须与「网络坏了」分道：前者是部署顺序错。"""
    from backend.ai.mq.publisher import TopologyMissing

    enabled.error = TopologyMissing("事件面 exchange 'closenai.events' 在 broker 上不存在")
    report = await source_events.publish_source_deleted(user_id="u", source_id="m")
    assert report["published"] is False
    assert report["reason"] == source_events.REASON_TOPOLOGY_MISSING


async def test_contract_error_keeps_its_own_code(enabled):
    """契约错的 reason 不许混进 publish_failed：那条码表要指回具体判据。

    ⚠️ 编码住在 `SourceEventProducer.publish` 里、排在出网之前，所以「被调用」与
    「上了网络」是两件事：这里判后者，`calls` 只证明前者。
    """
    report = await source_events.publish_source_deleted(user_id="", source_id="m")
    assert report["reason"] == source_events.REASON_CONTRACT
    assert report["code"] == REASON_MISSING_USER_ID
    assert enabled.wire == []


async def test_unexpected_error_does_not_escape(enabled):
    enabled.error = RuntimeError("broker 断了")
    report = await source_events.publish_source_deleted(user_id="u", source_id="m")
    assert report["reason"] == source_events.REASON_PUBLISH_FAILED
    assert report["published"] is False


def test_report_reasons_are_a_closed_set():
    """`reason` 取值面封闭：多一个桶＝那条 Loki 查询里出现没人认领的结果。

    正向证人：六支里只有一支是成功，其余五支都是失败——「published 的报告只有一个
    reason」这句口径要正着说一次，否则这条闸只是数了个集合大小。
    """
    allowed = {
        source_events.REASON_SWITCH_OFF, source_events.REASON_SWITCH_INVALID,
        source_events.REASON_CONTRACT, source_events.REASON_TOPOLOGY_MISSING,
        source_events.REASON_PUBLISH_FAILED, source_events.REASON_PUBLISHED,
    }
    assert len(allowed) == 6
    assert {source_events.REASON_PUBLISHED} < allowed


async def test_producer_resets_after_a_failed_publish():
    """坏连接不许缓存：下一次必须重新声明，否则整个进程余下的人生都投不出去。"""
    from backend.ai.mq.config import ProducerSwitch
    from backend.ai.mq.publisher import TopologyMissing
    from backend.services.source_events import SourceEventProducer

    producer = SourceEventProducer(
        ProducerSwitch(enabled=True, url="amqp://fake", max_body_bytes=MAX_BODY)
    )

    class _Boom:
        async def publish(self, _message):
            raise TopologyMissing("channel closed")

    producer._publisher = _Boom()
    with pytest.raises(TopologyMissing):
        await producer.publish(user_id="u", source_id="m", source_table="memories")
    assert producer._publisher is None


async def test_close_producer_clears_the_singleton(enabled):
    """停机要能关掉那条连接：留着它，下一次请求会拿一个已关闭的 channel 去 publish。"""
    source_events._producer = _FakeProducer()
    await source_events.close_producer()
    assert source_events._producer is None


# ── ⑤ 入口面：真路由 ＋ 真 sqlite 会话，判「提交之后」 ────


@pytest.fixture
def app(db):
    """真路由 + 真会话，只换掉两处依赖：会话来自 fixture，归属写死一个用户。"""
    built = FastAPI()
    register_exception_handlers(built)
    built.include_router(router, prefix="/api/v1")

    async def override_db(request: Request):
        # 真 `get_db` 会把会话挂进 request.state，路由层的提交点就找那一格。
        request.state.db_session = db
        yield db

    built.dependency_overrides[get_db] = override_db
    built.dependency_overrides[get_current_user] = lambda: USER_ID
    return built


async def _delete(app, memory_id: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        return await client.delete(f"/api/v1/memories/{memory_id}")


async def _plant(db) -> str:
    db.add(Memory(id=MEMORY_ID, user_id=USER_ID, category="habit",
                  content="每天写日记", status="current", source="user",
                  conviction_score=0.9))
    await db.flush()
    return MEMORY_ID


async def test_delete_endpoint_publishes_only_after_the_commit(app, db, enabled):
    """本批的核心判据（四件套的「执行」那一件）。

    探针是 `session.in_transaction()`：钩子跑的时候它必须已经是 **False**，
    也就是提交确实发生在事件出网之前。为什么不用「记一个顺序数组」：那要 patch
    `session.commit`，而被 patch 的方法正是判据要观察的那一次提交本身——两侧同源＝没判。
    """
    memory_id = await _plant(db)
    seen: dict[str, Any] = {}
    real_publish = enabled.publish

    async def spy(**kwargs):
        seen["in_transaction"] = db.in_transaction()
        # `get()` 排除了软删除行：读到 None 才算「库里那一行真的已经没了」。
        seen["still_readable"] = (await memory_repository.get(db, memory_id, USER_ID)) is not None
        return await real_publish(**kwargs)

    enabled.publish = spy  # type: ignore[method-assign]
    response = await _delete(app, memory_id)
    assert response.status_code == 200, response.text
    assert seen["in_transaction"] is False, "事件投在提交之前 ⇒ Go 读到的是翻转前的自己"
    assert seen["still_readable"] is False, "读到的是删除前的自己 ⇒ 提交发生在出网之后"
    assert [c["source_id"] for c in enabled.calls] == [memory_id]


async def test_delete_endpoint_publishes_exactly_the_contracted_shape(app, db, enabled):
    """正向证人：真走一次 DELETE 之后，出网的那条消息就是契约那四条键＋两个头。"""
    memory_id = await _plant(db)
    assert (await _delete(app, memory_id)).status_code == 200
    assert len(enabled.wire) == 1, enabled.calls
    msg = enabled.wire[0]
    assert msg.routing_key == "source.deleted" and msg.message_type == "source_deleted", msg
    assert msg.exchange == "closenai.events"
    body = json.loads(msg.body)
    assert body == {"schemaVersion": "1", "userId": USER_ID,
                    "sourceTable": "memories", "sourceId": memory_id}
    assert msg.headers[HEADER_USER_ID] == USER_ID


async def test_missing_memory_schedules_nothing(app, db, enabled):
    """404 那一支不许挂钩子：没删成东西就发删除事件＝凭空传播一次不存在的删除。"""
    response = await _delete(app, "does-not-exist")
    assert response.status_code == 404, response.text
    assert response.json()["code"] == ErrorCode.MEMORY_NOT_FOUND
    assert enabled.calls == []


async def test_publish_failure_keeps_response_200(app, db, enabled):
    """提交之后的失败不许改成 500（用户重试删除只会拿到 404，比丢一条事件更难解释）。

    ⚠️ 这一格留下的残余缺口如实登记：没有本地补投表，只有日志与报告面。
    归 T2-05 的重放/积压面。
    """
    from backend.ai.mq.publisher import TopologyMissing

    memory_id = await _plant(db)

    async def boom(**_kwargs):
        raise TopologyMissing("事件面 exchange 在 broker 上不存在")

    enabled.publish = boom  # type: ignore[method-assign]
    response = await _delete(app, memory_id)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["deleted"] is True


async def test_hooks_run_once_per_request():
    """钩子跑完必须清空：重复投递会被 `(message_id, consumer)` 判成重复，
    而那一次重复的第一条可能正好失败在阶梯上——账面就永远停在没翻转的样子。"""
    request = _bare_request()
    marker: list[int] = []

    async def hook():
        marker.append(1)

    source_events.add_post_commit_hook(request, hook)
    source_events.add_post_commit_hook(request, hook)
    assert getattr(request.state, STATE_ATTR) and len(getattr(request.state, STATE_ATTR)) == 2
    await run_post_commit_hooks(request)
    assert marker == [1, 1]
    await run_post_commit_hooks(request)
    assert marker == [1, 1], "第二次还在跑＝钩子没清空"
    assert getattr(request.state, STATE_ATTR) == []


async def test_a_failing_hook_does_not_swallow_the_next_one():
    """一条坏钩子连累另一条＝第二条删除事件静默丢失，而日志里只有第一条的名字。"""
    ran: list[str] = []

    async def bad():
        raise RuntimeError("boom")

    async def good():
        ran.append("good")

    request = _bare_request()
    source_events.add_post_commit_hook(request, bad)
    source_events.add_post_commit_hook(request, good)
    await run_post_commit_hooks(request)
    assert ran == ["good"]
