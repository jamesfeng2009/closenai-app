"""
LLM 客户端封装（多 Provider + 多语言）。

统一 OpenAI 兼容接口，按配置切换 Provider：
- dashscope（通义千问，默认）
- doubao（豆包）
- gemini / openai（兼容）

所有 LLM 调用都应走本封装，禁止在业务代码里直接 new OpenAI client。

多语言：
- 所有调用必须传 `language`（语言码），封装会自动注入 system prompt 语言指令
- 语言跟随：AI 用用户语言回复

用法：
    from backend.core.llm import llm
    text = await llm.complete("你好", language="zh")
    async for chunk in llm.stream("你好", language="zh"):
        ...
"""
import logging
from typing import AsyncGenerator, Optional

from openai import AsyncOpenAI

from backend.config import settings

logger = logging.getLogger("closenai.llm")

# OpenAI 兼容 client 缓存（按 provider 懒初始化）
_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    """按配置获取 OpenAI 兼容 client。"""
    global _client
    if _client is not None:
        return _client

    provider = settings.llm_provider
    if provider == "dashscope":
        api_key = settings.dashscope_api_key
        base_url = settings.dashscope_base_url
    elif provider == "doubao":
        api_key = settings.doubao_api_key
        base_url = settings.doubao_base_url
    elif provider == "gemini":
        api_key = settings.gemini_api_key
        base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
    else:  # openai 自定义
        api_key = settings.openai_api_key
        base_url = settings.openai_base_url

    if not api_key:
        raise RuntimeError(f"LLM provider '{provider}' 未配置 API Key")
    _client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)
    return _client


def get_model(lite: bool = False) -> str:
    """返回当前 provider 的模型名（lite 为轻量档）。"""
    provider = settings.llm_provider
    if provider == "dashscope":
        return settings.dashscope_lite_model if lite else settings.dashscope_model
    if provider == "doubao":
        return settings.doubao_lite_model or settings.doubao_model if lite else settings.doubao_model
    if provider == "gemini":
        return settings.gemini_lite_model if lite else settings.gemini_model
    return settings.openai_lite_model if lite else settings.openai_model


def _build_system_prompt(base_prompt: str, language: str) -> str:
    """注入语言跟随指令。"""
    return (
        f"{base_prompt}\n\n"
        f"重要：请始终使用语言码为 '{language}' 的语言回复用户，"
        f"不要混用其他语言。"
    )


async def complete(
    prompt: str,
    language: str = "en",
    system_prompt: str = "You are a helpful and caring AI companion.",
    lite: bool = False,
    max_tokens: int = 500,
    temperature: float = 0.7,
) -> str:
    """非流式补全。返回完整文本。"""
    client = _get_client()
    resp = await client.chat.completions.create(
        model=get_model(lite),
        messages=[
            {"role": "system", "content": _build_system_prompt(system_prompt, language)},
            {"role": "user", "content": prompt},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""


async def stream(
    prompt: str,
    language: str = "en",
    system_prompt: str = "You are a helpful and caring AI companion.",
    lite: bool = False,
    max_tokens: int = 500,
    temperature: float = 0.7,
) -> AsyncGenerator[str, None]:
    """流式补全。逐段 yield 文本。"""
    client = _get_client()
    stream_resp = await client.chat.completions.create(
        model=get_model(lite),
        messages=[
            {"role": "system", "content": _build_system_prompt(system_prompt, language)},
            {"role": "user", "content": prompt},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
        stream=True,
    )
    async for chunk in stream_resp:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta


async def embed(
    texts: list[str],
    dim: int | None = None,
) -> list[list[float]]:
    """
    文本向量化（用于 pgvector 语义检索）。

    按 provider 选择 embedding 模型：
    - dashscope: text-embedding-v4（MRL 支持 dimensions 128-2048，默认取 settings.embedding_dim=512）
    - gemini: text-embedding-004
    - openai / doubao: 走 OpenAI 兼容 /embeddings

    返回嵌套向量列表，维度与 models/memory.py 的 HALFVEC(512) 对齐。
    """
    client = _get_client()
    provider = settings.llm_provider
    model = settings.dashscope_embedding_model
    extra: dict = {}
    if dim is not None:
        extra["dimensions"] = dim
    elif provider == "dashscope":
        extra["dimensions"] = settings.embedding_dim

    resp = await client.embeddings.create(model=model, input=texts, **extra)
    # 按输入顺序返回向量
    ordered = sorted(resp.data, key=lambda x: x.index)
    return [item.embedding for item in ordered]


# 单例
llm = type("LLM", (), {
    "complete": staticmethod(complete),
    "stream": staticmethod(stream),
    "embed": staticmethod(embed),
    "get_model": staticmethod(get_model),
})()