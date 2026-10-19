"""T6-04（2026-10-20）· 运行环境词表与「非生产档不许碰生产库」这道闸的 Python 侧。

判据本体在 `go-core/internal/config/env_isolation.go`，这一份是**另一侧的实现**，
不是它的翻译——本仓的铁律是「断言两侧不许同源」，所以两侧各写各的，
由 `go-core/testdata/env_isolation_cases.json` 这份**用例表**（规格）对账：
任何一侧改了判据而没改规格，另一侧的闸就红。对账面在
`backend/tests/test_env_isolation_cross_language.py`。

为什么 Python 也要有一份：py-api 是 canonical 的**写方**。工单 §为什么 画的事故是
「staging 的 Go 连上生产库并调高切流 ⇒ 与生产 Python 双写」，那只挡 Go 等于把门开了一半——
staging 的 py-api 自己连上生产库，一样在双写，而且不需要任何百分比。

三条口径（决策表 D-T604-2/3/5/6，逐字在 CLOSENAI_V3_TASK_BOARD.md 的 T6-04 节）：
  · ``ENVIRONMENT`` 只认四个逐字小写值；``Production`` 是拼错，不是「大小写不敏感」。
  · ``staging`` 与 production 同为**受保护档**：强制 JWT、缺密钥拒启（D-T604-3）。
  · 报错只回显 host/dbname——连接串里那段是口令，日志不是它的去处。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

# 词表与 Go 侧 config.EnvironmentValues 同集合（跨语言闸逐字比这两个）。
ENVIRONMENT_VALUES = ("development", "staging", "production", "prod")
# 受保护档＝「这一档必须像生产一样严格」。staging 必须在里面，
# 否则设成 staging 换来的是 development 的设备身份透传口径（X-User-Id 直接当身份）。
PROTECTED_ENVIRONMENTS = ("staging", "production", "prod")

_PG_SCHEME = re.compile(r"^postgres(?:ql)?(?:\+[^:/]*)?://")


def is_valid_environment(value: str) -> bool:
    return value in ENVIRONMENT_VALUES


def is_production(value: str) -> bool:
    """逐字判「这一档就是生产」（与 Go 的 IsProductionEnv 同集合）。

    刻意不做 ``.lower()``：原先 Python 用 ``environment.lower() in ("production","prod")``，
    于是 ``ENVIRONMENT=Production`` 在 Python 侧算生产、在 Go 侧不算——同一份 ConfigMap
    喂出两套鉴权档。本票把两侧收成同一张逐字小写的表（D-T604-2）。
    """
    return value in ("production", "prod")


def is_protected(value: str) -> bool:
    return value in PROTECTED_ENVIRONMENTS


def parse_fingerprints(raw: str | None) -> list[str]:
    """``PROD_DB_FINGERPRINTS`` 的拆法：逗号分隔、逐项去空白、丢掉空项。

    空白不是指纹。把 ``" , , "`` 当成两条指纹会让任何 host 都命中——
    那根闸就变成了永久拒启，第一天就有人把它整条注释掉。
    """
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class DBTarget:
    """连接串里可比对的那一半。**永不含口令**，user 只参与匹配、不进报错文本。"""

    host: str
    dbname: str
    user: str


def parse_db_target(url: str) -> DBTarget | None:
    """拆一条 PG 串；不是 PG 串（sqlite 本机文件库）返回 None。

    None 不是「命中」也不是「没命中」，是「这条串没有可比对的 host」：
    生产档缺 PG 会在真正的建连处失败，那儿的报错比这里更准（与 Go 侧同口径）。
    """
    if not _PG_SCHEME.match(url or ""):
        return None
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    name = (parts.path or "").lstrip("/")
    if "/" in name:
        name = name.split("/")[0]
    user = (parts.username or "").lower()
    return DBTarget(host=host, dbname=name.lower(), user=user)


def match_fingerprint(fingerprint: str, target: DBTarget) -> bool:
    """带点的指纹按 host 形状判（逐字／后缀／dbname 逐字）；
    不带点的裸令牌（project ref、库名片段）按子串判 host/dbname/用户名。

    为什么裸令牌要开子串、带点的不开：见 Go 侧同函数的注释与用例表
    `staging_project_ref裸令牌命中用户名` 那一格——Supabase pooler 的 project ref
    只长在用户名里。漏判的方向是双写，误判的方向只是拒启，本闸一律选后者。
    """
    fp = (fingerprint or "").strip().lower()
    if not fp or not (target.host or target.dbname or target.user):
        return False
    if "." in fp:
        return fp in (target.host, target.dbname) or target.host.endswith(f".{fp}")
    return any(fp in part for part in (target.host, target.dbname, target.user))


def check_db_isolation(environment: str, database_url: str,
                       fingerprints: list[str]) -> str | None:
    """返回错误消息；``None`` ＝这一格合法。判据与 Go 的 CheckDBIsolation 逐格对齐。"""
    if not is_valid_environment(environment):
        return (
            f"unsupported ENVIRONMENT {environment!r}: only "
            f"{'|'.join(ENVIRONMENT_VALUES)} are accepted（词表外的值必须拒绝启动，D-T604-2）"
        )
    if is_production(environment):
        return None  # 生产档连自己的库是应有之义，黑名单对它不参与比较。
    if not fingerprints:
        if environment == "staging":
            return (
                "ENVIRONMENT=staging 必须配 PROD_DB_FINGERPRINTS（逗号分隔的生产库 "
                "host／dbname／project ref）：黑名单为空＝这道闸一条都不判（D-T604-6）"
            )
        return None  # development 不配也要能把本机编排起起来，否则第一天就有人绕闸。
    target = parse_db_target(database_url)
    if target is None:
        return None
    for fp in fingerprints:
        if match_fingerprint(fp, target):
            return (
                f"ENVIRONMENT={environment} 不许连 host={target.host!r} "
                f"dbname={target.dbname!r}：它命中了生产库指纹 {fp!r}"
                "（PROD_DB_FINGERPRINTS）。两套进程写同一个 canonical 库＝两份都看着对的账"
                "（Baseline §22 风险 3）。改法：把这一档的 DATABASE_URL 指到它自己的库，"
                "或按双签口径把 ENVIRONMENT 设成 production"
            )
    return None
