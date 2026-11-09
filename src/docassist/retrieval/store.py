"""Chunk storage and hybrid search (embeddings + BM25 keywords) on ChromaDB (ADR-003, ADR-023)."""

from __future__ import annotations

import math
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb

from docassist.ingest.chunker import CHUNKING, starts_with_list_item
from docassist.models import Chunk
from docassist.retrieval.embedder import Embedder
from docassist.retrieval.keywords import KeywordIndex

DEFAULT_COLLECTION = "chunks"
_ADD_BATCH = 1000
# Hybrid search adds this many top keyword (BM25) hits that embedding search did not return.
KEYWORD_EXTRA = 1
# Chunks next to a hit on the same page go to the model with it, so a list or a paragraph cut
# into several chunks arrives whole (ADR-030): this many before and after the hit, then more
# following chunks while they continue a list ("4) …", "- …"), up to `LIST_FOLLOW`.
NEIGHBORS = 1
LIST_FOLLOW = 3


@dataclass(frozen=True, slots=True)
class SearchResult:
    chunk: Chunk
    score: float  # cosine similarity, higher is better (1.0 = identical direction)
    keyword_rank: int | None = None  # 1-based rank in the keyword (BM25) results, if any
    neighbor: bool = False  # added next to a hit (`with_neighbors`); `score` is the hit's


@dataclass(frozen=True, slots=True)
class DocumentInfo:
    doc_id: str
    source: str
    chunks: int
    pages: int


