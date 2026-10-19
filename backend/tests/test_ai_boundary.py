"""O0 · `backend/ai/` 的边界闸（V3 12「Python AI Runtime」的分层约束落点）。

为什么需要闸而不是注释：`backend/ai/` 存在的唯一理由是「AI 能力面与权威写面可分进程」。
一旦有人在这里 import 仓储、模型或 HTTP 路由，这层就变回 `services/` 的别名，R3 的
gRPC server 会连带把数据库拖进 AI 进程，而那种退化是**加一行 import 就发生**的、
code review 最容易漏的一种。所以把它做成断言。

四条禁令（口径来自 tasks.md 的 O0 与 R3 两行）：
① 不许 import `backend.routers` / `backend.repositories` / `backend.models` / `backend.database`；
② 不许出现直连数据库或 HTTP 的符号（`SessionLocal` / `get_db` / `APIRouter`）；
③ `backend.gen.*`（protobuf 生成物）只许 `backend/ai/rpc/` 用——生成物不能变成业务层的依赖；
④ 闸本身必须有靶子：`backend/ai/` 只剩 `__init__.py` 时，前三条会静默全绿。

⚠️ ①②③ 走 AST，不做全文正则/文本匹配：本包 `__init__.py` 的 docstring 里就写着
"SessionLocal" 字样，文本匹配会误报；反过来 docstring 里的 `from backend.models import x`
也不是真依赖。只认 import 节点与 Name/Attribute 节点。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
AI_DIR = ROOT / "backend" / "ai"

FORBIDDEN_ROOTS = (
    "backend.routers",
    "backend.repositories",
    "backend.models",
    "backend.database",
)
FORBIDDEN_SYMBOLS = ("SessionLocal", "get_db", "APIRouter")
GEN_ROOT = "backend.gen"

# 靶子清单：改名或删文件会让 test_gate_has_targets 红，防止「扫不到文件＝通过」
# mq/* 九个文件是 R5（2026-10-16）加进来的；prompts/* 与 mq/handlers/* 是 T2-02（2026-10-18）
# 加进来的。**递归**反向登记由
# backend/tests/test_ai_mq_contract.py::test_mq_files_are_registered_in_the_boundary_gate 守
# （它 rglob ``mq/`` 与 ``prompts/`` 两棵树），
# 因为本清单的判据是子集——新文件不写进来不会让这里红，只会让禁令静默少扫一个文件。
EXPECTED_FILES = {
    "__init__.py",
    "llm.py",
    "safety.py",
    "rpc/__init__.py",
    "prompts/__init__.py",
    "prompts/observation.py",
    "mq/__init__.py",
    "mq/config.py",
    "mq/topology.py",
    "mq/contract.py",
    "mq/registry.py",
    "mq/pipeline.py",
    "mq/publisher.py",
    "mq/consumer.py",
    "mq/worker.py",
    "mq/handlers/__init__.py",
    "mq/handlers/observation.py",
}


def _ai_sources() -> dict[str, str]:
    """`backend/ai/` 下全部 .py（排除 __pycache__），键是相对该目录的 posix 路径。"""
    out: dict[str, str] = {}
    for path in sorted(AI_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        out[path.relative_to(AI_DIR).as_posix()] = path.read_text(encoding="utf-8")
    return out


def _package_of(rel: str) -> str:
    """文件所在包的点分名：rpc/server.py → backend.ai.rpc；llm.py → backend.ai。"""
    return ".".join(["backend", "ai", *pathlib.PurePosixPath(rel).parent.parts])


def _imported_modules(source: str, package: str) -> list[tuple[int, str]]:
    """抽出真实 import 的模块点分名（含相对 import 的解析结果）。"""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                found.append((node.lineno, node.module or ""))
                continue
            # level=1 就是本包所在目录，每多一级往上退一段
            base = package.split(".")
            cut = len(base) - (node.level - 1)
            tail = [node.module] if node.module else []
            found.append((node.lineno, ".".join(base[:cut] + tail)))
    return found


def _used_names(source: str) -> list[tuple[int, str]]:
    """代码里真实出现的标识符、属性名与被导入名（docstring 不算）。"""
    out: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            out.append((node.lineno, node.id))
        elif isinstance(node, ast.Attribute):
            out.append((node.lineno, node.attr))
        elif isinstance(node, ast.ImportFrom):
            out.extend((node.lineno, alias.name) for alias in node.names)
    return out


AI_FILES = sorted(_ai_sources())


# ── ④ 闸自己必须有靶子 ─────────────────────────────────────


def test_gate_has_targets():
    """`backend/ai/` 必须真的有待扫文件，否则下面三条禁令全是空转。"""
    missing = EXPECTED_FILES - set(AI_FILES)
    assert not missing, f"backend/ai/ 缺文件（改名或误删会让本闸静默放行）：{sorted(missing)}"
    assert len(AI_FILES) >= 4, f"扫描面过小，闸在空转：{AI_FILES}"


# ── ① 分层禁令 ────────────────────────────────────────────


@pytest.mark.parametrize("rel", AI_FILES)
def test_no_forbidden_layer_import(rel: str):
    hits = [(lineno, mod) for lineno, mod in _imported_modules(_ai_sources()[rel], _package_of(rel))
            if any(mod == r or mod.startswith(r + ".") for r in FORBIDDEN_ROOTS)]
    assert not hits, (
        f"backend/ai/{rel} 越层 import 了 {[f'{m}:{n}' for n, m in hits]}；"
        f"AI 能力层不许碰 {list(FORBIDDEN_ROOTS)}（数据库与 HTTP 属权威侧，见 backend/ai/__init__.py）")


# ── ② 数据库／HTTP 符号禁令 ───────────────────────────────


@pytest.mark.parametrize("rel", AI_FILES)
def test_no_direct_db_or_http_symbol(rel: str):
    hits = [(lineno, name) for lineno, name in _used_names(_ai_sources()[rel])
            if name in FORBIDDEN_SYMBOLS]
    assert not hits, (
        f"backend/ai/{rel} 出现直连符号 {[f'{n}:{l}' for l, n in hits]}"
        f"（Python 侧的合法出口只有 candidate 回投，那是 R5）")


# ── ③ 生成物只在 rpc/ 用 ──────────────────────────────────


@pytest.mark.parametrize("rel", AI_FILES)
def test_gen_import_is_confined_to_rpc(rel: str):
    hits = [mod for _, mod in _imported_modules(_ai_sources()[rel], _package_of(rel))
            if mod == GEN_ROOT or mod.startswith(GEN_ROOT + ".")]
    if pathlib.PurePosixPath(rel).parts[0] == "rpc":
        return  # rpc/ 允许：契约生成物是它入参／出参的类型来源
    assert not hits, (
        f"backend/ai/{rel} import 了 protobuf 生成物 {hits}；生成物只能出现在 rpc/，"
        f"否则能力层会被 proto 字段名绑住（R1 的兼容闸管不到那种耦合）")


# ── 自证：探测器本身有效（不依赖外部变异脚本）─────────────


def test_detector_catches_synthetic_violation():
    """往内存里塞违规源码，探测器必须逐条报出来。

    不落真文件：`backend/**.py` 正被 `test_canonical_schema.py` 的 rglob 扫着，
    测试里造真文件会污染别的闸。
    """
    bad_layer = "from backend.models.user import User\n"
    bad_db = "from backend.database import SessionLocal\n\n\nasync def f():\n    return SessionLocal\n"
    # 文件在 backend/ai/services/ 里时 `...` 上跳两级 = backend ⇒ backend.models.user
    bad_relative = "from ...models.user import User\n"

    assert any(m.startswith("backend.models") for _, m in _imported_modules(bad_layer, "backend.ai"))
    assert any(m.startswith("backend.database") for _, m in _imported_modules(bad_db, "backend.ai"))
    assert any(n == "SessionLocal" for _, n in _used_names(bad_db))
    resolved = [m for _, m in _imported_modules(bad_relative, "backend.ai.services")]
    assert resolved == ["backend.models.user"], f"相对 import 解析错：{resolved}"


def test_clean_module_passes_detector():
    """反向自证：合法 import 不许被误杀，否则禁令会被「换个写法绕过」而不是被纠正。"""
    ok = (
        "from backend.ai.llm import llm\n"
        "from backend.core import generation_run\n"
        "from .safety import safety_service\n"
    )
    # 当成 backend/ai/ 直属文件（package=backend.ai）：`.` 即本包
    mods = [m for _, m in _imported_modules(ok, "backend.ai")]
    assert mods == ["backend.ai.llm", "backend.core", "backend.ai.safety"], mods
    assert not [m for m in mods if any(m == r or m.startswith(r + ".") for r in FORBIDDEN_ROOTS)]
