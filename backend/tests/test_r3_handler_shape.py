"""R3-③ · 「handler 零业务分支」形状闸（`backend/ai/rpc/**` 的第二套判据必须为 0）。

这条闸是 R0 裁决的执行面：Python 侧只做能力，判定口径归 Go 领域门（`go-core/internal/domain/gate.go`）。
一旦 handler 里长出第二套判断——比如顺手 `if "打我" in request.text`，或者把 4000 上限重抄一遍——
两侧就会各判一份，跨进程后没人知道哪份生效，而这正是 R0 要避免的事。

判据不写死词表，而是**从被禁能力面反射出来**（`SafetyService` / `ChatService` /
`MessageGenerator` / `QuotaService` 的公开成员），所以下一个人新加一个业务方法，
不需要改这个测试就会自动进靶面。允许名单只有 R3 真正要调的那三个入口。

`test_shape_gate_detects_synthetic_violation` 是这条闸的「能打红」自证：
纯内存构造一段违规源码喂给探测器，断言它报出来——空文件集会让前面所有断言静默通过。
"""
from __future__ import annotations

import ast
import pathlib
from typing import Dict, List, Set

from backend.ai.safety import SafetyService
from backend.services.chat_service import ChatService
from backend.services.message_generator import MessageGenerator
from backend.services.quota_service import QuotaService

RPC_ROOT = pathlib.Path("backend/ai/rpc")

# R3 允许的调用入口：能力函数本体 + 入参校验 + 自带 session 的只生成链。
# 少了这一格，反射出来的词表会把「正确的那三个调用」也判成违规。
ALLOWED_MEMBERS = {
    "check_text_tri",           # SafetyService → ClassifySafety 的能力面
    "validate_stream_input",    # ChatService → StreamChat 的入参校验
    "rpc_chat_stream",          # ChatService → StreamChat 的生成链
}

FORBIDDEN_IMPORT_ROOTS = (
    "backend.routers",
    "backend.repositories",
    "backend.models",
    "backend.database",
)

# 输入上限/维度这类数字只能有一份来源（`schemas.py:22`、`config.py:86`）。
# 重抄一次＝两处会漂，proto:207-208 逐字点过这个坑。
FORBIDDEN_NUMERIC_CONSTANTS = {4000, 512, 200}


def _business_vocabulary() -> Dict[str, Set[str]]:
    """从业务/能力类反射出「判断口径所在的面」，而不是手抄一份词表。"""
    out: Dict[str, Set[str]] = {}
    for cls in (SafetyService, ChatService, MessageGenerator, QuotaService):
        members = {name for name in dir(cls) if not name.startswith("__")}
        out[cls.__name__] = members - ALLOWED_MEMBERS
    return out


def _rpc_sources() -> Dict[str, str]:
    return {
        p.as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(RPC_ROOT.rglob("*.py"))
        if "__pycache__" not in p.parts
    }


def _identifiers(source: str) -> Set[str]:
    """源码里出现过的所有名字：`ast.Name` 的 id ＋ `ast.Attribute` 的 attr。"""
    found: Set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
    return found


def _package_of(rel: str) -> str:
    """`rpc/server.py` → `backend.ai.rpc`；`rpc/x/y.py` → `backend.ai.rpc.x.y` 去掉尾段。"""
    parts = ["backend", "ai", "rpc", *rel.split("/")[:-1]]
    return ".".join(parts)


def _imported_roots(source: str, package: str = "backend.ai.rpc") -> Set[str]:
    """导入面（绝对名 + 相对导入换算后的绝对名）。

    相对导入必须展开：`from ..services.x import y` 留在 `services.x` 这种形态，
    模块白名单就形同虚设（AI 进程面里 `..` 的起点是 `backend.ai`，展开后落在
    `backend.ai.services.*`——照样不在允许名单里，会被闸拦下）。
    """
    roots: Set[str] = set()
    base = package.split(".")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module)
            if node.level:
                cut = len(base) - (node.level - 1)
                prefix = base[: max(cut, 0)]
                roots.add(".".join(prefix + node.module.split(".")))
        elif isinstance(node, ast.Import):
            roots.update(alias.name for alias in node.names)
    return roots


def _numeric_constants(source: str) -> Set[int]:
    return {
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, int)
    }


def _find_shape_violations(source: str, vocab: Dict[str, Set[str]]) -> List[str]:
    findings: List[str] = []
    used = _identifiers(source)
    for cls, members in sorted(vocab.items()):
        for name in sorted(used & members):
            findings.append(f"业务判断面被复制：{cls}.{name}")
    if "re" in _imported_roots(source):
        findings.append("rpc 层里有正则检测器（词表判据只能有一份，在 backend/ai/safety.py）")
    for number in sorted(FORBIDDEN_NUMERIC_CONSTANTS & _numeric_constants(source)):
        findings.append(f"重抄了数字常量 {number}（上限只能来自 Pydantic 模型或 settings）")
    return findings


