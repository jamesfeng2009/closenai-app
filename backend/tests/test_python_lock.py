"""T7-12：Python 依赖锁的静态判据（不联网、不装包）。

判的是四件事：
  1. 两份锁都是**完整**的（每个包都有 `==` 精确版本 + 至少一个 sha256 哈希）；
  2. `requirements.txt` 的每个直接依赖都在运行锁里，且锁版本满足声明的范围；
  3. 开发锁是运行锁的**超集**，共享包版本逐条相同（通过 `-r requirements.txt` 继承）；
  4. 镜像与 CI 的安装入口都指向锁文件，并且都带 `--require-hashes`。

「两个干净环境装出来一致」这件事靠 `scripts/verify_backend_lock.py` 实跑取证，
不在本文件里假装（静态闸无法证明索引上的文件没被换掉）。
"""
from __future__ import annotations

import re
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

REPO = Path(__file__).resolve().parents[2]
RUNTIME_LOCK = REPO / "backend" / "requirements.lock"
DEV_LOCK = REPO / "backend" / "requirements-dev.lock"
RUNTIME_IN = REPO / "backend" / "requirements.txt"
DEV_IN = REPO / "backend" / "requirements-dev.in"
DOCKERFILE = REPO / "deploy" / "Dockerfile.py-api"
CI = REPO / ".github" / "workflows" / "ci.yml"
NIGHTLY = REPO / ".github" / "workflows" / "mutation-nightly.yml"
DOC = REPO / "docs" / "python-dependency-lock.md"

REQ_LINE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^]]*\])?==([^\s;\\]+)(.*)$")
HASH_LINE = re.compile(r"^\s+--hash=sha256:([0-9a-f]{64})\s*\\?$")


def parse_lock(path: Path) -> dict[str, dict]:
    entries: dict[str, dict] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line[:1].isspace():
            m = HASH_LINE.match(line)
            assert m, f"{path.name} 出现不认识的续行：{line!r}"
            assert current, f"{path.name} 的哈希行没有归属的包：{line!r}"
            entries[current]["hashes"].append(m.group(1))
            continue
        m = REQ_LINE.match(line)
        assert m, f"{path.name} 出现不认识的依赖行：{line!r}"
        name = canonicalize_name(m.group(1))
        assert name not in entries, f"{path.name} 重复登记 {name}"
        entries[name] = {"version": m.group(2), "hashes": []}
        current = name
    return entries


def parse_requirements(path: Path) -> list[Requirement]:
    out: list[Requirement] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-r "):
            continue
        out.append(Requirement(line))
    return out


def test_locks_are_complete_hash_locked_and_exact() -> None:
    for path in (RUNTIME_LOCK, DEV_LOCK):
        entries = parse_lock(path)
        assert len(entries) > 50, f"{path.name} 只有 {len(entries)} 个包，不像完整锁"
        for name, entry in entries.items():
            assert entry["hashes"], f"{path.name}:{name} 没有 sha256 哈希"
            assert len(entry["hashes"]) == len(set(entry["hashes"])), (
                f"{path.name}:{name} 哈希重复"
            )
            Version(entry["version"])  # 版本形态不对就抛
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or line.lstrip().startswith("#"):
                continue
            if line[:1].isspace():
                continue  # 哈希续行已在 parse_lock 里验过
            assert "==" in line, f"{path.name} 有一条非精确依赖：{line!r}"
            for bad in (">=", "<=", "~=", "!=", "^"):
                assert bad not in line, (
                    f"{path.name} 仍是范围依赖（{bad}）：{line!r}；"
                    "锁文件必须逐包 == 加 --hash"
                )


def test_runtime_direct_requirements_are_covered_and_satisfied() -> None:
    runtime = parse_lock(RUNTIME_LOCK)
    direct = parse_requirements(RUNTIME_IN)
    assert direct, "requirements.txt 一条直接依赖都没解析出来"
    for req in direct:
        name = canonicalize_name(req.name)
        assert name in runtime, (
            f"运行时直接依赖 {name} 不在 requirements.lock 里："
            "锁不完整，干净环境会退回现场解析"
        )
        assert req.specifier.contains(runtime[name]["version"], prereleases=True), (
            f"{name} 的锁版本 {runtime[name]['version']} 不满足声明的 {req.specifier}："
            "锁与 requirements.txt 已经漂了"
        )


def test_dev_lock_supersets_runtime_lock() -> None:
    runtime = parse_lock(RUNTIME_LOCK)
    dev = parse_lock(DEV_LOCK)
    for name, entry in runtime.items():
        assert name in dev, f"开发锁缺运行时包 {name}"
        assert dev[name]["version"] == entry["version"], (
            f"{name} 在两份锁里版本不同：{entry['version']} vs {dev[name]['version']}"
        )
    for name in ("pypdf", "grpcio-tools", "setuptools"):
        assert name in dev, f"开发锁缺 {name}（测试/构建工具没有被锁住）"
    for req in parse_requirements(DEV_IN):
        assert canonicalize_name(req.name) in dev, f"开发输入 {req.name} 不在开发锁里"
    assert "-r requirements.txt" in DEV_IN.read_text(encoding="utf-8"), (
        "requirements-dev.in 必须继承 requirements.txt，否则两份锁会各自漂"
    )


def test_deploy_and_ci_install_from_the_lock() -> None:
    docker = DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY backend/requirements.lock ./" in docker, "镜像没有拷贝运行锁"
    assert "pip install --require-hashes -r requirements.lock" in docker, (
        "镜像安装没有 --require-hashes"
    )
    assert "requirements.txt" not in docker, "镜像仍在装 requirements.txt（范围依赖）"

    for workflow in (CI, NIGHTLY):
        text = workflow.read_text(encoding="utf-8")
        assert "requirements.txt" not in text, (
            f"{workflow.name} 仍引用 requirements.txt；CI 必须装锁文件"
        )
        assert "backend/requirements-dev.lock" in text, (
            f"{workflow.name} 的 pip 缓存/安装没有指向开发锁"
        )
        for line in text.splitlines():
            if "pip install" in line and "requirements" in line:
                assert ("install_backend_deps.sh" in line
                        or "requirements-dev.lock" in line), (
                    f"{workflow.name} 有一条绕过锁文件的安装：{line!r}"
                )
    assert "scripts/install_backend_deps.sh --dev" in CI.read_text(encoding="utf-8")


def test_documented_prerequisites_and_entrypoints() -> None:
    doc = DOC.read_text(encoding="utf-8")
    for needle in (
        "3.12", "3.13", "--require-hashes",
        "scripts/install_backend_deps.sh", "scripts/verify_backend_lock.py",
        "scripts/lock_backend_deps.sh",
    ):
        assert needle in doc, f"依赖说明缺 {needle!r}"
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.12"' in pyproject
