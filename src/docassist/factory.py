"""Build the configured components from `Settings` (one place that knows about providers)."""

from __future__ import annotations

from docassist.config import Settings
from docassist.llm import FakeProvider, LLMProvider
from docassist.retrieval import Embedder, FakeEmbedder, OpenAIEmbedder, VectorStore


class MissingAPIKeyError(RuntimeError):
    """A provider was selected but its API key is not configured."""


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "fake":
        return FakeEmbedder()
    if not settings.openai_api_key:
        raise MissingAPIKeyError(
            "OPENAI_API_KEY is not set (needed for embeddings). Add it to .env, "
            "or set EMBEDDING_PROVIDER=fake to try the app offline."
        )
    return OpenAIEmbedder(settings.embedding_model, api_key=settings.openai_api_key)


def build_store(settings: Settings, *, rebuild: bool = False) -> VectorStore:
    return VectorStore(settings.index_dir, build_embedder(settings), rebuild=rebuild)


def build_llm(settings: Settings, provider: str | None = None) -> LLMProvider:
    """The configured LLM, or `provider` ("claude" | "openai" | "fake") to override it."""
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
    raise ValueError(f"Unknown LLM provider {provider!r}")
