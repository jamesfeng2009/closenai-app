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

_PG_SCHEME = re.compile(r"^postgres(?:ql)?(?:\+[^:/]*)?://", re.IGNORECASE)
# libpq 的 keyword=value 形状（`host=… dbname=… user=…`）。这不是假想形状：
# pgx 的 ParseConfig 文档逐字写着它同时支持 URL 与 keyword=value 两种，
# 所以这种串在 Go 侧照样建得了连——只认 URL 的判据对它一个字都读不出来，
# 方向就是 staging 档过闸连上生产库（2026-10-20 code review 抓到）。
# Python 侧 SQLAlchemy 不接这种形状，但**判据必须两侧同判**：
# 用例表里那一格喂给两侧的必须是同一个结论。
_DSN_KEYWORD = re.compile(r"^[a-z_][a-z0-9_]*=", re.IGNORECASE)
_DSN_VALUE_TPL = r"(?:^|\s){key}\s*=\s*(?:'([^']*)'|([^\s]*))"


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


def dsn_value(raw: str, key: str) -> str:
    """取 keyword=value 形状里某个键的值；带单引号的值允许含空格。读不到给空串。"""
    m = re.search(_DSN_VALUE_TPL.format(key=re.escape(key)), raw or "", re.IGNORECASE)
    if not m:
        return ""
    return m.group(1) if m.group(1) else m.group(2)


def looks_like_pg_target(raw: str) -> bool:
    """判「这条串是不是一条该拿来比对的库串」（两种 PG 形状都算）。

    调用方用它把 `parse_db_target` 的两种 None 分开：sqlite 那种本机文件库确实没有 host
    可比（放行，让真正的建连处去报更准的错），而「是 PG 串但读不出可比字段」必须拒启——
    读不出来的方向是双写，误伤的方向只是起不来（D-T604-5）。
    """
    if not raw:
        return False
    return bool(_PG_SCHEME.match(raw) or _DSN_KEYWORD.match(raw.strip()))


@dataclass(frozen=True)
class DBTarget:
    """连接串里可比对的那一半。**永不含口令**，user 只参与匹配、不进报错文本。"""

    host: str
    dbname: str
    user: str


def parse_db_target(url: str) -> DBTarget | None:
    """拆一条 PG 串，认两种形状：URL 形式与 libpq 的 keyword=value 形式。

    返回 None 有两种含义，调用方用 `looks_like_pg_target` 分开：
    · 这不是库串可比对的形状（sqlite 本机文件库）⇒ 那一种应当放行；
    · 它是 PG 串但读不出可比字段（坏端口、`#` 把口令后面整段吃成 fragment、
      keyword 串里一个 host/dbname/user 都没有）⇒ 那一种必须拒启。
    第二种是 2026-10-20 code review 抓到的 fail-open：旧实现把两种 None 一律放行，
    于是「闸没看懂那条串」换成了「进程连上它黑名单里那个库」。
    """
    raw = url or ""
    if not _PG_SCHEME.match(raw):
        if not _DSN_KEYWORD.match(raw.strip()):
            return None
        t = DBTarget(host=dsn_value(raw, "host").lower(),
                     dbname=dsn_value(raw, "dbname").lower(),
                     user=dsn_value(raw, "user").lower())
        # 三个键一个都读不出来时不算 target；但只要有一个就读，哪怕是个空值也照样返回
        # （由 match_fingerprint 的空表守卫判「不可比对」）。
        if not (t.host or t.dbname or t.user):
            return None
        return t
    parts = urlsplit(raw)
    try:
        # 端口只在 .port 上校验（.hostname 不报）。Go 的 net/url.Parse 对 `:543x` 直接
        # parse error ⇒ 那一侧走「读不出」分支。这里把它对齐成同一种 None，否则同一条串
        # 在 Go 拒启、在 Python 连上——用例表里那一格两侧就给出两个结论。
        parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    name = (parts.path or "").lstrip("/")
    if "/" in name:
        name = name.split("/")[0]
    user = (parts.username or "").lower()
    if not (host or name or user):
        # `#` 那条形状：口令里带 # 时，后半段被当成 fragment，三个字段全空。
        # 返回 None 让调用方按「PG 串但读不出」拒启（与 Go 同一格）。
        return None
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
        # host 按逗号拆开逐个比：libpq 允许 `host=a,b` 那种多 host（HA）写法，pgx 的
        # ParseConfig 文档里逐字有这一条。整串逐字比会让
        # `host=staging.internal,db.prod.example.com` 静默漏过——漏过的方向是双写，
        # 拆开最多多拒一次，仍是本闸的选择（与 Go 的 MatchFingerprint 同一格）。
        for host in (h.strip() for h in target.host.split(",")):
            if host and (host == fp or host.endswith(f".{fp}")):
                return True
        return target.dbname == fp
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
        # 旧版这一格直接放行：拆不出来就当没这事。方向错了——拆不出来有两种，
        # sqlite 那种本机文件库该放行，「是 PG 串但形状读不出」必须拒启。
        # 后者放行的话，进程会连上它黑名单里那个库，只是这道闸没看懂那条串
        # （2026-10-20 code review 抓到）。
        if looks_like_pg_target(database_url):
            return (
                f"ENVIRONMENT={environment} 的 DATABASE_URL 是一条 PG 库串"
                "（postgres(ql):// 那种 URL 形状，或 libpq 的 keyword=value 形状），"
                "但拆不出 host/dbname/用户名，无法与 PROD_DB_FINGERPRINTS 比对 ⇒ 拒绝启动。"
                "读不出来的方向是两套进程写同一个 canonical 库，多拒一次的方向只是起不来"
                f"（D-T604-5）；本条不回显连接串本身。改法：把它写成 "
                "`postgres://user@host:5432/dbname` 那种标准形状，"
                "或按双签口径把 ENVIRONMENT 设成 production"
            )
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


def require_db_isolation(environment: str, database_url: str,
                         fingerprints_raw: str | None) -> None:
    """装配面调的那一句：判据没过就 ``RuntimeError``（与 Go 的 ``Config.RequireDBIsolation`` 同格）。

    为什么要有这一句、而不是每个入口自己 ``check`` ＋ ``if``：本仓抓过的装配缺陷全是
    「判据写对了、某个入口忘了调」。原先只有 `main.py::_validate_startup_config` 调它，
    而 **建连的那一行**在 `backend/database.py` 的模块顶层——migrate job、ai-rpc、
    py-ai-worker 三条入口都 import 引擎却走不到 FastAPI 的 startup，那三条上这道闸一个字都没判。
    把闸挪到引擎创建之前、消费点收成一行，「漏调」这个形状就没有了。
    """
    error = check_db_isolation(environment, database_url, parse_fingerprints(fingerprints_raw))
    if error:
        raise RuntimeError(error)
