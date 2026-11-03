"""LLM speed settings and per-call timing logs (used to find slow answers)."""
import logging

from src.config import load_settings
from src.services import llm_service
from src.services.llm_service import ChatMessage, LangChainChatService, _build_chat_model, is_reasoning_model


def _settings(**env):
    base = {"LLM_PROVIDER": "groq", "GROQ_API_KEY": "test-key"}
    return load_settings({**base, **env})


def test_reasoning_effort_defaults_to_low_for_gpt_oss_on_groq():
    model = _build_chat_model(_settings(LLM_MODEL="openai/gpt-oss-20b"), "test-key")
    assert model.reasoning_effort == "low"


def test_reasoning_effort_is_configurable():
    model = _build_chat_model(_settings(LLM_MODEL="openai/gpt-oss-120b", LLM_REASONING_EFFORT="medium"), "k")
    assert model.reasoning_effort == "medium"


def test_non_reasoning_models_do_not_get_the_setting():
    assert not is_reasoning_model("llama-3.3-70b-versatile")
    assert _build_chat_model(_settings(LLM_MODEL="llama-3.3-70b-versatile"), "k").reasoning_effort is None


def test_each_call_logs_its_duration_and_slow_calls_warn(monkeypatch, caplog):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    service = LangChainChatService(FakeListChatModel(responses=["hi", "hi"]), "fake")
    with caplog.at_level(logging.INFO, logger="rex.llm"):
        service.generate("system", [ChatMessage("user", "hello")])
        monkeypatch.setattr(llm_service, "SLOW_CALL_SECONDS", 0.0)
        service.generate("system", [ChatMessage("user", "hello")])
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("LLM call ok (fake):") for m in messages)
    assert any(m.startswith("Slow LLM call (fake):") for m in messages)
