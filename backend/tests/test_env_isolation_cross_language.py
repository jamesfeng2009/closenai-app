"""T6-04 的跨语言闸：库面隔离与运行环境词表，两侧各一份实现、一张用例表。

两侧不许同源（本仓铁律）。这份文件里三类读法都不同源：
· Go 的词表 ← 从 `go-core/internal/config/config.go` 的源文本里抓切片字面量；
· Python 的词表 ← 从 `backend/core/env_isolation.py` 的模块对象上读元组常量；
· 判据的规格 ← `go-core/testdata/env_isolation_cases.json`，一张**手工维护的用例表**
  （它不是任何一侧的输出，两侧各自跑它；改判据不改表，另一侧就红）。

以及装配证人（判据写得再对，没人调它等于没有）：
· `backend/main.py::_validate_startup_config` 必须真的调用词表校验与隔离校验；
· `backend/core/auth.py::_auth_enforced` 必须读 `is_protected()`——staging 那一档
  接错的后果是设备身份透传（X-User-Id 当身份），比它要防的双写更糟。

§验收 第一句「把 staging 指到生产串 ⇒ 启动失败并有明确报错」在 Python 侧的靶是
`test_staging_pointed_at_production_url_fails_fast`：它跑的是真入口函数，不是判据单测。
"""
from __future__ import annotations

import json
import re
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
    assert expects.count("allow") >= 4, f"allow 只有 {expects.count('allow')} 格"
    assert expects.count("refuse") >= 6, f"refuse 只有 {expects.count('refuse')} 格"
    named = {c["name"] for c in _cases()}
    for must_have in ("staging_host逐字命中生产host", "staging_黑名单为空拒启",
                      "production_连自己的库必须放行", "development_黑名单为空放行"):
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
    assert "prod_db_fingerprints" in (ROOT / "backend" / "config.py").read_text(encoding="utf-8"), (
        "backend/config.py 没有对应字段：pydantic 的 env 映射是字段名大写，"
        "没有字段就读不到这个键")


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
