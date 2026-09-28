"""内容安全服务（三层防御之 L3 输出后置过滤，唯一事实源）。

三层防御（详见 .trae/skills/closenai-llm/SKILL.md）：
- L1 Prompt 防御：SAFETY_BOUNDARY + EVIDENCE_POLICY 两段注入所有面向用户的 system prompt
- L2 Provider 原生审核：DashScope/Gemini 自带，命中走现有 LLM 降级兜底
- L3 输出后置过滤：本模块。流式按句缓冲闸门 + 非流式全量检测（确定性黑名单/句式 → 语义分类器）。

设计铁律：
- 用户发送路径零阻塞：输入不预检，检测全在输出端与生成并行
- 检测自身失败 fail-open（超时/异常 → 放行 + 日志），禁止阻断主流程
- 自伤信号不走拒答（由 L1 主模型关怀回应），本模块只拦 AI 违规输出

业务代码禁止复制检测判断逻辑，统一走本模块（同 quota_service 模式）。
"""
import asyncio
import logging
import re
from typing import AsyncGenerator, Awaitable, Callable, List, Optional, Tuple

from backend.core.llm import llm

logger = logging.getLogger("closenai.safety")


def _lang_key(language: str) -> str:
    """语言归一：zh* → zh，其余 → en（与现有双表文案约定一致）。"""
    return "zh" if (language or "").startswith("zh") else "en"


class SentenceGate:
    """流式输出按句缓冲闸门（L3 流式路径）。

    用法：
        gate = safety_service.sentence_gate(language)
        async for chunk in llm.stream(...):
            async for text in gate.feed(chunk):
                yield text            # SSE 下发
            if gate.blocked:
                break                 # 命中后停止消费 LLM 流
        async for text in gate.flush():
            yield text

    行为：
    - 按句读（。！？!?.\\n）切句，句完整后先检测后放行
    - 无句读超 max_sentence_len 字强制切句检测
    - 命中：不下发违规句，改发安全话术，blocked=True，后续 feed/flush 不再产出
    - 检测异常：fail-open 放行该句（check_text 内部已保证不抛异常）
    """

    # 句读字符（中英），连续句读与其后空白归入上一句
    _SENTENCE_END = re.compile(r"[。！？!?.\n]+\s*")

    def __init__(
        self,
        check: Callable[[str], Awaitable[bool]],
        safe_reply: str,
        max_sentence_len: int = 200,
    ):
        self._check = check
        self._safe_reply = safe_reply
        self._max_len = max_sentence_len
        self._buf = ""
        self.blocked = False

    async def feed(self, chunk: str) -> AsyncGenerator[str, None]:
        """喂入一段 LLM token，放行通过检测的完整句。"""
        if self.blocked:
            return
        self._buf += chunk
        async for text in self._drain(final=False):
            yield text

    async def flush(self) -> AsyncGenerator[str, None]:
        """LLM 流正常结束：检测并放行残余不足一句的文本。"""
        if self.blocked:
            return
        async for text in self._drain(final=True):
            yield text

    async def _drain(self, final: bool) -> AsyncGenerator[str, None]:
        while True:
            sentence, rest = self._pop_sentence(self._buf)
            if sentence is None:
                break
            self._buf = rest
            if await self._check(sentence):
                yield sentence
            else:
                self.blocked = True
                yield self._safe_reply
                return
        if final and self._buf:
            rest, self._buf = self._buf, ""
            if await self._check(rest):
                yield rest
            else:
                self.blocked = True
                yield self._safe_reply

    def _pop_sentence(self, buf: str) -> Tuple[Optional[str], str]:
        """切出首个完整句（含句读与尾部空白）；无完整句返回 (None, buf)。"""
        m = self._SENTENCE_END.search(buf)
        if m:
            return buf[: m.end()], buf[m.end():]
        if len(buf) >= self._max_len:
            return buf[: self._max_len], buf[self._max_len:]
        return None, buf


