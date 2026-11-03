"""Application settings loaded from environment variables (and a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

VALID_PROVIDERS = ("claude", "openai", "fake")  # fake = offline canned answers
VALID_EFFORTS = ("", "low", "medium", "high", "xhigh", "max")
# "fake" = offline hashing embedder: no API key, keyword-like matching (dev/tests only)
VALID_EMBEDDING_PROVIDERS = ("openai", "fake")


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
    index_dir: Path
    top_k: int
    allow_uploads: bool = True
    escalation_webhook_url: str | None = None
    outbox_dir: Path = Path("data/outbox")
    api_token: str | None = None
    max_questions_per_session: int = 30

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

        embedding_provider = _env("EMBEDDING_PROVIDER", "openai").lower()
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
            embedding_model=_env("EMBEDDING_MODEL", "text-embedding-3-small"),
            index_dir=Path(_env("INDEX_DIR", "data/index")),
            top_k=int(_env("TOP_K", "5")),
            escalation_webhook_url=_env("ESCALATION_WEBHOOK_URL") or None,
            outbox_dir=Path(_env("OUTBOX_DIR", "data/outbox")),
            api_token=_env("API_TOKEN") or None,
            allow_uploads=_env("ALLOW_UPLOADS", "true").lower() in ("1", "true", "yes"),
            max_questions_per_session=int(_env("MAX_QUESTIONS_PER_SESSION", "30")),
        )


def _env(name: str, default: str = "") -> str:
    """Environment variable, with blank values (``TOP_K=`` in .env) treated as unset."""
    value = os.getenv(name, "").strip()
    return value or default
