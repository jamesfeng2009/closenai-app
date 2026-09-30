"""服务端直出文案的 15 语言模板库（#42 · PRD i18n 纪律）。

前端 locale 已 15 语言（tasks.md L1-L4）；本模块把**后端直出文案**（标签/模板/降级
文案）从 zh/en 二值字典迁到同一口径：`backend/data/copy/{locale}.json` × 15。

约定：
1. 语言码归一：`users.language` 只有两个用途——zh* 归一 `zh`，其余按 15 码前缀匹配，
   未匹配回退 `en`（与前端 fallbackLng='en' 同口径）。
2. 键缺失回退：目标语言缺键取 `en` 同键；en 也缺（开发期错误）记 error 并返回键名，
   不静默返回空串、不抛错（文案层永不阻断主流程）。
3. 进程内缓存：模块加载后只读；改 JSON 需重启（与 journey/first_read 模板库一致）。
4. 守卫：`backend/tests/test_copy_library.py` 强制 15 文件键集/占位符一致、
   非拉丁语言值含目标文字——防"新增键只写了 en"静默降级。
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("closenai.copy")

_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "copy"
_DEFAULT_LOCALE = "en"

# 15 种支持语言（与 users.language / X-Language 同一清单；顺序即前缀匹配序）
SUPPORTED_LOCALES: Tuple[str, ...] = (
    "zh", "en", "es", "ko", "ja", "ru", "id", "ms", "th", "de", "fr", "it", "pt", "vi", "km",
)

_STORE: Dict[str, dict] = {}


def locale_for(language: Optional[str]) -> str:
    """users.language → 文案库语言码；未知码回退 en。"""
    code = (language or "").lower().strip()
    if not code:
        return _DEFAULT_LOCALE
    for loc in SUPPORTED_LOCALES:
        if code.startswith(loc):
            return loc
    return _DEFAULT_LOCALE


def _load(locale: str) -> dict:
    """加载单语言文案库（缺文件记 error 并返回空 dict——键缺失链会落到键名兜底）。"""
    if locale in _STORE:
        return _STORE[locale]
    path = _DATA_DIR / f"{locale}.json"
    try:
        _STORE[locale] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("文案库缺失或损坏：%s (%s)", path, exc)
        _STORE[locale] = {}
    return _STORE[locale]


def _lookup(store: dict, path: Tuple[str, ...]) -> Optional[Any]:
    node: Any = store
    for part in path:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def t(namespace: str, key: str, language: Optional[str]) -> str:
    """取文案 `namespace.key`：目标语言缺键回退 en；en 也缺返回键名（显式可见）。"""
    path = (namespace, *key.split("."))
    value = _lookup(_load(locale_for(language)), path)
    if isinstance(value, str) and value:
        return value
    value = _lookup(_load(_DEFAULT_LOCALE), path)
    if isinstance(value, str) and value:
        return value
    logger.error("文案键缺失：%s.%s", namespace, key)
    return f"{namespace}.{key}"


def t_list(namespace: str, key: str, language: Optional[str]) -> list:
    """取文案数组（如等级解锁项列表）：同样两级回退，en 也缺返回空数组。"""
    path = (namespace, *key.split("."))
    value = _lookup(_load(locale_for(language)), path)
    if isinstance(value, list):
        return value
    value = _lookup(_load(_DEFAULT_LOCALE), path)
    if isinstance(value, list):
        return value
    logger.error("文案数组键缺失：%s.%s", namespace, key)
    return []
