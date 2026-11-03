"""Chunk storage and similarity search on top of a local ChromaDB collection (ADR-003)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb

from docassist.models import Chunk
from docassist.retrieval.embedder import Embedder

DEFAULT_COLLECTION = "chunks"
_ADD_BATCH = 1000


@dataclass(frozen=True, slots=True)
class SearchResult:
    chunk: Chunk
    score: float  # cosine similarity, higher is better (1.0 = identical direction)


@dataclass(frozen=True, slots=True)
class DocumentInfo:
    doc_id: str
    source: str
    chunks: int
    pages: int


class EmbedderMismatchError(RuntimeError):
    """The index was built with a different embedder than the one in use now."""


class VectorStore:
    """Stores chunks with their embeddings and finds the most similar ones to a query.

    A document is identified by its file name (`source`): adding a document with a name that
    is already indexed replaces the old version, unless the content is unchanged.
    """

    def __init__(
        self,
        path: str | Path | None,
        embedder: Embedder,
        *,
        collection: str = DEFAULT_COLLECTION,
        rebuild: bool = False,
        client: Any = None,
    ) -> None:
        if client is None:
            if path is None:
                raise ValueError("Either path or client is required")
            Path(path).mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(
                path=str(path),
                settings=chromadb.Settings(anonymized_telemetry=False),
            )
        self._client = client
        self._embedder = embedder
        self._name = collection

        if rebuild and collection in {c.name for c in client.list_collections()}:
            client.delete_collection(collection)

        self._collection = client.get_or_create_collection(
            collection,
            configuration={"hnsw": {"space": "cosine"}},
            metadata={"embedder": embedder.name},
            embedding_function=None,
        )
        built_with = (self._collection.metadata or {}).get("embedder")
        if built_with != embedder.name:
            raise EmbedderMismatchError(
                f"Index was built with {built_with!r} but the current embedder is "
                f"{embedder.name!r}. Rebuild the index (cli: index --rebuild)."
            )

    def add_document(self, chunks: Sequence[Chunk]) -> int:
        """Index all chunks of one document. Returns the number of chunks embedded.

        Returns 0 when the same content is already indexed under the same name, so
        re-running the indexer does not pay for embeddings again.
        """
        if not chunks:
            return 0
        source, doc_id = chunks[0].source, chunks[0].doc_id
        if any(c.source != source or c.doc_id != doc_id for c in chunks):
            raise ValueError("add_document() expects the chunks of exactly one document")

        existing = self._collection.get(where={"source": source}, include=["metadatas"])
        existing_ids = {m["doc_id"] for m in existing["metadatas"] or []}
        if existing_ids == {doc_id} and len(existing["ids"]) == len(chunks):
            return 0
        if existing["ids"]:
            self._collection.delete(where={"source": source})

        embeddings = self._embedder.embed([c.text for c in chunks])
        for start in range(0, len(chunks), _ADD_BATCH):
            batch = chunks[start : start + _ADD_BATCH]
            self._collection.add(
                ids=[c.chunk_id for c in batch],
                embeddings=embeddings[start : start + _ADD_BATCH],
                documents=[c.text for c in batch],
                metadatas=[{"source": c.source, "page": c.page, "doc_id": c.doc_id} for c in batch],
            )
        return len(chunks)

    def search(self, query: str, k: int = 5) -> list[SearchResult]:
        if k <= 0 or self.count() == 0:
            return []
        query_embedding = self._embedder.embed_query(query)
        result = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=min(k, self.count()),
            include=["documents", "metadatas", "distances"],
        )
        return [
            SearchResult(
                chunk=Chunk(
                    chunk_id=chunk_id,
                    doc_id=meta["doc_id"],
                    source=meta["source"],
                    page=int(meta["page"]),
                    text=text,
                ),
                score=1.0 - distance,
            )
            for chunk_id, text, meta, distance in zip(
                result["ids"][0],
                result["documents"][0],
                result["metadatas"][0],
                result["distances"][0],
                strict=True,
            )
        ]

    def documents(self) -> list[DocumentInfo]:
        """All indexed documents, sorted by name."""
        metadatas = self._collection.get(include=["metadatas"])["metadatas"] or []
        grouped: dict[str, dict[str, Any]] = {}
        for meta in metadatas:
            info = grouped.setdefault(
                meta["doc_id"], {"source": meta["source"], "chunks": 0, "pages": set()}
            )
            info["chunks"] += 1
            info["pages"].add(meta["page"])
        docs = [
            DocumentInfo(doc_id, info["source"], info["chunks"], len(info["pages"]))
            for doc_id, info in grouped.items()
        ]
        return sorted(docs, key=lambda d: d.source.lower())

    def document_chunks(self, source: str) -> list[Chunk]:
        """All chunks of one document (by file name), in reading order."""
        found = self._collection.get(where={"source": source}, include=["documents", "metadatas"])
        chunks = [
            Chunk(chunk_id, meta["doc_id"], meta["source"], int(meta["page"]), text)
            for chunk_id, text, meta in zip(
                found["ids"], found["documents"] or [], found["metadatas"] or [], strict=True
            )
        ]
        return sorted(chunks, key=lambda c: (c.page, int(c.chunk_id.rsplit("::", 1)[-1])))

    def delete_document(self, doc_id: str) -> int:
        """Remove every chunk of a document. Returns the number of chunks removed."""
        ids = self._collection.get(where={"doc_id": doc_id})["ids"]
        if ids:
            self._collection.delete(ids=ids)
        return len(ids)

    def count(self) -> int:
        return self._collection.count()
