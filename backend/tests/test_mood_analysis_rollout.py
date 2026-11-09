"""#33 的放量面闸：KG-28 三层次分析**默认关**，且「怎么开」写在一处可对账。

为什么需要这张闸而不是只有行为测（`test_mood_analysis.py` 那 30 条）：行为测证的是
「开关关着时端点回 40402」，它证不了「生产清单上没人偷偷把它打开」——后者不是代码行为，
是**部署事实**。同理，行为测也不会因为有人把放量键名写成方案文档里的旧形态
（带 `CLOSEN_` 前缀）而红：那样改出来的是一个**静默无效**的开关，端点继续 40402，
所有测试照样绿。

所以本文件钉三类事实：
  1. 默认值仍是 `False`，三份 compose 与 k8s 清单里没有任何一格把它置真；
  2. 路由／服务／仪表盘三个读取点读的是**同一个** Settings 字段，且路由层的判定在
     同意检查之前（关着时不能靠 40303 泄露功能存在）；
  3. `docs/growth/mood-analysis-2026-11.md` §9 的放量清单与代码逐字对得上：
     键名＝字段名大写（无前缀）、配额默认值＝`backend/config.py` 的数、
     并且它明说「仍未放量」——清单不是读数。

真要放量时，**本文件就是登记点**：把预期值改成 `"1"` 并写上日期，和改 ConfigMap 同批提交。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "backend" / "config.py"
ROUTER = ROOT / "backend" / "routers" / "mood.py"
SERVICE = ROOT / "backend" / "services" / "mood_analysis_service.py"
GROWTH = ROOT / "backend" / "services" / "growth_service.py"
DOC = ROOT / "docs" / "growth" / "mood-analysis-2026-11.md"
MANIFEST_DIR = ROOT / "deploy" / "k8s"
COMPOSE_DIR = ROOT / "deploy" / "compose"

FIELD = "mood_analysis_depths_enabled"
ENV = "MOOD_ANALYSIS_DEPTHS_ENABLED"
QUOTA_FIELDS = ("free_daily_mood_analyses", "pro_daily_mood_analyses", "pro_plus_daily_mood_analyses")

_FALSY = {"", "0", "false", "no", "off"}


def _config_int(field: str) -> int:
    match = re.search(rf"^\s+{field}: int = (\d+)", CONFIG.read_text(encoding="utf-8"), re.M)
    assert match, f"backend/config.py 里找不到 {field} 的整型默认值"
    return int(match.group(1))


def _flag_values(text: str) -> list[str]:
    return [
        raw.strip().strip('"').strip("'")
        for raw in re.findall(rf"^\s*{ENV}\s*:\s*(.+?)\s*$", text, re.M)
    ]


def test_flag_defaults_off_and_no_manifest_turns_it_on():
    source = CONFIG.read_text(encoding="utf-8")
    match = re.search(rf"^\s+{FIELD}: bool = (True|False)", source, re.M)
    assert match, "backend/config.py 里找不到开关的布尔默认值：字段被改名了？"
    assert match.group(1) == "False", (
        "KG-28 的开关默认值不是 False。放量是一次有意的上线动作，不是改一个默认值——"
        "要放量请改 §9 的清单与本文件，并写上日期"
    )

    offenders: list[str] = []
    for path in sorted(MANIFEST_DIR.glob("*.yaml")) + sorted(COMPOSE_DIR.glob("*.yml")):
        for value in _flag_values(path.read_text(encoding="utf-8")):
            if value.lower() not in _FALSY:
                offenders.append(f"{path.name}: {value!r}")
    assert not offenders, (
        f"这些清单把三层次分析打开了：{offenders}。默认关是产品决定（ADR §1：4 周小实验），"
        "放量要走 §9 的清单并把本文件的预期值一起改"
    )


def test_all_three_read_points_share_one_settings_field():
    router = ROUTER.read_text(encoding="utf-8")
    service = SERVICE.read_text(encoding="utf-8")
    growth = GROWTH.read_text(encoding="utf-8")

    for label, text in (("路由", router), ("服务", service), ("仪表盘", growth)):
        assert f"settings.{FIELD}" in text, (
            f"{label}层没有读 settings.{FIELD}：一个开关三个读取点，少一处就会「关不干净」"
        )

    # 关着时先回 40402，再谈同意：40303 会告诉客户端「这里藏了个功能」。
    guard = router.index(f"if not settings.{FIELD}:")
    consent = router.index("await user_service.require_ai_consent")
    assert guard < consent, "路由层的开关判定被挪到了 require_ai_consent 之后：关着时会泄露 40303"

    # 服务层的兜底判定在任何业务动作之前（失败方向是「不给结果」，不是「猜调用方拦过了」）。
    svc_guard = service.index(f"if not settings.{FIELD}:")
    for later in ("require_user", "get_today", "reserve_mood_analysis"):
        assert svc_guard < service.index(later), (
            f"服务层的开关兜底排到了 {later} 之后：开关关着时仍会走业务路径"
        )

    flat_growth = re.sub(r"\s+", " ", growth)
    assert f"mood_analysis_depths=settings.{FIELD}" in flat_growth, (
        "仪表盘的 features.mood_analysis_depths 不再逐字镜像开关"
    )


def test_checklist_key_names_match_the_settings_fields():
    doc = DOC.read_text(encoding="utf-8")
    source = CONFIG.read_text(encoding="utf-8")

    assert ENV in doc, f"放量清单里没有逐字的 {ENV}：运维照文档设变量，名字错了就是假开关"
    # 字段名大写＝pydantic-settings 的取值方式；带 CLOSEN_ 前缀的旧写法只在方案文档里，
    # 照它设变量不会生效（§7 第 4 条已登记）。
    assert f"CLOSEN_{ENV}" not in doc, (
        f"放量清单里出现了 CLOSEN_{ENV}：backend/config.py 没有 env_prefix，这个前缀不存在"
    )

    # 这一句同时证明 §9.3 第 3 步「可以进 ConfigMap」是对的：
    # test_k8s_manifests.py 的跨语言 env 闸只认 config.go 的 env*() 与 config.py 的字段名。
    py_names = {
        name.upper()
        for name in re.findall(r"^\s+([a-z_][a-z0-9_]*)\s*:", source, re.M)
    }
    assert ENV in py_names, (
        f"{ENV} 不在 ConfigMap 闸认可的 Python 字段名里：写进 ConfigMap 会当场红"
    )

    for field in QUOTA_FIELDS:
        quota_env = field.upper()
        assert quota_env in doc, f"放量清单没有登记配额键 {quota_env}"
        assert quota_env in py_names, f"{quota_env} 不在 backend/config.py 的字段名里"


def test_checklist_is_marked_unshipped_and_numbers_match_the_code():
    doc = DOC.read_text(encoding="utf-8")

    assert "## 9." in doc and "仍未放量" in doc, (
        "放量清单必须自证「未放量」：这份文档是清单，不是读数（4 周实验还没跑）"
    )

    # 表格逐格对账：键名 → (默认值, 放量值)。
    rows: dict[str, tuple[str, str]] = {}
    for line in doc.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 4:
            continue
        name = cells[1].strip("` ")
        if re.fullmatch(r"[A-Z0-9_]+", name):
            rows[name] = (cells[2].strip("` "), cells[3].strip("` "))

    assert ENV in rows, "§9.2 的开关行不见了：放量步骤失去唯一事实源"
    default_cell, release_cell = rows[ENV]
    assert "关" in default_cell, f"清单把默认值写成 {default_cell!r}，与代码的 False 不符"
    assert release_cell == '"1"', f"清单写的放量值是 {release_cell!r}，代码只认 1/true/yes/on"

    for field in QUOTA_FIELDS:
        quota_env = field.upper()
        assert quota_env in rows, f"§9.2 缺 {quota_env} 这一行"
        assert rows[quota_env][0] == str(_config_int(field)), (
            f"{quota_env} 的默认值 {rows[quota_env][0]!r} 与 backend/config.py 的 "
            f"{_config_int(field)} 不同：清单在报一个不存在的配额"
        )

    # 前置与回滚的锚点：4 周读数要流量、真机走查、逐国热线、多副本幂等。
    for token in ("KG-31", "真机", "988", "replicas", "rollout restart deploy/py-api",
                  "40402", "不需要发版"):
        assert token in doc, f"放量清单缺少 {token!r}：前置或回滚没写清，下一个人会凭直觉改数"
