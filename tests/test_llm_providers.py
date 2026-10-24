"""Grok (xAI) and Groq through LangChain; failures never crash the assistant. No network used."""
import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from src.config import ConfigError, load_settings
from src.services.llm_service import ChatMessage, LangChainChatService, LLMError, create_llm_service


def test_langchain_adapter_sends_system_and_user_messages():
    svc = LangChainChatService(FakeListChatModel(responses=["grounded answer"]), name="fake")
    assert svc.generate("rules", [ChatMessage("user", "hi")]) == "grounded answer"


def test_langchain_errors_are_normalised():
    class Broken:
        def invoke(self, _): raise TimeoutError("network down")
    with pytest.raises(LLMError, match="TimeoutError"):
        LangChainChatService(Broken(), "broken").generate("s", [ChatMessage("user", "x")])


@pytest.mark.parametrize("provider,key_env,cls", [("xai", "XAI_API_KEY", "ChatXAI"), ("groq", "GROQ_API_KEY", "ChatGroq")])
def test_provider_builds_langchain_model_when_key_present(provider, key_env, cls):
    svc, status = create_llm_service(load_settings({"LLM_PROVIDER": provider, key_env: "test-key", "LLM_MODEL": "m"}))
    assert type(svc.chat_model).__name__ == cls and status == f"LLM enabled: {provider} / m"


@pytest.mark.parametrize("provider,key_env", [("xai", "XAI_API_KEY"), ("groq", "GROQ_API_KEY")])
def test_missing_key_falls_back_to_deterministic(provider, key_env):
    svc, status = create_llm_service(load_settings({"LLM_PROVIDER": provider}))
    assert svc is None and key_env in status


def test_secrets_never_in_repr_and_bad_chunking_rejected():
    s = load_settings({"XAI_API_KEY": "xai-secret", "GROQ_API_KEY": "gsk-secret", "ADMIN_PASSWORD": "pw"})
    assert "secret" not in repr(s) and "pw" not in repr(s)
    with pytest.raises(ConfigError):
        load_settings({"CHUNK_SIZE": "200", "CHUNK_OVERLAP": "300"})