class EmbedderMismatchError(RuntimeError):
    """The index was built with a different embedder (or chunking) than the one in use now."""


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
        hybrid: bool = True,
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
        self._hybrid = hybrid
        self._keywords: KeywordIndex | None = None  # built on first search, dropped on change
        # (chunk count, documents): reading every chunk's metadata takes ~40 ms per 1,000
        # chunks, and the list is read by every citation click and every library refresh.
        self._documents: tuple[int, list[DocumentInfo]] | None = None

        if rebuild and collection in {c.name for c in client.list_collections()}:
            client.delete_collection(collection)

        self._collection = client.get_or_create_collection(
            collection,
            configuration={"hnsw": {"space": "cosine"}},
            metadata={"embedder": embedder.name, "chunking": CHUNKING},
            embedding_function=None,
        )
        meta = self._collection.metadata or {}
        built_with = meta.get("embedder")
        if built_with != embedder.name:
            raise EmbedderMismatchError(
                f"Index was built with {built_with!r} but the current embedder is "
                f"{embedder.name!r}. Rebuild the index (cli: index data/documents --rebuild)."
            )
        if meta.get("chunking") != CHUNKING:
            raise EmbedderMismatchError(
                f"Index was built with chunking {meta.get('chunking')!r}, the current one is "
                f"{CHUNKING!r}. Rebuild the index (cli: index data/documents --rebuild)."
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
            self._changed()

        embeddings = self._embedder.embed([c.text for c in chunks])
        for start in range(0, len(chunks), _ADD_BATCH):
            batch = chunks[start : start + _ADD_BATCH]
            self._collection.add(
                ids=[c.chunk_id for c in batch],
                embeddings=embeddings[start : start + _ADD_BATCH],
                documents=[c.text for c in batch],
                metadatas=[{"source": c.source, "page": c.page, "doc_id": c.doc_id} for c in batch],
            )
        self._changed()
        return len(chunks)

    def search(
        self, query: str, k: int = 5, doc_ids: Collection[str] | None = None
    ) -> list[SearchResult]:
        """The `k` chunks most similar in meaning, best first; in hybrid mode (default) plus
        up to `KEYWORD_EXTRA` chunks that keyword search ranks first but embeddings missed.
        With `doc_ids`, only the chunks of those documents are searched.

        Keyword hits are added, not merged into the ranking: merging (tried: Reciprocal Rank
        Fusion, also with lower keyword weight) pushed correct German passages for Serbian
        questions out of the top k, because keywords cannot match across languages
        (measured in docs/EVAL_RESULTS.md, ADR-023). `score` is always the cosine similarity.
        """
        if k <= 0 or self.count() == 0 or (doc_ids is not None and not doc_ids):
            return []
        query_embedding = self._embedder.embed_query(query)
        hits = self._vector_search(query_embedding, min(k, self.count()), doc_ids)
        if not self._hybrid:
            return hits

        seen = {hit.chunk.chunk_id for hit in hits}
        keyword_hits = self._keyword_index().search(query, k, doc_ids)
        extra = [
            (rank, chunk)
            for rank, (chunk, _) in enumerate(keyword_hits, start=1)
            if chunk.chunk_id not in seen
        ][:KEYWORD_EXTRA]
        if extra:
            scored = self._scored([chunk.chunk_id for _, chunk in extra], query_embedding)
            hits += [
                SearchResult(chunk, scored[chunk.chunk_id].score, keyword_rank=rank)
                for rank, chunk in extra
            ]
        return hits

    def with_neighbors(
        self, results: Sequence[SearchResult], window: int = NEIGHBORS
    ) -> list[SearchResult]:
        """`results` plus the chunks next to each hit on the same page (see `NEIGHBORS`).

        Hits keep their order; added chunks follow them, marked `neighbor`, with the score of
        the hit they belong to. Each chunk appears once.
        """
        if window <= 0 or not results:
            return list(results)
        positions = {r.chunk.chunk_id: _position(r.chunk.chunk_id) for r in results}
        wanted = {
            f"{prefix}::{i}"
            for prefix, index in filter(None, positions.values())
            for i in range(max(0, index - window), index + window + LIST_FOLLOW + 1)
        }
        found = self._collection.get(ids=sorted(wanted), include=["documents", "metadatas"])
        nearby = {
            chunk_id: _chunk(chunk_id, text, meta)
            for chunk_id, text, meta in zip(
                found["ids"], found["documents"], found["metadatas"], strict=True
            )
        }

        seen = set(positions)
        added: list[SearchResult] = []
        for hit in results:
            position = positions[hit.chunk.chunk_id]
            if position is None:
                continue
            prefix, index = position
            ids = [f"{prefix}::{i}" for i in range(max(0, index - window), index + window + 1)]
            last = index + window
            while last - index - window < LIST_FOLLOW:
                following = nearby.get(f"{prefix}::{last + 1}")
                if following is None or not starts_with_list_item(following.text):
                    break
                last += 1
                ids.append(following.chunk_id)
            for chunk_id in ids:
                if chunk_id in nearby and chunk_id not in seen:
                    seen.add(chunk_id)
                    added.append(SearchResult(nearby[chunk_id], hit.score, neighbor=True))
        return [*results, *added]

    def _vector_search(
        self, query_embedding: list[float], n: int, doc_ids: Collection[str] | None = None
    ) -> list[SearchResult]:
        where = None if doc_ids is None else {"doc_id": {"$in": sorted(doc_ids)}}
        result = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=n,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        return [
            SearchResult(chunk=_chunk(chunk_id, text, meta), score=1.0 - distance)
            for chunk_id, text, meta, distance in zip(
                result["ids"][0],
                result["documents"][0],
                result["metadatas"][0],
                result["distances"][0],
                strict=True,
            )
        ]

    def _scored(self, ids: list[str], query_embedding: list[float]) -> dict[str, SearchResult]:
        found = self._collection.get(ids=ids, include=["documents", "metadatas", "embeddings"])
        results = {}
        for chunk_id, text, meta, vector in zip(
            found["ids"], found["documents"], found["metadatas"], found["embeddings"], strict=True
        ):
            results[chunk_id] = SearchResult(
                _chunk(chunk_id, text, meta), _cosine(query_embedding, list(vector))
            )
        return results

    def _keyword_index(self) -> KeywordIndex:
        # Rebuilt when another process (UI and API share the index) changed the collection.
        if self._keywords is None or len(self._keywords) != self.count():
            found = self._collection.get(include=["documents", "metadatas"])
            self._keywords = KeywordIndex(
                [
                    _chunk(chunk_id, text, meta)
                    for chunk_id, text, meta in zip(
                        found["ids"], found["documents"], found["metadatas"], strict=True
                    )
                ]
            )
        return self._keywords

    def _changed(self) -> None:
        """Forget what was derived from the chunks (after adding or deleting some)."""
        self._keywords = None
        self._documents = None

    def documents(self) -> list[DocumentInfo]:
        """All indexed documents, sorted by name.

        Kept until this store changes the index; like the keyword index, it is also read
        again when the number of chunks changed (another process added or deleted some).
        """
        count = self.count()
        if self._documents is None or self._documents[0] != count:
            self._documents = (count, self._read_documents())
        return list(self._documents[1])

    def _read_documents(self) -> list[DocumentInfo]:
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
            self._changed()
        return len(ids)

    def count(self) -> int:
        return self._collection.count()


def _chunk(chunk_id: str, text: str, meta: dict[str, Any]) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        doc_id=meta["doc_id"],
        source=meta["source"],
        page=int(meta["page"]),
        text=text,
    )


def _position(chunk_id: str) -> tuple[str, int] | None:
    """("x.pdf::p3", 2) for "x.pdf::p3::2"; None for an id in another form."""
    prefix, _, index = chunk_id.rpartition("::")
    return (prefix, int(index)) if prefix and index.isdigit() else None


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / norm if norm else 0.0
