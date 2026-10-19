"""T5-05 · 「设置关闭 → 定时任务触发 → 不发送」的端到端闸（Baseline §813）。

为什么单独一批：T5-02 拍静默窗口本身、T5-03 拍幂等索引本身，而 Baseline 要求的是
**一条从设置到投递的链**——用户在界面上关掉的每一样东西，都要能在 cron 那一轮里
被证明「真的没发」。这类闸最容易造假的地方是"门写在了根本不会执行到的分支上"，
所以四种情形全部走 `dispatch_daily_messages`（含到点筛选），不走内部私有函数。

三红一绿：
1. 免打扰窗口内 ⇒ `skipped_quiet`，零 `chat_messages`、零 LLM 调用、零推送；
2. `push_frequency="off"` ⇒ `skipped_off`，同上（该档的用户文案就是「不再主动发消息」）；
3. `push_enabled=False` ⇒ **零推送**，消息仍落库——这一档刻意与 1/2 不同，
   判据与理由写在下面的用例里；
4. 对照组全部放开 ⇒ 1 条消息 + 1 次推送。缺了这条，前三条用「永远不发」也能绿。

第 3 条的形状要说清（这是本批登记任务板的偏离）：`push_enabled` 列**默认 False**，
把它也做成「不生成」会把从未碰过这个开关的存量用户整体静音，那是一次没有人签字的
产品改判；所以它拦在投递那一步（七处发送门同口径），而 off／静默是用户的显式选择，
拦在写库与配额之前。两种关闭的**可观测**都是「用户没有被打扰」。
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from backend.config import settings
from backend.models.chat import ChatMessage
from backend.repositories import chat_repository, push_token_repository
from backend.services import cron_service as cron_module
from backend.services.cron_service import cron_service
from backend.services.message_generator import message_generator


class PushSpy:
    """替换 cron_service 里的 push_service：记录调用，并保证不会被真发出去。"""

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    async def send_to_user(self, db, user_id, title, body, data=None):
        self.sent.append((user_id, title))
        return {"sent": 1, "failed": 0}


@pytest.fixture
def push_spy(monkeypatch):
    spy = PushSpy()
    monkeypatch.setattr(cron_module, "push_service", spy)
    return spy


@pytest.fixture
def due_now(monkeypatch):
    """把用户变成「本轮到点」：抖动归零，push_time 取用户本地此刻的小时。

    `dispatch_daily_messages` 的入册条件是 `now.hour == target // 60`，而 target 里含
    每用户确定性抖动——不抹掉抖动，用例会在某些小时里根本没有 due 用户，
    于是「三红」全部因为空集而假绿。
    """
    monkeypatch.setattr(settings, "qstash_token", "", raising=False)
    monkeypatch.setattr(settings, "backend_base_url", "", raising=False)
    monkeypatch.setattr(cron_service, "_jitter_minutes", lambda user, day, hour=None: 0)
    return datetime.now(ZoneInfo("UTC")).strftime("%H:00")


@pytest.fixture
def generator(monkeypatch):
    """生成器替换成计数器：被叫到就 +1 并返回一句话；窗口内被叫到即判据失败。"""
    calls: list[str] = []

    async def fake_generate(db, user_id, language):
        calls.append(user_id)
        return "早安，今天想聊点什么？"

    monkeypatch.setattr(message_generator, "generate_proactive_message", fake_generate)
    return calls


def _quiet_window_around_now(tz: str = "UTC", inside: bool = True):
    now = datetime.now(ZoneInfo(tz))
    if inside:
        return (now - timedelta(hours=1)).time(), (now + timedelta(hours=1)).time()
    return (now + timedelta(hours=2)).time(), (now + timedelta(hours=3)).time()


async def _rows(db, user_id: str) -> int:
    return int(await db.scalar(
        select(func.count(ChatMessage.id)).where(ChatMessage.user_id == user_id)
    ) or 0)


async def test_quiet_window_delivers_nothing(db, make_user, due_now, generator, push_spy):
    start, end = _quiet_window_around_now(inside=True)
    await make_user("u-quiet", ai_consent=True, timezone="UTC", push_time=due_now,
                    push_enabled=True, push_frequency="daily",
                    quiet_start=start, quiet_end=end)

    result = await cron_service.dispatch_daily_messages(db, "UTC")

    assert result["skipped_quiet"] == 1, f"静默门没命中：{result}"
    assert result["success"] == 0
    assert await _rows(db, "u-quiet") == 0, "静默窗口内不该有新 chat_messages（proactive）"
    assert generator == [], "静默窗口内不该调用 LLM 生成（成本与配额都不该被吃掉）"
    assert push_spy.sent == []


async def test_frequency_off_delivers_nothing(db, make_user, due_now, generator, push_spy):
    start, end = _quiet_window_around_now(inside=False)
    await make_user("u-off", ai_consent=True, timezone="UTC", push_time=due_now,
                    push_enabled=True, push_frequency="off",
                    quiet_start=start, quiet_end=end)

    result = await cron_service.dispatch_daily_messages(db, "UTC")

    assert result["skipped_off"] == 1, f"频率 off 的门没命中：{result}"
    assert await _rows(db, "u-off") == 0, (
        "off 档的用户文案是「不再主动发消息」，消息仍每天落库就是虚假承诺"
    )
    assert generator == []
    assert push_spy.sent == []


async def test_push_enabled_off_sends_no_push_but_keeps_message(
    db, make_user, due_now, generator, push_spy
):
    """这一档的「不发送」＝不投递，而不是不生成。

    `push_enabled` 列默认 False（models/user.py:33），把它做成「不生成」等于把从未
    打开过这个开关的全部存量用户整体静音——那需要产品签字，不是这条闸能顺带改的口径。
    所以这里断言的是两件事：零推送（用户没被打扰），且消息仍落库（钉死这个口径，
    防止后来人"顺手统一"成不生成，也防止这条门被删掉后没人发现）。
    """
    start, end = _quiet_window_around_now(inside=False)
    await make_user("u-nopush", ai_consent=True, timezone="UTC", push_time=due_now,
                    push_enabled=False, push_frequency="daily",
                    quiet_start=start, quiet_end=end)

    result = await cron_service.dispatch_daily_messages(db, "UTC")

    assert push_spy.sent == [], "push_enabled=False 仍推送＝开关是假的"
    assert result["success"] == 1
    assert await _rows(db, "u-nopush") == 1
    assert generator == ["u-nopush"]


async def test_all_on_delivers_one_message_and_one_push(
    db, make_user, due_now, generator, push_spy
):
    """对照组（那条「绿」）：三项全放开 ⇒ 一条消息 + 一次推送。

    没有这条，上面三条在「门写错方向」「due 筛选为空」「生成器被打空」三种坏状态下
    都能一起绿。这里还顺带证明 `dispatch_daily_messages` 真的把用户选进来了。
    """
    start, end = _quiet_window_around_now(inside=False)
    await make_user("u-on", ai_consent=True, timezone="UTC", push_time=due_now,
                    push_enabled=True, push_frequency="daily",
                    quiet_start=start, quiet_end=end)

    result = await cron_service.dispatch_daily_messages(db, "UTC")

    assert result["success"] == 1, f"对照组没投递：{result}"
    assert await _rows(db, "u-on") == 1
    assert generator == ["u-on"]
    assert [uid for uid, _ in push_spy.sent] == ["u-on"]


async def test_push_gate_blocks_device_lookup_in_quiet_window(db, make_user, monkeypatch):
    """投递面兜底（T5-02 的第 4 层，收进这批一起打红）：静默窗口内连设备都不该查。

    剧本、里程碑、关心、回访等九处投递点都过 `push_service.send_to_user`，
    门放在那里才谈得上「关掉 → 不发送」；这条用 spy 让「真的去查了设备」直接炸。
    """
    from backend.services.push import push_service

    start, end = _quiet_window_around_now(inside=True)
    await make_user("u-push", timezone="UTC", quiet_start=start, quiet_end=end)

    async def spy_list(db2, user_id):
        raise AssertionError("免打扰窗口内不该去查设备 token")

    monkeypatch.setattr(push_token_repository, "list_for_user", spy_list)
    out = await push_service.send_to_user(db, "u-push", "标题", "正文", {"type": "journey"})
    assert out == {"sent": 0, "failed": 0}
