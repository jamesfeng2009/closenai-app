"""N8 批 6（memories 域两条纯读）的跨语言逐字闸——每域四步的第②步。

被验对象：`go-core/internal/domain/memory.go`（limit 三件套、11 列行形状）、
`internal/store/legacy_batch6.go`（三条 SQL）、`internal/httpapi/batch6.go`
（响应形状与 `_parse_labels` 的复刻）、`internal/httpapi/{memories_list,memory_detail}.go`
（两条端点），以及夹具的 memories 组（43 条：列表 26／详情 17）与 `seed.memories`（63 行）。

三段式口径与批 1–5 相同：
1. Python 侧现算——本文件；
2. 夹具存着 Python 当时的结果——`scripts/gen_n8_contract_goldens.py` 从真 FastAPI＋真 PG 录出；
3. Go 侧就地重算并与夹具比——`internal/httpapi/n8_replay_test.go::TestN8MemoriesContractBytes`。
⇒ 本文件证「夹具 == Python」，Go 测证「Go == 夹具」，两段的输入是同一份夹具。

为什么本批的 Python 侧要**重算一遍投影**（下面第 3 条）而不只比源码字面量：
这两条端点没有时钟、没有外发、没有写，整条响应就是「过滤＋排序＋截断＋逐列序列化」四步，
而这四步全部可以在纯 Python 里对着 `seed.memories` 重算——重算出来还逐行等于夹具，
就说明两侧共享的确实只有那一条 SQL 的语义，而不是各自碰巧读到同一批行。

口径来源（逐条点名到行）：
- 两条 GET 的参数面        ← routers/memories.py:31-55 ↔ domain/memory.go:16-38
- list 与 get 的 WHERE 不同 ← memory_repository.py:103-121 / :61-70 ↔ store/legacy_batch6.go:47-72
- 13 个键的声明序          ← models/schemas.py:49-63 ↔ httpapi/batch6.go:66-82
- `_parse_labels` 四条规则  ← memory_service.py:102-112 ↔ httpapi/batch6.go:96-115
- conviction 走 repr(float) ← pydantic 的 `conviction: float` ↔ pyFloatValue
- 播种列清单含 drift_score  ← scripts/gen_n8_contract_goldens.py 的 INSERT_MEMORY
                              ↔ n8_replay_test.go 的 memories INSERT（实测 DDL：NOT NULL 无默认）
- D-A/M-G 的同刻三行判据    ← 夹具 mem-tie ↔ domain/memory.go 的 ORDER BY 注释
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
GO = ROOT / "go-core"
FIXTURE = GO / "testdata" / "n8_contract.json"
RECORDER = ROOT / "scripts" / "gen_n8_contract_goldens.py"

STORE_BATCH6 = GO / "internal" / "store" / "legacy_batch6.go"
DOMAIN_MEMORY = GO / "internal" / "domain" / "memory.go"
HTTPAPI_BATCH6 = GO / "internal" / "httpapi" / "batch6.go"
REPLAY_TEST = GO / "internal" / "httpapi" / "n8_replay_test.go"

from backend.services.memory_service import _parse_labels  # noqa: E402

LIST_PATH = "/api/v1/memories"
# labels 的 14 种列原文里，真正解析成数组的只有这五条（其余九条一律 null）。
# 这个数字是**判据**不是记录：加了或删了种子行，这里必须跟着重算一遍。
LABELS_ARRAY_ROWS = 5
LABELS_NULL_ROWS = 9


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _cases(group: str) -> list[dict]:
    return [c for c in _fixture()["cases"] if c["group"] == group]


def _rows_by_id() -> dict[str, dict]:
    return {r["id"]: r for r in _fixture()["seed"]["memories"]}


def _go(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _query(path: str) -> dict[str, str]:
    """按「重复键取最后一个」解析 query——这是今天实测到的 FastAPI 行为。

    证人不是注释而是夹具：`reflections-limit-duplicate`（`?limit=2&limit=1`）录到的
    是 1 行，本批的 `memories-limit-duplicate` 与 `memories-cat-duplicate` 同样按 last-wins
    才能重算出同一批行。写成 first-wins 的那一版会在两条用例上红。
    """
    out: dict[str, str] = {}
    for pair in path.partition("?")[2].split("&"):
        if not pair:
            continue
        key, _, value = pair.partition("=")
        out[unquote(key)] = unquote(value)
    return out


def _data(case: dict) -> object:
    return json.loads(case["response_text"])["data"]


def _user_of(case: dict) -> str:
    """录制侧的 X-User-Id；缺席就是 dev 口径的 "anonymous"（get_current_user 不查库）。"""
    return case["headers"].get("X-User-Id", "anonymous")


def _iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def test_labels_column_text_replays_through_python_parse_into_fixture_bytes():
    """labels 的 14 种列原文，用**现在这份** `_parse_labels` 重算一遍必须等于夹具。

    这一条同时钉两件事：
      - 夹具里那些 null 不是「录制时炸了」，而是 `_parse_labels` 的 falsy／非 dict／
        空数组三支各自给的 null；
      - Go 侧复刻的是**这四条规则**而不是「列里有文本就回数组」。
    """
    case = next(c for c in _cases("memories") if c["name"] == "memories-labels")
    rows = _rows_by_id()
    arrays = nulls = 0
    for item in _data(case):
        column = rows[item["id"]]["labels"]
        assert item["labels"] == _parse_labels(column), (
            f'{item["id"]} 的 labels 与现算不符：列原文 {column!r} ⇒ '
            f'现算 {_parse_labels(column)!r}，夹具 {item["labels"]!r}'
        )
        if item["labels"] is None:
            nulls += 1
        else:
            arrays += 1
    assert (arrays, nulls) == (LABELS_ARRAY_ROWS, LABELS_NULL_ROWS), (
        f"labels 的两种归宿变成 {arrays} 数组／{nulls} null——种子行或解析规则动过，"
        "判据要跟着重算（夹具这一组共 14 行）"
    )
    # NULL 与空串**必须同判**（falsy 判据，不是 IS NULL）：这一格在列上不同、在字节上相同
    assert _parse_labels(None) is _parse_labels("") is None
    # 顶层是合法 JSON 数组也只认 dict 里的 "labels" 键——不许「顺手」支持裸数组
    assert _parse_labels('["commitment"]') is None


def test_tie_group_is_neither_insertion_order_nor_its_reverse():
    """同刻那一组的夹具字节必须**既不等于插入序、也不等于插入序的逆序**。

    这是批 5 那次空转实测换来的判据（tasks.md 的 M-G 段）：数据库在缺次级键时只可能给
    这两支之一，所以两行同刻必有一支与 `id DESC` 重合 ⇒ 撤键字节不动 ⇒ 那格白种。
    三行、且 id 最小的那行插在中间，才能做到两支都不重合。
    """
    prefix = "n8b1-mem-tie-"
    # 播种序按 user_id 取后缀，按**最后一个**连字符切会得到 'a' 而不是 'tie-a'——
    # 而判据比的正是这一串后缀，切错一位就让「与插入序重合」那条断言永远为真。
    seed = [r["id"].removeprefix(prefix) for r in _fixture()["seed"]["memories"]
            if r["user_id"] == prefix.rstrip("-")]
    assert seed == ["tie-a", "tie-z", "tie-m", "tie-old"], f"播种序变了：{seed}"
    case = next(c for c in _cases("memories") if c["name"] == "memories-tie")
    got = [item["id"] for item in _data(case)]
    want = [f"{prefix}{k}" for k in ("tie-z", "tie-m", "tie-a", "tie-old")]
    assert got == want, f"同刻组的序是 {got}，want {want}"
    suffix = [g.removeprefix(prefix) for g in got]
    assert suffix != seed, "与插入序重合 ⇒ 撤掉次级键也照样绿"
    assert suffix != seed[::-1], (
        "与逆插入序重合 ⇒ 无键时数据库正好给这一支，那格还是空转"
    )
    # 前三行必须**真的同一瞬**，否则这条比的只是 created_at 而不是 id；
    # tie-old 故意早一瞬，用来证明「同刻组不会把一条更早的行吸进来」。
    same_instant = [r["created_at"] for r in _fixture()["seed"]["memories"]
                    if r["id"].startswith(prefix + "tie-") and r["id"] != prefix + "tie-old"]
    assert len(set(same_instant)) == 1, f"同刻组不再是同一瞬：{same_instant}"
    one = next(c for c in _cases("memories") if c["name"] == "memories-tie-limit-1")
    assert [item["id"] for item in _data(one)] == ["n8b1-mem-tie-tie-z"], (
        "limit=1 留下的不是 id 最大那条：「同刻里谁活下来」没有证人")


def test_list_projection_recomputed_from_seed_equals_fixture_for_every_case():
    """每条列表用例都按 Python 的语义重算一遍（过滤＋排序＋截断），逐行等于夹具。

    重算用的是 `seed.memories` 而不是数据库——这恰是 Go 回放读的同一批行，
    所以三段拼起来才是「Go == Python」而不是一句口号。
    """
    rows = _fixture()["seed"]["memories"]
    recomputed = 0
    for case in _cases("memories"):
        if not (case["path"] == LIST_PATH or case["path"].startswith(LIST_PATH + "?")):
            continue
        query = _query(case["path"])
        limit = int(query.get("limit", "20"))
        category = query.get("category", "")
        selected = [
            r for r in rows
            if r["user_id"] == _user_of(case)
            and r["status"] == "current"
            and r["deleted"] is False
            and (not category or r["category"] == category)
        ]
        selected.sort(key=lambda r: (_iso(r["created_at"]), r["id"]), reverse=True)
        want = [r["id"] for r in selected[:limit]]
        got = [item["id"] for item in _data(case)]
        assert got == want, (
            f'{case["name"]}：重算 {want}，夹具 {got}（limit={limit} '
            f'category={category!r}）——两侧读的不再是同一条 SQL 的语义'
        )
        recomputed += 1
    assert recomputed >= 20, f"列表用例只剩 {recomputed} 条，投影重算的覆盖面塌了"


def test_conviction_is_serialized_as_repr_float_not_json_number():
    """conviction 的字节必须等于 Python 的 `repr(float)`——0.0 不许被写成 0。

    这是全仓第一条**非整型浮点**响应列（批 1–5 没有），而 Go 的 encoding/json 会把
    0.0 编成 0：值相同、字节不同。逐字比能看见，但只有把「每个种子的每个档位」都
    拿来现算才说明这不是巧合。
    """
    rows = _rows_by_id()
    checked = 0
    for case in _cases("memories"):
        text = case["response_text"]
        data = _data(case)
        for item in (data if isinstance(data, list) else [data] if data else []):
            seed_value = rows[item["id"]]["conviction_score"]
            assert f'"conviction":{seed_value!r}' in text, (
                f'{case["name"]} 里 {item["id"]} 的 conviction 不是 repr 形状'
                f"（现算 {seed_value!r}）")
            checked += 1
    assert checked >= 60, f"只核到 {checked} 个 conviction 值，覆盖面塌了"
    for fragment in ('"conviction":0,', '"conviction":1,', '"conviction":0.3330000'):
        assert not any(fragment in c["response_text"] for c in _cases("memories")), (
            f"夹具里出现 {fragment}：浮点档位不再是「带小数点的 repr」，本闸的判据要重定")


def test_the_two_sql_shapes_are_one_to_one():
    """两条 SQL 只收缩列，WHERE 与 ORDER BY 逐字照抄——两支的过滤差异是本批的核心事实。

    列表过 `status='current'` 与 `deleted=false`，详情**只**过 `deleted=false`（加上主键
    与 user_id）。把详情也加上 status 过滤＝让一条 superseded 记忆从两个面上都消失，
    那是行为改变而不是同形（夹具 mem-detail-superseded 200／memories-status-list 里它缺席
    就是这一对的字节证人）。
    """
    repo = (ROOT / "backend" / "repositories" / "memory_repository.py").read_text(
        encoding="utf-8")
    listing = re.search(r"async def list\(.*?return list", repo, re.S)
    assert listing, "memory_repository.list 写法变了，本闸口径要跟着改"
    assert ("Memory.user_id == user_id,\n            Memory.status == status,\n"
            "            Memory.deleted == False,") in listing.group(0), (
        "list 的三条 WHERE 动了：现网口径是 user_id + status + deleted")
    assert 'status: str = "current"' in listing.group(0), "list 的默认状态过滤不再是 current"
    assert (".order_by(Memory.created_at.desc(), Memory.id.desc()).limit(limit)"
            in listing.group(0)), "list 的次级键没了或方向反了"

    getter = re.search(r"async def get\(.*?scalar_one_or_none", repo, re.S)
    assert getter, "memory_repository.get 写法变了"
    assert "Memory.status" not in getter.group(0), (
        "详情那条**不许**加 status 过滤：加了就和列表同一条 WHERE，两支的差别失去证人")

    def sql(name: str) -> str:
        """把「反引号字面量 + memoryResponseColumns + 反引号字面量」的拼接还原成一条 SQL。

        这一条必须认拼接：批 6 的三条 const 是**列清单复用**写的（store 文件第 42-43 行
        的 memoryResponseColumns），整段反引号正则只截到第一段，量到的是半条语句。
        """
        src = _go(STORE_BATCH6)
        cols = re.search(r"const memoryResponseColumns = `([^`]*)`", src, re.S)
        assert cols, "store/legacy_batch6.go 里列常量 memoryResponseColumns 的写法变了"
        head = src.index(f"const {name} =")
        # 每条 const 后面都紧跟一个空行＋注释：拿「到下一个空行」当声明边界，
        # 再用 FROM memories 断言这一刀没抓歪（抓歪时宁可炸，不许静默量到半条语句）。
        body = src[head:src.index("\n\n", head)]
        assert "FROM memories" in body, f"抓 {name} 的边界抓歪了：{body!r}"
        rendered = body.replace("` + memoryResponseColumns + `", cols.group(1))
        rendered = re.sub(r"const \w+ = ", "", rendered).replace("`", "")
        return " ".join(rendered.split()).upper()

    def predicate(name: str) -> str:
        """只看 WHERE 之后——category/status 同时也是**投影列**，整句里搜必然误报。"""
        shape = sql(name)
        assert " WHERE " in shape, f"{name} 里没有 WHERE：{shape}"
        return shape.split(" WHERE ", 1)[1]

    plain, by_category = predicate("selectMemoryList"), predicate("selectMemoryListByCategory")
    detail = predicate("selectMemoryByWholeID")
    for shape in (plain, by_category):
        assert "AND STATUS = 'CURRENT'" in shape and "AND DELETED = FALSE" in shape, (
            f"列表 SQL 少了 status 或 deleted 过滤：{shape}")
        assert "ORDER BY CREATED_AT DESC, ID DESC" in shape, (
            f"列表 SQL 的次级键缺失或方向与 Python 不一致：{shape}")
    assert "LIMIT $2" in plain and "LIMIT $3" in by_category
    assert "AND CATEGORY = $2" in by_category, "带 category 的那一支必须走精确等值"
    assert "CATEGORY" not in plain, "不带 category 的那一支不该有这条谓词（假值判据）"
    assert "STATUS" not in detail, f"详情 SQL 里冒出了 status 过滤：{detail}"
    assert "AND DELETED = FALSE" in detail and "ID = $1 AND USER_ID = $2" in detail
    # embedding 是 halfvec：Go 全仓没有它的解析器，SELECT * 会在 Scan 当场炸
    for shape in (sql("selectMemoryList"), sql("selectMemoryListByCategory"),
                  sql("selectMemoryByWholeID")):
        assert "EMBEDDING" not in shape and "SELECT *" not in shape, (
            f"SQL 把 embedding 带回来了（半向量列没有 Go 解析器）：{shape}")


def test_response_key_order_is_declaration_order_with_two_constant_keys():
    """13 个键的序＝pydantic 的声明序，且 similarity／regressed 排在 labels **之前**。

    这两键在这两条路上恒为 null／false（只有语义搜索那条 `_to_response_from_row` 才填
    similarity），但**键必须在**——前端读的是键存在性。Go 侧是手写 `v.set(...)` 串，
    所以这里比的是调用序。
    """
    from backend.models.schemas import MemoryResponse

    want = list(MemoryResponse.model_fields)
    assert want == ["id", "user_id", "content", "category", "status", "source",
                    "conviction", "created_at", "superseded_at", "transition_reason",
                    "similarity", "regressed", "labels"], f"声明序变了：{want}"
    sets = re.findall(r'v\.set\("([^"]+)"', _go(HTTPAPI_BATCH6))
    assert sets == want, f"Go 侧的 set 序与声明序不同：{sets}"

    # 夹具的字节序也得真的是这一串：把 data 里第一个对象的键按原文取出来
    case = next(c for c in _cases("memories") if c["name"] == "mem-detail-basic")
    keys = re.findall(r'"([a-z_]+)":',
                      case["response_text"].split('"data":')[1].split("},", 1)[0])
    assert keys == want, f"录制体的键序不是声明序：{keys}"


def test_limit_spec_and_the_400_envelope_match_go():
    """`Query(20, ge=1, le=100)` 的三件套与 400／40001 的信封形状，两侧同源。

    422 那一格是**登记面**漂移（endpoints.md 写的是 422，实测字节是 400），
    本批只记录不手改生成物；这里钉的是「夹具站在 400 这边」这件事还成立。

    原计划在这里比「一条请求同时给出 int_parsing 与 ge/le 两条消息的次序」，实测不成立：
    重复键走 last-wins，`?limit=abc&limit=±` 只回**一条** int_parsing（input 是第二个值），
    所以消息序没有证人；能钉住的改成三样——每支各自的 `type`＋`ctx` 常量，
    和顶层 message 由首条 msg 拼出来这一串公式。
    """
    router = (ROOT / "backend" / "routers" / "memories.py").read_text(encoding="utf-8")
    assert 'limit: int = Query(20, ge=1, le=100)' in router, "limit 的参数面变了"
    domain_src = _go(DOMAIN_MEMORY)
    for name, want in (("MemoriesDefaultLimit", 20), ("MemoriesMinLimit", 1),
                       ("MemoriesMaxLimit", 100)):
        match = re.search(rf"{name}\s*=\s*(\d+)", domain_src)
        assert match, f"Go 侧找不到 {name}"
        assert int(match.group(1)) == want, f"{name} = {match.group(1)}，want {want}"
    assert re.search(r"MemoriesLimitSpec = IntQuerySpec\{\s*Default: MemoriesDefaultLimit,"
                     r" Ge: MemoriesMinLimit, Le: MemoriesMaxLimit,", domain_src), (
        "MemoriesLimitSpec 不再是那三个常量的组合（改成了字面量就会和参数面分叉）")

    bad = [c for c in _cases("validation")
           if c["path"].startswith(LIST_PATH + "?") and "limit=" in c["path"]]
    assert len(bad) >= 8, f"limit 的校验用例只剩 {len(bad)} 条"
    for case in bad:
        body = json.loads(case["response_text"])
        assert (case["status"], body["code"]) == (400, 40001), (
            f'{case["name"]} 不再是 400/40001：{case["status"]}/{body["code"]}')
        # `data` **就是**那个错误列表，不是 `{"errors": [...]}`——这一格的形状是实测拿来的：
        # 写成 data["errors"] 会在这八条上同时 KeyError，而红字看不出是谁变了。
        errors = body["data"]
        assert isinstance(errors, list) and errors, f'{case["name"]} 的 data 不是非空数组'
        assert all(e["loc"] == ["query", "limit"] for e in errors), (
            f'loc 变了：{[e["loc"] for e in errors]}')
        # 顶层 message 由这一段拼出来，两侧都得照抄同一串
        assert body["message"].startswith(f'参数错误: query.limit {errors[0]["msg"]}'), (
            f'{case["name"]} 的 message 不再是「参数错误: query.limit <首条 msg>」')
    by_name = {c["name"]: c for c in bad}
    # ge／le 两支各自只有一个 ctx 常量，且必须和 `Query(20, ge=1, le=100)` 的三个数对齐：
    # 这三条把「参数面的数」和「字节里的数」缝在一起，改一头必有一条红。
    for name, typ, key, want in (("memories-limit-zero", "greater_than_equal", "ge", 1),
                                 ("memories-limit-negative", "greater_than_equal", "ge", 1),
                                 ("memories-limit-101", "less_than_equal", "le", 100)):
        err = json.loads(by_name[name]["response_text"])["data"][0]
        assert (err["type"], err["ctx"][key]) == (typ, want), f"{name}: {err}"
    # 重复键那两条钉的是**last-wins**：`?limit=abc&limit=±` 只回一条 int_parsing，
    # input 是第二个值——first-wins 的话 input 会是 "abc"。
    two = json.loads(by_name["memories-limit-two-errors-order"]["response_text"])["data"]
    assert [e["type"] for e in two] == ["int_parsing"], f"重复键给出的错误序变了：{two}"
    assert two[0]["input"] == "±", f"重复键按 first-wins 解析了：{two[0]['input']!r}"
    assert json.loads(by_name["memories-limit-abc"]["response_text"])["data"][0]["input"] == "abc"


def test_not_found_branches_are_byte_identical_and_never_leak_existence():
    """不存在／他人／已软删／匿名四支必须给**同一串** 40403（只差 user_id）。

    回 403 或给两条不同文案＝替旁人确认这颗 id 是真的，这条端点就退化成 id 枚举 oracle。
    """
    envelope = ('{"code":40403,"message":"记忆不存在 / Memory not found","data":null,'
                '"trace_id":"00000000-0000-0000-0000-000000000001","user_id":"')
    by_name = {c["name"]: c for c in _cases("memories")}
    for name in ("mem-detail-missing", "mem-detail-cross-user",
                 "mem-detail-soft-deleted", "mem-detail-anonymous",
                 "mem-detail-path-collision"):
        case = by_name[name]
        assert case["status"] == 404, f'{name} 的 HTTP 状态变成 {case["status"]}'
        text = case["response_text"]
        assert text.startswith(envelope) and text.endswith('"}'), f"{name} 的信封形状变了"
        user = text[len(envelope):-2]
        assert user == case["headers"].get("X-User-Id", "anonymous"), (
            f'{name} 的 user_id 与请求头不一致：{user}')
    bodies = {by_name[n]["response_text"].split('"user_id":"')[0]
              for n in ("mem-detail-missing", "mem-detail-cross-user",
                        "mem-detail-soft-deleted", "mem-detail-anonymous")}
    assert len(bodies) == 1, f"四支 40403 不再是同一串：{bodies}"


def test_memories_group_is_mask_free_purely_read_and_never_writes():
    """memories 组必须零掩码、零外发、全 GET——这三样任何一样冒头都说明口径变了。

    没有掩码是因为这颗主键是**种子字面量**（不像批 3/批 4 会现生成 uuid4），
    也没有 created_at 掩码是因为这一组的时刻全是列原文。
    一旦有人给这两条 GET 加了「顺手写一行」或分析事件，逐字比会照样绿，
    灰度期的数据面差异却永远看不见——所以这三条断言各管一件事。
    """
    cases = _cases("memories")
    assert len(cases) >= 40, f"memories 组只剩 {len(cases)} 条"
    for case in cases:
        assert (case["id_masks"], case["code_masks"], case["created_at_masks"]) == (0, 0, 0), (
            f'{case["name"]} 带上了掩码：这一组没有任何现生成值，掩码出现＝分支变了')
        for placeholder in ("<generated_at>", "<event_id>", "/r/<code>", "<created_at>"):
            assert placeholder not in case["response_text"], (
                f'{case["name"]} 的响应里有 {placeholder}')
        assert case["method"] == "GET", f'{case["name"]} 不再是纯读：{case["method"]}'
    # 详情用例全部按种子主键拼路径，不靠现写出来的行
    rows = _rows_by_id()
    for case in cases:
        if case["path"].startswith(LIST_PATH + "/"):
            ident = unquote(case["path"][len(LIST_PATH) + 1:])
            assert ident in rows or case["status"] == 404, (
                f'{case["name"]} 查了一颗既不在种子里、又没有 404 的 id：{ident}')


def test_recorder_and_go_seed_insert_the_same_column_list():
    """两侧播种必须同一条列清单——drift_score 那一列是这条闸存在的原因。

    实测 DDL：`conviction_score` 与 `drift_score` 都是 NOT NULL 且**没有 server_default**
    （ORM 侧的 default=… 只写在 Python，不进 DDL）。少写一列的 INSERT 不是「落 0.0」，
    是被数据库直接拒掉；而拒掉的只有回放那一侧的话，测试就会变成一串看不出所以然的 seed 失败。
    """
    # 生成脚本为了行宽把一条长 SQL 拆成了相邻字面量（`"…, conviction_score, "` 换行
    # `"source, …"`）：先把这种拼接折叠回去，否则 `[^)]*` 会连着引号一起当列名截出来。
    recorder = re.sub(r'"\s*\n\s*"', "", _go(RECORDER))
    match = re.search(r'INSERT_MEMORY = \(\s*"INSERT INTO memories \(([^)]*)\)', recorder)
    assert match, "生成脚本的 INSERT_MEMORY 写法变了"
    py_columns = [c.strip() for c in match.group(1).split(",")]

    replay = _go(REPLAY_TEST)
    go_match = re.search(r"INSERT INTO memories \(([^)]*)\)", replay, re.S)
    assert go_match, "Go 回放侧找不到 memories 的 INSERT"
    go_columns = [c.strip() for c in re.sub(r"\s+", " ", go_match.group(1)).split(",")]

    assert py_columns == go_columns, f"播种列清单分叉：py {py_columns} / go {go_columns}"
    for required in ("id", "user_id", "category", "content", "status", "conviction_score",
                     "source", "created_at", "superseded_at", "transition_reason",
                     "labels", "deleted", "drift_score"):
        assert required in py_columns, f"播种列少了 {required}"
    assert "embedding" not in py_columns, (
        "播种不许带 embedding：halfvec 在两种语言里都要额外的编解码，而响应根本不读它")