# ── 闸本身 ───────────────────────────────────────────────


def test_gate_has_targets():
    sources = _rpc_sources()
    assert "backend/ai/rpc/server.py" in sources, sources.keys()
    assert len(sources) >= 2, "rpc 层至少要有 __init__.py 与 server.py，否则本闸是空跑"


def test_vocabulary_is_not_empty():
    """反射出来的靶面必须有内容：靶表空了，下面的断言会全绿但什么都没钉住。

    逐类点名的都是**真的判断口径**（词表命中、越界句式、配额判定、生成链），
    一旦有人把某个方法改名或删掉，这条会先红——比默默失靶好。
    """
    vocab = _business_vocabulary()
    for cls, members in vocab.items():
        assert members, cls
    assert sum(len(m) for m in vocab.values()) >= 40, {k: len(v) for k, v in vocab.items()}
    assert {"crisis_intercept", "banned_hit", "attribution_hit", "sentence_gate"} <= vocab["SafetyService"]
    assert {"prepare_chat_turn"} <= vocab["ChatService"]
    assert {"generate_chat_stream", "get_memory_context"} <= vocab["MessageGenerator"]
    assert "is_chat_quota_exceeded" in vocab["QuotaService"]


def test_rpc_layer_has_zero_business_branches():
    vocab = _business_vocabulary()
    findings = []
    for path, source in _rpc_sources().items():
        findings.extend(f"{path}: {f}" for f in _find_shape_violations(source, vocab))
    assert not findings, "\n".join(findings)


def test_rpc_layer_imports_only_the_allowed_service_modules():
    """服务层只能引 `chat_service` 一个（它带 session 的生成链是 R3 的落点）。

    `backend.ai.rpc` 自己不许持有数据库会话——那是 O0 边界闸定的，R3 只在 rpc 这一层再收紧一次：
    能 import 的 service 模块写死，多了就说明有人把权威态写入搬进了 AI 进程。
    """
    allowed = {
        "backend.services.chat_service",
        "backend.ai.safety",
        "backend.ai.llm",
        "backend.ai.rpc",
        "backend.ai.rpc.server",
    }
    checked = 0
    for path, source in _rpc_sources().items():
        for root in sorted(_imported_roots(source, _package_of(path))):
            if root.startswith(("backend.services", "backend.ai")):
                assert root in allowed, f"{path} 导入了契约外的模块：{root}"
                checked += 1
            assert not root.startswith(FORBIDDEN_IMPORT_ROOTS), (
                f"{path} 导入了 {root}（AI 进程面不碰 HTTP/仓储/模型/数据库）"
            )
    assert checked >= 3, f"本闸没靶：只数到 {checked} 条受控导入"


def test_shape_gate_detects_synthetic_violations():
    """探测器必须能打红——每条判据各造一个样本，且干净样本必须放行。"""
    vocab = _business_vocabulary()

    dirty = [
        # ① handler 里内嵌业务分支：直接调危机词表
        "async def ClassifySafety(self, request, context):\n"
        "    if safety_service.crisis_intercept(request.text, 'zh'):\n"
        "        return ok()\n",
        # ② 在 rpc 层抄一份正则判据
        "import re\n\nasync def Embed(self, request, context):\n"
        "    return re.match('x', request.texts[0])\n",
        # ③ 把输入上限重抄进 handler
        "async def StreamChat(self, request, context):\n"
        "    if len(request.content) > 4000:\n"
        "        await abort()\n",
        # ④ 把权威态写入搬进 AI 进程
        "from backend.services.quota_service import quota_service\n\n"
        "async def StreamChat(self, request, context):\n"
        "    return await quota_service.is_chat_quota_exceeded()\n",
    ]
    for sample in dirty:
        assert _find_shape_violations(sample, vocab), sample

    clean = (
        "import grpc\n\n"
        "from backend.ai.safety import safety_service\n\n"
        "async def ClassifySafety(self, request, context):\n"
        "    scope = request.scope or 'chat'\n"
        "    return await safety_service.check_text_tri(request.text, request.language, scope=scope)\n"
    )
    assert _find_shape_violations(clean, vocab) == []


def test_shape_gate_catches_relative_import_escape():
    """相对导入必须展开成绝对名，否则 `from ..services.x import y` 能绕过模块闸。"""
    roots = _imported_roots("from ..services.chat_service import chat_service\n", "backend.ai.rpc")
    assert "backend.ai.services.chat_service" in roots, roots
    # 子包里的 `.` 起点是它自己所在的包，不能一路算到 backend.ai 头上
    nested = _imported_roots("from .safety import safety_service\n", "backend.ai.rpc.sub")
    assert "backend.ai.rpc.sub.safety" in nested, nested
