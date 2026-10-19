"""T0-06 变异抽样驱动器的闸：`scripts/mutation_sample.py` 自己也得有「能红」的证明。

被验对象不是业务，而是这套抽样机制的四条判据：**选择、正向证人、汇总解析、恢复核验**。
这四条里任何一条写空，CI 的 `mutation-sample` 作业就退化成「永远绿的那类闸」——
所以逐条打靶，且每条反面都配一条正面（防止判据扫空集）。

`--list` 与选择映射是真跑（只读，不起变异进程，秒级）；`parse_summary`/`gate_witnesses`
拿仓库里真的 `mutate_*.py` 当输入，不造玩具样本。

变异自证：`scripts/mutate_t006_sample_driver_gates.py`（把驱动器四条判据逐条掏空，
要求本文件的对应用例打红）。
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import mutation_sample as ms  # noqa: E402

SCRIPTS = sorted((ROOT / "scripts").glob("mutate_*.py"))


def run_driver(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "scripts/mutation_sample.py", *argv],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )


# ── 选择面：改动文件 → 被抽到的变异脚本 ──────────────────────────────────


def test_list_mode_reports_registry_without_running_anything():
    proc = run_driver("--list")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("mutate_")]
    assert len(lines) == len(SCRIPTS), f"--list 报了 {len(lines)} 条，磁盘上有 {len(SCRIPTS)} 条"
    assert "打红到位" not in proc.stdout, "--list 不该真的跑任何变异脚本"


def test_backend_change_selects_the_script_that_mutates_it():
    """正面证人：改 `cron_service.py` 必须命中 `mutate_w5_gates.py`（它的靶就是这个文件）。"""
    proc = run_driver("--files", "backend/services/cron_service.py", "--select-only")
    assert "选中 mutate_w5_gates.py" in proc.stdout, (
        f"改了投递主文件却什么都没抽到：{proc.stdout[:400]}"
    )


def test_unrelated_change_selects_nothing_and_does_not_claim_green():
    proc = run_driver("--files", "docs/definitely-unrelated-note.md")
    assert proc.returncode == 0
    assert "抽样集为空" in proc.stdout, "空集要说成空集，不许写成通过"


def test_generic_filenames_are_not_used_for_selection():
    """`index.ts`／`schemas.py` 这类名字到处都在，拿它们选样本＝每次改动都命中一堆无关脚本。"""
    proc = run_driver(
        "--files", "frontend/src/types/index.ts,backend/models/schemas.py", "--select-only"
    )
    assert "选中 mutate_" not in proc.stdout, (
        f"通用文件名被当成选择依据，抽到了：{proc.stdout[:400]}"
    )


# ── 判据面：证人、汇总解析 ───────────────────────────────────────────────


def test_witnesses_are_real_files_from_a_real_script():
    """正向证人必须真的存在：`gate_witnesses()` 不许把不存在的测试文件列成证人。"""
    witnesses = ms.gate_witnesses(ROOT / "scripts" / "mutate_w5_gates.py")
    assert witnesses, "mutate_w5_gates.py 一个证人都点不出来：这一层的判据是空的"
    for cmd in witnesses:
        rel = cmd[3]  # pytest 与 jest 的证人命令都把测试文件放在第 4 个参数上
        target = (ROOT / "frontend" / rel) if cmd[0] == "npx" else (ROOT / rel)
        assert target.is_file(), f"证人指向不存在的文件：{rel}"
        assert cmd[:4] == [cmd[0], "-m", "pytest", rel] or cmd[:4] == ["npx", "jest", "--silent", rel], cmd


def test_witness_list_ignores_files_that_do_not_exist():
    """判据的负半：源码里点名的测试文件如果不在盘上，就不该被列成证人（否则 pytest 用非零退出伪装成绿）。"""
    src = (
        'QUIET_GATE = "backend/tests/test_a_file_that_does_not_exist.py"\n'
        'OFF_GATE = "backend/tests/test_delivery_off_e2e.py"\n'
    )
    tmp = ROOT / "scripts" / "_tmp_witness_probe.py"
    tmp.write_text(src, encoding="utf-8")
    try:
        cmds = ms.gate_witnesses(tmp)
    finally:
        tmp.unlink()
    assert len(cmds) == 1, f"不存在的闸被当成证人了：{cmds}"
    assert "test_delivery_off_e2e.py" in cmds[0][3]


def test_summary_parser_requires_equal_fractions():
    """汇总行认分数不认文案：不齐平的红/绿闸必须判不过，缺行也判不过。"""
    ok, note = ms.parse_summary("变异自证合计：12 条｜红闸 10/10｜绿闸 2/2")
    assert ok, note
    ok, note = ms.parse_summary("变异自证合计：12 条｜红闸 9/10｜绿闸 2/2")
    assert not ok and "9/10" in note, note
    ok, note = ms.parse_summary("跑完了，都过了")
    assert not ok and "可解析" in note, "没有分数就放行＝这条判据空转"
    ok, note = ms.parse_summary("合计：10 打红 / 10，绿闸 2/2")
    assert ok, note


def test_every_mutation_script_can_be_selected_by_its_own_name():
    """每个变异脚本都要能被「改它自己」这件事抽到——否则新增的脚本永远不会进抽样。"""
    for script in SCRIPTS:
        assert script.name in ms.script_targets(script), f"{script.name} 的靶里没有它自己"


def test_run_sample_fails_when_the_workspace_is_not_restored(monkeypatch, capsys):
    """恢复核验（判据 4）：证人绿＋打红到位都不够，工作区残留一格也必须红。

    这里不起真的变异进程——用假 `run`／假 `porcelain` 只验一件事：`run_sample()` 有没有
    把「跑前跑后的工作区」拿去比对。正反两半都在同一条用例里：先证「一致就过」，
    再证「不一致就红」，防止这条判据被写成恒红或恒绿。
    """
    script = ROOT / "scripts" / "mutate_w5_gates.py"
    monkeypatch.setattr(ms, "gate_witnesses", lambda _s: [["x", "-m", "pytest", "t.py"]])
    monkeypatch.setattr(ms, "run", lambda cmd, timeout: (0, "红闸 1/1｜绿闸 1/1"))

    state = {"after": ""}
    calls = {"n": 0}

    def fake_porcelain() -> str:
        # 第一次调用是「跑前快照」，之后都是「跑后快照」——这才像真实工作区的形状。
        calls["n"] += 1
        return "" if calls["n"] == 1 else state["after"]

    monkeypatch.setattr(ms, "porcelain", fake_porcelain)
    assert ms.run_sample(script, []) is True, "工作区一致时 run_sample 应当放行"

    calls["n"] = 0
    state["after"] = " M backend/services/cron_service.py"
    assert ms.run_sample(script, []) is False, "变异残留没被发现：恢复核验是空的"
    assert "恢复" in capsys.readouterr().out


def test_registry_is_not_empty_and_targets_look_like_files():
    registry = ms.discover_scripts()
    assert len(registry) == len(SCRIPTS), f"变异脚本认到 {len(registry)} 个，盘上有 {len(SCRIPTS)} 个"
    assert len(registry) >= 30, f"选择表只剩 {len(registry)} 条：抽样面塌了"
    for name, targets in registry.items():
        assert targets, f"{name} 的目标集为空"
        for token in targets:
            assert Path(token).suffix in ms.FILE_SUFFIXES, f"{name} 收了不像文件的名字：{token}"


def test_driver_has_no_business_dependencies():
    """驱动器只依赖标准库＋git：`mutation-sample` 作业就算没装业务依赖也要能起（否则红在环境上，不是红在代码上）。"""
    src = (ROOT / "scripts" / "mutation_sample.py").read_text(encoding="utf-8")
    flat = {x for pair in re.findall(r"^import (\w+)|^from (\w+)", src, re.M) for x in pair if x}
    allowed = {"argparse", "re", "subprocess", "sys", "pathlib", "__future__"}
    assert flat <= allowed, f"驱动器引入了业务依赖：{flat - allowed}"
