"""Build the configured components from `Settings` (one place that knows about providers)."""

from __future__ import annotations

from docassist.agent.tools import Escalator, JsonlFile, ToolContext, WebhookEscalator
from docassist.config import Settings
from docassist.library import Library
from docassist.llm import FakeProvider, LLMProvider
from docassist.retrieval import Embedder, FakeEmbedder, LocalEmbedder, OpenAIEmbedder, VectorStore


class MissingAPIKeyError(RuntimeError):
    """A provider was selected but its API key is not configured."""


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "fake":
        return FakeEmbedder()
    if settings.embedding_provider == "local":
        return LocalEmbedder(settings.embedding_model, cache_dir=settings.embedding_cache_dir)
    if not settings.openai_api_key:
        raise MissingAPIKeyError(
            "OPENAI_API_KEY is not set (needed for EMBEDDING_PROVIDER=openai). Add it to .env, "
            "or use EMBEDDING_PROVIDER=local (default, no key needed)."
        )
    return OpenAIEmbedder(settings.embedding_model, api_key=settings.openai_api_key)


def build_store(settings: Settings, *, rebuild: bool = False) -> VectorStore:
    return VectorStore(settings.index_dir, build_embedder(settings), rebuild=rebuild)


def build_llm(settings: Settings, provider: str | None = None) -> LLMProvider:
    """The configured LLM, or `provider` ("claude" | "openai" | "gemini" | "fake")."""
    provider = provider or settings.llm_provider
    if provider == "fake":
        return FakeProvider()
    if provider == "claude":
        if not settings.anthropic_api_key:
            raise MissingAPIKeyError("ANTHROPIC_API_KEY is not set. Add it to .env.")
        from docassist.llm.claude import ClaudeProvider

        return ClaudeProvider(
            settings.anthropic_model,
            api_key=settings.anthropic_api_key,
            effort=settings.anthropic_effort,
        )
    if provider == "openai":
        if not settings.openai_api_key:
            raise MissingAPIKeyError("OPENAI_API_KEY is not set. Add it to .env.")
        if not settings.openai_model:
            raise MissingAPIKeyError("OPENAI_MODEL is not set. Add a current chat model to .env.")
        from docassist.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(settings.openai_model, api_key=settings.openai_api_key)
    if provider == "gemini":
        if not settings.gemini_api_key:
            raise MissingAPIKeyError(
                "GEMINI_API_KEY is not set. Get a free key at https://aistudio.google.com "
                "and add it to .env."
            )
        from docassist.llm.gemini import GeminiProvider

        return GeminiProvider(
            settings.gemini_model,
            api_key=settings.gemini_api_key,
            fallback_models=(settings.gemini_fallback_model,),
        )
    raise ValueError(f"Unknown LLM provider {provider!r}")


def build_escalator(settings: Settings) -> Escalator:
    """Escalations go to the webhook (n8n) if configured, otherwise to a local file."""
    if settings.escalation_webhook_url:
        return WebhookEscalator(settings.escalation_webhook_url)
    return JsonlFile(settings.outbox_dir / "escalations.jsonl")


def build_tool_context(settings: Settings, store: VectorStore) -> ToolContext:
    """Everything the agent tools need."""
    return ToolContext(
        store=store,
        escalator=build_escalator(settings),
        leave_requests=JsonlFile(settings.outbox_dir / "leave_requests.jsonl"),
        top_k=settings.top_k,
        teams=settings.escalation_teams,
    )


def build_library(settings: Settings, *, rebuild: bool = False) -> Library:
    """The index plus the stored originals (for the viewer and downloads)."""
    return Library(build_store(settings, rebuild=rebuild), settings.documents_dir)
