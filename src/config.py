"""Application configuration loaded from environment variables.

Secrets (API keys) are read from the environment only and are never logged or shown in
`repr`. Copy `.env.example` to `.env` for local development.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SUPPORTED_LLM_PROVIDERS = ("none", "anthropic", "groq", "xai")
SUPPORTED_EMBEDDING_PROVIDERS = ("hashing", "sentence_transformers")
SUPPORTED_COURSE_BACKENDS = ("direct", "mcp")

# Default model per provider. Model names change often: check the provider console.
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5-5",
    "groq": "openai/gpt-oss-120b",
    "xai": "grok-4",
    "none": "",
}


class ConfigError(ValueError):
    """Raised when configuration values are invalid."""


def _load_dotenv() -> None:
    """Load a local .env file if python-dotenv is installed. Never required."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - optional dependency
        return
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def _path(env: dict[str, str], key: str, default: str) -> Path:
    p = Path(env.get(key, default))
    return p if p.is_absolute() else PROJECT_ROOT / p


def _number(env: dict[str, str], key: str, default: str, cast=float, minimum: float | None = None):
    try:
        value = cast(env.get(key, default))
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number.") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{key} must be >= {minimum}.")
    return value


def _choice(env: dict[str, str], key: str, default: str, allowed: tuple[str, ...]) -> str:
    value = env.get(key, default).strip().lower() or default
    if value not in allowed:
        raise ConfigError(f"Unsupported {key} '{value}'. Use one of: {', '.join(allowed)}.")
    return value


@dataclass(frozen=True)
class Settings:
    courses_path: Path
    # --- LLM ---------------------------------------------------------------------------
    llm_provider: str = "none"
    llm_model: str = ""
    anthropic_api_key: str | None = field(default=None, repr=False)
    groq_api_key: str | None = field(default=None, repr=False)
    xai_api_key: str | None = field(default=None, repr=False)
    llm_timeout_seconds: float = 30.0
    llm_temperature: float = 0.2
    # --- Storage -----------------------------------------------------------------------
    database_url: str = "sqlite:///data/rex.db"
    vector_dir: Path = PROJECT_ROOT / "data" / "vectorstore"
    upload_dir: Path = PROJECT_ROOT / "data" / "uploads"
    # --- RAG ---------------------------------------------------------------------------
    embedding_provider: str = "hashing"
    embedding_model: str = "intfloat/multilingual-e5-small"
    chunk_size: int = 800
    chunk_overlap: int = 120
    retrieval_top_k: int = 4
    retrieval_min_score: float = 0.15
    retrieval_keyword_gate: str = "auto"     # auto = on for the hashing embedding, off otherwise
    max_upload_mb: float = 20.0
    max_pdf_pages: int = 300
    # --- Integration -------------------------------------------------------------------
    course_backend: str = "direct"
    mcp_timeout_seconds: float = 20.0
    admin_password: str | None = field(default=None, repr=False)
    log_level: str = "INFO"

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider != "none"

    @property
    def keyword_gate_enabled(self) -> bool:
        if self.retrieval_keyword_gate == "auto":
            return self.embedding_provider == "hashing"
        return self.retrieval_keyword_gate == "on"

    def api_key_for(self, provider: str) -> str | None:
        return {"anthropic": self.anthropic_api_key, "groq": self.groq_api_key,
                "xai": self.xai_api_key}.get(provider)


def _database_url(env: dict[str, str]) -> str:
    url = env.get("DATABASE_URL", "sqlite:///data/rex.db").strip()
    # Relative SQLite paths are resolved against the project root so the app works from any cwd.
    prefix = "sqlite:///"
    if url.startswith(prefix) and not url.startswith("sqlite:////") and url != "sqlite://":
        rel = url[len(prefix):]
        if rel and rel != ":memory:" and not Path(rel).is_absolute():
            url = prefix + str(PROJECT_ROOT / rel)
    return url


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Build Settings from `env` (defaults to os.environ, after loading .env)."""
    if env is None:
        _load_dotenv()
        env = dict(os.environ)

    provider = _choice(env, "LLM_PROVIDER", "none", SUPPORTED_LLM_PROVIDERS)
    chunk_size = _number(env, "CHUNK_SIZE", "800", int, minimum=100)
    chunk_overlap = _number(env, "CHUNK_OVERLAP", "120", int, minimum=0)
    if chunk_overlap >= chunk_size:
        raise ConfigError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE.")

    return Settings(
        courses_path=_path(env, "REX_COURSES_PATH", "data/courses_official.json"),
        llm_provider=provider,
        llm_model=env.get("LLM_MODEL", "").strip() or DEFAULT_MODELS[provider],
        anthropic_api_key=env.get("ANTHROPIC_API_KEY") or None,
        groq_api_key=env.get("GROQ_API_KEY") or None,
        xai_api_key=env.get("XAI_API_KEY") or None,
        llm_timeout_seconds=_number(env, "LLM_TIMEOUT_SECONDS", "30", minimum=1),
        llm_temperature=_number(env, "LLM_TEMPERATURE", "0.2", minimum=0),
        database_url=_database_url(env),
        vector_dir=_path(env, "VECTOR_DIR", "data/vectorstore"),
        upload_dir=_path(env, "UPLOAD_DIR", "data/uploads"),
        embedding_provider=_choice(env, "EMBEDDING_PROVIDER", "hashing", SUPPORTED_EMBEDDING_PROVIDERS),
        embedding_model=env.get("EMBEDDING_MODEL", "intfloat/multilingual-e5-small").strip(),
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        retrieval_top_k=_number(env, "RETRIEVAL_TOP_K", "4", int, minimum=1),
        retrieval_min_score=_number(env, "RETRIEVAL_MIN_SCORE", "0.15", minimum=0),
        retrieval_keyword_gate=_choice(env, "RETRIEVAL_KEYWORD_GATE", "auto", ("auto", "on", "off")),
        max_upload_mb=_number(env, "MAX_UPLOAD_MB", "20", minimum=0.1),
        max_pdf_pages=_number(env, "MAX_PDF_PAGES", "300", int, minimum=1),
        course_backend=_choice(env, "COURSE_BACKEND", "direct", SUPPORTED_COURSE_BACKENDS),
        mcp_timeout_seconds=_number(env, "MCP_TIMEOUT_SECONDS", "20", minimum=1),
        admin_password=env.get("ADMIN_PASSWORD") or None,
        log_level=env.get("LOG_LEVEL", "INFO").upper(),
    )
