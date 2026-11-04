"""Application settings loaded from environment variables (and a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

VALID_PROVIDERS = ("claude", "openai", "fake")  # fake = offline canned answers
VALID_EFFORTS = ("", "low", "medium", "high", "xhigh", "max")
# local = multilingual model on this machine (default, ADR-019); openai = optional API;
# fake = hashing embedder for tests (keyword-like matching only)
VALID_EMBEDDING_PROVIDERS = ("local", "openai", "fake")
DEFAULT_EMBEDDING_MODELS = {
    "local": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "openai": "text-embedding-3-small",
    "fake": "hashing",
}


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    anthropic_api_key: str | None
    anthropic_model: str
    anthropic_effort: str | None
    openai_api_key: str | None
    openai_model: str
    embedding_provider: str
    embedding_model: str
    embedding_cache_dir: Path
    index_dir: Path
    top_k: int
    allow_uploads: bool = True
    escalation_webhook_url: str | None = None
    outbox_dir: Path = Path("data/outbox")
    api_token: str | None = None
    log_db: Path = Path("data/logs.sqlite")
    max_questions_per_session: int = 30
    max_questions_per_ip_hour: int = 120
    documents_dir: Path = Path("data/documents")
    max_upload_mb: int = 20

    @classmethod
    def from_env(cls, load_dotenv_file: bool = True) -> Settings:
        if load_dotenv_file:
            load_dotenv()

        provider = _env("LLM_PROVIDER", "claude").lower()
        if provider not in VALID_PROVIDERS:
            raise ValueError(f"LLM_PROVIDER must be one of {VALID_PROVIDERS}, got {provider!r}")

        effort = _env("ANTHROPIC_EFFORT", "").lower()
        if effort not in VALID_EFFORTS:
            raise ValueError(f"ANTHROPIC_EFFORT must be one of {VALID_EFFORTS[1:]}, got {effort!r}")

        embedding_provider = _env("EMBEDDING_PROVIDER", "local").lower()
        if embedding_provider not in VALID_EMBEDDING_PROVIDERS:
            raise ValueError(
                f"EMBEDDING_PROVIDER must be one of {VALID_EMBEDDING_PROVIDERS}, "
                f"got {embedding_provider!r}"
            )

        return cls(
            llm_provider=provider,
            anthropic_api_key=_env("ANTHROPIC_API_KEY") or None,
            anthropic_model=_env("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
            anthropic_effort=effort or None,
            openai_api_key=_env("OPENAI_API_KEY") or None,
            openai_model=_env("OPENAI_MODEL", ""),
            embedding_provider=embedding_provider,
            embedding_model=_env("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODELS[embedding_provider]),
            embedding_cache_dir=Path(_env("EMBEDDING_CACHE_DIR", "data/models")),
            index_dir=Path(_env("INDEX_DIR", "data/index")),
            top_k=int(_env("TOP_K", "5")),
            escalation_webhook_url=_env("ESCALATION_WEBHOOK_URL") or None,
            outbox_dir=Path(_env("OUTBOX_DIR", "data/outbox")),
            api_token=_env("API_TOKEN") or None,
            log_db=Path(_env("LOG_DB", "data/logs.sqlite")),
            allow_uploads=_env("ALLOW_UPLOADS", "true").lower() in ("1", "true", "yes"),
            max_questions_per_session=int(_env("MAX_QUESTIONS_PER_SESSION", "30")),
            max_questions_per_ip_hour=int(_env("MAX_QUESTIONS_PER_IP_HOUR", "120")),
            documents_dir=Path(_env("DOCUMENTS_DIR", "data/documents")),
            max_upload_mb=int(_env("MAX_UPLOAD_MB", "20")),
        )


def _env(name: str, default: str = "") -> str:
    """Environment variable, with blank values (``TOP_K=`` in .env) treated as unset."""
    value = os.getenv(name, "").strip()
    return value or default
