"""Provider-independent LLM interface. Business logic depends only on `LLMService`.

Providers:
  - "groq"      -> LangChain `ChatGroq`   (Groq Cloud; free tier with rate limits)
  - "xai"       -> LangChain `ChatXAI`    (xAI Grok models; paid API)
  - "anthropic" -> Anthropic SDK          (kept from Sprint 1)
  - "none"      -> no LLM: the assistant runs in deterministic mode

Any LangChain chat model can be wrapped with `LangChainChatService`, so adding a provider
is one line in `_build_chat_model()`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

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


class LangChainChatService:
    """Adapts any LangChain chat model (BaseChatModel) to the project's LLMService protocol.

    Keeping this adapter means the agents never import LangChain provider classes directly:
    swapping Groq for Grok (or a fake model in tests) does not touch business logic.
    """

    def __init__(self, chat_model: Any, name: str) -> None:
        self._model = chat_model
        self.name = name

    @property
    def chat_model(self) -> Any:
        return self._model

    def generate(self, system_prompt: str, messages: list[ChatMessage]) -> str:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        lc_messages: list[Any] = [SystemMessage(content=system_prompt)]
        for m in messages:
            lc_messages.append(HumanMessage(content=m.content) if m.role == "user"
                               else AIMessage(content=m.content))
        try:
            result = self._model.invoke(lc_messages)
        except Exception as exc:  # provider SDKs raise many error types; normalise them
            logger.warning("LLM call failed (%s): %s", self.name, type(exc).__name__)
            raise LLMError(f"LLM request failed ({type(exc).__name__}).") from exc
        content = result.content
        if isinstance(content, list):  # some providers return content blocks
            content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        text = (content or "").strip()
        if not text:
            raise LLMError("LLM returned an empty response.")
        return text


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


_KEY_ENV = {"groq": "GROQ_API_KEY", "xai": "XAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


def _build_chat_model(settings: Settings, api_key: str) -> Any:
    """Create the LangChain chat model for an OpenAI-style provider (groq / xai)."""
    common = dict(model=settings.llm_model, api_key=api_key, temperature=settings.llm_temperature,
                  timeout=settings.llm_timeout_seconds, max_retries=1, max_tokens=1024)
    try:
        if settings.llm_provider == "groq":
            from langchain_groq import ChatGroq
            return ChatGroq(**common)
        if settings.llm_provider == "xai":
            from langchain_xai import ChatXAI
            return ChatXAI(**common)
    except ImportError as exc:
        package = "langchain-groq" if settings.llm_provider == "groq" else "langchain-xai"
        raise LLMError(f"The '{package}' package is not installed. Run: pip install {package}") from exc
    raise LLMError(f"No LangChain model for provider '{settings.llm_provider}'.")


def create_llm_service(settings: Settings) -> tuple[LLMService | None, str]:
    """Return (service or None, human-readable status). Never raises for missing config."""
    provider = settings.llm_provider
    if not settings.llm_enabled:
        return None, "LLM disabled (LLM_PROVIDER=none). Running in deterministic demo mode."
    api_key = settings.api_key_for(provider)
    if not api_key:
        return None, f"{_KEY_ENV[provider]} is not set. Running in deterministic demo mode."
    try:
        if provider == "anthropic":
            service: LLMService = AnthropicLLMService(api_key, settings.llm_model, settings.llm_timeout_seconds)
        else:
            service = LangChainChatService(_build_chat_model(settings, api_key), name=provider)
    except LLMError as exc:
        return None, f"{exc} Running in deterministic demo mode."
    return service, f"LLM enabled: {provider} / {settings.llm_model}"
