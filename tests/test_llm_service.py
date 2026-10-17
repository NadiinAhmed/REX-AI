"""Role 1 — LLM service factory never breaks the app when misconfigured."""
from src.config import load_settings
from src.services.llm_service import create_llm_service


def test_disabled_provider_returns_none():
    service, status = create_llm_service(load_settings({"LLM_PROVIDER": "none"}))
    assert service is None and "deterministic" in status


def test_missing_api_key_returns_none_without_leaking():
    service, status = create_llm_service(load_settings({"LLM_PROVIDER": "anthropic"}))
    assert service is None and "ANTHROPIC_API_KEY" in status
