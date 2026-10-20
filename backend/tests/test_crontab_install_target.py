"""T6-04 §怎么做.3：调度源只属于生产那一台，而且这条判断**有执行者**。

工单的原话是「crontab 只装生产」。这句话今天能被两种方式满足：
1. README 里写一句「注意别装到 staging」——零执行者，复制粘贴那一行的人不会先问环境；
2. 装排程这个动作本身要先过一道档籍判断，不过就非零退出、并且什么都不动。

这里选 2，所以闸要证的不是「文档里有没有这句话」，而是**脚本真会拒绝**。
每条拒启分支都用 subprocess 实跑一遍（`--check`：只判不装，绝不动宿主状态），
每条都配一条正向证人，否则「永远返回非零」的脚本也能让这批用例全绿。

判据来源是宿主 env 文件里的 `ENVIRONMENT=`，与两侧运行时同一张词表
（D-T604-2：逐字小写，`Production` 不在词表里＝拒启，而不是折叠成 production）。
脚本与 crontab 与 README 三方对账：crontab 顶部的 `INSTALL-TARGET: production-only`
标记既是给人看的声明，也是脚本的一条判据（标记没了＝有人改表时把口径删了）。

2026-10-20 code review 补的三条腿（原批都在「判据对、证人不够」这一类）：
· 端口面判的是每条调度行的 **host:port**，不是文件里有没有 `127.0.0.1:` 那串字面量——
  旧判据下 `http://localhost:18000/…` 这种漂法脚本与闸同时看不见，方向是双跑复活；
  配一条反向证人：`localhost:8000`／`[::1]:8000` 是合法写法，不许误伤。
· staging 的端口面有**两条**独立的腿：判展开结果的那条（缺 docker 就 skip）与
  判两份文件节点的那条（`test_staging_overlay_overrides_every_inherited_port_face`，
  不需要 docker）——只留前者的话，CI 上 skip 一次，整套端口判据就退化成读 overlay 文本。
· 展开那条**不按 host_ip 过滤**：prod 的 caddy 写的是裸 `80:80`，展开后没有 host_ip 键，
  过滤等于把公网面挡在断言之外（摘掉 caddy 的 `!override` 时旧断言照样绿）。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCHEDULE_DIR = ROOT / "deploy" / "schedule"
SCRIPT = SCHEDULE_DIR / "install-crontab.sh"
CRONTAB = SCHEDULE_DIR / "crontab"
README = ROOT / "deploy" / "README.md"
COMPOSE_DIR = ROOT / "deploy" / "compose"
STAGING_OVERLAY = COMPOSE_DIR / "docker-compose.staging.yml"
PROD_COMPOSE = COMPOSE_DIR / "docker-compose.prod.yml"
PRODUCTION_PORT = 8000
STAGING_PORT = 18000
# 调度源唯一许可的那一格目标：host 是回环、端口是 prod py-api 的宿主发布口。
PRODUCTION_TARGET = f"127.0.0.1:{PRODUCTION_PORT}"
MARKER = "# INSTALL-TARGET: production-only"
ENVIRONMENT_STAGING = "staging"
# 展开证人用的两个 host 字面量：prod 那一个只许出现在黑名单键里。
PRODUCTION_DB_HOST = "expansion-prod-host"
STAGING_DB_HOST = "expansion-staging-host"
FAKE_PW = "fakePwForExpansionOnly"
# 只用于证「报错不回显凭据」的假值，任何分支都不该把它打到 stderr/stdout。
FAKE_SECRET = "FAKE_pw_not_real_do_not_echo"


def _run(tmp_path: Path, env_value: str | None, table: Path | None = None,
         *, env_file_exists: bool = True,
         locale: str = "C") -> subprocess.CompletedProcess:
    """跑一次 `--check`：判据照跑，安装那一步永远不执行。

    LC_ALL 固定成 "C" 而不是继承本机 locale：这台机器上运维敲下那一行时用什么语言环境
    是不可控的，而 `sh` 对「合法变量名字符」的判断是按字节的——2026-10-20 那条
    `PORTS\xef: unbound variable` 就是这个字节面在 C locale 下才暴露的。
    errors="replace" 让「脚本自己崩了」表现成一条可读的断言失败，而不是一页解码栈。
    """
    env_file = tmp_path / "env"
    if env_file_exists:
        lines = [f"CRON_SECRET={FAKE_SECRET}"]
        if env_value is not None:
            lines.insert(0, f"ENVIRONMENT={env_value}")
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cmd = ["sh", str(SCRIPT), "--check", "--env-file", str(env_file)]
    if table is not None:
        cmd += ["--crontab-file", str(table)]
    return subprocess.run(cmd, capture_output=True, text=True, check=False,
                          encoding="utf-8", errors="replace",
                          env={**os.environ, "LC_ALL": locale, "LANG": locale})


class _ComposeLoader(yaml.SafeLoader):
    """认 compose 的合并标签（`!override`／`!reset`），而且只认这两个。

    刻意列死这张表：以后 overlay 里冒出别的标签，加载会**报错**而不是静默接受。
    需要这个加载器是因为 `!override` 本身——2026-10-20 实跑展开发现 compose 对
    overlay 的 sequence 是**追加**，基线里的 8000 会跟着进 staging。
    """


for _tag in ("!override", "!reset"):
    _ComposeLoader.add_constructor(
        _tag, lambda loader, node: loader.construct_sequence(node, deep=True))


def _table(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "crontab"
    path.write_text(text, encoding="utf-8")
    return path


def _refuses(result: subprocess.CompletedProcess, *needles: str) -> None:
    assert result.returncode != 0, (
        f"这一格本该拒启却通过了：exit={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    blob = result.stdout + result.stderr
    for needle in needles:
        assert needle in blob, f"拒绝的理由不够明确，缺 {needle!r}：{blob!r}"
    assert FAKE_SECRET not in blob, f"报错回显了凭据：{blob!r}"


# ── 判据本体：非生产档装不上调度 ────────────────────────────

@pytest.mark.parametrize("env_value,needle", [
    ("staging", "ENVIRONMENT=staging 不是生产档"),
    ("development", "ENVIRONMENT=development 不是生产档"),
    # D-T604-2：大小写折叠会把「拼错的值」静默当成合法档；这里既不能放行，
    # 也不能被 production 的别名兜住。
    ("Production", "不在词表里"),
    ("prod ", "不在词表里"),
], ids=["staging", "development", "大小写错", "尾随空格"])
def test_installer_refuses_every_non_production_tier(
        tmp_path: Path, env_value: str, needle: str) -> None:
    _refuses(_run(tmp_path, env_value), "拒绝安装", needle)


def test_installer_accepts_production_and_its_alias(tmp_path: Path) -> None:
    """正向证人：只写「拒绝」的那批用例，对一个永远 exit 1 的脚本同样全绿。"""
    for env_value in ("production", "prod"):
        result = _run(tmp_path, env_value)
        assert result.returncode == 0, (
            f"{env_value} 本该放行：{result.stderr!r}")
        assert "--check 不安装" in result.stdout
        assert FAKE_SECRET not in result.stdout + result.stderr


@pytest.mark.parametrize("case", ["missing_file", "missing_line"])
def test_installer_refuses_when_the_tier_is_not_declared(
        tmp_path: Path, case: str) -> None:
    """「读不到档籍」不等于「读到了非生产」，但结论一样：不装。"""
    if case == "missing_file":
        result = _run(tmp_path, "production", env_file_exists=False)
        needle = "没有声明自己是哪一档"
    else:
        result = _run(tmp_path, None)
        needle = "没有 ENVIRONMENT= 那一行"
    _refuses(result, "拒绝安装", needle)


# ── 调度源自己那两条判据（标记 + 端口面）────────────────────

def test_crontab_declares_its_target(tmp_path: Path) -> None:
    """声明与判据是同一行字：改了表没改标记，脚本先拒绝。"""
    text = CRONTAB.read_text(encoding="utf-8")
    assert MARKER in text.splitlines(), f"crontab 顶部的 {MARKER!r} 被删了"
    _refuses(_run(tmp_path, "production",
                  table=_table(tmp_path, text.replace(MARKER + "\n", ""))),
             "拒绝安装", "INSTALL-TARGET: production-only 标记不在了")


def _schedule_lines(text: str) -> list[str]:
    """调度行＝非注释、非空、不是 SHELL/PATH 赋值的那些行（注释不参与执行）。"""
    return [line for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
            and "=" not in line.split()[0]]


def _schedule_targets(text: str) -> set[str]:
    """只看会执行的那几行，取每条 URL 的 authority（host:port）。

    刻意不复用脚本里的 grep/sed：那一侧是 shell、这一侧是 Python，各自从同一批行里
    解析出目标面，「闸认为该打哪」和「这张表实际打哪」才不是同一个来源。
    判 host:port 而不是判端口数字，是因为 `http://localhost:18000/…` 这种写法
    在上一版两侧都读成「没看见」——回环别名漂到 staging 端口时闸是绿的。
    """
    out: set[str] = set()
    for line in _schedule_lines(text):
        for authority in re.findall(r"https?://[^/?#\s\"']+", line):
            out.add(authority.split("://", 1)[1])
    return out


def test_crontab_only_reaches_the_production_port_face(tmp_path: Path) -> None:
    """这张表物理上够不到 staging：staging 的 py-api 不占宿主 8000。

    目标面必须是**单一那一格**，而不许出现任何别的 host 或端口。
    """
    text = CRONTAB.read_text(encoding="utf-8")
    targets = _schedule_targets(text)
    assert targets == {PRODUCTION_TARGET}, f"crontab 的目标面不是单一生产回环格：{targets}"
    assert _schedule_lines(text), "crontab 里一条调度行都没有：调度源被掏空了"
    # 执行者分支①：同一 host、漂到 staging 的宿主端口。
    drifted = _table(tmp_path, text.replace(PRODUCTION_TARGET, f"127.0.0.1:{STAGING_PORT}"))
    _refuses(_run(tmp_path, "production", table=drifted), "拒绝安装", "目标面漂了")
    # 执行者分支②：换一种回环写法漂过去（2026-10-20 code review 抓到的那条盲区，
    # 上一版脚本与这条闸在这一格上同时是绿的）。
    slipped_text = text.replace(
        f"http://{PRODUCTION_TARGET}/api/v1/cron/health",
        f"http://localhost:{STAGING_PORT}/api/v1/cron/health")
    slipped = _table(tmp_path, slipped_text)
    assert _schedule_targets(slipped_text) == {PRODUCTION_TARGET, f"localhost:{STAGING_PORT}"}, (
        "这条用例自己没漂到东西，等于没测")
    _refuses(_run(tmp_path, "production", table=slipped), "拒绝安装", "目标面漂了")


@pytest.mark.parametrize("spelling", [
    f"localhost:{PRODUCTION_PORT}", f"[::1]:{PRODUCTION_PORT}"])
def test_crontab_loopback_aliases_are_not_a_violation(tmp_path: Path, spelling: str) -> None:
    """正向证人：`localhost` 与 `[::1]` 打的是同一个发布面，闸不许在合法写法上拒启。

    少了这条，端口判据就退化成「文件里有没有 `127.0.0.1` 那串字面量」——那是文本 diff，
    不是闸；而它误伤的下一步就是有人把整条判据注释掉。
    """
    text = CRONTAB.read_text(encoding="utf-8")
    rewritten = text.replace(PRODUCTION_TARGET, spelling)
    assert _schedule_targets(rewritten) == {spelling}, "这条用例自己没换干净"
    result = _run(tmp_path, "production", table=_table(tmp_path, rewritten))
    assert result.returncode == 0, f"{spelling} 是合法回环写法却被拒：{result.stderr!r}"


def test_a_comment_about_the_staging_port_is_not_a_violation(tmp_path: Path) -> None:
    """这条闸不是文本 diff：注释里出现 18000 不算「调度源打到了 staging」。

    没有这条证人，上一条判红的方式就退化成「文件里有没有那个字符串」，
    而下一个人改注释时会直接把整条判据注释掉。
    """
    text = CRONTAB.read_text(encoding="utf-8")
    annotated = text.replace(
        "SHELL=/bin/sh\n",
        "# 注意：staging 那套 bind 的是 127.0.0.1:18000，这张表不许指到那里去。\n"
        "SHELL=/bin/sh\n")
    path = _table(tmp_path, annotated)
    assert _schedule_targets(annotated) == {PRODUCTION_TARGET}
    result = _run(tmp_path, "production", table=path)
    assert result.returncode == 0, f"注释里的端口把闸误伤了：{result.stderr!r}"


def test_installer_refuses_a_hollowed_out_table(tmp_path: Path) -> None:
    """端口判据的另一种坏法：把每一行都删掉，只剩注释和标记。"""
    hollow = "# INSTALL-TARGET: production-only\nSHELL=/bin/sh\n"
    _refuses(_run(tmp_path, "production", table=_table(tmp_path, hollow)),
             "拒绝安装", "调度源被掏空了")


@pytest.mark.parametrize("locale", ["C", "en_US.UTF-8"])
def test_every_message_renders_whatever_locale_the_operator_has(
        tmp_path: Path, locale: str) -> None:
    """拒绝的分支不能自己在报错时崩掉。

    这条不是装饰：`sh` 按字节判断变量名字符，`$PORTS）` 里的全角括号首字节在 C locale 下
    会被并进变量名，set -u 直接报 `unbound variable`——判据成立、消息没了，运维看到的
    只有一个退出码（2026-10-20 实跑抓到，修法是把紧跟中文的变量写成 ${NAME}）。
    """
    drifted = _table(tmp_path, CRONTAB.read_text(encoding="utf-8")
                     .replace("127.0.0.1:8000", "127.0.0.1:18000"))
    cases = [
        (_run(tmp_path, "production", locale=locale), "判据通过"),
        (_run(tmp_path, "staging", locale=locale), "不是生产档"),
        (_run(tmp_path, "production", table=drifted, locale=locale), "目标面漂了"),
    ]
    for result, needle in cases:
        blob = result.stdout + result.stderr
        assert "unbound variable" not in blob, f"[{locale}] 报错时自己崩了：{blob!r}"
        assert needle in blob, f"[{locale}] 消息没渲染出来：{blob!r}"


def test_staging_overlay_binds_a_different_host_port(tmp_path: Path) -> None:
    """端口面隔离的另一半：overlay 不给 staging 留 8000/80/443。"""
    doc = yaml.load(STAGING_OVERLAY.read_text(encoding="utf-8"), Loader=_ComposeLoader)
    assert doc["name"] == "closenai-staging"
    py_api = [p for p in doc["services"]["py-api"]["ports"]
              if p.split(":")[0] == "127.0.0.1"]
    host_ports = {p.split(":")[1] for p in py_api}
    assert host_ports == {"18000"}, f"staging 的宿主端口面漂了：{py_api}"
    assert all(p.endswith(":8000") for p in py_api), py_api
    assert doc["services"]["py-api"]["environment"]["ENVIRONMENT"] == ENVIRONMENT_STAGING
    # 整份 overlay 的宿主发布面都要避开生产那三格——只看 py-api 那一格时，
    # caddy 漂回 80/443 这条判据一句话都不会说（2026-10-20 code review 抓到）。
    face = {_host_port(p) for spec in doc["services"].values()
            for p in (spec.get("ports") or [])}
    assert face == {"18000", "18080", "18443"}, f"overlay 自己声明的宿主端口面漂了：{face}"
    assert not face & {"8000", "80", "443"}, face


def _host_port(spec: str) -> str:
    """一条 ports 写法里宿主那一格：`IP:宿主:容器` 取中段，`宿主:容器` 取首段。"""
    parts = str(spec).split(":")
    return parts[1] if len(parts) == 3 else parts[0]


def _ports_nodes(path: Path) -> dict[str, tuple[str, list[str]]]:
    """从 YAML **节点**上取每个服务的 ports ⇒ {服务名: (标签, [原文写法])}。

    用 `yaml.compose` 而不是 `load`：`!override` 是个标签，值面加载会把它抹掉，
    而这条判据判的正是「overlay 有没有用那条标签去接管基线的 sequence」。
    """
    root = yaml.compose(path.read_text(encoding="utf-8"), Loader=yaml.SafeLoader)
    out: dict[str, tuple[str, list[str]]] = {}

    def _mapping(node: yaml.Node) -> dict[str, yaml.Node]:
        if not isinstance(node, yaml.MappingNode):
            raise AssertionError(f"{path.name} 的结构不是预期的映射：{type(node)}")
        return {str(k.value): v for k, v in node.value}

    services = _mapping(root).get("services")
    if not isinstance(services, yaml.MappingNode):
        return out
    for name, spec in _mapping(services).items():
        for key, node in _mapping(spec).items():
            if key == "ports":
                assert isinstance(node, yaml.SequenceNode), f"{name} 的 ports 不是列表"
                out[name] = (node.tag, [str(item.value) for item in node.value])
    return out


def test_staging_overlay_overrides_every_inherited_port_face() -> None:
    """不需要 docker 的那条腿：基线每发布一格宿主端口，overlay 就必须带 `!override` 接管它。

    为什么还要一条静态判据：展开那条在没装 docker 的环境里是 skip，而 skip 不算绿——
    那时端口面的判据会整批退化成「读 overlay 自己的文本」，正好是 compose
    「对 overlay 的 sequence 是追加」看不见的那类坏法。这条把两份文件的节点摆在一起判：
    凡 prod 发布过宿主端口的服务，staging overlay 必须①点名它、②用 `!override`、
    ③不重复基线任何一个宿主端口。三条里任意一条没做到，展开结果里就留着生产那一格。
    """
    baseline = _ports_nodes(PROD_COMPOSE)
    overlay = _ports_nodes(STAGING_OVERLAY)
    assert baseline, "prod 基线一个宿主端口都不发布：这条判据扫的是空集，不算证人"
    for name, (_base_tag, base_specs) in sorted(baseline.items()):
        base_ports = {_host_port(p) for p in base_specs}
        assert name in overlay, (
            f"prod 在 {name} 上发布 {sorted(base_ports)}，而 staging overlay 没接管这个服务："
            "compose 会原样继承，staging 起来就占着生产那一格")
        got_tag, got_specs = overlay[name]
        assert got_tag == "!override", (
            f"{name} 的 ports 没写 `!override`（实际标签 {got_tag}）：compose 对 overlay 的 "
            f"sequence 是**追加**而不是替换，基线那 {sorted(base_ports)} 会跟着进展开")
        leaked = base_ports & {_host_port(p) for p in got_specs}
        assert not leaked, f"{name} 在 overlay 里又发布了一遍基线端口：{sorted(leaked)}"


def _compose_env(tmp_path: Path) -> Path:
    """拼一次展开要用的 env：两份 example 的非注释行 ＋ prod 基线必需的宿主变量假值。

    期望值全部来自这里的字面量与这份 env 本身，不取自 overlay——否则「overlay 写了什么」
    就和「闸认为 overlay 该写什么」同源，overlay 少一条判据时闸会跟着少一条。
    """
    lines: list[str] = []
    for name in (".env.example", ".env.staging.example"):
        text = (COMPOSE_DIR / name).read_text(encoding="utf-8")
        lines += [ln.strip() for ln in text.splitlines()
                  if ln.strip() and not ln.lstrip().startswith("#") and "=" in ln]
    env = {ln.split("=", 1)[0]: ln.split("=", 1)[1] for ln in lines}
    # 这两个键名就是本票的防法本身：staging 只许读 STAGING_DATABASE_URL。
    env["DATABASE_URL"] = f"postgres://svc:{FAKE_PW}@{PRODUCTION_DB_HOST}.example:5432/postgres"
    env["STAGING_DATABASE_URL"] = (
        f"postgres://svc:{FAKE_PW}@{STAGING_DB_HOST}.example:5432/closenai_staging")
    env["PROD_DB_FINGERPRINTS"] = f"{PRODUCTION_DB_HOST}.example,postgres"
    env.setdefault("RABBITMQ_USER", "svc")
    env.setdefault("RABBITMQ_PASSWORD", FAKE_PW)
    env.setdefault("PY_IMAGE", "closenai-py-api:expansion-witness")
    env.setdefault("GO_IMAGE_TAG", "closenai-go-api:expansion-witness")
    path = tmp_path / "compose.env"
    path.write_text("\n".join(f"{k}={v}" for k, v in env.items()) + "\n", encoding="utf-8")
    return path


def test_staging_overlay_expansion_drops_production_ports(tmp_path: Path) -> None:
    """上一条只读 overlay 自己的文本，而 **compose 对 overlay 的 sequence 是追加**：
    2026-10-20 实跑展开里 py-api 同时挂着 8000 与 18000、caddy 同时挂着 80/443 与
    18080/18443——`!override` 加上之前，「宿主 crontab 打不到 staging」这句是假的。
    所以这一条判的是**展开结果**：prod 那三个端口一个都不许出现，生产串一个字都不许出现。
    """
    if shutil.which("docker") is None:
        pytest.skip("docker 不在 PATH：本条判的是 `docker compose config` 的展开，取不了证就不算绿")
    env_file = _compose_env(tmp_path)
    cmd = ["docker", "compose", "-p", "closenai-staging",
           "-f", str(PROD_COMPOSE), "-f", str(STAGING_OVERLAY),
           "--env-file", str(env_file), "config"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=str(ROOT))
    assert result.returncode == 0, (
        f"overlay 叠在 prod 上展不开（exit={result.returncode}）："
        f"{result.stderr[-1200:]}"
    )
    doc = yaml.safe_load(result.stdout)
    services = doc["services"]

    # 判**全部**发布端口，不按 host_ip 过滤：prod 基线的 caddy 写的是裸 `"80:80"`，
    # 展开后根本没有 host_ip 这个键——上一版那句过滤条件把公网面整片挡在断言之外，
    # 摘掉 caddy 的 `!override` 时这条闸照样绿（2026-10-20 code review 抓到，已实测复现）。
    published = {str(p["published"]) for svc in services.values()
                 for p in (svc.get("ports") or [])}
    assert published == {"18000", "18080", "18443"}, (
        f"staging 展开后的宿主端口面不是这三个：{published}（出现 8000/80/443 就意味着"
        "宿主 crontab 那一行或公网入口能打进 staging）"
    )

    for name in ("py-api", "ai-rpc", "py-ai-worker", "go-api"):
        env = services[name]["environment"]
        assert env["ENVIRONMENT"] == ENVIRONMENT_STAGING, (name, env.get("ENVIRONMENT"))
        assert STAGING_DB_HOST in str(env["DATABASE_URL"]), (
            f"{name} 的库串不是 staging 自己那套：{env.get('DATABASE_URL')!r}"
        )
    # 生产 host 的字面量在整份展开里只许出现在一个位置：黑名单键本身。
    # 出现在任何别的键（尤其是 DATABASE_URL）里，就是「两套编排指向同一个库」那个形状。
    leaks = [(name, key) for name, svc in services.items()
             for key, value in (svc.get("environment") or {}).items()
             if PRODUCTION_DB_HOST in str(value) and key != "PROD_DB_FINGERPRINTS"]
    assert not leaks, f"生产库 host 漏进了这些键：{leaks}"
    for name in ("py-api", "go-api"):
        fp = str(services[name]["environment"].get("PROD_DB_FINGERPRINTS", ""))
        assert fp.strip(), f"{name} 在 staging 展开里没有 PROD_DB_FINGERPRINTS＝这道闸一条都不判"


# ── README：装排程那一行不再是不问环境的裸命令 ──────────────

def test_readme_installs_through_the_gate() -> None:
    text = README.read_text(encoding="utf-8")
    assert "deploy/schedule/install-crontab.sh" in text, "README 没把装排程指向脚本"
    bare = re.findall(r"^crontab deploy/schedule/crontab", text, re.MULTILINE)
    assert not bare, f"README 里仍有裸装命令（它不问环境）：{bare}"
    assert "crontab 只装生产" in text, "README 缺『crontab 只装生产』这条结论"


def test_crontab_header_points_at_the_script() -> None:
    header = "\n".join(CRONTAB.read_text(encoding="utf-8").splitlines()[:14])
    assert "install-crontab.sh" in header, "crontab 的安装说明还指向裸 crontab 命令"


def test_script_is_executable_because_readme_says_run_it() -> None:
    assert SCRIPT.stat().st_mode & 0o111, (
        "README 让运维直接执行这个文件，但它没有可执行位（./install-crontab.sh 会 Permission denied）")
    assert os.access(SCRIPT, os.X_OK)


def test_unknown_argument_is_not_silently_ignored(tmp_path: Path) -> None:
    """拼错的参数不能当成「没传」——那会让一次显式的 --env-file 覆盖静默失效。"""
    result = subprocess.run(
        ["sh", str(SCRIPT), "--check", "--env-flle", str(tmp_path / "env")],
        capture_output=True, text=True, check=False)
    assert result.returncode == 2, result.stderr
    assert "未知参数" in result.stderr
