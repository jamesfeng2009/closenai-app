"""内容安全服务（三层防御之 L3 输出后置过滤，唯一事实源）。

三层防御（详见 .trae/skills/closenai-llm/SKILL.md）：
- L1 Prompt 防御：SAFETY_BOUNDARY 段落注入所有面向用户的 system prompt
- L2 Provider 原生审核：DashScope/Gemini 自带，命中走现有 LLM 降级兜底
- L3 输出后置过滤：本模块。流式按句缓冲闸门 + 非流式全量检测。

设计铁律：
- 用户发送路径零阻塞：输入不预检，检测全在输出端与生成并行
- 检测自身失败 fail-open（超时/异常 → 放行 + 日志），禁止阻断主流程
- 自伤信号不走拒答（由 L1 主模型关怀回应），本模块只拦 AI 违规输出

业务代码禁止复制检测判断逻辑，统一走本模块（同 quota_service 模式）。
"""
import asyncio
import logging
import re
from typing import AsyncGenerator, Awaitable, Callable, Optional, Tuple

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

    # L3 命中后的替换话术（话题转回成长，温和不指责；中英双表）
    SAFE_REPLY = {
        "zh": "这个话题我可能不太适合深入聊呢。不如说说，今天有什么让你印象深刻的事吗？",
        "en": "That's a topic I'd rather not dive into. How about telling me — what's one thing that stood out to you today?",
    }

    # 检测调用超时（秒）：超时 fail-open 放行
    CHECK_TIMEOUT = 0.8

    # 句缓冲区强制切句长度（字）
    MAX_SENTENCE_LEN = 200

    _CHECK_PROMPT = (
        "你是内容安全分类器。判断以下 AI 生成文本是否包含：仇恨言论、暴力、恐吓、"
        "政治或宗教争议、色情内容、极端人生建议（如直接劝辞职/分手）。"
        "只回答 SAFE 或 UNSAFE，不要输出其他内容。\n\n文本：\n"
    )

    def boundary(self, language: str) -> str:
        """L1 安全边界段落（按语言）。"""
        return self.SAFETY_BOUNDARY[_lang_key(language)]

    def safe_reply(self, language: str) -> str:
        """命中替换话术（按语言）。"""
        return self.SAFE_REPLY[_lang_key(language)]

    async def check_text(self, text: str, language: str = "en") -> bool:
        """全量检测（非流式路径）。True=安全。

        lite 模型分类，不计入用户配额；超时/异常/不可解析 → fail-open True + 日志。
        """
        if not text or not text.strip():
            return True
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

    async def check_or(self, text: str, language: str, fallback: str) -> str:
        """非流式便捷封装：text 违规时返回 fallback，否则返回 text。"""
        if text and text.strip() and not await self.check_text(text, language):
            return fallback
        return text

    def sentence_gate(self, language: str = "en") -> SentenceGate:
        """流式按句缓冲闸门（见 SentenceGate docstring）。"""
        return SentenceGate(
            check=lambda text: self.check_text(text, language),
            safe_reply=self.safe_reply(language),
            max_sentence_len=self.MAX_SENTENCE_LEN,
        )


safety_service = SafetyService()
