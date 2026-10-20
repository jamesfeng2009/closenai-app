"""T6-04 的跨语言闸：库面隔离与运行环境词表，两侧各一份实现、一张用例表。

两侧不许同源（本仓铁律）。这份文件里三类读法都不同源：
· Go 的词表 ← 从 `go-core/internal/config/config.go` 的源文本里抓切片字面量；
· Python 的词表 ← 从 `backend/core/env_isolation.py` 的模块对象上读元组常量；
· 判据的规格 ← `go-core/testdata/env_isolation_cases.json`，一张**手工维护的用例表**
  （它不是任何一侧的输出，两侧各自跑它；改判据不改表，另一侧就红）。

以及装配证人（判据写得再对，没人调它等于没有）：
· `backend/main.py::_validate_startup_config` 必须真的调用词表校验与隔离校验；
· `backend/core/auth.py::_auth_enforced` 必须读 `is_protected()`——staging 那一档
  接错的后果是设备身份透传（X-User-Id 当身份），比它要防的双写更糟；
· `backend/database.py` 与 `backend/alembic/env.py` 必须在**建引擎之前**调闸，且有一条
  真子进程靶（`import backend.database` 在非零退出）——migrate／worker／ai-rpc 三条入口
  都不经过 FastAPI 的 startup，只钉 main.py 等于那三条没人管。

§验收 第一句「把 staging 指到生产串 ⇒ 启动失败并有明确报错」在 Python 侧的靶是
`test_staging_pointed_at_production_url_fails_fast`：它跑的是真入口函数，不是判据单测。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from backend.config import settings
from backend.core import env_isolation
from backend.main import _validate_startup_config

ROOT = Path(__file__).resolve().parents[2]
GO_CONFIG = ROOT / "go-core" / "internal" / "config" / "config.go"
CASES = ROOT / "go-core" / "testdata" / "env_isolation_cases.json"

LEAK = "FAKE_pw_not_real"


def _go_slice(name: str) -> list[str]:
    """从 Go 源文本里抓 `var <name> = []string{"a", "b"}` 的字面量集合。

    读源文本而不是读 Go 侧的测试输出：后者要让 pytest 去跑 `go test`，
    那会让这条闸依赖 Go 工具链（本仓口径：本机不可取证的那一格不许算绿）。
    """
    src = GO_CONFIG.read_text(encoding="utf-8")
    pattern = "var " + name + r" = \[]string\{([^}]*)\}"
    m = re.search(pattern, src)
    assert m, f"config.go 里找不到 var {name} = []string{{…}}：词表换了形状，本闸会空转"
    values = re.findall(r'"([^"]+)"', m.group(1))
    assert values, f"var {name} 是空的：空词表会让所有环境都合法"
    return values


def _cases() -> list[dict]:
    doc = json.loads(CASES.read_text(encoding="utf-8"))
    cases = doc["cases"]
    assert len(cases) >= 12, f"用例表只剩 {len(cases)} 格：这张表是规格，缩水＝两侧同时朝同向瞎"
    return cases


def test_environment_vocabulary_is_the_same_table_both_sides():
    """词表必须逐字同集合（顺序无关，值一个都不能多一个都不能少）。

    Python 原先把 `ENVIRONMENT` 用 `.lower()` 放宽，Go 用逐字比较——同一份 ConfigMap
    喂出两个档：`Production` 在 Python 是生产、在 Go 是「非生产所以放行设备身份透传」。
    本票把两侧收成同一张逐字小写的表，这条就是那道缝的封口证人。
    """
    go_values = set(_go_slice("EnvironmentValues"))
    assert go_values == set(env_isolation.ENVIRONMENT_VALUES), (
        f"词表两侧不同：Go {sorted(go_values)} vs Python "
        f"{sorted(env_isolation.ENVIRONMENT_VALUES)}")
    assert {"development", "staging", "production", "prod"} == go_values

    go_protected = set(_go_slice("ProtectedEnvironments"))
    assert go_protected == set(env_isolation.PROTECTED_ENVIRONMENTS), (
        f"受保护档两侧不同：Go {sorted(go_protected)} vs Python "
        f"{sorted(env_isolation.PROTECTED_ENVIRONMENTS)}")
    # staging 必须同时在词表里**且**在受保护档里：只在前者＝一个拿到开发档鉴权的新环境。
    assert "staging" in go_protected and "staging" in go_values
    assert go_protected < go_values, "受保护档必须是词表的真子集（development 不在里面）"


def test_python_defaults_are_the_shared_table_not_a_copy():
    """模块常量与判据必须读同一张表，不许在函数里再抄一份字面量。"""
    assert env_isolation.is_valid_environment("staging")
    assert not env_isolation.is_valid_environment("Production")
    assert not env_isolation.is_valid_environment("staging ")
    assert env_isolation.is_production("prod") and not env_isolation.is_production("staging")
    assert env_isolation.is_protected("staging")
    assert not env_isolation.is_protected("development")
    # 逐字判档，不许折叠大小写——这三行钉的是本票改判前的旧形状：
    # 旧 Python 用 `environment.lower() in ("production", "prod")`，于是 `Production`
    # 在 py-api 是生产、在 go-api 是「非生产所以放行设备身份透传」，
    # 同一份 ConfigMap 喂出两套鉴权档（D-T604-2）。
    assert not env_isolation.is_production("Production"), (
        "is_production 又开始折叠大小写了：那会重新打开两侧档位不对称的那道缝")
    assert not env_isolation.is_production("PRODUCTION ")
    assert not env_isolation.is_protected("Staging"), (
        "受保护档折叠大小写＝`Staging` 那台既没被词表承认、也没被当成受保护档")


@pytest.mark.parametrize("case", _cases(), ids=[c["name"] for c in _cases()])
def test_shared_case_table_python_side(case):
    """同一张用例表在 Python 侧再跑一遍：verdict 必须与规格逐格一致。

    这一条不是「Python 自己测自己」——expect 来自 JSON 规格，
    Go 侧的 `internal/config/env_isolation_test.go` 跑的是**同一个文件**。
    任何一侧改判据而没改规格，另一侧那条就红。
    """
    err = env_isolation.check_db_isolation(
        case["environment"], case["database_url"], case["fingerprints"])
    if case["expect"] == "refuse":
        assert err, f"该拒启却放行：{case['name']}——{case['reason']}"
        assert LEAK not in err, f"报错回显了口令（凭据不入日志）：{err}"
        assert "postgres.abcd1234" not in err, f"报错回显了用户名：{err}"
    else:
        assert err is None, f"该放行却拒启：{case['name']}——{err}"


def test_case_table_has_both_directions():
    """反向归零闸要配正向证人：全是 refuse 的表可以用「一律拒启」骗过两侧。"""
    expects = [c["expect"] for c in _cases()]
    assert expects.count("allow") >= 6, f"allow 只有 {expects.count('allow')} 格"
    assert expects.count("refuse") >= 12, f"refuse 只有 {expects.count('refuse')} 格"
    named = {c["name"] for c in _cases()}
    for must_have in ("staging_host逐字命中生产host", "staging_黑名单为空拒启",
                      "production_连自己的库必须放行", "development_黑名单为空放行",
                      # 2026-10-20 code review 之后补的四格，每格都是一种「读不出来」的形状：
                      # 少了它们，判据可以退回「只认小写 URL 形式」而两侧用例仍然全绿。
                      "staging_keyword形状命中生产host",
                      "staging_keyword串指自己的库必须放行",
                      "staging_大写scheme也要命中", "staging_多host逗号任一格命中",
                      "staging_坏端口读不出拒启", "staging_口令带hash读不出拒启"):
        assert must_have in named, f"用例表缺 {must_have}：那一格是本闸的方向性证人"


def test_fingerprint_parser_drops_blank_items():
    """`" , , "` 拆成空列表而不是两条能匹配任何 host 的指纹。"""
    assert env_isolation.parse_fingerprints(" db.prod.example.com ,closenai_prod, ,") == \
        ["db.prod.example.com", "closenai_prod"]
    assert env_isolation.parse_fingerprints("") == []
    assert env_isolation.parse_fingerprints(None) == []
    assert env_isolation.parse_fingerprints("  ") == []


def test_target_parser_never_carries_the_password():
    """DBTarget 只带 host/dbname/user；口令没有任何一条路径能被打印出来。"""
    t = env_isolation.parse_db_target(
        f"postgresql://closenai:{LEAK}@db.Prod.Example.COM:5432/postgres?sslmode=require")
    assert t is not None
    assert t.host == "db.prod.example.com" and t.dbname == "postgres"
    assert t.user == "closenai"
    assert LEAK not in repr(t), f"DBTarget 的 repr 里出现了口令：{t!r}"
    assert env_isolation.parse_db_target("sqlite+aiosqlite:///./closenai.db") is None
    # 带驱动的 SQLAlchemy 形状也要认（同一个 DATABASE_URL 两侧共用）。
    assert env_isolation.parse_db_target(
        "postgresql+psycopg://u:p@h.example.com:5432/d").host == "h.example.com"
    # 大小写 scheme 也要认：scheme 按 RFC 3986 不区分大小写，漂一下大小写不是绕过。
    assert env_isolation.parse_db_target(
        "POSTGRESQL://u:p@db.prod.example.com:5432/postgres").host == "db.prod.example.com"
    # keyword=value 形状（libpq 与 pgx 都接的一种）。旧实现只认 URL，对它返回 None
    # 并且**放行**——那条进程连的正是黑名单里的库。
    kw = env_isolation.parse_db_target(
        f"host=db.prod.example.com dbname=postgres user=closenai password={LEAK}")
    assert kw is not None and kw.host == "db.prod.example.com"
    assert kw.dbname == "postgres" and kw.user == "closenai"
    assert LEAK not in repr(kw)


def test_unreadable_pg_shapes_are_recognised_as_database_strings():
    """认得出「这是一条该拿来比对的库串」的那半边判据（与「拆不拆得出来」是两件事）。

    2026-10-20 之前只有 `parse_db_target` 返回 None 一种信号，而两种 None 没分开：
    sqlite（没有 host 可比，该放行）与「PG 串但读不出」（该拒启）在闸眼里长得一模一样。
    `looks_like_pg_target` 就是为了把它们拆开而存在，这一条钉它的两个方向。
    """
    for pg_shape in ("postgresql://u:p@db.prod.example.com:5432/postgres",
                     "postgresql://u:p@db.prod.example.com:543x/postgres",
                     "postgresql://u:FAKE#pw@db.prod.example.com:5432/postgres",
                     "POSTGRESQL://u:p@db.prod.example.com:5432/postgres",
                     "host=db.prod.example.com dbname=postgres",
                     "password=nohost port=5432"):
        assert env_isolation.looks_like_pg_target(pg_shape), pg_shape
    for not_a_db_string in ("sqlite+aiosqlite:///./closenai.db", "", "   ", "redis://h:6379"):
        assert not env_isolation.looks_like_pg_target(not_a_db_string), not_a_db_string


def test_pg_string_the_gate_cannot_read_still_refuses():
    """「读不出」必须拒启：sqlite 那种放行是**另一条测**，这一格不许一起放宽。

    三条形状各自都是现成的漂法（坏端口、口令里的 `#`、只有 password 的 keyword 串），
    共同点是**闸看不懂这条串**，而不是这条串安全。真实 Supabase 的口令就是随机 20 位、
    会出现 `#`，所以第二格不是假想形状。报错不许回显串本身（凭据不入日志）。
    """
    for unreadable in ("postgresql://closenai:p@db.prod.example.com:543x/postgres",
                       "postgresql://closenai:FAKE#pw@db.prod.example.com:5432/postgres",
                       "password=nohost port=5432"):
        assert env_isolation.parse_db_target(unreadable) is None, unreadable
        err = env_isolation.check_db_isolation("staging", unreadable, ["prod.example.com"])
        assert err, f"读不出的 PG 串被放行了：{unreadable}"
        assert "拆不出" in err and "拒绝启动" in err, err
        assert unreadable not in err, f"报错回显了连接串本身：{err}"
    # 这一格的 host 是它自己的、不命中任何指纹 ⇒ 它只能被「读不出」这一支判红。
    # 端口校验被摘掉时 Python 会解析成功然后放行，而 Go 的 url.Parse 直接报错拒启：
    # 同一条串两侧必须同一个结论（2026-10-20 code review 抓到的那一格）。
    own_host_bad_port = "postgresql://closenai:p@postgres.staging.internal:543x/closenai_staging"
    assert env_isolation.check_db_isolation("staging", own_host_bad_port, ["prod.example.com"]), \
        "坏端口＋不命中黑名单的串被放行了：那说明端口校验那一格没了"


def test_sqlite_string_is_not_this_gates_business():
    """可用性证人：非 PG 串（本机 dev 的 sqlite 文件库）不许由这道闸报错。

    「读不出一律拒启」那种写法会连这一格一起吃掉——生产档缺 PG 会在真正的建连处报更准的错，
    而本机 dev 与 alembic 的 sqlite 路径第一天就起不来，第二天就有人把整道闸注释掉。
    """
    url = "sqlite+aiosqlite:///./closenai.db"
    assert env_isolation.parse_db_target(url) is None
    assert not env_isolation.looks_like_pg_target(url)
    assert env_isolation.check_db_isolation("development", url, ["prod.example.com"]) is None
    assert env_isolation.check_db_isolation("staging", url, ["prod.example.com"]) is None


def test_keyword_value_dsn_is_checked_both_directions():
    """keyword=value 形状的**判据级**证人，两向都有（只钉一个方向就能被「一律拒」骗过去）。

    pgconn.ParseConfig 的文档逐字写着「connString may either be in URL format or
    keyword = value format」，所以这种串在 Go 侧照样建得了连；只认 URL 的判据对它一个字
    读不出来然后放行＝staging 档过闸连上生产库。反向那半句同样必须钉住：指自己库的
    keyword 串要放行，否则「凡是 keyword 串都拒」的写法也算过了这一条。
    """
    refused = env_isolation.check_db_isolation(
        "staging",
        f"host=db.prod.example.com dbname=postgres user=closenai password={LEAK}",
        ["prod.example.com"])
    assert refused, "keyword 形状连到生产库却被放行"
    assert LEAK not in refused, f"报错回显了口令：{refused}"
    assert env_isolation.check_db_isolation(
        "staging",
        "host=postgres.staging.internal dbname=closenai_staging user=closenai password=x",
        ["prod.example.com", "closenai_prod"]) is None, \
        "staging 连自己的库（keyword 形状）被拒了"


def test_multi_host_shape_is_compared_per_entry():
    """`host=a,b` 那种 HA 写法：整串逐字比会让生产那一格静默漏过。

    指纹写**逐字 host**（`db.prod.example.com`）而不是域根：写 `prod.example.com` 时
    「整串按后缀」那条规则会把逗号串也判中，摘掉拆分照样绿——P12 第一次就是这么等价掉的
    （2026-10-20 变异自证）。
    """
    fp = "db.prod.example.com"
    target = env_isolation.DBTarget(host="db.staging.internal,db.prod.example.com",
                                    dbname="app", user="u")
    assert env_isolation.match_fingerprint(fp, target)
    clean = env_isolation.DBTarget(host="db.staging.internal,db.other.example.net",
                                   dbname="app", user="u")
    assert not env_isolation.match_fingerprint(fp, clean), \
        "多 host 全不命中却拒了：误伤的方向是运维绕闸"


def test_environment_read_is_unset_vs_empty_on_both_sides():
    """词表的读法必须两侧同判：**没设**才回落 development，**设成空串**算词表外。

    Go 侧原先用 `envString`（`ok && v != ""`），于是 `ENVIRONMENT=` 回落成 development，
    那台机器既不是受保护档（X-User-Id 设备身份透传），黑名单为空时连库面都不判；
    而 pydantic 对同一个 env 读到的是 ""，走拒启。两侧对同一条 env 给两个结论时，
    **fail-open 那一侧就是实际生效的那一侧**（2026-10-20 code review 抓到）。
    """
    # Python 这一侧的实测：显式空串不会偷偷变成 development。
    from backend.config import Settings

    saved = os.environ.get("ENVIRONMENT")
    try:
        os.environ["ENVIRONMENT"] = ""
        assert Settings(_env_file=None).environment == ""
        os.environ.pop("ENVIRONMENT")
        assert Settings(_env_file=None).environment == "development"
    finally:
        if saved is None:
            os.environ.pop("ENVIRONMENT", None)
        else:
            os.environ["ENVIRONMENT"] = saved

    # Go 那一侧只能读源文本（本机跑 `go test` 会让这条闸依赖 Go 工具链）。
    # 读的必须是 envSetString 的**函数体**而不是那个调用点：调用点写得再对，
    # 体内补一句 `&& v != ""` 就退回「空串算没设」了——第一版证人正是这样漏掉 P19 的。
    go_src = GO_CONFIG.read_text(encoding="utf-8")
    m = re.search(r"func envSetString\([^)]*\) string \{\n(.*?)\n\}", go_src, re.DOTALL)
    assert m, "config.go 里找不到 func envSetString：ENVIRONMENT 的读法换了形状，本闸会空转"
    body = m.group(1)
    assert 'if v, ok := os.LookupEnv(key); ok {\n\t\treturn v' in body, (
        "Go 侧的 envSetString 又开始把空串当没设了：那会把「设成空」回落成 development，"
        "而 pydantic 读到的是空串、按词表外拒启（两侧两个结论时 fail-open 那侧生效）")
    assert "return fallback" in body, "未设时也必须能回落到 development（本机 dev 的常态）"
    assert 'Environment:            envSetString("ENVIRONMENT"' in go_src, (
        "ENVIRONMENT 没走 envSetString：读法换了消费点")


def test_staging_pointed_at_production_url_fails_fast(monkeypatch):
    """§验收 第一句的 Python 侧靶：走真入口 `_validate_startup_config`，不是判据单测。"""
    monkeypatch.setattr(settings, "environment", "staging")
    monkeypatch.setattr(settings, "jwt_secret", "s3cret")
    monkeypatch.setattr(settings, "cron_secret", "cron-s3cret")
    monkeypatch.setattr(settings, "prod_db_fingerprints", "db.closenai-prod.supabase.co")
    monkeypatch.setattr(settings, "database_url",
                        f"postgresql+psycopg://closenai:{LEAK}@db.closenai-prod.supabase.co:5432/postgres")
    with pytest.raises(RuntimeError) as exc:
        _validate_startup_config()
    msg = str(exc.value)
    for want in ("staging", "db.closenai-prod.supabase.co", "PROD_DB_FINGERPRINTS", "host="):
        assert want in msg, f"报错要说清 {want}：{msg}"
    assert LEAK not in msg, f"启动失败的报错里出现了口令：{msg}"


def test_staging_without_fingerprints_fails_fast(monkeypatch):
    """D-T604-6 的 Python 侧：staging 没配黑名单＝这道闸一条都不判＝拒启。"""
    monkeypatch.setattr(settings, "environment", "staging")
    monkeypatch.setattr(settings, "jwt_secret", "s3cret")
    monkeypatch.setattr(settings, "cron_secret", "cron-s3cret")
    monkeypatch.setattr(settings, "prod_db_fingerprints", "")
    monkeypatch.setattr(settings, "database_url",
                        "postgresql+psycopg://closenai:p@postgres.staging.internal:5432/closenai_staging")
    with pytest.raises(RuntimeError, match="PROD_DB_FINGERPRINTS"):
        _validate_startup_config()


def test_unknown_environment_fails_fast(monkeypatch):
    """词表外的值在 Python 侧也必须起不来（D-T604-2，与 Go 的 Load 同向）。"""
    monkeypatch.setattr(settings, "environment", "Production")
    monkeypatch.setattr(settings, "jwt_secret", "s3cret")
    monkeypatch.setattr(settings, "cron_secret", "cron-s3cret")
    monkeypatch.setattr(settings, "prod_db_fingerprints", "")
    monkeypatch.setattr(settings, "database_url",
                        "postgresql+psycopg://closenai:p@db.prod.example.com:5432/postgres")
    with pytest.raises(RuntimeError, match="ENVIRONMENT"):
        _validate_startup_config()


def test_production_tier_still_boots(monkeypatch):
    """正向证人：生产档连自己的库、带齐密钥，必须过得去——
    否则这道闸上线第一天就会被整条注释掉（那比没有闸更糟）。"""
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "jwt_secret", "s3cret")
    monkeypatch.setattr(settings, "cron_secret", "cron-s3cret")
    monkeypatch.setattr(settings, "prod_db_fingerprints", "db.prod.example.com")
    monkeypatch.setattr(settings, "database_url",
                        f"postgresql+psycopg://closenai:{LEAK}@db.prod.example.com:5432/postgres")
    _validate_startup_config()  # 不抛异常即通过


def test_startup_validation_wiring_uses_the_shared_module():
    """装配证人：两个消费点必须读 `env_isolation`，不许在各自文件里再搓一份判据。

    这一条是「两侧不许同源」在本仓的第二种坏法：同一侧的**两个函数**各抄一份词表，
    改一处另一处静默不动——`_auth_enforced` 少一个 staging 就是设备身份透传。
    """
    main_src = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    auth_src = (ROOT / "backend" / "core" / "auth.py").read_text(encoding="utf-8")

    assert "env_isolation.check_db_isolation(" in main_src, (
        "main.py 不再调用隔离判据：那道闸只剩 Go 一侧，py-api 连错库没人管")
    assert "env_isolation.is_protected(" in main_src, (
        "main.py 的缺密钥 fail-fast 又回到只认 production 了（D-T604-3）")
    assert re.search(r"environment\.lower\(\)\s+in\s*\(", main_src) is None, (
        "main.py 里又出现 environment.lower() 的成员判断：绕过词表的那道缝（D-T604-2）")

    assert "env_isolation.is_protected(settings.environment)" in auth_src, (
        "core/auth.py 的强制鉴权没接 is_protected()：staging 会落到开发档，"
        "X-User-Id 直接当身份——那比本票要防的双写更糟")
    assert 'settings.environment.lower() in ("production", "prod")' not in auth_src, (
        "core/auth.py 又读回旧的那句了：它不含 staging，且大小写不敏感会吞掉拼错的那一档")


def test_database_engine_site_calls_the_gate_before_creating_engine():
    """装配证人（P1-4 之一）：`backend/database.py` 建引擎那行**之前**必须有闸。

    判据原先只有一个消费点 `main.py::_validate_startup_config`。而引擎在
    `backend/database.py` 的模块顶层就建了——migrate job、ai-rpc、py-ai-worker 三条入口
    各自 import 引擎，一辈子走不到 FastAPI 的 startup 钩子，那三条上「staging 连生产库」
    没人拦。闸落在建连处，且必须在 `create_async_engine` **之前**：那句调用落在建连之后
    就只是事后追认，拦不住任何人。
    """
    src = (ROOT / "backend" / "database.py").read_text(encoding="utf-8")
    assert "env_isolation.require_db_isolation(" in src, (
        "database.py 不再调用库面闸：staging 连生产库会在没有任何报错的情况下建连")
    assert src.index("env_isolation.require_db_isolation(") < src.index("create_async_engine("), (
        "database.py 的闸挪到了 create_async_engine 之后：那条调用已经拦不住建连了")


def test_alembic_env_calls_the_gate_itself():
    """装配证人（P1-4 之二）：migrate 入口**自己**调一次，不靠 models 顺手 import 到引擎模块。

    alembic 建的是它**自己的** engine（下面 `async_engine_from_config`，NullPool），
    与 py-api 那条连接不是同一个对象；而 migrate 是「连上生产库并改它的结构」那一步，
    DDL 比业务写更难回滚。只靠 `from backend.models import Base` 那条 import 链把闸带进来，
    等于把这道防线挂在「没人删这行导入」上——那条 import 一旦漂了（改用 sync env.py、
    或有人按 lint 建议删掉「未使用」导入），闸就静默消失。
    """
    src = (ROOT / "backend" / "alembic" / "env.py").read_text(encoding="utf-8")
    assert "env_isolation.require_db_isolation(" in src, (
        "alembic/env.py 不再自己调用库面闸：migrate 这一刀只挂在 models 那条 import 上")
    assert src.index("env_isolation.require_db_isolation(") < src.index(
        "async_engine_from_config("), "alembic/env.py 的闸挪到了建 engine 之后"


def test_importing_database_refuses_staging_pointed_at_production(tmp_path):
    """§验收 第一句在 Python 侧最硬的一条靶：**真进程 import 真模块**，不是 monkeypatch。

    这条跑的是 `import backend.database` 这条**没有 FastAPI 参与**的路径（migrate job 与
    worker 就是这么起身的），所以 main.py 的 startup 闸红不红都与它无关。
    断言只看两件事：进程非零退出，且报错说清了是哪一档、哪个 host、哪条指纹。
    """
    env = {
        **os.environ,
        "ENVIRONMENT": "staging",
        "DATABASE_URL": f"postgresql+psycopg://closenai:{LEAK}@db.prod.example.com:5432/postgres",
        "PROD_DB_FINGERPRINTS": "prod.example.com",
        "PYTHONPATH": str(ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    proc = subprocess.run(
        [sys.executable, "-c", "import backend.database"],
        env=env, cwd=str(tmp_path), capture_output=True, text=True, timeout=120)
    assert proc.returncode != 0, (
        f"staging 指到生产串却 import 成功了（闸没在建连处）：{proc.stdout[-400:]}")
    combined = proc.stdout + proc.stderr
    for want in ("staging", "db.prod.example.com", "PROD_DB_FINGERPRINTS", "host="):
        assert want in combined, f"报错要说清 {want}：{combined[-600:]}"
    assert LEAK not in combined, f"启动失败的报错里出现了口令：{combined[-600:]}"
    # 它必须是被这道闸拦下的，不是 sqlalchemy 连不上库那种事后错误。
    assert "RuntimeError" in combined, combined[-600:]


def test_importing_database_still_boots_for_local_sqlite(tmp_path):
    """正向证人：本机 dev（未设 ENVIRONMENT ＋ sqlite 串）必须 import 得动。

    这条红了不是安全，是第一天有人绕闸——migrate job 与 dev 编排都走这一条路径。
    """
    env = {**os.environ,
           "ENVIRONMENT": "development",
           "DATABASE_URL": "sqlite+aiosqlite:///./closenai.db",
           "PROD_DB_FINGERPRINTS": "",
           "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run([sys.executable, "-c", "import backend.database"],
                          env=env, cwd=str(tmp_path), capture_output=True, text=True,
                          timeout=120)
    assert proc.returncode == 0, f"本机 dev 起不来了：{proc.stderr[-600:]}"


def test_prod_db_fingerprints_is_a_shared_cross_language_key():
    """`PROD_DB_FINGERPRINTS` 两侧都有读数点，且它**不带** CLOSEN_ 前缀（D-T604-4）。

    带前缀在这个仓里的含义是「只有 Go 认得的键」（对照 CLOSEN_PY_API_URL 与
    config.go 里 GoServiceToken 那条注释）；这个键 Python 必须也认，
    所以它按 GO_SERVICE_TOKEN 的先例命名。
    """
    go_src = GO_CONFIG.read_text(encoding="utf-8")
    assert 'envList("PROD_DB_FINGERPRINTS")' in go_src, "Go 侧读数点漂了"
    assert "CLOSEN_PROD_DB_FINGERPRINTS" not in go_src, (
        "这个键不该带 CLOSEN_ 前缀：那是「只有 Go 认得」的记号，py-api 也要读它")
    py_cfg = (ROOT / "backend" / "config.py").read_text(encoding="utf-8")
    assert "\n    prod_db_fingerprints: str" in py_cfg, (
        "backend/config.py 没有这个**字段**：pydantic 的 env 映射是字段名大写，"
        "没有字段就读不到这个键。判行首缩进而不是判子串——`closen_prod_db_fingerprints` "
        "那种加前缀的改名子串仍在，读的 env 键却已经换了（Go 侧证人同判）")
    assert "CLOSEN_PROD_DB_FINGERPRINTS" not in py_cfg, (
        "Python 侧把指纹键写成带 CLOSEN_ 前缀＝只有 py-api 这一侧读不到它")


def test_staging_overlay_names_the_database_key_differently():
    """部署面：staging 的 overlay 不许复用生产那把库键名，且黑名单必须是**必填**。

    工单 §为什么 画的事故形状是「两套编排的变量指向同一个库」。这道防法把
    「可指错的键名」都不给：overlay 只能引用 `STAGING_DATABASE_URL`，
    引用 `DATABASE_URL` 就是让同一个值有两个去处，那才是双写的入口。
    `PROD_DB_FINGERPRINTS` 那面必须是 `:?`（缺了 compose 直接报错），
    写成 `:-` 或干脆不写＝这一档带着空黑名单起来，而空黑名单＝一条都不判（D-T604-6）。
    """
    overlay = (ROOT / "deploy" / "compose" / "docker-compose.staging.yml").read_text(
        encoding="utf-8")
    assert "${STAGING_DATABASE_URL" in overlay, "overlay 不再引用 staging 自己的库键名"
    assert "${DATABASE_URL" not in overlay, (
        "overlay 里出现了生产的库键名 ${DATABASE_URL}：两套编排共用一个变量＝双写的形状")
    for service in ("py-api", "go-api"):
        block = _service_block(overlay, service)
        assert "ENVIRONMENT: staging" in block, f"{service} 没被这一档覆写成 staging"
        assert "PROD_DB_FINGERPRINTS: ${PROD_DB_FINGERPRINTS:?" in block, (
            f"{service} 的 PROD_DB_FINGERPRINTS 不是必填：空黑名单会让这道闸一条都不判")


def _service_block(text: str, service: str) -> str:
    """取某个服务自己的那段 overlay（按两空格缩进的顶层键切，不靠 yaml 解析猜结构）。"""
    m = re.search(rf"^  {re.escape(service)}:\n(.*?)(?=^  \S|\Z)", text,
                  re.MULTILINE | re.DOTALL)
    assert m, f"overlay 里没有 {service} 这一段：这一档的覆写形状变了，本闸会空转"
    return m.group(1)


def test_env_isolation_case_table_is_the_only_spec():
    """用例表是**规格**：它不属于任何一侧，两侧各自实现、各自跑它。

    这条守的是对账关系本身——如果哪天有人把用例表搬进 backend/tests/（Python 独占）
    或在 Go 里硬编码同样的格，跨语言对账就退化成「各自测自己」，
    而这正是本仓「断言两侧不许同源」要消灭的形状。
    """
    assert CASES.exists() and CASES.parent.name == "testdata"
    doc = json.loads(CASES.read_text(encoding="utf-8"))
    assert "cases" in doc and doc.get("_about"), "用例表少了 _about 那半：没人说明它约束的是两侧"
    assert doc.get("_why_shared"), "用例表没说清两侧各自实现这件事"
    for case in doc["cases"]:
        assert {"name", "environment", "database_url", "fingerprints", "expect", "reason"} \
            <= set(case), f"用例表某一行缺列：{case.get('name')}"
