"""T5-03 幂等唯一约束的验收闸（chat_messages 主动消息 / journey_events 日序）。

Baseline 不变量 9 说「同一件事只触达一次」，此前只靠行锁 + 当日计数，表上没有兜底。
本文件拍四层，缺一层这条不变量就是空的：

1. **形状对账**：模型里的部分唯一索引与迁移件 `c3e5a7b9d1f2` 的 DDL 同名、同谓词、
   同列。两边各写一遍迟早漂移，漂了才在生产撞约束。
2. **幂等写入**：同键两次只 1 行、第二次 `created=False` 且不抛 500；非 proactive
   同键照样两行（证明索引是**部分**的，不是全表唯一——全表唯一等于把对话功能打死）。
3. **索引范围**：journey 的 `commitment_follow_up` 与 `skipped` 排除项各有证人。
   排除写错的方向是「合法写入变 500」，所以两条都必须是正向计数，不是"没报错"。
4. **回填脚本**：dry-run 默认不写库、`--apply` 只给每个本地日的最早一行补键、
   重跑改 0 行（可重跑性）、键按**用户本地日**而不是 UTC 日。

sqlite 单写者会把两次 INSERT 串起来，所以「同键两次」在这里等价于并发双写：
唯一索引是在语句执行时判定的，与交错顺序无关。真并发（PG）取证走 CI 的
`CLOSEN_REQUIRE_PG=1` 一轮，本文件不假装覆盖它。
"""
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from backend.models.chat import _PROACTIVE_KEY_PREDICATE, ChatMessage
from backend.models.journey import _USER_KIND_DAY_PREDICATE, JourneyEvent
from backend.repositories.chat_repository import chat_repository
from backend.repositories.journey_repository import journey_repository

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    ROOT / "backend" / "alembic" / "versions"
    / "c3e5a7b9d1f2_add_idempotency_constraints.py"
)


def _sql(text_: str) -> str:
    """压平空白后再比对：迁移里是带缩进的多行 SQL，模型里是一行。"""
    return " ".join(text_.split())


async def _rows(db, model) -> list:
    result = await db.execute(select(model))
    return list(result.scalars().all())


# ── 1. 形状对账：迁移 DDL == 模型声明 ──

def test_chat_index_ddl_matches_model():
    migration = _sql(MIGRATION_PATH.read_text(encoding="utf-8"))
    name = "uq_chat_messages_proactive_key"
    predicate = _sql(_PROACTIVE_KEY_PREDICATE.text)
    expected = (
        f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON chat_messages (idempotency_key) "
        f"WHERE {predicate}"
    )
    assert expected in migration, f"迁移里的 chat 索引与模型不同：\n{expected}"

    declared = ChatMessage.__table__.indexes
    index = next(i for i in declared if i.name == name)
    assert index.unique is True
    assert _sql(index.dialect_kwargs["postgresql_where"].text) == predicate
    assert _sql(index.dialect_kwargs["sqlite_where"].text) == predicate
    assert [c.name for c in index.columns] == ["idempotency_key"]


def test_journey_index_ddl_matches_model():
    migration = _sql(MIGRATION_PATH.read_text(encoding="utf-8"))
    name = "uq_journey_events_user_kind_day"
    predicate = _sql(_USER_KIND_DAY_PREDICATE.text)
    expected = (
        f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON journey_events "
        f"(user_id, event_kind, day_no) WHERE {predicate}"
    )
    assert expected in migration, f"迁移里的 journey 索引与模型不同：\n{expected}"

    index = next(i for i in JourneyEvent.__table__.indexes if i.name == name)
    assert index.unique is True
    assert _sql(index.dialect_kwargs["postgresql_where"].text) == predicate
    assert _sql(index.dialect_kwargs["sqlite_where"].text) == predicate
    assert [c.name for c in index.columns] == ["user_id", "event_kind", "day_no"]


