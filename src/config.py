"""Application configuration loaded from environment variables.

Secrets (API keys) are read from the environment only and are never logged.
Copy `.env.example` to `.env` for local development.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SUPPORTED_LLM_PROVIDERS = ("none", "anthropic")


class ConfigError(ValueError):
    """Raised when configuration values are invalid."""


def _load_dotenv() -> None:
    """Load a local .env file if python-dotenv is installed. Never required."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - optional dependency
        return
    load_dotenv(PROJECT_ROOT / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    courses_path: Path
    llm_provider: str = "none"
    llm_model: str = "claude-sonnet-5-5"
    anthropic_api_key: str | None = field(default=None, repr=False)
    llm_timeout_seconds: float = 30.0
    log_level: str = "INFO"

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider != "none"


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Build Settings from `env` (defaults to os.environ, after loading .env)."""
    if env is None:
        _load_dotenv()
        env = dict(os.environ)

    raw_path = env.get("REX_COURSES_PATH", "data/courses.json")
    courses_path = Path(raw_path)
    if not courses_path.is_absolute():
        courses_path = PROJECT_ROOT / courses_path

    provider = env.get("LLM_PROVIDER", "none").strip().lower() or "none"
    if provider not in SUPPORTED_LLM_PROVIDERS:
        raise ConfigError(
            f"Unsupported LLM_PROVIDER '{provider}'. "
            f"Use one of: {', '.join(SUPPORTED_LLM_PROVIDERS)}."
        )

    try:
        timeout = float(env.get("LLM_TIMEOUT_SECONDS", "30"))
    except ValueError as exc:
        raise ConfigError("LLM_TIMEOUT_SECONDS must be a number.") from exc

    return Settings(
        courses_path=courses_path,
        llm_provider=provider,
        llm_model=env.get("LLM_MODEL", "claude-sonnet-5-5"),
        anthropic_api_key=env.get("ANTHROPIC_API_KEY") or None,
        llm_timeout_seconds=timeout,
        log_level=env.get("LOG_LEVEL", "INFO").upper(),
    )
