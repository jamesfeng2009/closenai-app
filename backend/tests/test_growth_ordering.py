"""D-A（created_at 根治）的三条闸：写侧取值、读侧同刻定序、六处形状齐全。

被验对象：`backend/models/growth.py` 的 `created_at` 默认值、
`backend/repositories/growth_repository.py` 的六条 ORDER BY。

口径（缺一条就有空转的格子）：
- 根因是 **PG 的 `now()` 是事务级时间戳**：一个事务写 N 行 ⇒ 整批 created_at 逐位相同
  （`cron_service.py:962` 按用户一次 commit，:1011 在 memories 循环里写 N 行）。
  sqlite 更严：`CURRENT_TIMESTAMP` 只到秒 ⇒ 同一测里连写的两条必然同瞬。
  所以「同事务两行互异」这一格在 sqlite 上就是写侧回退的现成证人，不需要真 PG。
- 第 3 条数的是**齐全**而不是存在：只给 `list_by_type` 加次级键，另外五处照旧未定义，
  而 growth 七条要复刻的正是这六处。
- 次级键是 `id`（uuid4）⇒ 给的是**字典序不是写入序**。这里刻意把种子的插入序排成
  与字典序相反，否则「撤掉次级键」和「插入序恰好等于期望序」会同时绿灯，测了个空转。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from backend.models.growth import GrowthRecord
from backend.repositories.growth_repository import growth_repository

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT / "backend" / "repositories" / "growth_repository.py"

SAME_INSTANT = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)


async def _plant(db, user_id: str, ids: list[str], at: datetime) -> None:
    """按给定顺序插入同刻若干行——id 的顺序就是「字典序 vs 插入序」的把手。"""
    for rid in ids:
        db.add(
            GrowthRecord(
                id=rid, user_id=user_id, record_type="reflection",
                content=rid, created_at=at, follow_up_sent=False,
            )
        )
    await db.flush()


async def test_two_rows_in_one_transaction_get_distinct_instants(db, make_user):
    """写侧根治的证据：同一事务连写两条，created_at 必须互不相同。

    撤掉 `default=utc_now` 就回到 `server_default=func.now()`：sqlite 给秒级、
    PG 给事务级，两条逐位相同 ⇒ 这条先红。它是「最新一条」「留 5 条丢最旧」
    重新有意义的唯一前提。
    """
    await make_user("u-write")
    first = await growth_repository.create(db, "u-write", "daily_insight", "第一条")
    second = await growth_repository.create(db, "u-write", "daily_insight", "第二条")
    await db.flush()
    assert first.created_at is not None and second.created_at is not None
    assert first.created_at != second.created_at, (
        f"同一事务写两行拿到同一个瞬间（{first.created_at}）："
        "写侧又交给 DB 的 now() 了，同刻行会由查询计划决定顺序"
    )


async def test_same_instant_rows_order_by_id_desc(db, make_user):
    """读侧倒序：同刻两行的返回序由 `id` 决定，而不是由插入序或查询计划决定。

    插入序 [..-a, ..-z]（a 先），期望序 [..-z, ..-a]（id DESC）⇒ 撤掉次级键时
    sqlite 退回插入序而变红；夹具的 refl-tie 两格是同一判据在 PG 上的另一证人。
    """
    await make_user("u-desc")
    await _plant(db, "u-desc", ["tie-a", "tie-z"], SAME_INSTANT)

    rows = await growth_repository.list_by_type(db, "u-desc", "reflection")
    assert [r.id for r in rows] == ["tie-z", "tie-a"]

    latest = await growth_repository.get_latest(db, "u-desc", "reflection")
    assert latest.id == "tie-z", "DESC LIMIT 1 给的是 id 小那条：同刻时「最新一条」不确定"


async def test_same_instant_rows_order_by_id_asc(db, make_user):
    """读侧升序（跟进扫描那条）方向也必须跟着翻，而且同样不能等于插入序。

    插入序 [..-z, ..-a]，期望序 [..-a, ..-z]（id ASC）。次级键写成与 created_at
    反向时这一条红、上面那条绿——两条合起来才排掉「顺手写反方向」。
    """
    await make_user("u-asc")
    await _plant(db, "u-asc", ["tie-z", "tie-a"], SAME_INSTANT)

    rows = await growth_repository.list_pending_follow_ups(
        db, ["reflection"],
        datetime(2026, 10, 1, tzinfo=timezone.utc),
        datetime(2026, 10, 20, tzinfo=timezone.utc),
    )
    assert [r.id for r in rows if r.user_id == "u-asc"] == ["tie-a", "tie-z"]


def _order_by_args(src: str) -> list[str]:
    """逐个取出 `.order_by(...)` 的实参——不能按「第一个右括号」截，
    因为实参里就有 `.desc()`，那种正则只会把每条都读成缺次级键。"""
    out: list[str] = []
    for match in re.finditer(r"\.order_by\(", src):
        i, depth = match.end(), 1
        while depth:
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
            i += 1
        out.append(src[match.end():i - 1])
    return out


def test_every_growth_ordering_carries_a_secondary_key_same_direction():
    """六条 ORDER BY **全部**带次级键、且方向与 created_at 一致。

    按源码形状数：本批 growth 七条要在 Go 侧复刻的就是这六条，少一处就意味着
    那一条端点的返回序仍由查询计划决定——两侧逐字节比较在该处不成立。
    """
    calls = _order_by_args(REPO.read_text(encoding="utf-8"))
    assert len(calls) == 6, f"ORDER BY 变成 {len(calls)} 处，本闸的清点口径要跟着改"
    for order in calls:
        flat = " ".join(order.split())
        assert "GrowthRecord.created_at." in flat, f"主键不是 created_at：{flat}"
        assert "GrowthRecord.id." in flat, f"缺 id 次级键：{flat}"
        primary = "desc" if ".created_at.desc()" in flat else "asc"
        assert f".id.{primary}()" in flat, f"次级键方向与主键不一致：{flat}"
    assert sum(".created_at.asc()" in c for c in calls) == 1, (
        "升序那处（跟进扫描）必须只有一条：多一条就说明有人把方向写反了"
    )
