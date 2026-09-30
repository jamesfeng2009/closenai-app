"""#42 批1：后端 15 语言文案库（core/copy.py + data/copy/*.json）守卫。

防止"新增键只写了 zh/en"静默降级：
1. 15 文件全部存在且 JSON 有效；
2. 键集与 en 完全一致（en 为全集，缺键回退 en 只是运行时兜底，不是偷懒借口）；
3. 数组长度一致；
4. 非拉丁语言（ko/ja/ru/th/zh/km）值真的落成该文字（不含纯 ASCII 兜底）；
5. locale_for 归一 + t() 两级回退行为正确。
"""
import json
import re
from pathlib import Path

import pytest

from backend.core.copy import SUPPORTED_LOCALES, locale_for, t, t_list

DATA_DIR = Path("backend/data/copy")

# 非拉丁文字语言 → 该文字必须出现的正则（抽查若干键即可覆盖"纯英文兜底"）
_SCRIPT_CHECKS = {
    "zh": re.compile(r"[\u4e00-\u9fff]"),
    "ko": re.compile(r"[\uac00-\ud7af]"),
    # 日语汉字（主人公）与假名同为"真日语"，正则两者皆收
    "ja": re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]"),
    "ru": re.compile(r"[\u0400-\u04ff]"),
    "th": re.compile(r"[\u0e00-\u0e7f]"),
    "km": re.compile(r"[\u1780-\u17ff]"),
}


def _load(locale: str) -> dict:
    return json.loads((DATA_DIR / f"{locale}.json").read_text(encoding="utf-8"))


def _strip_comments(node):
    """剔除 `_comment` 等下划线注释键（不参与键集比对）。"""
    if isinstance(node, dict):
        return {k: _strip_comments(v) for k, v in node.items() if not str(k).startswith("_")}
    if isinstance(node, list):
        return [_strip_comments(v) for v in node]
    return node


def test_all_15_files_exist_and_valid_json():
    for loc in SUPPORTED_LOCALES:
        doc = _load(loc)
        assert isinstance(doc, dict) and doc, loc


def test_key_sets_match_en_across_all_locales():
    en = _strip_comments(_load("en"))
    for loc in SUPPORTED_LOCALES:
        if loc != "en":
            _assert_same_shape(en, _strip_comments(_load(loc)), f"{loc}.")


def _assert_same_shape(en: dict, other: dict, path: str):
    """键集一致 + 数组长度一致（值不比内容）。"""
    assert set(en.keys()) == set(other.keys()), f"键集不一致 @ {path}"
    for key, value in en.items():
        if isinstance(value, dict):
            _assert_same_shape(value, other[key], f"{path}{key}.")
        elif isinstance(value, list):
            assert len(value) == len(other[key]), f"数组长度不一致 @ {path}{key}"


_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def test_placeholders_match_en_across_all_locales():
    """占位符（{name}/{mbti}/{tail}…）集合必须与 en 完全一致。

    模板靠 str.format 注入占位符：某语言漏写/写错占位符会 KeyError 崩溃
    或漏注数据。数组按索引比对，dict 按键递归。
    """
    en = _strip_comments(_load("en"))

    def _walk(en_node, other_node, loc, path):
        if isinstance(en_node, dict):
            for key in en_node:
                _walk(en_node[key], other_node[key], loc, f"{path}.{key}")
        elif isinstance(en_node, list):
            for i, (a, b) in enumerate(zip(en_node, other_node)):
                _walk(a, b, loc, f"{path}[{i}]")
        elif isinstance(en_node, str):
            en_ph = set(_PLACEHOLDER.findall(en_node))
            other_ph = set(_PLACEHOLDER.findall(other_node))
            assert en_ph == other_ph, f"{loc}{path} 占位符不一致: {en_ph} vs {other_ph}"

    for loc in SUPPORTED_LOCALES:
        if loc != "en":
            _walk(en, _strip_comments(_load(loc)), loc, "")


