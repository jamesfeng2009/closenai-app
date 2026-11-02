"""
反思提示库（KG-26）。

数据源是 `backend/content/reflection_prompts.json`——**仓内唯一的一份 30 条提示**。
站点侧（KG-12 的 `/journal-prompts`）由 `scripts/export_prompts.py` 从同一个文件生成，
不允许在站点里手写第二份：两份文案一旦分叉，「这条提示依据哪条记录」就没法回答了。

三条口径：

1. **语言只有 en/zh**。其余 13 个语言请求回退 en，并在响应里显式给
   `language_used` + `fallback=true`——前端据此决定要不要在标题旁标「未翻译」。
   按 30 条 × 15 语言翻译会爆炸，静默用 en 又不告诉用户，两头都不如实。
2. **校验在加载时做，且一次性列全问题**（条数、簇计数、id 唯一、en/zh 非空、
   `function` 枚举、禁词）。内容文件是给人编辑的，报错必须能一次改完，
   而不是改一条跑一次。校验不过＝`PromptLibraryError`，接口 500，
   比端着一个残缺提示库上线好。
3. **禁词是硬约束**：这批提示会出现在 App 商店截图和落地页上，
   任何「诊断／治疗／疗法／symptom／disorder」类表述都会把产品推到医疗边界外
   （Baseline §1.2、台账 KG-01 的医疗边界）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

CONTENT_PATH = Path(__file__).resolve().parents[1] / "content" / "reflection_prompts.json"

#: 库内文本只有这两种语言；其他请求码回退 en。
LIBRARY_LANGUAGES = ("en", "zh")
FALLBACK_LANGUAGE = "en"

FUNCTIONS = ("reflect", "mood", "goal")

#: 30 条按簇的应有数量（台账 KG-26 的「四簇 8/8/7/7」）。
EXPECTED_TOTAL = 30
EXPECTED_CLUSTER_COUNTS = {
    "reflection_method": 8,
    "long_term_understanding": 8,
    "action_outcome": 7,
    "ai_credibility": 7,
}

#: 每条提示必填的可本地化字段（`title`／`question`／`why`／`follow_up` 各含 en/zh）。
LOCALIZED_FIELDS = ("title", "question", "why", "follow_up")

#: 医疗／诊断／绝对化承诺三类禁词。写成一条一条带标签，是为了报错时能指出**哪一条**踩了。
BANNED_PATTERNS = (
    ("en 治疗/疗法", re.compile(r"\btherap\w+|\btreat\b|\btreating\b|\btreatment\b|\bcure\b", re.I)),
    ("en 诊断", re.compile(r"\bdiagnos\w*", re.I)),
    ("en 症状/疾病/药物", re.compile(r"\bsymptom\w*|\bdisorder\w*|\bmedication\w*|\bpill\w*", re.I)),
    ("en 来访者/病人", re.compile(r"\bpatient\w*|\bcounsell?or\w*|\bpsycholog\w*", re.I)),
    ("en 保证/一定", re.compile(r"\bguarantee\w*|\will (definitely|certainly)\b", re.I)),
    ("zh 诊断/确诊", re.compile(r"诊断|确诊")),
    ("zh 治疗/疗法", re.compile(r"治疗|疗法|疗程")),
    ("zh 心理咨询/咨询师", re.compile(r"心理咨询|咨询师|心理医生")),
    ("zh 症状/药物/治愈", re.compile(r"症状|药物|治愈|疾病")),
    ("zh 保证", re.compile(r"保证|一定能|必然会")),
)


class PromptLibraryError(RuntimeError):
    """内容文件不合法。携带逐条问题，便于一次改完。"""

    def __init__(self, problems: List[str]) -> None:
        self.problems = problems
        super().__init__("反思提示库校验失败：" + "；".join(problems))


def _walk_strings(node: Any, prefix: str = ""):
    """产出 (路径, 字符串) 对，供禁词扫描用——包括簇标题与 hint。"""
    if isinstance(node, str):
        yield prefix, node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _walk_strings(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_strings(v, f"{prefix}[{i}]")


def _banned_problems(payload: Dict[str, Any]) -> List[str]:
    """禁词扫描。路径按**提示 id** 起头（`rp-001.title.en`），不按数组下标：
    内容文件是给人编辑的，`prompts[7]` 那种位置在增删一条之后就指错了条目。"""
    hits: List[str] = []

    def scan(where: str, text: str) -> None:
        for label, pattern in BANNED_PATTERNS:
            m = pattern.search(text)
            if m:
                hits.append(f"{where} 命中禁词[{label}]「{m.group(0)}」")

    for index, cluster in enumerate(payload.get("clusters") or []):
        if not isinstance(cluster, dict):
            continue
        key = cluster.get("key") or f"clusters[{index}]"
        for path, text in _walk_strings(cluster, f"cluster[{key}]"):
            scan(path, text)
    for index, prompt in enumerate(payload.get("prompts") or []):
        if not isinstance(prompt, dict):
            continue
        pid = str(prompt.get("id") or f"prompts[{index}]")
        body = {k: v for k, v in prompt.items() if k != "id"}
        for path, text in _walk_strings(body, pid):
            scan(path, text)
    top = {k: v for k, v in payload.items() if k not in ("clusters", "prompts")}
    for path, text in _walk_strings(top, ""):
        scan(path or "root", text)
    return sorted(set(hits))


def validate(payload: Dict[str, Any]) -> List[str]:
    """返回问题列表（空＝通过）。不抛异常，方便测试逐条验。"""
    problems: List[str] = []

    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        problems.append("version 必须是非空字符串")

    clusters = payload.get("clusters")
    if not isinstance(clusters, list):
        problems.append("clusters 必须是数组")
        clusters = []
    keys = [c.get("key") for c in clusters if isinstance(c, dict)]
    if len(keys) != len(set(keys)):
        problems.append(f"clusters.key 重复：{sorted(k for k in set(keys) if keys.count(k) > 1)}")
    missing_clusters = sorted(set(EXPECTED_CLUSTER_COUNTS) - set(keys))
    extra_clusters = sorted(set(keys) - set(EXPECTED_CLUSTER_COUNTS))
    if missing_clusters:
        problems.append(f"缺簇：{missing_clusters}")
    if extra_clusters:
        problems.append(f"多出未登记的簇：{extra_clusters}")
    for c in clusters if isinstance(clusters, list) else []:
        if not isinstance(c, dict):
            problems.append("簇项必须是对象")
            continue
        title = c.get("title")
        if not isinstance(title, dict) or any(
            not str(title.get(lang, "")).strip() for lang in LIBRARY_LANGUAGES
        ):
            problems.append(f"簇 {c.get('key')} 的 title 缺 en/zh")

    prompts = payload.get("prompts")
    if not isinstance(prompts, list):
        problems.append("prompts 必须是数组")
        return problems

    if len(prompts) != EXPECTED_TOTAL:
        problems.append(f"提示条数应为 {EXPECTED_TOTAL}，实得 {len(prompts)}")

    ids = [p.get("id") for p in prompts if isinstance(p, dict)]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        problems.append(f"id 重复：{dupes}")

    per_cluster: Dict[str, int] = {}
    for p in prompts:
        if not isinstance(p, dict):
            problems.append("提示项必须是对象")
            continue
        pid = str(p.get("id") or "?")
        cluster = p.get("cluster")
        if cluster in EXPECTED_CLUSTER_COUNTS:
            per_cluster[cluster] = per_cluster.get(cluster, 0) + 1
        else:
            problems.append(f"{pid} 的 cluster 未登记：{cluster!r}")
        if p.get("function") not in FUNCTIONS:
            problems.append(f"{pid} 的 function 不在枚举 {FUNCTIONS} 内：{p.get('function')!r}")
        for field in LOCALIZED_FIELDS:
            value = p.get(field)
            if not isinstance(value, dict):
                problems.append(f"{pid}.{field} 必须是 {{en, zh}} 对象")
                continue
            for lang in LIBRARY_LANGUAGES:
                if not str(value.get(lang, "")).strip():
                    problems.append(f"{pid}.{field}.{lang} 为空")

    for cluster, expected in EXPECTED_CLUSTER_COUNTS.items():
        got = per_cluster.get(cluster, 0)
        if got != expected:
            problems.append(f"簇 {cluster} 应有 {expected} 条，实得 {got}")

    problems.extend(_banned_problems(payload))
    return problems


_cache: Optional[Dict[str, Any]] = None


def library() -> Dict[str, Any]:
    """加载并校验内容文件；缓存解析结果（内容文件只在构建期变，不做热重载）。"""
    global _cache
    if _cache is None:
        raw = json.loads(CONTENT_PATH.read_text(encoding="utf-8"))
        problems = validate(raw)
        if problems:
            raise PromptLibraryError(problems)
        _cache = raw
    return _cache


def reset_cache() -> None:
    """测试用：让下一次 `library()` 重新读文件。"""
    global _cache
    _cache = None


def version() -> str:
    return str(library()["version"])


def get(prompt_id: str) -> Optional[Dict[str, Any]]:
    for p in library()["prompts"]:
        if p.get("id") == prompt_id:
            return p
    return None


def resolve_language(language: Optional[str]) -> tuple[str, bool]:
    """请求语言 → (实际使用语言, 是否回退)。

    归一化口径与 `backend/core/language.py` 一致（`zh-CN` → `zh`）。
    """
    code = (language or FALLBACK_LANGUAGE).split("-")[0].lower()
    if code in LIBRARY_LANGUAGES:
        return code, False
    return FALLBACK_LANGUAGE, True


def _text(value: Any, language: str) -> str:
    if isinstance(value, dict):
        return str(value.get(language) or value.get(FALLBACK_LANGUAGE) or "")
    return str(value or "")


def list_prompts(cluster: Optional[str] = None, language: Optional[str] = None) -> Dict[str, Any]:
    """提示库列表（契约：`GET /api/v1/reflection-prompts`）。

    `cluster` 传未登记的簇名＝空列表 + 该簇不在 `clusters` 里，不报错：
    前端按簇 tab 逐个请求，一个过期簇名不该让整屏白掉。
    """
    data = library()
    used, fallback = resolve_language(language)

    rows = [p for p in data["prompts"] if cluster is None or p["cluster"] == cluster]
    prompts = [
        {
            "id": p["id"],
            "cluster": p["cluster"],
            "function": p["function"],
            "title": _text(p["title"], used),
            "question": _text(p["question"], used),
            "why": _text(p["why"], used),
            "follow_up": _text(p["follow_up"], used),
        }
        for p in rows
    ]

    # clusters 计数始终按**全库**给，前端切簇 tab 时不会看到「当前簇 8、其余 0」。
    counts: Dict[str, int] = {}
    for p in data["prompts"]:
        counts[p["cluster"]] = counts.get(p["cluster"], 0) + 1
    clusters = [
        {
            "key": c["key"],
            "title": _text(c.get("title"), used),
            "hint": _text(c.get("hint"), used),
            "count": counts.get(c["key"], 0),
        }
        for c in data["clusters"]
    ]

    return {
        "version": data["version"],
        "language_used": used,
        "fallback": fallback,
        "total": len(prompts),
        "clusters": clusters,
        "prompts": prompts,
    }
