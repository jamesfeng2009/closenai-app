"""``observation.extract`` 的 LLM 契约：提示词、取值面、以及**怎么把模型的回答读回来**。

三条口径决定了这个文件为什么长这样：

1. **摘录必须是原文子串**。V3 Baseline 不变量 1「没有可追溯证据的 Pattern 不得展示为事实」
   的可执行形式就在 ``parse_items`` 的 ``excerpt in content`` 那一格：模型一旦改写原话，
   回链给用户看的「依据」就不再是他写过的东西。这一条在 T2-04 还会由 Go 侧再判一次
   （双层校验），但**主闸在这里**——Go 不读 legacy 原文表，判不了子串。
   子串之外还有一格**长度**（``MAX_EXCERPT_CHARS``）：它判的不是「指指得回吗」而是
   「这一栏摊给用户看会不会变成一整页」，两层判据不重叠。
2. **``evidence_type`` 是模型面的词表，不是线上的字段值**。这里五值是「这条观察属于哪类」，
   上线前要过 ``handlers/observation.py`` 的 ``WIRE_EVIDENCE_TYPE`` 映射成
   Go ``allowedOrigins``（``gate.go:60-62``）那六值之一。为什么不在 prompt 里直接要六值：
   那六值是按**可靠度来源**切的（direct/repeated/temporal/…），模型分不出「用户明说的偏好」
   该算 direct 还是 value_statement，而分错的代价是算分档位虚高。分两层：模型管语义，
   映射表管可靠度，映射是一张看得见的表，改它要过一次评审。
3. **模型不许自报终局分**。``strength`` 只作输入留进 ``metadata_json``（03 §5:97-108 逐字
   「LLM confidence 是输入之一，绝不是终局分」），而 ``explicitness``／``specificity``
   这两个真正进算分的档位**由本文件的映射表给**，不由模型给。

不连真 LLM 的单测靠 ``parse_items`` 与 ``build_prompt`` 都是纯函数这一条。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

#: 提示词与产出契约的版本号。它要能进候选（``metadata_json.promptVersion``），
#: 因为「同一句原文、不同 prompt 版本抽出不同观察」是排障时的第一问。
#:
#: 2026-10-19 从 ``obs-v1`` 升到 ``obs-v2``：指令第 1 条加了摘录长度那一句。
#: 版本号不许原地不动——否则两条 ``promptVersion`` 相同的证据出自不同指令，
#: 而「换个 prompt 重跑」这件事的起点就查不出来了。
PROMPT_VERSION = "obs-v2"

#: 一次提取最多出几条。工作单 T2-02 的「宁少不滥」落在这里：
#: 上限做成常量而不是散在 prompt 里的数字，是让「改成 5 条」这件事必须改代码、
#: 并且被 ``test_observation_contract_numbers_are_single_sourced`` 抓到。
MAX_ITEMS = 3

#: 模型面的观察类型取值面（超出这个集合的一条直接丢，不进映射表）。
OBSERVATION_TYPES: tuple[str, ...] = (
    "stated_preference",
    "repeated_behavior",
    "emotional_pattern",
    "value_statement",
    "relationship_pattern",
)

#: 每条观察最多几个词——超出就丢这一条而不是截断：截断会造出一句用户没说过、
#: 又指不回原文的话，那正是子串校验要防的形状。
MAX_OBSERVATION_CHARS = 160

#: 摘录（＝回链摊给用户看的那句原文引用）最多几个字符。超了同样**丢整条**而不是截断：
#: 截出来的半句既不是用户写完的意思，又会让「依据」那一栏看起来像断章取义。
#:
#: ⚠️ 这一格与列宽无关：``evidence.excerpt`` 在 DDL 里是 TEXT（`c8a2e4b6d0f2:138`），
#: 数据库不会拒 501 字，是**我们**不许把整页塞进一句引用里。对面那一格住在
#: ``go-core/internal/domain/service_evidence.go:MaxExcerptChars``（超限永久拒），
#: 两侧由 ``test_ai_mq_contract.py`` 钉成同值。为什么这里也要判：生产者出网前就该丢条，
#: 让 Go 拒等于把一条永远非法的候选投上网络、撞完阶梯再进 DLQ。
#:
#: 计数口径是**码点**（Python 的 ``len(str)``），与 Go 侧的 ``utf8.RuneCountInString``
#: 同形：按 UTF-8 字节数判会让同一条中文摘录在两侧得出不同结论。
MAX_EXCERPT_CHARS = 500

#: 模型自报强度只作输入留痕（终局分由 Go 算），所以取值面也钉死，避免自由文本混进 metadata。
STRENGTHS: frozenset[str] = frozenset({"low", "medium", "high"})

_ROLE_PATTERN = re.compile(r"^(支持|支撑|supporting|contradicting|反对|反驳)$")

#: system 侧提示词。为什么要有独立的一句而不是沿用 ``llm.complete`` 的默认
#: （"a helpful and caring AI companion"）：那句会把模型带回「陪着聊」的语域，
#: 而这一格的产出**只有一个 JSON 对象**——多一句客套就是一次解析失败。
#: 中英同文：契约面（键名、取值）全是英文标识符，翻译它只会造出第二种形状。
SYSTEM_PROMPT = (
    "You extract evidence from a single user-written record and answer with one strict JSON "
    "object and nothing else: no greeting, no explanation, no markdown. You never invent text: "
    "an excerpt you cannot copy verbatim from the record is an item you do not output."
)

_INSTRUCTION = (
    "You are extracting evidence from ONE record written by the user.\n"
    "Rules:\n"
    "1) Every item MUST carry an \"excerpt\" that is a VERBATIM substring of the record. "
    "Copy it character for character; do not translate, shorten, fix punctuation or paraphrase. "
    f"Keep each excerpt under {MAX_EXCERPT_CHARS} characters: quote the shortest span that still "
    "stands on its own. If you cannot copy exact text, do not output that item.\n"
    "2) \"observation\" is ONE short sentence about what the record shows. Phrase it as "
    "co-occurrence, never as a cause: no \"because\", no \"leads to\", no diagnosis wording. "
    "Write it in the same language as the record.\n"
    "3) \"evidence_type\" MUST be one of: "
    + ", ".join(OBSERVATION_TYPES)
    + ".\n"
    "4) \"role\" MUST be one of: supporting, contradicting. Use contradicting only when this "
    "record runs against something the user said elsewhere.\n"
    "5) \"strength\" MUST be one of: "
    + ", ".join(sorted(STRENGTHS))
    + ". It is only your impression; the final score is computed elsewhere.\n"
    f"6) Output at most {MAX_ITEMS} items. Prefer fewer: drop an item when the record only "
    "hints at it.\n"
    "7) Output strict JSON and nothing else: {\"items\":[{\"evidence_type\":str,\"role\":str,"
    "\"excerpt\":str,\"observation\":str,\"strength\":str}]}. No extra keys, no comments, "
    "no markdown fences. When nothing is directly supported, output {\"items\":[]}.\n"
)


@dataclass(frozen=True)
class ObservationItem:
    """一条过了模型面契约的观察（还没过子串校验，那是 ``parse_items`` 之后的事）。"""

    evidence_type: str
    role: str
    excerpt: str
    observation: str
    strength: str


def build_prompt(content: str, negatives: tuple[str, ...] = ()) -> str:
    """拼 user 侧提示词。

    ``negatives``＝该用户近期纠正／拒绝过的归纳（工作单 §怎么做.5）。它排在指令之前、
    原文之后各一次：放在指令里会被当成「示例内容」，放在原文里会被当成「用户写的东西」。
    """
    lines = [_INSTRUCTION]
    if negatives:
        lines.append(
            "The user has corrected or rejected these summaries before. "
            "Do NOT generalise them again:\n"
            + "\n".join(f"- {item}" for item in negatives)
            + "\n"
        )
    lines.append(f"Record:\n<<<\n{content}\n>>>\n")
    return "\n".join(lines)


def extract_json(raw: str) -> Mapping[str, Any]:
    """从模型回答里取出那个 JSON 对象。

    容错只做**一层**（剥掉 ``` 代码围栏、截最外层花括号）：越界的语法一律抛
    ``ValueError``，由 handler 转成瞬时失败重试，而不是在这里猜一个「看起来像」的片段——
    猜出来的片段会把一条畸形回答读成零条观察，而「零条」在日志里长得像「这次确实没东西」，
    那是最难发现的一种静默。
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("模型回答为空")
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"回答里找不到 JSON 对象：{text[:80]!r}")
    decoded = json.loads(text[start : end + 1])
    if not isinstance(decoded, dict):
        raise ValueError(f"顶层不是 JSON 对象而是 {type(decoded).__name__}")
    return decoded


def answered_items(raw: str) -> int:
    """模型这一答**给了几条**（校验之前的条数）。

    只有 handler 的分态需要它：``evidence_extracted_total{result=invalid}`` 要的是
    「模型给了东西、但全部被闸掉」，而 ``parse_items`` 的返回值已经把不合格的丢了，
    从它的长度分不出「确实没东西」和「全在编」这两种零产出（台账 §口径.4）。

    ⚠️ 只在 ``parse_items`` **已经成功**之后调用：那条路径保证了 ``extract_json`` 不抛、
    且 ``items`` 是一个数组。换到别处调用就要自己处理 ``ValueError``，那会把一次正常抽取
    变成崩溃——而这一格要的恰恰是「数一下」。
    """
    return len(extract_json(raw)["items"])


def parse_items(raw: str, content: str, *, limit: int = MAX_ITEMS) -> tuple[ObservationItem, ...]:
    """把模型回答读成**已经过子串校验**的观察序列。

    返回顺序是把不合格的那几条丢掉之后的顺序，再按 ``(observation, excerpt)`` 重排：
    排序是为了让「同一次生成的第几条」这件事与模型的输出顺序无关——候选 id 的尾巴由
    ``item_key`` 派生（见 ``registry.Proposal``），而 ``item_key`` 由这里的字段拼成，
    模型换个顺序就换 id 的话，inbox 的幂等键就白设了。
    """
    payload = extract_json(raw)
    items = payload.get("items")
    if items is None:
        raise ValueError('回答里没有 items 这一键（契约要求 {"items": [...]}）')
    if not isinstance(items, list):
        raise ValueError(f"items 不是数组而是 {type(items).__name__}")

    kept: list[ObservationItem] = []
    seen: set[tuple[str, str]] = set()
    for entry in items:
        if not isinstance(entry, dict):
            continue
        evidence_type = str(entry.get("evidence_type") or "").strip()
        excerpt = str(entry.get("excerpt") or "")
        observation = str(entry.get("observation") or "").strip()
        role = str(entry.get("role") or "").strip().lower()
        strength = str(entry.get("strength") or "").strip().lower()
        if evidence_type not in OBSERVATION_TYPES:
            continue
        if not observation or len(observation) > MAX_OBSERVATION_CHARS:
            continue
        # ⚠️ 子串校验用**不 strip 的 excerpt**：模型在引文前后补的空格也是它加的，
        # 但 strip 之后再判会把「原文里根本没有这段」读成「有」。
        if not excerpt.strip() or excerpt not in content:
            continue
        # 长度闸排在子串之后：先确认「这句话用户真写过」，再问「写得太长能不能给用户看」。
        # 反过来的话，一条既超长又不是子串的摘录会被记成超长，而排障要的是「模型在编」。
        if len(excerpt) > MAX_EXCERPT_CHARS:
            continue
        if not _ROLE_PATTERN.match(role):
            continue
        identity = (observation, excerpt)
        if identity in seen:  # 同一条观测被说了两遍：只留第一条
            continue
        seen.add(identity)
        kept.append(
            ObservationItem(
                evidence_type=evidence_type,
                role="contradicting" if role in ("contradicting", "反对", "反驳") else "supporting",
                excerpt=excerpt,
                observation=observation,
                strength=strength if strength in STRENGTHS else "medium",
            )
        )
    return tuple(sorted(kept, key=lambda i: (i.observation, i.excerpt))[:limit])
