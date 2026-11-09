"""llm 客户端预算：显式 timeout + 不交给 SDK 静默重试。"""
from backend.ai import llm as llm_mod
from backend.config import settings


def test_build_client_sets_explicit_timeout_and_no_sdk_retries(monkeypatch):
    """SDK 默认 timeout=600s、max_retries=2：端点卡住能把调用方挂半小时并重复计费。"""
    seen: dict = {}

    class _FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(llm_mod, "AsyncOpenAI", _FakeAsyncOpenAI)
    monkeypatch.setattr(settings, "dashscope_api_key", "sk-test")

    llm_mod._build_client("dashscope")

    assert seen["timeout"] == settings.llm_timeout_seconds
    assert seen["max_retries"] == settings.llm_max_retries == 0
