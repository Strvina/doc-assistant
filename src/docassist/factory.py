"""Build the configured components from `Settings` (one place that knows about providers)."""

from __future__ import annotations

from docassist.config import Settings
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
