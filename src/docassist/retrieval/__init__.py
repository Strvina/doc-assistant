"""Embeddings, vector store and retrieval."""

from collections.abc import Collection

from docassist.retrieval.embedder import Embedder, FakeEmbedder, LocalEmbedder, OpenAIEmbedder
from docassist.retrieval.store import (
    DocumentInfo,
    EmbedderMismatchError,
    SearchResult,
    VectorStore,
)


def retrieve(
    store: VectorStore,
    question: str,
    k: int = 5,
    min_score: float = 0.0,
    doc_ids: Collection[str] | None = None,
) -> list[SearchResult]:
    """The `k` chunks most similar to the question, best first, ignoring weak matches; only
    from the documents in `doc_ids` when it is given."""
    return [r for r in store.search(question, k, doc_ids) if r.score >= min_score]


__all__ = [
    "DocumentInfo",
    "Embedder",
    "EmbedderMismatchError",
    "FakeEmbedder",
    "LocalEmbedder",
    "OpenAIEmbedder",
    "SearchResult",
    "VectorStore",
    "retrieve",
]
