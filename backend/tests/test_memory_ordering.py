"""N8 批 6（memories 列表读补 `id` 次级键）的行为闸与形状闸。

被验对象：`backend/repositories/memory_repository.py:123` 的 `list()`
（`GET /api/v1/memories` 的唯一一条读；详情那条 `get()` 没有 ORDER BY，不在本文件射程）。

口径（与 D-A 的 `test_growth_ordering.py`、M-G 的 `test_mood_ordering.py` 同型）：
- 根因同一条：`models/memory.py:56-58` 的 `created_at` 只有 `server_default=func.now()`
  （没有 Python 侧 `default=utc_now`），而 PG 的 `now()` 是**事务级**时间戳
  ⇒ 同一事务写的多行逐位相同。本批按决策点 ⑦(a) **不改写侧**，所以同刻行会常态化，
  读侧次级键就是这个域唯一的确定性来源。
- 期望序由**种子的 id** 决定，不交给钟或数据库返回序。同刻组一律**三行**，且 id 最小的
  那行插在**中间**——两行做不到：数据库无次级键时只可能给插入序或其逆序，
  必有一支与期望序重合，那格退化成空转（M-G 第一轮实测的教训，tasks.md 的 M-G 段有记录）。
- 形状闸只清点**点名的那一条**（`list`）。`get_superseded_by`(:84)、`list_between`(:159)、
  `list_drift_candidates`(:318) 是同型缺陷，按「发现的缺陷不顺手修」登记在 tasks.md 批 6 段。
- Go 镜像（`go-core/internal/store/legacy_batch6.go` 的三条 SQL）的同形由
  `backend/tests/test_n8_batch6_contract.py` 与真库逐字回放守，不在本文件射程。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from backend.models.memory import Memory
from backend.repositories.memory_repository import memory_repository

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT / "backend" / "repositories" / "memory_repository.py"

# 显式钉时间戳（不靠 server_default）：三行逐位相同才是本闸要测的情形。
SAME_INSTANT = datetime(2026, 10, 12, 9, 0, 0, tzinfo=timezone.utc)


async def _plant(db, user_id: str, ids: list[str], **overrides) -> None:
    """按给定顺序插入同刻若干行——ids 的**数组序**就是「字典序 vs 插入序」的把手。

    列默认值全部由 ORM 给出（`deleted` 只有 ORM `default=False`、没有 server_default），
    所以这里显式写 `deleted=False`：要种软删行时由 overrides 覆盖。
    """
    base = {
        "user_id": user_id, "content": "c", "category": "values",
        "status": "current", "source": "self", "conviction_score": 0.5,
        "deleted": False, "created_at": SAME_INSTANT,
    }
    for rid in ids:
        db.add(Memory(**{**base, "id": rid, **overrides}))
    await db.flush()


async def test_list_breaks_same_instant_ties_by_id_desc(db, make_user):
    """同刻三行的返回序与「LIMIT 1 留下谁」都由 id 决定。

    插入序 [tie-a, tie-z, tie-m] ⇒ 期望序 [tie-z, tie-m, tie-a]（id DESC），
    它与插入序和逆插入序都不重合，所以无键时数据库给哪一支本条都会红。
    第二格是这条端点的真正风险：`GET /memories?limit=1` 与前端 `listMemories(limit=50)`
    的截断都靠它，同刻时「最近一条」不确定＝两侧灰度给出不同答案。
    """
    await make_user("u-mem-desc")
    await _plant(db, "u-mem-desc", ["tie-a", "tie-z", "tie-m"])

    rows = await memory_repository.list(db, "u-mem-desc", limit=7)
    assert [r.id for r in rows] == ["tie-z", "tie-m", "tie-a"], (
        "同刻三行的倒序仍由数据库返回序决定：次级键没生效或方向写反了"
    )

    one = await memory_repository.list(db, "u-mem-desc", limit=1)
    assert [r.id for r in one] == ["tie-z"], (
        f"DESC LIMIT 1 给的不是 id 最大那条（实得 {[r.id for r in one]}）"
    )


async def test_list_secondary_key_survives_the_category_filter(db, make_user):
    """带 `category` 那一支与不带那一条**共用同一个 ORDER BY**（:123 在 `if category:` 之后）。

    Go 侧是两条分开的 SQL（legacy_batch6.go），所以这条也钉住「加过滤不改排序」——
    否则夹具的 `list-category-*` 格子与 `list-tie` 格子会各自成立、合起来不同形。
    """
    await make_user("u-mem-cat")
    await _plant(db, "u-mem-cat", ["tie-a", "tie-z", "tie-m"])
    await _plant(db, "u-mem-cat", ["tie-b"], category="goal")

    rows = await memory_repository.list(db, "u-mem-cat", limit=7, category="values")
    assert [r.id for r in rows] == ["tie-z", "tie-m", "tie-a"], (
        f"category 过滤那支的排序与不带过滤那支不同：{[r.id for r in rows]}"
    )


async def test_list_excludes_superseded_and_soft_deleted_rows(db, make_user):
    """列表的两条谓词（`status='current'`、`deleted=false`）不因加次级键而松动。

    这三行与上面三行**同刻**，正是详情端点读得到、列表读不到的那批
    （`get()` 的 WHERE 没有 status），夹具 detail-superseded 钉的是同一件事的字节面。
    """
    await make_user("u-mem-filter")
    await _plant(db, "u-mem-filter", ["cur-1"])
    await _plant(db, "u-mem-filter", ["sup-1"], status="superseded")
    await _plant(db, "u-mem-filter", ["del-1"], deleted=True)

    rows = await memory_repository.list(db, "u-mem-filter", limit=10)
    assert [r.id for r in rows] == ["cur-1"], f"列表漏进了不该出现的行：{[r.id for r in rows]}"


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


def test_the_named_memories_list_query_carries_a_secondary_key():
    """点名的那一条：主键 created_at、次级键 id，且**同方向**（双 DESC）。

    方向不一致（一处 DESC 一处 ASC）时两侧读到同一批行也会给出不同数组序，
    那就不是格式差异而是契约差异。
    """
    src = REPO.read_text(encoding="utf-8")
    body = re.search(
        r"\n    async def list\(.*?(?=\n    async def )", src, re.S
    )
    assert body, "list() 找不到了，本闸的口径要跟着改"
    calls = _order_by_args(body.group(0))
    assert len(calls) == 1, f"list() 里有 {len(calls)} 条 ORDER BY，本闸只认一条"
    order = calls[0]
    assert "Memory.created_at.desc()" in order, f"主键或它的方向变了：{order}"
    assert "Memory.id.desc()" in order, f"缺 id 次级键或方向与主键不一致：{order}"
