"""Role 3 — configuration."""
import pytest

from src.config import PROJECT_ROOT, ConfigError, load_settings


def test_defaults_are_safe():
    s = load_settings({})
    assert s.llm_enabled is False
    # Sprint 2: the official academy catalog is the default; the synthetic file is a test fixture.
    assert s.courses_path == PROJECT_ROOT / "data" / "courses_official.json"


def test_invalid_provider_and_timeout_rejected():
    with pytest.raises(ConfigError):
        load_settings({"LLM_PROVIDER": "skynet"})
    with pytest.raises(ConfigError):
        load_settings({"LLM_TIMEOUT_SECONDS": "soon"})


def test_api_key_not_in_repr():
    assert "sk-secret" not in repr(load_settings({"ANTHROPIC_API_KEY": "sk-secret"}))