def test_journey_dedupe_statement_is_portable():
    """去重 UPDATE 的 **SQL 字面量**不能用只有 PG 有的窗口函数/array_agg。

    本仓的本地开发库与迁移往返测试都跑 SQLite，首版写了 `array_agg(...)[1]` 直接
    报语法错。只取 `upgrade()` 里 `op.execute()` 的字符串常量：函数体内的注释也
    提到 array_agg，那是「为什么不用」的理由陈述，扫散文会把解释当成违规。
    """
    import ast
    import inspect

    spec = importlib.util.spec_from_file_location("w5_idem_migration", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tree = ast.parse(inspect.getsource(module.upgrade))
    sql = " ".join(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    assert "array_agg" not in sql.lower()
    assert "row_number()" not in sql.lower()
    assert "exists ( select 1 from journey_events as earlier" in _sql(sql).lower()
    # 正向证人：确实扫到了 SQL，不是空串通过
    assert "create unique index" in _sql(sql).lower()


# ── 2. chat 幂等写入 ──

KEY = "proactive:u1:2026-10-18:0"


async def test_same_key_twice_writes_one_row(db, make_user):
    await make_user("u1")
    first, created1 = await chat_repository.create_idempotent(
        db, "u1", "assistant", "早安", "proactive", KEY, ai_generated=True
    )
    second, created2 = await chat_repository.create_idempotent(
        db, "u1", "assistant", "早安（重试）", "proactive", KEY, ai_generated=True
    )
    assert created1 is True and created2 is False
    assert second.id == first.id, "重放必须返回既有行，而不是静默丢弃后调用方无从核对"
    assert second.content == "早安"
    rows = await _rows(db, ChatMessage)
    assert len(rows) == 1, f"同键两写后应只剩 1 行，实得 {len(rows)}"


async def test_different_keys_write_both(db, make_user):
    """正向证人：唯一索引没有把正常的跨日写入也吃掉。"""
    await make_user("u1")
    for day in ("2026-10-18", "2026-10-19"):
        _, created = await chat_repository.create_idempotent(
            db, "u1", "assistant", f"{day} 早安", "proactive",
            f"proactive:u1:{day}:0", ai_generated=True,
        )
        assert created is True
    n = await db.scalar(select(func.count(ChatMessage.id)))
    assert n == 2


async def test_index_is_partial_non_proactive_same_key_allowed(db, make_user):
    """索引谓词只圈 proactive：回复消息复用同一串键不能撞约束。

    写成全表唯一的话，这条立刻红——正是这个变异靶（见 mutate 脚本）。
    """
    await make_user("u1")
    for content in ("a", "b"):
        await chat_repository.create(
            db, "u1", "user", content, "reply", idempotency_key=KEY
        )
    n = await db.scalar(
        select(func.count(ChatMessage.id)).where(ChatMessage.message_type == "reply")
    )
    assert n == 2


async def test_replay_of_backfilled_legacy_row_adds_nothing(db, make_user):
    """验收口径「重放旧消息不新增」：历史行补键后，同键再写命中既有行。"""
    await make_user("u1")
    legacy = await chat_repository.create(
        db, "u1", "assistant", "上周的早安", "proactive", idempotency_key=KEY
    )
    row, created = await chat_repository.create_idempotent(
        db, "u1", "assistant", "重放", "proactive", KEY
    )
    assert created is False
    assert row.id == legacy.id
    n = await db.scalar(select(func.count(ChatMessage.id)))
    assert n == 1


# ── 3. journey 索引范围 ──

async def test_journey_same_triple_is_deduped(db, make_user):
    await make_user("u1")
    t1 = datetime(2026, 10, 18, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 10, 19, 1, tzinfo=timezone.utc)
    first = await journey_repository.schedule(db, "u1", "day2_first_outreach", 2, t1)
    second = await journey_repository.schedule(db, "u1", "day2_first_outreach", 2, t2)
    assert second.id == first.id, "同 (user,kind,day) 预写必须回到既有行"
    assert second.scheduled_at == t2, "幂等分支仍要刷新计划时刻（既有口径）"
    n = await db.scalar(select(func.count(JourneyEvent.id)))
    assert n == 1


async def test_commitment_follow_up_allows_same_day_rows(db, make_user):
    """排除项的正向证人：§5.18 同一日回访两条不同承诺是合法写入。

    若索引漏了 `event_kind <> 'commitment_follow_up'`，第二次 schedule 直接撞约束。
    """
    await make_user("u1")
    moment = datetime(2026, 10, 18, 1, tzinfo=timezone.utc)
    for idx in range(2):
        await journey_repository.schedule(
            db, "u1", "commitment_follow_up", 3, moment,
            payload={"memory_id": f"m{idx}"}, dedupe=False,
        )
    n = await db.scalar(
        select(func.count(JourneyEvent.id)).where(
            JourneyEvent.event_kind == "commitment_follow_up"
        )
    )
    assert n == 2, f"同日两条不同承诺回访应都在，实得 {n}"


async def test_index_covers_two_pending_rows_but_not_a_skipped_one(db, make_user):
    """索引范围两半都拍：pending×2 必撞（否则索引等于没建），skipped+pending 可共存。

    排除 `status='skipped'` 是因为迁移把重复组的较晚行置 skipped 而不删行——不排
    除它们，索引根本建不起来。写这条行用裸 INSERT 而不是 `schedule()`：Go 侧
    onboarding prefill（`go-core/internal/httpapi/server.go` 的批 4 六行）也是裸写，
    它才是这个索引真正要兜的第二个写入方；Python 的 `schedule()` 会先 `get_by_kind`
    命中 skipped 行并复用，走不到插入分支（该口径后果已在任务板结案行登记）。
    """
    await make_user("u1")
    moment = datetime(2026, 10, 18, 1, tzinfo=timezone.utc)
    first = await journey_repository.schedule(db, "u1", "day5_stat", 5, moment)
    first.status = "skipped"
    await db.flush()

    second = JourneyEvent(id="after-skip", user_id="u1", event_kind="day5_stat",
                          day_no=5, status="pending", scheduled_at=moment)
    db.add(second)
    await db.flush()  # 不抛 ⇒ skipped 确实不在索引范围内

    third = JourneyEvent(id="another-pending", user_id="u1", event_kind="day5_stat",
                         day_no=5, status="pending", scheduled_at=moment)
    db.add(third)
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


# ── 4. 回填脚本 ──

def _load_backfill():
    spec = importlib.util.spec_from_file_location(
        "backfill_idempotency", ROOT / "scripts" / "backfill_idempotency.py"
    )
    module = importlib.util.module_from_spec(spec)
    # 必须先挂进 sys.modules 再 exec：脚本里有 @dataclass，`from __future__ import
    # annotations` 让字段类型变成字符串，dataclasses 回查 sys.modules[__name__] 解析
    # 前向引用——没注册就直接 AttributeError。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def backfill():
    return _load_backfill()


async def test_dry_run_writes_nothing(db, make_user, backfill):
    await make_user("u1", timezone="UTC")
    await db.flush()
    day = datetime(2026, 10, 18, 9, tzinfo=timezone.utc)
    db.add(ChatMessage(id="legacy-1", user_id="u1", role="assistant", content="x",
                       message_type="proactive", created_at=day))
    await db.flush()

    plan = await backfill.plan_chat_keys(db)
    assert len(plan.set_key) == 1, "dry-run 至少得算出会改 1 行，否则这条闸在扫空集"
    row = await db.get(ChatMessage, "legacy-1")
    assert row.idempotency_key is None, "默认路径不得写库"


async def test_apply_keys_earliest_row_per_local_day(db, make_user, backfill):
    await make_user("u1", timezone="UTC")
    db.add_all([
        ChatMessage(id="early", user_id="u1", role="assistant", content="a",
                    message_type="proactive",
                    created_at=datetime(2026, 10, 18, 1, tzinfo=timezone.utc)),
        ChatMessage(id="late", user_id="u1", role="assistant", content="b",
                    message_type="proactive",
                    created_at=datetime(2026, 10, 18, 20, tzinfo=timezone.utc)),
    ])
    await db.flush()

    chat = await backfill.plan_chat_keys(db)
    await backfill.apply(db, chat, await backfill.plan_journey_dupes(db))

    early = await db.get(ChatMessage, "early")
    late = await db.get(ChatMessage, "late")
    assert early.idempotency_key == "proactive:u1:2026-10-18:0"
    assert late.idempotency_key is None, "同日本就多出的行不补键（补了两行同键必撞索引）"
    assert len(chat.left_null) == 1


async def test_backfill_is_rerunnable(db, make_user, backfill):
    await make_user("u1", timezone="UTC")
    db.add(ChatMessage(id="early", user_id="u1", role="assistant", content="a",
                       message_type="proactive",
                       created_at=datetime(2026, 10, 18, 1, tzinfo=timezone.utc)))
    await db.flush()
    first = await backfill.plan_chat_keys(db)
    await backfill.apply(db, first, await backfill.plan_journey_dupes(db))
    second = await backfill.plan_chat_keys(db)
    assert second.set_key == [], "重跑必须算出 0 行变更，否则脚本不是幂等的"


async def test_key_uses_user_local_day_not_utc_day(db, make_user, backfill):
    """Asia/Tokyo（+9）：两个 UTC 同一日的时刻可能落在用户的不同日历日。

    键若按 UTC 日生成，本地 9:00 的主动消息与回填出来的键就不是同一个串，
    幂等只在同一天内成立、跨零点全部失效。
    """
    await make_user("u1", timezone="Asia/Tokyo")
    db.add_all([
        ChatMessage(id="d17", user_id="u1", role="assistant", content="a",
                    message_type="proactive",
                    created_at=datetime(2026, 10, 17, 10, tzinfo=timezone.utc)),
        ChatMessage(id="d18", user_id="u1", role="assistant", content="b",
                    message_type="proactive",
                    created_at=datetime(2026, 10, 17, 20, tzinfo=timezone.utc)),
    ])
    await db.flush()
    chat = await backfill.plan_chat_keys(db)
    assert len(chat.set_key) == 2, "本地是两个日历日，两行都要各自拿到键"
    keys = {key for _, key in chat.set_key}
    assert keys == {"proactive:u1:2026-10-17:0", "proactive:u1:2026-10-18:0"}


async def test_backfill_skips_later_journey_dupes(db, make_user, backfill):
    """重复组「除最早一条外置 skipped」的判据。

    索引已经在表上，正常的 INSERT 根本造不出重复行，所以这里先 DROP INDEX 模拟
    迁移前的库状态——回填脚本要治的正是那种库。
    """
    await make_user("u1", timezone="UTC")
    await db.execute(text("DROP INDEX IF EXISTS uq_journey_events_user_kind_day"))
    moment = datetime(2026, 10, 18, 1, tzinfo=timezone.utc)
    base = await journey_repository.schedule(db, "u1", "day3_weak_assert", 3, moment)
    # schedule() 不写 created_at（靠 server_default=now()，取的是**真实此刻**），
    # 这里显式钉到 00:30，否则"最早一条"是谁取决于这条用例几点跑。
    base.created_at = datetime(2026, 10, 18, 0, 30, tzinfo=timezone.utc)
    db.add(JourneyEvent(id="dupe", user_id="u1", event_kind="day3_weak_assert",
                        day_no=3, status="pending", scheduled_at=moment,
                        created_at=datetime(2026, 10, 18, 2, tzinfo=timezone.utc)))
    db.add(JourneyEvent(id="follow-up-dupe", user_id="u1",
                        event_kind="commitment_follow_up", day_no=3, status="pending",
                        scheduled_at=moment,
                        created_at=datetime(2026, 10, 18, 3, tzinfo=timezone.utc)))
    await db.flush()

    plan = await backfill.plan_journey_dupes(db)
    assert plan.skip == ["dupe"], f"重复组应把较晚那条置 skipped，实得 {plan.skip}"
    await backfill.apply(db, await backfill.plan_chat_keys(db), plan)
    assert (await db.get(JourneyEvent, "dupe")).status == "skipped"
    assert base.status == "pending", "最早一条必须原样保留"
    assert (await db.get(JourneyEvent, "follow-up-dupe")).status == "pending", (
        "承诺回访同日多条是合法写入，不能当重复清掉"
    )


async def test_backfill_reports_and_stays_dry_by_default(db, make_user, backfill, capsys):
    await make_user("u1", timezone="UTC")
    db.add(ChatMessage(id="legacy-1", user_id="u1", role="assistant", content="a",
                       message_type="proactive",
                       created_at=datetime(2026, 10, 18, 1, tzinfo=timezone.utc)))
    await db.flush()
    chat = await backfill.plan_chat_keys(db)
    backfill.report(chat, await backfill.plan_journey_dupes(db), applied=False)
    out = capsys.readouterr().out
    assert "dry-run" in out, "默认口径必须在输出里自证"
    assert "将补键 1 行" in out
    row = await db.get(ChatMessage, "legacy-1")
    assert row.idempotency_key is None
