"""core/llm.get_embedding_model 回归测试（B12：embed 曾恒用 dashscope 模型名）。

切换 provider 后 embedding 模型必须同步切换，否则请求打到对应端点必失败，
且失败被 _embed_or_none 静默吞掉 → 全链路失忆。
"""
import pytest

from backend.config import settings
from backend.ai.llm import _chat_kwargs, _constraints_for, get_embedding_model, get_pro_model


@pytest.fixture(autouse=True)
def _plain_embedding_provider(monkeypatch):
    """本文件聚焦「embedding 是否跟随 llm_provider」的映射关系。

    本地 .env 可能把 EMBEDDING_PROVIDER 钉在 dashscope（chat 走代理的场景），
    不清空就会让这里的映射断言失效；分离场景由下面的专用用例显式设置。
    """
    monkeypatch.setattr(settings, "embedding_provider", "")


def test_dashscope_default(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "dashscope")
    assert get_embedding_model() == "text-embedding-v4"


def test_gemini(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    assert get_embedding_model() == "text-embedding-004"


def test_openai(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    assert get_embedding_model() == "text-embedding-3-small"


def test_doubao_configured(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "doubao")
    monkeypatch.setattr(settings, "doubao_embedding_model", "ep-embedding-xxx")
    assert get_embedding_model() == "ep-embedding-xxx"


def test_doubao_unconfigured_raises(monkeypatch):
    """doubao 未配置 embedding 模型时显式报错（清晰失败，而非打空模型名到端点）。"""
    monkeypatch.setattr(settings, "llm_provider", "doubao")
    monkeypatch.setattr(settings, "doubao_embedding_model", "")
    with pytest.raises(RuntimeError, match="DOUBAO_EMBEDDING_MODEL"):
        get_embedding_model()


# --- embedding 与 chat 分离（代理端点只放行 chat 模型的场景） -------------------
# 代理类端点（如 token.agi.bar）的 key 白名单只含 chat 模型，/v1/embeddings 返回 403。
# 若 embedding 跟随 llm_provider 一起切过去，_embed_or_none 会静默吞掉 403 → 记忆全失忆。


def test_embedding_provider_overrides_chat(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "embedding_provider", "dashscope")
    assert get_embedding_model() == "text-embedding-v4"


def test_embedding_provider_empty_follows_chat(monkeypatch):
    """留空必须保持 B12 旧语义：embedding 跟随 llm_provider。"""
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "embedding_provider", "")
    assert get_embedding_model() == "text-embedding-3-small"


def test_embed_client_isolated_from_chat_client(monkeypatch):
    """分离时 chat 与 embedding 必须是两个指向不同 base_url 的 client。"""
    from backend.ai import llm as llm_mod

    monkeypatch.setattr(llm_mod, "_client", None)
    monkeypatch.setattr(llm_mod, "_embed_client", None)
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "sk-chat")
    monkeypatch.setattr(settings, "openai_base_url", "https://chat.example/v1")
    monkeypatch.setattr(settings, "embedding_provider", "dashscope")
    monkeypatch.setattr(settings, "dashscope_api_key", "sk-embed")
    monkeypatch.setattr(settings, "dashscope_base_url", "https://embed.example/v1")

    chat_client = llm_mod._get_client()
    embed_client = llm_mod._get_embedding_client()
    assert chat_client is not embed_client
    assert str(chat_client.base_url).rstrip("/") == "https://chat.example/v1"
    assert str(embed_client.base_url).rstrip("/") == "https://embed.example/v1"


def test_embed_client_reused_when_same_provider(monkeypatch):
    """未分离时必须复用 chat client——测试替换 llm_mod._client 才能对 embed 继续生效。"""
    from backend.ai import llm as llm_mod

    sentinel = object()
    monkeypatch.setattr(llm_mod, "_client", sentinel)
    monkeypatch.setattr(settings, "llm_provider", "dashscope")
    monkeypatch.setattr(settings, "embedding_provider", "")
    assert llm_mod._get_embedding_client() is sentinel


# --- 模型能力表（推理模型 k3.1-pro）-------------------------------------------
# 三条约束都来自实测：只接受 temperature=1、reasoning 计入 max_tokens、首个正文增量 11s。


def test_k3_drops_temperature_and_floors_budget():
    kwargs, notes = _chat_kwargs("k3.1-pro", 10, 0.0)
    assert "temperature" not in kwargs, "k3 只接受 temperature=1，必须丢弃该参数"
    assert kwargs["max_tokens"] == 800, "低于 800 会被推理 token 吃光，返回空 content"
    assert any("max_tokens" in n for n in notes)


def test_k3_keeps_larger_budget_but_still_drops_temperature():
    kwargs, notes = _chat_kwargs("k3.1-pro", 2000, 0.3)
    assert kwargs["max_tokens"] == 2000
    assert "temperature" not in kwargs
    assert notes == ["temperature dropped"]


def test_constraint_lookup_is_case_insensitive():
    assert _constraints_for("K3.1-Pro") == _constraints_for("k3.1-pro")
    assert _constraints_for("k3.1-pro")["streaming"] is False


def test_default_model_passes_params_through():
    """未登记约束的模型必须零改动透传，避免能力表影响主路径。"""
    kwargs, notes = _chat_kwargs("qwen-flash", 10, 0.0)
    assert kwargs == {"max_tokens": 10, "temperature": 0.0}
    assert notes == []


async def test_k3_streaming_rejected_before_network():
    """conftest 的 autouse mock_llm 替换了单例属性，故直接测模块实现。"""
    from backend.ai.llm import stream as llm_stream

    with pytest.raises(ValueError, match="不支持流式"):
        await anext(llm_stream("hi", language="zh", model="k3.1-pro"))


def test_pro_model_unconfigured_raises(monkeypatch):
    monkeypatch.setattr(settings, "openai_pro_model", "")
    with pytest.raises(RuntimeError, match="OPENAI_PRO_MODEL"):
        get_pro_model()


def test_pro_model_configured(monkeypatch):
    monkeypatch.setattr(settings, "openai_pro_model", "k3.1-pro")
    assert get_pro_model() == "k3.1-pro"
