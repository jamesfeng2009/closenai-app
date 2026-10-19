"""N8 批 2（curriculum 域）的跨语言逐字闸。

被验对象：`go-core/internal/{domain,httpapi,config}` 里从 Python 移植的 curriculum 口径，
以及 `contracts/openapi.json` 里这两个端点的 deprecated 标记。

为什么这一批要额外钉三类东西（批 1 没这个负担）：
1. **日历**。curriculum 的响应字节里有 `week_start`、`days[].date`、`revealed`，三者全由
   「用户本地今天」推出。批 1 的响应只有计数与时间戳，抄错一个数字就红；这里抄错一次
   时钟解释（本地零点落在 DST 缺位日）会**整周**错位，所以夹具带了 899 条
   week_window 语料，本文件反向要求它不许退化成只测 UTC 午夜。
2. **写**。`POST /curriculum/{id}/read` 会改库，回放必须与录制同字节 ⇒ 录制侧冻时钟、
   播种用绝对 `scheduled_at`、Go 侧把 `Server.now` 钉在夹具的 `recorded_at`。
   本文件守的是这套机制的**前提**（recorded_at 存在、用例顺序成对）。
3. **时区数据库**。Go 的运行镜像是 alpine + ca-certificates，没有 tzdata；不嵌
   `_ "time/tzdata"`，`time.LoadLocation` 会对每个真实用户失败并静默退回 UTC——
   而 domain.loadUserZone 的回退与 Python 的 safe_zoneinfo 同形，**契约测全绿**，
   因为只有非法时区名才走那条分支。这一条只能在这里守。

口径来源（每条点名到行）：
- KIND / WEEK_DAYS        ← curriculum_service.py:52,59 ↔ domain/curriculum.go::CurriculumKind/CurriculumWeekDays
- limit=50                ← journey_repository.py:192-200 ↔ domain/curriculum.go::CurriculumListLimit
- 「无权访问该主题卡」      ← curriculum_service.py:205      ↔ httpapi/curriculum_read.go::forbiddenCardMessage
- AI 同意文案 / 40303      ← user_service.py:227-229        ↔ httpapi/batch2.go::errAIAgreementRequired
- 40401 / 50003           ← core/errors.py:57,77           ↔ apierr/apierr.go:30,35
- CLOSEN_CUTOVER_CURRICULUM ← config/config.go:63          ↔ deploy/k8s/10-config.yaml
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from backend.main import app
from backend.services import curriculum_service as curriculum_module

from go_route_table import cutover_domain_keys, go_ops, route_row

ROOT = Path(__file__).resolve().parents[2]
GO = ROOT / "go-core"
FIXTURE = GO / "testdata" / "n8_contract.json"
MANIFESTS = ROOT / "deploy" / "k8s"

BATCH2_DOMAIN = "curriculum"
BATCH2_PATHS = {"/api/v1/curriculum/week", "/api/v1/curriculum/{event_id}/read"}
# 批 1 的两条 + 批 2 的两条 + 批 3 的两条 + 批 4 的两条 + 批 5 的两条：
# 已切流域的全量登记表，放量一批就在此处加一批。
CUT_DOMAINS = ("analytics", "discovery", "curriculum", "journey", "onboarding", "mood")
CUT_PATHS = {
    "/api/v1/analytics/batch",
    "/api/v1/discovery/daily",
    "/api/v1/curriculum/week",
    "/api/v1/curriculum/{event_id}/read",
    "/api/v1/journey/events",
    "/api/v1/journey/events/acknowledge",
    "/api/v1/onboarding/first-read",
    "/api/v1/mood/history",
    "/api/v1/mood/trend",
}
# 灰度键已在 ConfigMap 声明、但本批**没有** Ingress path 的域（决策点 D-10）。
# 与 CUT_DOMAINS 分开的理由写在 test_three_registers_agree_on_the_cut_domains 的文档串里。
# 批 5 只有 reflections 一格；批 6 的 memories 是**两条 path 都这格**（`/memories` 同 path
# 挂 POST、`/memories/{memory_id}` 同 path 挂 DELETE），所以这一档从一格变两格。
PATHLESS_KEYS = frozenset({"reflections", "memories"})


def _go(rel: str) -> str:
    return (GO / rel).read_text(encoding="utf-8")


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _go_const(name: str, rel: str = "internal/domain/curriculum.go") -> str:
    src = _go(rel)
    match = re.search(rf"{name}\s*=\s*([^=\n]+?)\n", src)
    assert match, f"Go 侧 {rel} 找不到 {name} 常量——改名或换写法了，闸要跟着改"
    return match.group(1).strip().strip('"')


def test_deprecated_set_is_exactly_the_cut_domains():
    """第④步的边界（从批 1 的文件上移到这里的唯一原因：集合会逐批长大）。

    多出来＝提前标废弃还没切流的域，客户端会看到一条其实仍由 Python 全量回答的废弃线；
    少了＝这一批的第④步没做，openapi 与 Ingress 归属互相说谎。
    """
    paths = app.openapi()["paths"]
    deprecated = {
        p
        for p, item in paths.items()
        for op in item.values()
        if isinstance(op, dict) and op.get("deprecated")
    }
    assert deprecated == CUT_PATHS, (
        f"deprecated 集合 = {sorted(deprecated)}，期望 {sorted(CUT_PATHS)}"
    )


def test_cut_paths_are_still_routable_for_rollback():
    """回滚＝把灰度改回 0，前提是 Python 还在答这几条路——所以只标 deprecated，不删路由。"""
    registered = {p for p, item in app.openapi()["paths"].items() for method in item}
    assert CUT_PATHS <= registered, f"路由本体被删了：{sorted(CUT_PATHS - registered)}"


def test_kind_week_days_and_limit_match_go():
    """三个数字/一个字面量决定取哪些行、出几张卡：抄错就是响应内容不同。"""
    assert _go_const("CurriculumKind") == curriculum_module.KIND == "curriculum"
    assert int(_go_const("CurriculumWeekDays")) == curriculum_module.WEEK_DAYS == 7
    src = (ROOT / "backend" / "repositories" / "journey_repository.py").read_text(
        encoding="utf-8"
    )
    limit = re.search(r"async def list_kind_between\(.*?limit: int = (\d+)", src, re.S)
    assert limit, "journey_repository 的 limit 默认值改了写法，闸要跟着改"
    assert int(_go_const("CurriculumListLimit")) == int(limit.group(1)) == 50


def test_forbidden_card_message_and_codes_match_go():
    """越权文案与错误码：三种情形（无行／别人的／别的 kind）合成**同一条** 403，
    少合成一次就把这个端点变成 id 枚举 oracle。"""
    src = (ROOT / "backend" / "services" / "curriculum_service.py").read_text(encoding="utf-8")
    assert '"无权访问该主题卡"' in src, "Python 侧的越权文案改了字面量"
    assert _go_const("forbiddenCardMessage", "internal/httpapi/curriculum_read.go") == "无权访问该主题卡"

    # 合成一条：mark_read 的判定式里三个条件用 or 连，且只抛一次 FORBIDDEN。
    mark = re.search(r"async def mark_read.*?return \{", src, re.S)
    assert mark and mark.group(0).count("AppException(ErrorCode.FORBIDDEN") == 1
    assert "event is None or event.user_id != user.id or event.event_kind != KIND" in src

    # 码值两侧同数：403/40303/40401/50003。
    from backend.core.errors import ErrorCode

    go_codes = _go("internal/apierr/apierr.go")
    for const, code in (("CodeForbidden", ErrorCode.FORBIDDEN),
                        ("CodeAIConsentRequired", ErrorCode.AI_CONSENT_REQUIRED),
                        ("CodeUserNotFound", ErrorCode.USER_NOT_FOUND),
                        ("CodeDatabaseError", ErrorCode.DATABASE_ERROR)):
        match = re.search(rf"{const}\s*=\s*(\d+)", go_codes)
        assert match, f"Go 侧找不到 {const}"
        assert int(match.group(1)) == int(code), f"{const}={match.group(1)}，Python={int(code)}"


def test_ai_consent_message_matches_go_literal():
    """同意门文案是逐字契约的一半：Go 少抄后半句英文，400 体就与 Python 不同字节。"""
    src = (ROOT / "backend" / "services" / "user_service.py").read_text(encoding="utf-8")
    block = re.search(r"ErrorCode\.AI_CONSENT_REQUIRED,\s*\n(.*?)\n\s*\)", src, re.S)
    assert block, "require_ai_consent 的抛错写法变了，闸要跟着改"
    py_text = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', block.group(1)))
    go_block = re.search(r'errAIAgreementRequired = ((?:.|\n)*?)\n\n',
                         _go("internal/httpapi/batch2.go"))
    assert go_block, "Go 侧找不到 errAIAgreementRequired"
    go_text = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', go_block.group(1)))
    assert go_text == py_text, f"Go={go_text!r} Python={py_text!r}"


def test_consent_checks_run_before_any_event_read():
    """两道同意门都在碰事件表**之前**，且是两条独立 SELECT（user_service 与
    user_repository 各查一次）。Go 合并或颠倒，「用户已注销」与「卡片越权」谁先报错就换侧。"""
    src = (ROOT / "backend" / "routers" / "curriculum.py").read_text(encoding="utf-8")
    for handler in re.finditer(r"async def curriculum_\w+.*?(?=\n@router|\Z)", src, re.S):
        body = handler.group(0)
        calls = re.findall(r"await (user_service\.require_ai_consent|user_repository\.require_user)",
                           body)
        assert calls == ["user_service.require_ai_consent", "user_repository.require_user"], (
            f"{handler.group(0)[:40]}… 的门序＝{calls}"
        )
    go = _go("internal/httpapi/batch2.go")
    assert go.count("s.batch2.Store.CurriculumUser(") == 2, "Go 侧的第二趟 SELECT 被合并了"


def test_runtime_images_agree_on_where_zoneinfo_comes_from():
    """两个运行层的时区数据来源各只允许一种，且不得同时改——否则「两侧一致」会变成「两侧一起错」。

    实测（2026-10-14，本机 docker run）：
    - `alpine:3.20`（go-core/Dockerfile 的运行层）：**没有** /usr/share/zoneinfo，
      `apk info` 里 tzdata 计数 0 ⇒ Go 的 time.LoadLocation 必然失败，
      domain.loadUserZone 退回 UTC，与 Python 对**非法时区名**的回退同形，
      所以 899 条语料（开发机有 zoneinfo）与 28 条 HTTP 用例全都看不出异常。
      二进制必须自己嵌 `_ "time/tzdata"`。
    - `python:3.13-slim`（py-api 运行层）：有 /usr/share/zoneinfo（顶层 23 项），
      `ZoneInfo("Asia/Shanghai")` 可解析 ⇒ Python 侧不需要额外装包。
    """
    main = _go("cmd/go-api/main.go")
    assert re.search(r'^\t_ "time/tzdata"$', main, re.M), "go-api 没嵌 IANA 时区数据库"
    go_docker = (GO / "Dockerfile").read_text(encoding="utf-8")
    stage = go_docker.split("FROM alpine")[-1]
    assert "tzdata" not in stage, (
        "运行层装了 tzdata：本闸的前提变了，要么删掉嵌入并改这条断言，"
        "要么确认嵌库仍是唯一来源"
    )

    py_docker = (ROOT / "deploy" / "Dockerfile.py-api").read_text(encoding="utf-8")
    bases = re.findall(r"^ARG PYTHON_IMAGE=(\S+)", py_docker, re.M)
    assert bases, "Dockerfile.py-api 的基础镜像写法变了，闸要跟着改"
    # 白名单＝本机实测过带 zoneinfo 的基础镜像；换基线就要重新实测再登记，不许凭印象放行。
    measured = {"python:3.13-slim"}
    assert set(bases) <= measured, (
        f"py-api 基础镜像 {bases} 不在已实测带 /usr/share/zoneinfo 的清单 {sorted(measured)} 里："
        "Python 侧的 safe_zoneinfo 会静默退回 UTC，两侧一起错而契约测全绿"
    )


def test_cutover_switch_exists_in_go_and_manifest_not_python():
    """CLOSEN_CUTOVER_* 是 Go-only 的灰度面：Python 读了它就会有「两侧都以为自己在答」的
    分裂状态；ConfigMap 里缺它则这一批无法回滚到 0。"""
    cfg = _go("internal/config/config.go")
    assert 'envInt("CLOSEN_CUTOVER_CURRICULUM", 0)' in cfg
    cm = yaml.safe_load_all((MANIFESTS / "10-config.yaml").read_text(encoding="utf-8"))
    data = next(d for d in cm if d and d["kind"] == "ConfigMap")["data"]
    assert data["CLOSEN_CUTOVER_CURRICULUM"] == "0", "这一批落地必须是 0%，放量是一次独立上线动作"
    py_src = (ROOT / "backend" / "config.py").read_text(encoding="utf-8")
    assert "CLOSEN_CUTOVER" not in py_src, "Python 侧读了 Go 的灰度开关"


def test_three_registers_agree_on_the_cut_domains():
    """三个登记面必须给出同一个「已切流域」集合：Ingress 的 backend、ConfigMap 的百分比键、
    openapi 的 deprecated 标记，分别是运维、Go 运行时、客户端看到的同一件事。

    为什么单独钉这一条（批 1 的两条登记闸各自只比两侧）：把 curriculum 的 backend 改回
    py-api 时**功能上看不出差别**——0% 下 Go 本来就只逐字转交——但「这一域已交给 Go」这件事
    在三个登记处只剩两个还说，放量那一次就会以为自己正在切一条其实没接管的域。
    集合等式让任何一侧单独改都当场红。

    ↳ 批 5 起两侧不再天然同形，按**域**归一而不是按 path：
    ① Ingress 的一刀从批 5 起可以只到端点级（`/api/v1/mood/history`），域仍取第一段
       ——与 backend/tests/test_k8s_manifests.py 的口径一致，两边各自取段会互相说谎；
    ② PATHLESS_KEYS 是「键已声明、但本批**没有**入口 path」的登记面（决策点 D-10：
       Ingress 的 path 匹配不看 HTTP 方法，而 /api/v1/reflections 的 GET 与 POST 共用
       一个 path，把 GET 搬走就会让 POST 打在一台没注册它的 mux 上 ⇒ 405）。
       这类键在本批调高也不放量（入口没有指向它的 path），失败方向是「没切」而不是 405；
       它也**不进** deprecated 集合——
       标废弃＝告诉客户端这条路要没了，而 Python 还在整条答它。
       ↳ 这一档**只有这一处登记表**：backend/tests/test_k8s_manifests.py 的
       `test_cutover_keys_without_ingress_path_stay_unrouted` 从清单本身推出同一个集合
       （声明了键、却没有 go-api path 的域），只负责钉每一格的内容；
       在两个文件各写一份域名清单就是给下一批留一处会漂移的缝。
       ↳ 批 6 起这一档有两格（reflections、memories）；实测证据是加键那一刻——
       `CLOSEN_CUTOVER_MEMORIES` 落进 `10-config.yaml` 之后只有这条闸红，
       k8s 那份「有 path ⇒ 有键」的遍历对它完全无感。
    """
    ing = next(d for d in yaml.safe_load_all(
        (MANIFESTS / "50-ingress.yaml").read_text(encoding="utf-8"))
        if d and d["kind"] == "Ingress")
    ingress_domains = {
        p["path"].removeprefix("/api/v1/").split("/")[0]
        for rule in ing["spec"]["rules"]
        for p in rule["http"]["paths"]
        if p["backend"]["service"]["name"] == "go-api"
    }

    cm = next(d for d in yaml.safe_load_all(
        (MANIFESTS / "10-config.yaml").read_text(encoding="utf-8"))
        if d and d["kind"] == "ConfigMap")
    configmap_domains = {
        k.removeprefix("CLOSEN_CUTOVER_").lower()
        for k in cutover_domain_keys(cm["data"])
    }

    openapi_domains = {
        p.removeprefix("/api/v1/").split("/")[0]
        for p, item in app.openapi()["paths"].items()
        for op in item.values()
        if isinstance(op, dict) and op.get("deprecated")
    }

    want = set(CUT_DOMAINS)
    assert ingress_domains == want, f"Ingress 指 go-api 的域 = {sorted(ingress_domains)}"
    assert configmap_domains == want | set(PATHLESS_KEYS), (
        f"ConfigMap 里的灰度键 = {sorted(configmap_domains)}，期望已切流域 {sorted(want)} "
        f"再加只声明不上 path 的 {sorted(PATHLESS_KEYS)}"
    )
    assert openapi_domains == want, f"openapi 标 deprecated 的域 = {sorted(openapi_domains)}"
    # PATHLESS_KEYS 不许偷偷长出另外两侧：它一旦拿到 Ingress path 或废弃标记，
    # 上面两条等式各自会红，但红得像「登记面漂移」而不是「这一域转正了」——
    # 所以这里点名它，转正那一批要做的动作是**把这个键移进 CUT_DOMAINS**，而不是删断言。
    assert not (PATHLESS_KEYS & ingress_domains), (
        f"只声明不上 path 的域拿到了 path：{sorted(PATHLESS_KEYS & ingress_domains)}"
    )
    assert not (PATHLESS_KEYS & openapi_domains), (
        f"入口还没接管的域被标了废弃：{sorted(PATHLESS_KEYS & openapi_domains)}"
    )


def test_go_registers_both_curriculum_routes():
    """Ingress 一刀指 go-api 的 /api/v1/curriculum，Go 必须有这两条注册，否则上线即 404。

    注册串带方法前缀（stdlib ServeMux 的 `GET /path` 模式），所以这里比的是整条模式，
    不只是 path——少了方法前缀会退化成「任意方法都收」，那是另一种契约。
    """
    # T6-02 起注册面住在 `internal/httpapi/routes.go` 的声明表里（域键与批次开关都是表字段），
    # 所以这里比的是**表里的形状**：方法＋路径＋域键＋挂在哪个批次开关下，逐字段核。
    # 「两条都必须在批 2 开关下」这句话没有放松：gated 是这条声明的一部分，比不出来就是红。
    for method, path in (
        ("GET", "/api/v1/curriculum/week"),
        ("POST", "/api/v1/curriculum/{event_id}/read"),
    ):
        assert route_row(method, path, "curriculum", "s.batch2 != nil"), (
            f"声明表里 {method} {path} 不是「domain=curriculum 且 gated 批 2」的形状："
            "域键错＝切流矩阵把它算进别的域；开关丢＝机制没接线时 Go 会抢先回答这一域"
        )
    # 反向证人：生成物（Go 从同一张表生成、并由 path_method_matrix_test.go 钉住与现账同源）
    # 里这两条也在。两侧不同源（一侧源码表字段，一侧生成物表格），漂了会红。
    assert {("GET", "/api/v1/curriculum/week"),
            ("POST", "/api/v1/curriculum/{}/read")} <= go_ops(), (
        "矩阵里没有这两条 ⇒ routes.go 与 contracts/cutover-matrix.md 不同源（重跑生成器）")


def test_fixture_curriculum_group_covers_the_branches():
    """回放闸的覆盖面自身要钉住：这一组必须含**两个端点**、成功面与三条 403 面。"""
    fixture = _fixture()
    cases = [c for c in fixture["cases"] if c["group"] == BATCH2_DOMAIN]
    assert cases, "夹具里没有 curriculum 组——批 2 的逐字节闸在空跑"
    paths = {c["path"] for c in cases}
    assert "/api/v1/curriculum/week" in paths
    assert any(p.startswith("/api/v1/curriculum/") and p.endswith("/read") for p in paths)
    statuses = {c["status"] for c in cases}
    # 404＝没带 token / 用户不存在，403＝未同意 AI 或越权，200＝成功面。
    assert {200, 403, 404} == statuses, statuses

    # 幂等：同一张卡连着两次 read 都在用例里，且第二次仍 200（不写库、回同样字节）。
    reads = [c["name"] for c in cases if c["method"] == "POST"]
    assert "read-card" in reads and "read-card-again" in reads, reads

    # 读后必须紧跟一次 week：revealed/read/unread_count 三个键由**写过的**行读出。
    names = [c["name"] for c in cases]
    assert "week-full-after-read" in names, names
    assert any(n.startswith("week-") and "after-read" in n for n in names)


def test_fixture_seed_face_has_the_timezone_and_dirty_row_shapes():
    """播种面必须含「非 UTC 时区／非法时区名／坏 payload／别的 kind／已注销」这些形状，
    否则逐字节回放只在测同一条 happy path。"""
    fixture = _fixture()
    users = {u["id"].removeprefix("n8b1-"): u for u in fixture["seed"]["users"]}
    for slug, why in (("curr-sh", "非 UTC 时区的本地周窗口"),
                      ("curr-badtz", "非法时区名走 safe_zoneinfo 回退"),
                      ("curr-off", "未同意 AI ⇒ 40303"),
                      ("curr-deleted", "status 非 active ⇒ 40401"),
                      ("curr-kind", "event_kind 不是 curriculum ⇒ 合成 403")):
        assert slug in users, f"缺 {slug} 用户：{why} 这条分支没测"
    assert users["curr-sh"]["timezone"] == "Asia/Shanghai"
    assert users["curr-badtz"]["timezone"] == "Not/AZone"
    assert users["curr-deleted"]["status"] == "deleted"

    events = fixture["seed"]["journey_events"]
    kinds = {e["event_kind"] for e in events}
    assert "curriculum" in kinds
    # 坏 payload：NULL 与非法文本都要有——parse_json 的回退分支只有这两样能同时触发。
    payloads = [e["payload"] for e in events]
    assert any(p is None for p in payloads), "没有 NULL payload，_payload 的回退没测"
    assert any(p and not p.startswith(("{", "[")) for p in payloads), "没有坏文本 payload"
    # 负 day_no 与越界 day_no：Python 的 % 对负数返回非负，Go 的 % 跟被除数符号。
    day_nos = {e["day_no"] for e in events}
    assert any(d < 0 for d in day_nos), "没有负 day_no，day_no % 7 的发散点没测"
    assert any(d >= 7 for d in day_nos), "没有周外 day_no，槽位归一没测"


def test_week_window_corpus_is_not_degenerated():
    """语料退化＝日历核失去意义，而 Go 侧仍会全绿，所以这条在 Python 侧反向守。

    录制侧一旦把「每 10 天扫一遍」改成「只扫 UTC」，899 行会变成 53 行且全带 +00:00，
    Go 的逐条比依旧 100% 通过，但 week_start 的本地口径没人测了。
    """
    rows = _fixture()["week_window_corpus"]
    assert len(rows) >= 800, f"语料只剩 {len(rows)} 行，覆盖面在缩"
    zones = {r["tz"] for r in rows}
    assert len(zones) >= 16, f"只剩 {len(zones)} 个时区名：{sorted(zones)}"
    assert {"", "Not/AZone", "Asia/Shanghai", "Pacific/Kiritimati", "America/Santiago"} <= zones
    assert any(not r["start"].endswith("T00:00:00+00:00") for r in rows), \
        "全部窗口起点都在 UTC 午夜：本地零点折算没被走过"
    gap = [r for r in rows if r["gap"]]
    assert gap, "没有 gap 行：本地零点缺位/重复这一类样本被扫掉了"
    assert any(r["today"] == r["monday"] for r in gap), \
        "gap 行没落在周一上：窗口的左右端点都没压在异常午夜"


def test_anchor_is_recorded_for_the_frozen_clock():
    """curriculum 组回放时必须把 Go 的 now 钉在这一刻（go-core n8_replay_test.go）。

    没有 recorded_at，重放只要跨过本地午夜或周一，week_start/date/revealed 就整周平移，
    「逐字比」立刻变成比两个不同星期的响应。
    """
    fixture = _fixture()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", fixture["recorded_at"]), (
        fixture.get("recorded_at")
    )
    events = fixture["seed"]["journey_events"]
    assert all(re.match(r"\d{4}-\d{2}-\d{2}T", e["scheduled_at"]) for e in events), \
        "curriculum 的播种行必须是**绝对** scheduled_at（相对当下会让两侧读到不同一周）"


def test_recorder_freezes_python_clock_for_curriculum():
    """冻时钟得真的冻：录制脚本把 curriculum_service 的 local_today/utc_now 换成锚点，
    否则录出来的 week_start 属于录制那一刻，而 Go 侧锚点却钉在别处。

    2026-10-15 改窄：录制脚本现在钉**四个**模块（curriculum/journey/first_read/mood），
    原来那两条 `\\.local_today\\s*=` 这种「脚本里出现过就算」的正则会被别家模块的那行
    喂饱——单独撤掉 curriculum 那一行反而打不红。口径与批 3 的 journey 闸统一：
    **行首 + 模块名**，注释掉的一行不算冻钟。
    """
    script = (ROOT / "scripts" / "gen_n8_contract_goldens.py").read_text(encoding="utf-8")
    # 打在 curriculum_service 的**模块全局**上：get_week 里的 local_today(...) 是晚绑定，
    # 所以覆盖这一个名字就够了（time_utils 里那份不动，别的调用方仍读真实时钟）。
    assert re.search(r"^\s*curriculum_module\.local_today\s*=", script, re.M), (
        "录制侧没冻 curriculum 的 local_today"
    )
    assert re.search(r"^\s*curriculum_module\.utc_now\s*=", script, re.M), (
        "录制侧没冻 curriculum 的 utc_now"
    )
    assert "recorded_at" in script


def test_mark_read_is_idempotent_at_the_python_edge():
    """幂等口径的来源核验（Go 的 `if !pyTruthy(...)` 抄的就是这一行）：
    已有 read_at 时**不写库**，第二次调用与第一次回同样字节。"""
    src = (ROOT / "backend" / "services" / "curriculum_service.py").read_text(encoding="utf-8")
    body = re.search(r"async def mark_read.*?return \{", src, re.S).group(0)
    assert 'if not payload.get("read_at"):' in body
    assert body.count("set_payload") == 1, "写库次数变了：Go 的幂等判定要跟着改"
    assert "await db.flush()" in body
