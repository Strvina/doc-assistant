"""Embeddings, vector store and retrieval."""

from docassist.retrieval.embedder import Embedder, FakeEmbedder, OpenAIEmbedder
from docassist.retrieval.store import (
    DocumentInfo,
    EmbedderMismatchError,
    SearchResult,
    VectorStore,
)


def retrieve(
    store: VectorStore, question: str, k: int = 5, min_score: float = 0.0
) -> list[SearchResult]:
    """The `k` chunks most similar to the question, best first, ignoring weak matches."""
    return [r for r in store.search(question, k) if r.score >= min_score]


__all__ = [
    "DocumentInfo",
    "Embedder",
    "EmbedderMismatchError",
    "FakeEmbedder",
    "OpenAIEmbedder",
    "SearchResult",
    "VectorStore",
    "retrieve",
]
