"""Application settings loaded from environment variables (and a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

VALID_PROVIDERS = ("claude", "openai")
# "fake" = offline hashing embedder: no API key, keyword-like matching (dev/tests only)
VALID_EMBEDDING_PROVIDERS = ("openai", "fake")


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    anthropic_api_key: str | None
    anthropic_model: str
    openai_api_key: str | None
    openai_model: str
    embedding_provider: str
    embedding_model: str
    index_dir: Path
    top_k: int

    @classmethod
    def from_env(cls, load_dotenv_file: bool = True) -> Settings:
        if load_dotenv_file:
            load_dotenv()

        provider = os.getenv("LLM_PROVIDER", "claude").lower()
        if provider not in VALID_PROVIDERS:
            raise ValueError(f"LLM_PROVIDER must be one of {VALID_PROVIDERS}, got {provider!r}")

        embedding_provider = os.getenv("EMBEDDING_PROVIDER", "openai").lower()
        if embedding_provider not in VALID_EMBEDDING_PROVIDERS:
            raise ValueError(
                f"EMBEDDING_PROVIDER must be one of {VALID_EMBEDDING_PROVIDERS}, "
                f"got {embedding_provider!r}"
            )

        return cls(
            llm_provider=provider,
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
            anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=os.getenv("OPENAI_MODEL", ""),
            embedding_provider=embedding_provider,
            embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
            index_dir=Path(os.getenv("INDEX_DIR", "data/index")),
            top_k=int(os.getenv("TOP_K", "5")),
        )
