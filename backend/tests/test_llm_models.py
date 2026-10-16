"""core/llm.get_embedding_model 回归测试（B12：embed 曾恒用 dashscope 模型名）。

切换 provider 后 embedding 模型必须同步切换，否则请求打到对应端点必失败，
且失败被 _embed_or_none 静默吞掉 → 全链路失忆。
"""
import pytest

from backend.config import settings
from backend.ai.llm import get_embedding_model


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
