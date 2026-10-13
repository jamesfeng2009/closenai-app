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

生成留痕（架构方案 O4）：三个入口每次调用都写一行 ai_generation_runs（含异常路径），
落库 AI 产出前用 `llm.current_run_id()` 取回链，闸门丢弃产出时用 `llm.mark_fallback()`。
留痕写失败只记日志，不影响生成结果。

用法：
    from backend.core.llm import llm
    text = await llm.complete("你好", language="zh")
    async for chunk in llm.stream("你好", language="zh"):
        ...
"""
import json
import logging
import sys
import time
from typing import AsyncGenerator, Optional

from openai import AsyncOpenAI

from backend.config import settings
from backend.core import generation_run

logger = logging.getLogger("closenai.llm")

# OpenAI 兼容 client 缓存（按 provider 懒初始化）
_client: Optional[AsyncOpenAI] = None


def _purpose_from_frame(depth: int = 2) -> str:
    """调用点标识，由调用帧推导（module.func）。

    留痕要能回答「这次生成是为了什么」，而 20+ 个调用点显式传 purpose 的成本远高于
    一次帧读取；调用方可传 purpose= 覆盖推导值。depth=2 对应「本函数的调用者」。
    """
    try:
        frame = sys._getframe(depth)
    except (ValueError, AttributeError):
        return "unknown"
    module = str(frame.f_globals.get("__name__", "")).rsplit(".", 1)[-1]
    return f"{module}.{frame.f_code.co_name}"[:80]


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _usage_fields(resp) -> dict:
    """从响应里取 token 用量；OpenAI 兼容 provider 不回传 usage 时留空。"""
    usage = getattr(resp, "usage", None)
    if usage is None:
        return {}
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
    }


def _run_fields(op: str, purpose: Optional[str], *, model: str, lite: bool, language: str, prompt: str) -> dict:
    return {
        "op": op,
        "purpose": purpose or _purpose_from_frame(depth=3),
        "provider": settings.llm_provider,
        "model": model,
        "lite": lite,
        "language": language,
        "input_chars": len(prompt or ""),
    }


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
        # 注意括号：`a or b if lite else b` 会解析为 `a or (b if lite else b)`，
        # 导致非 lite 场景也返回 lite 模型
        return (settings.doubao_lite_model or settings.doubao_model) if lite else settings.doubao_model
    if provider == "gemini":
        return settings.gemini_lite_model if lite else settings.gemini_model
    return settings.openai_lite_model if lite else settings.openai_model


def get_embedding_model() -> str:
    """返回当前 provider 的 embedding 模型名（与 get_model 同口径按 provider 切换）。

    注意：向量空间跨模型不兼容，切换 provider/模型后存量向量需重嵌入。
    """
    provider = settings.llm_provider
    if provider == "dashscope":
        return settings.dashscope_embedding_model
    if provider == "gemini":
        return settings.gemini_embedding_model
    if provider == "doubao":
        if not settings.doubao_embedding_model:
            raise RuntimeError("provider 'doubao' 未配置 DOUBAO_EMBEDDING_MODEL")
        return settings.doubao_embedding_model
    return settings.openai_embedding_model


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
    purpose: Optional[str] = None,
    track: bool = True,
) -> str:
    """非流式补全。返回完整文本。

    purpose：留痕的调用点标识，缺省由调用帧推导；track=False 用于辅助调用
    （如安全分类器自检），写留痕但不占用「当前生成」指针。
    """
    client = _get_client()
    model = get_model(lite)
    fields = _run_fields(
        "complete", purpose, model=model, lite=lite, language=language, prompt=prompt
    )
    started = time.perf_counter()
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _build_system_prompt(system_prompt, language)},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text = resp.choices[0].message.content or ""
    except Exception as exc:  # noqa: BLE001  异常照常留痕后原样上抛
        await generation_run.record_run(
            **fields,
            status="error",
            error_type=type(exc).__name__,
            output_chars=0,
            latency_ms=_elapsed_ms(started),
            track=track,
        )
        raise
    await generation_run.record_run(
        **fields,
        status="ok",
        output_chars=len(text),
        latency_ms=_elapsed_ms(started),
        track=track,
        **_usage_fields(resp),
    )
    return text


async def stream(
    prompt: str,
    language: str = "en",
    system_prompt: str = "You are a helpful and caring AI companion.",
    lite: bool = False,
    max_tokens: int = 500,
    temperature: float = 0.7,
    purpose: Optional[str] = None,
    track: bool = True,
) -> AsyncGenerator[str, None]:
    """流式补全。逐段 yield 文本。

    留痕在流自然结束时写（累计产出长度即实际下发内容）；调用方中途放弃迭代不会有留痕行。
    """
    client = _get_client()
    model = get_model(lite)
    fields = _run_fields(
        "stream", purpose, model=model, lite=lite, language=language, prompt=prompt
    )
    started = time.perf_counter()
    pieces: list[str] = []
    try:
        stream_resp = await client.chat.completions.create(
            model=model,
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
                pieces.append(delta)
                yield delta
    except Exception as exc:  # noqa: BLE001
        await generation_run.record_run(
            **fields,
            status="error",
            error_type=type(exc).__name__,
            output_chars=sum(len(p) for p in pieces),
            latency_ms=_elapsed_ms(started),
            track=track,
        )
        raise
    text = "".join(pieces)
    await generation_run.record_run(
        **fields,
        status="ok",
        output_chars=len(text),
        latency_ms=_elapsed_ms(started),
        track=track,
    )


async def embed(
    texts: list[str],
    dim: int | None = None,
    purpose: Optional[str] = None,
) -> list[list[float]]:
    """
    文本向量化（用于 pgvector 语义检索）。

    按 provider 选择 embedding 模型：
    - dashscope: text-embedding-v4（MRL 支持 dimensions 128-2048，默认取 settings.embedding_dim=512）
    - gemini: text-embedding-004
    - openai / doubao: 走 OpenAI 兼容 /embeddings

    返回嵌套向量列表，维度与 models/memory.py 的 HALFVEC(512) 对齐。

    同样写留痕（op=embed）：embedding 也是模型调用，成本与「切换 provider 后需重嵌入」
    的排障都要能查到。language 传空串——embedding 调用不跟随用户语言，该列对 embed 无意义。
    """
    client = _get_client()
    provider = settings.llm_provider
    model = get_embedding_model()
    extra: dict = {}
    if dim is not None:
        extra["dimensions"] = dim
    elif provider == "dashscope":
        extra["dimensions"] = settings.embedding_dim

    fields = _run_fields(
        "embed", purpose, model=model, lite=False, language="", prompt="".join(texts or [])
    )
    started = time.perf_counter()
    try:
        resp = await client.embeddings.create(model=model, input=texts, **extra)
        # 按输入顺序返回向量
        ordered = sorted(resp.data, key=lambda x: x.index)
    except Exception as exc:  # noqa: BLE001
        await generation_run.record_run(
            **fields,
            status="error",
            error_type=type(exc).__name__,
            latency_ms=_elapsed_ms(started),
            meta=json.dumps({"texts": len(texts or [])}),
        )
        raise
    vectors = [item.embedding for item in ordered]
    await generation_run.record_run(
        **fields,
        status="ok",
        latency_ms=_elapsed_ms(started),
        meta=json.dumps(
            {"texts": len(texts or []), "dim": len(vectors[0]) if vectors else 0}
        ),
        **_usage_fields(resp),
    )
    return vectors


# 单例
llm = type("LLM", (), {
    "complete": staticmethod(complete),
    "stream": staticmethod(stream),
    "embed": staticmethod(embed),
    "get_model": staticmethod(get_model),
    "get_embedding_model": staticmethod(get_embedding_model),
    # 生成留痕取用：落库前读 current_run_id()，产出被闸门丢弃时调 mark_fallback()
    "current_run_id": staticmethod(generation_run.current_run_id),
    "mark_fallback": staticmethod(generation_run.mark_fallback),
})()