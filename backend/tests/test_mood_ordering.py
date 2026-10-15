"""M-G（mood 两处 ORDER BY 补 id 次级键）的行为闸与形状闸。

被验对象：`backend/repositories/mood_repository.py` 的
`get_recent`（history 用，倒序＋LIMIT）与 `count_by_day`（trend 用，升序＋「当日取首个」）。

口径（与 D-A 的 `test_growth_ordering.py` 同型，缺一条就有空转的格子）：
- 根因是 PG 的 `now()` 是**事务级**时间戳，而 `models/mood.py:28-30` 的 `created_at`
  只有 `server_default=func.now()`（没有 Python 侧 default）⇒ 同事务写的多行逐位相同。
  mood 侧**没有**改写侧：`(user_id, checkin_date)` 唯一约束让正常签到一天只落一行，
  同刻行只来自裸 SQL/历史回填；本批只按点名范围补读侧次级键。
- 期望序由**种子的 id** 决定，而不是交给钟或数据库返回序。同刻组一律**三行**，
  且主键的升序与降序排列都既不等于插入序、也不等于插入序的逆序——两行做不到这一点：
  数据库没有次级键时只可能给这两支之一，必有一支与期望序重合，那格就退化成空转
  （M-G 的第一版正是这样，2026-10-15 实测撤键不翻字节才改成三行）。
- 形状闸只清点**点名的两处**；`get_history`/`list_between` 的同型缺陷已按
  「发现的缺陷不顺手修」登记在 tasks.md（M-G 段），不在本文件射程内。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.core.time_utils import utc_now
from backend.models.mood import MoodCheckin
from backend.repositories.mood_repository import mood_repository

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT / "backend" / "repositories" / "mood_repository.py"

SAME_INSTANT = utc_now().replace(microsecond=0) - timedelta(days=1)


async def _plant(db, user_id: str, rows: list[tuple[str, str]]) -> None:
    """按给定顺序插入同刻若干行——rows 是 [(id, emoji)]，id 序就是「字典序 vs 插入序」的把手。

    `checkin_date` 一律留 NULL：唯一约束 `(user_id, checkin_date)` 对 NULL 不参与冲突，
    同刻多行才种得进去（生产里这种行也来自裸 SQL/回填，同形）。
    """
    for rid, emoji in rows:
        db.add(
            MoodCheckin(
                id=rid, user_id=user_id, mood_emoji=emoji, created_at=SAME_INSTANT,
            )
        )
    await db.flush()


async def test_get_recent_breaks_same_instant_ties_by_id_desc(db, make_user):
    """倒序那处：同刻三行的返回序与「LIMIT 1 留下谁」都由 id 决定。

    插入序 [tie-a, tie-z, tie-m]，期望序 [tie-z, tie-m, tie-a]（id DESC）——它与插入序
    和逆插入序**都不重合**，所以无论数据库在没有次级键时给这两支里的哪一支，本条都会红。
    夹具 `mood-h-tie` 是同一判据在 PG 字节面上的另一证人。
    """
    await make_user("u-mood-desc")
    await _plant(db, "u-mood-desc",
                 [("tie-a", "😊"), ("tie-z", "😢"), ("tie-m", "😌")])

    rows = await mood_repository.get_recent(db, "u-mood-desc", limit=7)
    assert [r.id for r in rows] == ["tie-z", "tie-m", "tie-a"], (
        "同刻三行的倒序仍由数据库返回序决定：次级键没生效或方向写反了"
    )

    one = await mood_repository.get_recent(db, "u-mood-desc", limit=1)
    assert [r.id for r in one] == ["tie-z"], (
        "DESC LIMIT 1 给的不是 id 最大那条：同刻时「最近一条」不确定，"
        "而 personality/mood/journey 有 9 处按 limit=1 取最近一条"
    )


async def test_count_by_day_breaks_same_instant_ties_by_id_asc(db, make_user):
    """升序那处：同一本地日的同刻行，**返回序列的第一行**必须是 id 小的那条。

    `count_by_day` 本身不去重（一天一行是它下游的事），它给的是**逐行**的
    (本地日, emoji)；去重在 `get_trend` 的 `if key not in day_map`（mood_service.py:330-333）
    ——那里取的是升序首行。所以本格判的是「首行是谁」：
    插入序 [tie-z(😊), tie-a(😢), tie-m(😌)]（id 最小的那行放在**中间**插），
    期望序 [tie-a, tie-m, tie-z]（id ASC）⇒ 当日代表 emoji 是 😢，而插入序给 😊、
    逆插入序给 😌，两支都不等于它。代表 emoji 换了就换 MOOD_MAP 的值，
    趋势四档阈值（avg>=4 / >=3）可能整体翻面。
    """
    await make_user("u-mood-asc")
    await _plant(db, "u-mood-asc",
                 [("tie-z", "😊"), ("tie-a", "😢"), ("tie-m", "😌")])

    rows = await mood_repository.count_by_day(db, "u-mood-asc", days=14, tz="UTC")
    assert len(rows) == 3, f"三行同刻应当都返回（去重在调用方）：{rows}"
    assert rows[0][1] == "😢", (
        f"当日首行不是 id 小的那条（实得 {rows[0]}）："
        "count_by_day 的次级键没生效或方向与 Go 侧不一致"
    )
    assert [emoji for _day, emoji in rows] == ["😢", "😌", "😊"]


def _order_by_args(src: str) -> list[str]:
    """逐个取出 `.order_by(...)` 的实参（括号配平）。

    不能按「第一个右括号」截——实参里就有 `.desc()`，那种切法会把每条都读成缺次级键。
    """
    out: list[str] = []
    for match in re.finditer(r"\.order_by\(", src):
        i, depth = match.end(), 1
        while depth:
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
            i += 1
        out.append(" ".join(src[match.end():i - 1].split()))
    return out


def _the_only_order_by(func: str) -> str:
    src = REPO.read_text(encoding="utf-8")
    body = re.search(
        rf"async def {func}\(.*?(?=\n    async def |\nmood_repository = )", src, re.S
    )
    assert body, f"{func} 找不到了，本闸的口径要跟着改"
    calls = _order_by_args(body.group(0))
    assert len(calls) == 1, f"{func} 里有 {len(calls)} 条 ORDER BY，本闸只认一条"
    return calls[0]


def test_the_two_named_mood_queries_carry_a_secondary_key_same_direction():
    """点名的两处：主键是 created_at、次级键是 id，且**同方向**。

    方向不一致（一处 DESC 一处 ASC）时两侧读到同一批行也会给出不同的当日首行，
    那就不再是格式差异而是字节差异——Go 侧的同形由
    `test_n8_batch5_contract.py::test_batch5_sql_shapes_are_one_to_one` 守。
    """
    for func, direction in (("get_recent", "desc"), ("count_by_day", "asc")):
        order = _the_only_order_by(func)
        assert "MoodCheckin.created_at." in order, f"{func} 主键不是 created_at：{order}"
        assert f"MoodCheckin.created_at.{direction}()" in order, (
            f"{func} 主键方向变了（期望 {direction}）：{order}"
        )
        assert f"MoodCheckin.id.{direction}()" in order, (
            f"{func} 缺 id 次级键或方向与主键不一致：{order}"
        )