def test_no_english_verbatim_fallback_in_other_locales():
    """非 en 语言不允许任何值逐字等于英文（同 journey/前端 #39 守卫口径）。

    例外：跨语言同形外来语（如 es/id/ms/it 的 "Virtuoso" 拼写与英文完全一致），
    见 LOANWORDS 白名单——按语言放行，防止误放行真正的英文兜底。
    """
    en = _strip_comments(_load("en"))
    loanwords = {
        "es": {"Virtuoso"},
        "id": {"Virtuoso"},
        "ms": {"Virtuoso"},
        "it": {"Virtuoso"},
        "pt": {"Virtuoso"},
        "de": {"Protagonist", "Individualist", "Loyalist", "Enthusiast"},
        "fr": {"Entrepreneur", "Consul"},
    }

    def _walk(en_node, other_node, loc, path):
        if isinstance(en_node, dict):
            for key in en_node:
                _walk(en_node[key], other_node[key], loc, f"{path}.{key}")
        elif isinstance(en_node, list):
            for i, (a, b) in enumerate(zip(en_node, other_node)):
                _walk(a, b, loc, f"{path}[{i}]")
        elif isinstance(en_node, str) and isinstance(other_node, str):
            if en_node == other_node and other_node not in loanwords.get(loc, set()):
                pytest.fail(f"{loc}{path} 逐字等于英文兜底：{en_node!r}")

    for loc in SUPPORTED_LOCALES:
        if loc != "en":
            _walk(en, _strip_comments(_load(loc)), loc, "")


def test_non_latin_scripts_actually_use_target_script():
    """ko/ja/ru/th/zh/km 至少在 mood 标签、等级名与 MBTI 名处出现目标文字。"""
    for loc, pattern in _SCRIPT_CHECKS.items():
        doc = _load(loc)
        assert pattern.search(doc["mood"]["labels"]["happy"]), loc
        assert pattern.search(doc["levels"]["name"]["4"]), loc
        assert pattern.search(doc["mbti"]["ENFJ"]), loc


def test_locale_for_normalization():
    assert locale_for("zh-CN") == "zh"
    assert locale_for("zh") == "zh"
    assert locale_for("en-US") == "en"
    assert locale_for("pt-BR") == "pt"
    for loc in ("es", "ko", "ja", "ru", "id", "ms", "th", "de", "fr", "it", "pt", "vi", "km"):
        assert locale_for(loc) == loc
    assert locale_for("fr-CA") == "fr"
    # 未知码与空值回退 en
    assert locale_for("xx-Unknown") == "en"
    assert locale_for("") == "en"
    assert locale_for(None) == "en"


def test_t_falls_back_to_en_then_key_name():
    # 正常取值
    assert t("mood", "labels.happy", "zh") == "开心"
    assert t("mood", "labels.happy", "es") == "Feliz"
    assert t("mood", "labels.happy", "de") == "Fröhlich"
    # 完全不存在的键 → 返回键名（显式可见，不静默空串）
    assert t("mood", "labels.__nope__", "zh") == "mood.labels.__nope__"
    # 数组取值
    assert len(t_list("levels", "features.3", "ko")) == 3
    assert t_list("levels", "features.3", "en") == [
        "Deep personality analysis", "Pattern discovery", "AI weekly report",
    ]
    assert t_list("levels", "features.__nope__", "zh") == []


def test_msg_style_push_templates_render_for_all_locales():
    """M2 冒烟：msg/style/push 三 namespace 全语言可用，占位符 format 不崩。"""
    push_ctx = {"name": "A", "mood": "开心", "milestone": 7}
    for loc in SUPPORTED_LOCALES:
        # 兜底回复轮换（4 条）
        assert len(t_list("msg", "fallback", loc)) == 4
        # 跟进降级 + 占位符注入
        assert "XYZ" in t("msg", "follow_up_fallback", loc).format(excerpt="XYZ")
        # Day1 开场（mbti / 无 mbti 两分支，{tail} 均在句尾）
        tail = t("msg", "day1.tail", loc)
        assert t("msg", "day1.intro_mbti", loc).format(mbti="INTP", tail=tail).endswith(tail)
        assert t("msg", "day1.intro_plain", loc).format(mbti="", tail=tail).endswith(tail)
        # 里程碑 4 档 + 主动消息三开场
        for d in ("7", "30", "100", "365"):
            assert t("msg", f"milestone.{d}", loc)
        assert t("msg", "proactive.memory_opener", loc).format(first="X")
        assert t("msg", "proactive.mood_opener", loc).format(mood_desc="平静")
        assert t("msg", "proactive.plain", loc)
        # cron 文案（再 engage / 漂移提醒 / 试用提醒）
        assert t("msg", "reengage", loc)
        assert t("msg", "drift_alert", loc).format(memory_content="X")
        assert t("msg", "trial_reminder", loc)
        # 对话风格指令段
        for key in ("warm", "rational", "philosophical", "safety_note"):
            assert t("style", key, loc)
        # 推送 6 场景模板池可渲染
        for sc in ("proactive", "mood_reminder", "weekly", "milestone", "care", "bedtime"):
            pool = t_list("push", sc, loc)
            assert pool, (loc, sc)
            for item in pool:
                item["title"].format(**push_ctx)
                if item.get("body"):
                    item["body"].format(**push_ctx)
