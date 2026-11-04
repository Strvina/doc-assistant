"""Application settings loaded from environment variables (and a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

VALID_PROVIDERS = ("claude", "openai", "gemini", "groq", "fake")  # fake = offline canned answers
VALID_EFFORTS = ("", "low", "medium", "high", "xhigh", "max")
# local = multilingual model on this machine (default, ADR-019); openai = optional API;
# fake = hashing embedder for tests (keyword-like matching only)
VALID_EMBEDDING_PROVIDERS = ("local", "openai", "fake")
DEFAULT_EMBEDDING_MODELS = {
    "local": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "openai": "text-embedding-3-small",
    "fake": "hashing",
}


# Free tier of the Gemini API (ADR-021). A pinned model: the "-latest" alias points to the
# newest model, which is often overloaded (HTTP 503) on the free tier.
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
DEFAULT_GEMINI_FALLBACK_MODEL = "gemini-flash-lite-latest"
# Groq free tier (ADR-022): fast open models, 1,000 requests/day and 8,000 tokens/min each.
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
DEFAULT_GROQ_FALLBACK_MODEL = "openai/gpt-oss-20b"


# Who an unanswered question can be forwarded to: "id:Label:e-mail" separated by commas.
# The demo company's teams; the n8n workflow sends the e-mail to the chosen team's address.
DEFAULT_ESCALATION_TEAMS = (
    "hr:HR:hr@example.com,"
    "maintenance:Održavanje:odrzavanje@example.com,"
    "quality:Kontrola kvaliteta:kvalitet@example.com"
)


@dataclass(frozen=True, slots=True)
class Team:
    """A team that can receive escalated questions (a button in the UI, a choice for the agent)."""

    id: str
    label: str
    email: str = ""


def parse_teams(value: str) -> tuple[Team, ...]:
    """Parse ESCALATION_TEAMS, e.g. "hr:HR:hr@firma.rs,maintenance:Održavanje"."""
    teams: list[Team] = []
    for item in value.split(","):
        if not item.strip():
            continue
        parts = [part.strip() for part in item.split(":")]
        if len(parts) > 3 or not parts[0]:
            raise ValueError(f"ESCALATION_TEAMS: expected id:Label:e-mail, got {item.strip()!r}")
        team_id = parts[0].lower()
        label = parts[1] if len(parts) > 1 and parts[1] else team_id
        email = parts[2] if len(parts) > 2 else ""
        if any(team.id == team_id for team in teams):
            raise ValueError(f"ESCALATION_TEAMS: duplicate team id {team_id!r}")
        teams.append(Team(team_id, label, email))
    if not teams:
        raise ValueError("ESCALATION_TEAMS must name at least one team")
    return tuple(teams)


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
    gemini_api_key: str | None = None
    gemini_model: str = DEFAULT_GEMINI_MODEL
    gemini_fallback_model: str = DEFAULT_GEMINI_FALLBACK_MODEL
    groq_api_key: str | None = None
    groq_model: str = DEFAULT_GROQ_MODEL
    groq_fallback_model: str = DEFAULT_GROQ_FALLBACK_MODEL
    escalation_teams: tuple[Team, ...] = parse_teams(DEFAULT_ESCALATION_TEAMS)
    hybrid_search: bool = True  # embeddings + keywords (ADR-023); false = embeddings only
    # Sites allowed to show the app in an iframe, e.g. https://huggingface.co for a Space.
    embed_origins: tuple[str, ...] = ()

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
            gemini_api_key=_env("GEMINI_API_KEY") or None,
            gemini_model=_env("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
            gemini_fallback_model=_env("GEMINI_FALLBACK_MODEL", DEFAULT_GEMINI_FALLBACK_MODEL),
            groq_api_key=_env("GROQ_API_KEY") or None,
            groq_model=_env("GROQ_MODEL", DEFAULT_GROQ_MODEL),
            groq_fallback_model=_env("GROQ_FALLBACK_MODEL", DEFAULT_GROQ_FALLBACK_MODEL),
            embedding_provider=embedding_provider,
            embedding_model=_env("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODELS[embedding_provider]),
            embedding_cache_dir=Path(_env("EMBEDDING_CACHE_DIR", "data/models")),
            index_dir=Path(_env("INDEX_DIR", "data/index")),
            top_k=int(_env("TOP_K", "5")),
            hybrid_search=_env("HYBRID_SEARCH", "true").lower() in ("1", "true", "yes"),
            embed_origins=tuple(o for o in _env("EMBED_ORIGINS").replace(",", " ").split() if o),
            escalation_webhook_url=_env("ESCALATION_WEBHOOK_URL") or None,
            escalation_teams=parse_teams(_env("ESCALATION_TEAMS", DEFAULT_ESCALATION_TEAMS)),
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