class SafetyService:
    """内容安全检测（L1 prompt 段落 + L3 输出检测）。"""

    # L1：注入所有面向用户 LLM system prompt 的安全边界段落（中英双表）
    SAFETY_BOUNDARY = {
        "zh": (
            "安全边界（必须遵守）："
            "1) 不参与政治或宗教争论，用户提起时温和引导回个人成长话题；"
            "2) 绝不生成仇恨、暴力、恐吓或色情内容；"
            "3) 不给极端人生建议（如直接劝辞职/分手），提供平衡视角；"
            "4) 若用户流露自伤或危机信号，以关怀回应，鼓励寻求专业心理援助或当地求助资源。"
        ),
        "en": (
            "Safety boundaries (must follow): "
            "1) Do not engage in political or religious debates; gently redirect to personal growth topics. "
            "2) Never generate hateful, violent, threatening, or sexually explicit content. "
            "3) Never give extreme life advice (e.g. telling the user to quit a job or end a relationship) — offer balanced perspectives. "
            "4) If the user shows signs of self-harm or crisis, respond with care and encourage seeking professional help or a local helpline."
        ),
    }

    # L1：证据分级与措辞纪律段落（《沟通证据与措辞规范-2026-09-28》§一）。
    # 决策点 1：独立段落、与 boundary() 平级同注入点，不压缩进 boundary——两者关注点不同，
    # 合并会超载且无法单测隔离。
    EVIDENCE_POLICY = {
        "zh": (
            "证据与措辞纪律（必须遵守）："
            "1) 按证据强度选措辞：A 级（官方求助资源、法规要求）可陈述事实；"
            "B 级（心理学概念与研究）必须带限制语，写成「研究提示……，不等于……」；"
            "C 级（用户自己记录里的观察）只允许共现措辞，如「在你的记录里，X 与 Y 同时出现过」；"
            "D 级（星座配对、爱情语言、MBTI、九型等流行框架）仅可复述用户对自己的描述，"
            "禁止用于预测或定性任何第三方。"
            "2) 禁止四类推理错误：① 相关当因果——不得用「导致/说明/因为……所以」连接两条记录，"
            "只用「伴随/同时出现/在记录中相关」；② 平均当个人——不得把群体统计结论当作对某个具体的人"
            "（用户提到的对方）的判断；③ 标签当解释——不得用类型标签替代「触发情境—具体行为—可观察后果」"
            "的描述；④ 技巧脱离权力——疑似不平等或胁迫关系里，不得建议用户「更温柔表达/再沟通一次」，"
            "改为个人安全与求助方向。"
            "3) 结论强度闸门：记录内的共现永远不得升格成因果机制；观察幅度中等时降格为提问句，"
            "幅度低时不输出。禁止医疗化措辞（治疗/疗愈/诊断/处方/焦虑症/抑郁症/康复）"
            "与 D 级词汇（星座配对、爱情语言、高价值/低价值、PUA 话术）。"
        ),
        "en": (
            "Evidence and wording discipline (must follow): "
            "1) Match wording to evidence strength: level A (official helplines, legal requirements) may state facts; "
            "level B (psychology concepts and research) must carry a qualifier, e.g. \"research suggests ..., which is not the same as ...\"; "
            "level C (observations from the user's own records) may only state co-occurrence, e.g. \"in your records, X and Y appeared together\"; "
            "level D (pop frameworks such as zodiac compatibility, love languages, MBTI, Enneagram) may only restate how the user "
            "describes themselves, and must never be used to predict or characterise any third party. "
            "2) Four reasoning errors are forbidden: (i) correlation as causation — never link two records with "
            "\"causes / proves / because...so\"; use \"went together / appeared at the same time / correlated in your records\"; "
            "(ii) averages as individuals — never apply a population statistic as a judgement about a specific person "
            "(the other party the user mentions); (iii) labels as explanations — never substitute a type label for the "
            "\"trigger situation — concrete behaviour — observable consequence\" description; (iv) technique ignoring power — "
            "in a possibly unequal or coercive relationship, never advise the user to \"express more gently\" or \"talk it through again\"; "
            "redirect to personal safety and help-seeking. "
            "3) Conclusion gate: co-occurrence inside the records may never be upgraded into a causal mechanism; "
            "a medium-magnitude observation becomes a question; a low-magnitude one is not output at all. "
            "Medical wording (treat, heal, diagnose, prescription, anxiety disorder, depression, recovery) and level-D "
            "vocabulary (zodiac compatibility, love languages, high/low value, PUA scripts) are forbidden."
        ),
    }

    # L3 命中后的替换话术（话题转回成长，温和不指责；中英双表）
    SAFE_REPLY = {
        "zh": "这个话题我可能不太适合深入聊呢。不如说说，今天有什么让你印象深刻的事吗？",
        "en": "That's a topic I'd rather not dive into. How about telling me — what's one thing that stood out to you today?",
    }

    # L3 危机转介触发词两类（PRD §5.16 ④ / 规范 §二 2.2）。v1 仅 zh/en 词表（规范 §六 决策点 3），
    # 其余语言由 L1 boundary 第 4 条 + 语义分类器兜底。
    # 词表取多字信号词而非单字（"打"、"死"），把误报压到可接受范围：
    # 误报的代价是一条关怀文案 + 一次停推，漏报的代价是人。
    # 条目按正则匹配（en 忽略大小写）；`(?<!动)打我` 这类负向后视专防"打动我"误报。
    CRISIS_TRIGGERS = {
        "self_harm": {
            "zh": ("自杀", "自伤", "不想活", "活不下去", "结束生命", "结束自己的生命", "伤害自己",
                   "割腕", "轻生", "寻短见"),
            "en": ("suicide", "suicidal", "self-harm", "hurt myself", "kill myself", "end my life",
                   "end myself", "want to die", "die on purpose"),
        },
        "relationship_violence": {
            "zh": ("动手打", "殴打", "被打", "(?<!动)打我", "推我一把", "威胁我", "恐吓我", "跟踪我",
                   "砸东西", "摔东西", "不让见朋友", "不让见家人", "不许我见", "经济控制", "管我的钱",
                   "家暴"),
            "en": ("hits me", "hit me", "beat me", "beating me", "pushed me", "threatens me",
                   "threatened me", "stalks me", "stalking me", "smashes things", "throws things",
                   "won'?t let me see", "controls my money", "financial abuse",
                   "domestic violence", "abusive to me"),
        },
    }

    # 命中后的转介文案（A 级：热线事实 + 关怀；措辞过 §5.16 ① 黑名单）。
    # 关系暴力类按规范 §二 铁律：只走个人安全与法律求助，禁沟通/关系修复建议、禁归因到用户表达。
    CRISIS_COPY = {
        "self_harm": {
            "zh": (
                "我很在意你现在的安全，也谢谢你愿意说出来。"
                "现在请联系一个信任的人陪在你身边，或拨打 {hotline}——那里随时有人可以帮你。"
                "我不会替你下人生判断，但我会一直在这里，陪你先过好这一刻。"
            ),
            "en": (
                "Your safety matters to me, and thank you for telling me. "
                "Right now, please reach out to someone you trust and keep them with you, "
                "or contact {hotline} — someone there can help at any hour. "
                "I won't make life decisions for you, but I'll stay right here with you through this moment."
            ),
        },
        "relationship_violence": {
            "zh": (
                "你经历的这些不是你的错，也不是靠你换一种说法就能改变的。"
                "现在最重要的是你的人身安全：如果有即刻危险，请联系当地紧急号码，或告诉信任的人、"
                "邻居、同事让他们陪你；也可以拨打 {hotline} 求助。"
                "我不会给你沟通或关系修复方面的建议，只想陪你把安全这一步先确认好。"
            ),
            "en": (
                "What happened is not your fault, and it is not something you can fix by choosing "
                "different words. Your physical safety comes first: if you are in immediate danger, "
                "contact your local emergency number, or tell someone you trust, a neighbour or a "
                "coworker so they can stay with you; you can also contact {hotline}. "
                "I won't give you communication or relationship-repair advice — I only want to help "
                "you confirm the safety step first."
            ),
        },
    }

    # 地区热线（按 users.timezone 推断；PRD §5.16 ④「其余地区按配置表」→ 兜底走当地紧急号码）。
    # 无地区列可用（backend/models/user.py:25-27 仅 timezone/language），时区是当前唯一服务端信号。
    HOTLINES = {
        "cn": "12356（中国大陆全国统一心理援助热线）",
        "us": "988（美国 988 危机生命线）",
        "ca": "988（加拿大 988 危机支援线）",
        "fallback": "当地紧急求助号码（如 112 / 911）或当地心理援助热线",
    }

    # 检测调用超时（秒）：超时 fail-open 放行
    CHECK_TIMEOUT = 0.8

    # 句缓冲区强制切句长度（字）
    MAX_SENTENCE_LEN = 200

    # L3 确定性黑名单（PRD §5.16 ① 医疗化红线 + 措辞规范 §1.4 D 级词汇扩充）。
    # v1 仅 zh/en 词表（规范 §六 决策点 3），其余语言由 L1 段落 + 语义分类器兜底。
    # 注：「高价值/低价值」不入正则表——该词在本项目已用作 Discovery 幅度分级的内部术语
    # （discovery_service.py:24、message_generator 疲劳控制），纯正则会误杀；
    # 因此它只进 EVIDENCE_POLICY 段落与语义分类 prompt。
    # en 表另不含 treat / therapy：作普通动词与求助语境高频（"how they treat you"、"seek therapy"），
    # 正则误杀率过高；医疗化判定由 treatment + 语义分类器承担。
    BANNED_TERMS = {
        "zh": (
            "焦虑症", "抑郁症", "治疗", "疗愈", "诊断", "缓解焦虑", "处方", "康复", "心理健康服务",
            "星座配对", "星座运势", "爱情语言", "爱的五种语言", "话术", "PUA",
        ),
        "en": (
            "anxiety disorder", "depression", "depressive", "treatment", "heal",
            "diagnose", "diagnosis", "prescription", "medication", "rehab", "mental health service",
            "zodiac compatibility", "star sign", "horoscope", "fortune telling", "love language",
            "pick-up artist", "pua",
        ),
    }

    # L3 断言型出口的越界推理句式指纹（措辞规范 §1.2）。
    # 确定性规则只覆盖「对第三方的归因」这一条红线（§1.1：类型仅可描述用户自身，禁止推断他人）：
    # 第三方指称 + (类型标签 | 因果连接词) → 违规。
    # 不匹配「标签+因果但无第三方」——组合报告等性格功能天然要对自己的类型做解释（否则整功能被拦），
    # 该情形交给 L1 段落与语义分类器。
    # 只在 scope="assertion"（洞察/模式/报告/剧本/主动消息/分享卡等断言型文案）生效——
    # 对话流里"因为他没回你"是正常复述用户叙述，正则一律拦截会毁掉可用性，语义分类器负责。
    ATTRIBUTION_MARKERS = {
        "zh": {
            "third": (r"TA", r"(?<![其这那每])他", r"她", r"对方", r"男朋友", r"女朋友", r"伴侣",
                      r"老公", r"老婆", r"前任", r"另一半"),
            "label": (r"焦虑型", r"回避型", r"安全型", r"依恋", r"MBTI", r"九型", r"星座",
                      r"人格类型", r"性格类型", r"爱情语言"),
            "causal": (r"导致", r"致使", r"注定", r"说明", r"因此", r"所以", r"因为"),
        },
        "en": {
            "third": (r"\bhe\b", r"\bshe\b", r"\bhim\b", r"\bher\b", r"\bthey\b", r"\bpartner\b",
                      r"\bex\b", r"\bboss\b", r"\bcolleague\b"),
            "label": (r"attachment style", r"anxious attach", r"avoidant", r"\bmbti\b", r"enneagram",
                      r"zodiac", r"love language", r"personality type"),
            "causal": (r"\bbecause\b", r"\btherefore\b", r"\bmeans\b", r"\bproves\b", r"\bcauses?\b",
                      r"\bleads? to\b", r"\bis why\b", r"\bdestined\b"),
        },
    }

    _CHECK_PROMPT = (
        "你是内容安全与措辞合规分类器。判断以下 AI 生成文本是否包含：仇恨言论、暴力、恐吓、"
        "政治或宗教争议、色情内容、极端人生建议（如直接劝辞职/分手）、"
        "医疗化措辞（治疗/疗愈/诊断/处方/焦虑症/抑郁症/康复）、"
        "D 级流行框架词汇（星座配对/爱情语言/高价值或低价值/PUA 话术）、"
        "推理越界（把记录里的共现写成因果、用类型标签推断或定性第三方、把群体统计当成对某个具体的人的判断）。"
        "只回答 SAFE 或 UNSAFE，不要输出其他内容。\n\n文本：\n"
    )

    def boundary(self, language: str) -> str:
        """L1 安全边界段落（按语言）。"""
        return self.SAFETY_BOUNDARY[_lang_key(language)]

    def evidence_policy(self, language: str) -> str:
        """L1 证据分级与措辞纪律段落（按语言），与 boundary() 平级、同注入点。"""
        return self.EVIDENCE_POLICY[_lang_key(language)]

    def resolve_hotline(self, timezone: Optional[str]) -> str:
        """按 users.timezone 推断求助热线（PRD §5.16 ④ 分地区；无匹配走兜底）。"""
        tz = (timezone or "").strip()
        if tz in ("Asia/Shanghai", "Asia/Urumqi", "Asia/Chongqing", "Asia/Harbin"):
            return self.HOTLINES["cn"]
        if tz.startswith("America/"):
            return self.HOTLINES["us"]
        if tz.startswith("Canada/") or tz.startswith("America/Toronto") or tz == "Vancouver":
            return self.HOTLINES["ca"]
        return self.HOTLINES["fallback"]

    def crisis_intercept(
        self, text: str, language: str = "en", timezone: Optional[str] = None
    ) -> Optional[dict]:
        """危机触发词检测（PRD §5.16 ④ / 规范 §二）：命中返回转介载荷，未命中返回 None。

        返回 `{"kind", "matched", "hotline", "reply", "advice_blocked"}`：
        - kind 判定顺序 self_harm → relationship_violence（自伤优先，生命风险等级更高）
        - advice_blocked=True 表示按 §二 铁律禁用沟通/关系修复类建议（关系暴力类恒为 True）
        - reply 已按语言与地区热线渲染，调用方直接下发即可，不再走 LLM
        """
        if not text or not text.strip():
            return None
        lang = _lang_key(language)
        flags = re.IGNORECASE if lang == "en" else 0
        for kind in ("self_harm", "relationship_violence"):
            matched: List[str] = []
            for pattern in self.CRISIS_TRIGGERS[kind][lang]:
                m = re.search(pattern, text, flags)
                if m:
                    matched.append(m.group(0))
            if matched:
                hotline = self.resolve_hotline(timezone)
                return {
                    "kind": kind,
                    "matched": matched,
                    "hotline": hotline,
                    "reply": self.CRISIS_COPY[kind][lang].format(hotline=hotline),
                    "advice_blocked": kind == "relationship_violence",
                }
        return None

    def safe_reply(self, language: str) -> str:
        """命中替换话术（按语言）。"""
        return self.SAFE_REPLY[_lang_key(language)]

    def banned_hit(self, text: str, language: str) -> Optional[str]:
        """确定性黑名单命中判定（§5.16 ① + §1.4）。命中返回违规词，否则 None。"""
        lang = _lang_key(language)
        for term in self.BANNED_TERMS[lang]:
            if lang == "en":
                # 词边界匹配：substrings 会误杀（"treat" 命中 "retreat"）
                if re.search(rf"\b{re.escape(term)}", text, re.IGNORECASE):
                    return term
            elif term in text:
                return term
        return None

    def attribution_hit(self, text: str, language: str) -> Optional[str]:
        """断言型出口的推理越界判定（§1.2）。命中返回错误类别，否则 None。"""
        markers = self.ATTRIBUTION_MARKERS[_lang_key(language)]

        def _any(keys: tuple) -> Optional[str]:
            for pattern in keys:
                if re.search(pattern, text, re.IGNORECASE):
                    return pattern
            return None

        third = _any(markers["third"])
        label = _any(markers["label"])
        causal = _any(markers["causal"])
        if third and label:
            return "标签推断第三方"
        if third and causal:
            return "因果断言指向第三方"
        return None

    async def check_text(self, text: str, language: str = "en", *, scope: str = "chat") -> bool:
        """全量检测（非流式路径）。True=安全。

        两级判定：① 确定性黑名单（+ scope="assertion" 时的推理越界句式）——命中即拦，不调模型；
        ② lite 模型语义分类，不计入用户配额；超时/异常/不可解析 → fail-open True + 日志。
        """
        if not text or not text.strip():
            return True
        banned = self.banned_hit(text, language)
        if banned:
            logger.warning("safety L3 黑名单命中(%s)，拦截: %.100s", banned, text)
            return False
        if scope == "assertion":
            overreach = self.attribution_hit(text, language)
            if overreach:
                logger.warning("safety L3 推理越界(%s)，拦截: %.100s", overreach, text)
                return False
        try:
            result = await asyncio.wait_for(
                llm.complete(
                    f"{self._CHECK_PROMPT}{text}",
                    language=language,
                    lite=True,
                    max_tokens=10,
                    temperature=0.0,
                ),
                timeout=self.CHECK_TIMEOUT,
            )
            verdict = (result or "").strip().upper()
            safe = "UNSAFE" not in verdict
            if not safe:
                logger.warning("safety L3 命中拦截: %.100s", text)
            return safe
        except Exception:  # noqa: BLE001  fail-open：检测故障不阻断主流程
            logger.exception("safety 检测失败 fail-open 放行: %.100s", text)
            return True

    async def check_or(
        self, text: str, language: str, fallback: str, *, scope: str = "chat"
    ) -> str:
        """非流式便捷封装：text 违规时返回 fallback，否则返回 text。"""
        if text and text.strip() and not await self.check_text(text, language, scope=scope):
            return fallback
        return text

    def filter_template(self, text: str, language: str = "en") -> str:
        """静态模板文案出口的确定性闸（架构方案 §六-1）。命中返回 ""，调用方自行降级。

        只跑 banned_hit / attribution_hit，**不调 LLM**：模板由我方撰写、内容固定，
        词表与句式正则对它有 100% 把握；语义分类器留给不可控的 LLM 产出。
        命中即说明模板本身违反措辞规范，按错误级别记日志。
        """
        if not text or not text.strip():
            return ""
        banned = self.banned_hit(text, language)
        if banned:
            logger.error("静态模板文案命中黑名单(%s): %.100s", banned, text)
            return ""
        overreach = self.attribution_hit(text, language)
        if overreach:
            logger.error("静态模板文案推理越界(%s): %.100s", overreach, text)
            return ""
        return text

    def sentence_gate(self, language: str = "en", *, scope: str = "chat") -> SentenceGate:
        """流式按句缓冲闸门（见 SentenceGate docstring）。"""
        return SentenceGate(
            check=lambda text: self.check_text(text, language, scope=scope),
            safe_reply=self.safe_reply(language),
            max_sentence_len=self.MAX_SENTENCE_LEN,
        )


safety_service = SafetyService()
