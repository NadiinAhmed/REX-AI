"""Provider-independent LLM interface. Business logic depends only on `LLMService`.

Sprint 1 ships one real provider (Anthropic) and returns None when no provider is
configured, so the assistant runs in deterministic mode. To add a provider, implement
`generate()` and extend `create_llm_service()`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from src.config import Settings
from src.utils.logging_config import get_logger

logger = get_logger("llm")


class LLMError(Exception):
    """Raised when an LLM call fails. Callers must fall back gracefully."""


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["user", "assistant"]
    content: str


class LLMService(Protocol):
    name: str

    def generate(self, system_prompt: str, messages: list[ChatMessage]) -> str: ...


class AnthropicLLMService:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, timeout: float = 30.0, max_tokens: int = 1024) -> None:
        try:
            import anthropic  # imported lazily: optional dependency
        except ImportError as exc:
            raise LLMError("The 'anthropic' package is not installed. Run: pip install anthropic") from exc
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        self._model = model
        self._max_tokens = max_tokens

    def generate(self, system_prompt: str, messages: list[ChatMessage]) -> str:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system_prompt,
                messages=[{"role": m.role, "content": m.content} for m in messages],
            )
        except Exception as exc:  # SDK raises several error types; normalise them
            logger.warning("LLM call failed: %s", type(exc).__name__)
            raise LLMError(f"LLM request failed ({type(exc).__name__}).") from exc
        text = "".join(getattr(block, "text", "") for block in response.content).strip()
        if not text:
            raise LLMError("LLM returned an empty response.")
        return text


def create_llm_service(settings: Settings) -> tuple[LLMService | None, str]:
    """Return (service or None, human-readable status). Never raises for missing config."""
    if not settings.llm_enabled:
        return None, "LLM disabled (LLM_PROVIDER=none). Running in deterministic demo mode."
    if settings.llm_provider == "anthropic":
        if not settings.anthropic_api_key:
            return None, "ANTHROPIC_API_KEY is not set. Running in deterministic demo mode."
        try:
            service = AnthropicLLMService(
                settings.anthropic_api_key, settings.llm_model, settings.llm_timeout_seconds
            )
        except LLMError as exc:
            return None, f"{exc} Running in deterministic demo mode."
        return service, f"LLM enabled: anthropic / {settings.llm_model}"
    return None, f"Unknown provider '{settings.llm_provider}'. Running in deterministic demo mode."
