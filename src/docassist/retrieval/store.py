"""Chunk storage and hybrid search (embeddings + BM25 keywords): SQLite and numpy (ADR-044).

The chunks, their embeddings and the index settings live in one SQLite file
(`INDEX_DIR/index.sqlite`). Search compares the question with every chunk (exact cosine
similarity, one matrix product in numpy) instead of an approximate index: for a company's
documents (thousands to tens of thousands of chunks) that takes a few milliseconds and never
misses the best chunk. Measured against the ChromaDB index used before (ADR-003): the same
top-5 chunks for every evaluation question, 7x faster, without its 48 extra packages.

The chunks are read into memory once and read again only when the file changes: by this
store (it forgets them on every write) or by another process (SQLite's `data_version`).
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from docassist.ingest.chunker import CHUNKING, starts_with_list_item
from docassist.models import Chunk
from docassist.retrieval.embedder import Embedder
from docassist.retrieval.keywords import KeywordIndex

INDEX_FILE = "index.sqlite"
# The ChromaDB index of earlier versions: when only that one exists, the index is rebuilt.
CHROMA_FILE = "chroma.sqlite3"
# Hybrid search adds this many top keyword (BM25) hits that embedding search did not return.
KEYWORD_EXTRA = 1
# Chunks next to a hit on the same page go to the model with it, so a list or a paragraph cut
# into several chunks arrives whole (ADR-030): this many before and after the hit, then more
# following chunks while they continue a list ("4) …", "- …"), up to `LIST_FOLLOW`.
NEIGHBORS = 1
LIST_FOLLOW = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,                 -- embedder | chunking
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id  TEXT PRIMARY KEY,             -- "source::pN::i"
    doc_id    TEXT NOT NULL,                -- hash of the file's content
    source    TEXT NOT NULL,                -- file name
    page      INTEGER NOT NULL,
    position  INTEGER NOT NULL,             -- i: order of the chunk on its page
    text      TEXT NOT NULL,
    embedding BLOB NOT NULL                 -- float32, unit length
);
CREATE INDEX IF NOT EXISTS chunks_source ON chunks (source);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks (doc_id);
"""


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


@dataclass
class _Snapshot:
    """Every chunk in reading order, with its embedding as a row of `vectors`."""

    chunks: list[Chunk]
    vectors: np.ndarray  # shape (chunks, dimensions), float32, unit rows
    doc_ids: np.ndarray  # the chunks' doc ids, to limit a search to some documents
    by_id: dict[str, int] = field(init=False)
    keywords: KeywordIndex | None = None  # built on the first hybrid search
    documents: list[DocumentInfo] | None = None  # built on the first `documents()`

    def __post_init__(self) -> None:
        self.by_id = {chunk.chunk_id: i for i, chunk in enumerate(self.chunks)}


class VectorStore:
    """Stores chunks with their embeddings and finds the most similar ones to a query.

    A document is identified by its file name (`source`): adding a document with a name that
    is already indexed replaces the old version, unless the content is unchanged.
    """

    def __init__(
        self,
        path: str | Path,
        embedder: Embedder,
        *,
        rebuild: bool = False,
        hybrid: bool = True,
    ) -> None:
        folder = Path(path)
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / INDEX_FILE
        if not self.path.exists() and (folder / CHROMA_FILE).exists() and not rebuild:
            raise EmbedderMismatchError(
                "The index is in the format of an earlier version (ChromaDB). Rebuild the "
                "index (cli: index data/documents --rebuild)."
            )
        self._embedder = embedder
        self._hybrid = hybrid
        self._lock = threading.Lock()  # one connection, shared by the server's threads
        self._db = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        self._snapshot: _Snapshot | None = None
        self._version = -1  # SQLite's data_version when the snapshot was read

        wanted = {"embedder": embedder.name, "chunking": CHUNKING}
        with self._lock, self._db:
            if rebuild:
                self._db.executescript("DROP TABLE IF EXISTS chunks; DROP TABLE IF EXISTS settings")
            self._db.executescript(_SCHEMA)
            built_with = dict(self._db.execute("SELECT key, value FROM settings").fetchall())
            if not built_with:  # a new index
                self._db.executemany("INSERT INTO settings VALUES (?, ?)", wanted.items())
                built_with = wanted
        if built_with.get("embedder") != embedder.name:
            raise EmbedderMismatchError(
                f"Index was built with {built_with.get('embedder')!r} but the current embedder "
                f"is {embedder.name!r}. Rebuild the index (cli: index data/documents --rebuild)."
            )
        if built_with.get("chunking") != CHUNKING:
            raise EmbedderMismatchError(
                f"Index was built with chunking {built_with.get('chunking')!r}, the current one "
                f"is {CHUNKING!r}. Rebuild the index (cli: index data/documents --rebuild)."
            )

    def close(self) -> None:
        self._db.close()

    # --- changing the index ----------------------------------------------------------------

    def add_document(self, chunks: Sequence[Chunk]) -> int:
        """Index all chunks of one document. Returns the number of chunks embedded.

        Returns 0 when the same content is already indexed under the same name, so
        re-running the indexer does not pay for embeddings again. A new version replaces the
        old one in one transaction: a search never sees half of each.
        """
        if not chunks:
            return 0
        source, doc_id = chunks[0].source, chunks[0].doc_id
        if any(c.source != source or c.doc_id != doc_id for c in chunks):
            raise ValueError("add_document() expects the chunks of exactly one document")

        with self._lock:
            existing = self._db.execute(
                "SELECT doc_id, COUNT(*) FROM chunks WHERE source = ? GROUP BY doc_id", (source,)
            ).fetchall()
        if existing == [(doc_id, len(chunks))]:
            return 0

        vectors = _unit_rows(self._embedder.embed([c.text for c in chunks]))  # outside the lock
        rows = [
            (c.chunk_id, c.doc_id, c.source, c.page, _index(c.chunk_id), c.text, vector.tobytes())
            for c, vector in zip(chunks, vectors, strict=True)
        ]
        with self._lock, self._db:
            self._db.execute("DELETE FROM chunks WHERE source = ?", (source,))
            self._db.executemany("INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            self._snapshot = None
        return len(chunks)

    def delete_document(self, doc_id: str) -> int:
        """Remove every chunk of a document. Returns the number of chunks removed."""
        with self._lock, self._db:
            removed = self._db.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,)).rowcount
            if removed:
                self._snapshot = None
        return removed

    # --- reading it ------------------------------------------------------------------------

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
        snapshot = self._current()
        if k <= 0 or not snapshot.chunks or (doc_ids is not None and not doc_ids):
            return []
        scores = snapshot.vectors @ _unit_rows([self._embedder.embed_query(query)])[0]
        if doc_ids is not None:
            scores = np.where(np.isin(snapshot.doc_ids, list(doc_ids)), scores, -np.inf)
        candidates = int(np.isfinite(scores).sum())
        best = np.argsort(-scores, kind="stable")[: min(k, candidates)]
        hits = [SearchResult(snapshot.chunks[i], float(scores[i])) for i in best]
        if not self._hybrid:
            return hits

        if snapshot.keywords is None:
            snapshot.keywords = KeywordIndex(snapshot.chunks)
        seen = {hit.chunk.chunk_id for hit in hits}
        keyword_hits = snapshot.keywords.search(query, k, doc_ids)
        extra = [
            (rank, chunk)
            for rank, (chunk, _) in enumerate(keyword_hits, start=1)
            if chunk.chunk_id not in seen
        ][:KEYWORD_EXTRA]
        return hits + [
            SearchResult(chunk, float(scores[snapshot.by_id[chunk.chunk_id]]), keyword_rank=rank)
            for rank, chunk in extra
        ]

    def with_neighbors(
        self, results: Sequence[SearchResult], window: int = NEIGHBORS
    ) -> list[SearchResult]:
        """`results` plus the chunks next to each hit on the same page (see `NEIGHBORS`).

        Hits keep their order; added chunks follow them, marked `neighbor`, with the score of
        the hit they belong to. Each chunk appears once.
        """
        if window <= 0 or not results:
            return list(results)
        snapshot = self._current()

        def nearby(chunk_id: str) -> Chunk | None:
            index = snapshot.by_id.get(chunk_id)
            return None if index is None else snapshot.chunks[index]

        seen = {r.chunk.chunk_id for r in results}
        added: list[SearchResult] = []
        for hit in results:
            position = _position(hit.chunk.chunk_id)
            if position is None:
                continue
            prefix, index = position
            ids = [f"{prefix}::{i}" for i in range(max(0, index - window), index + window + 1)]
            last = index + window
            while last - index - window < LIST_FOLLOW:
                following = nearby(f"{prefix}::{last + 1}")
                if following is None or not starts_with_list_item(following.text):
                    break
                last += 1
                ids.append(following.chunk_id)
            for chunk_id in ids:
                chunk = nearby(chunk_id)
                if chunk is not None and chunk_id not in seen:
                    seen.add(chunk_id)
                    added.append(SearchResult(chunk, hit.score, neighbor=True))
        return [*results, *added]

    def documents(self) -> list[DocumentInfo]:
        """All indexed documents, sorted by name."""
        snapshot = self._current()
        if snapshot.documents is None:
            grouped: dict[str, tuple[str, int, set[int]]] = {}
            for chunk in snapshot.chunks:
                source, count, pages = grouped.get(chunk.doc_id, (chunk.source, 0, set()))
                pages.add(chunk.page)
                grouped[chunk.doc_id] = (source, count + 1, pages)
            docs = [
                DocumentInfo(doc_id, source, count, len(pages))
                for doc_id, (source, count, pages) in grouped.items()
            ]
            snapshot.documents = sorted(docs, key=lambda d: d.source.lower())
        return list(snapshot.documents)

    def document_chunks(self, source: str) -> list[Chunk]:
        """All chunks of one document (by file name), in reading order."""
        return [c for c in self._current().chunks if c.source == source]

    def count(self) -> int:
        return len(self._current().chunks)

    def _current(self) -> _Snapshot:
        """The chunks in memory, read again when the index file changed."""
        with self._lock:
            version = self._db.execute("PRAGMA data_version").fetchone()[0]
            if self._snapshot is None or version != self._version:
                self._snapshot = self._read()
                self._version = version
            return self._snapshot

    def _read(self) -> _Snapshot:
        rows = self._db.execute(
            "SELECT chunk_id, doc_id, source, page, text, embedding FROM chunks"
            " ORDER BY source, page, position"
        ).fetchall()
        chunks = [Chunk(*row[:5]) for row in rows]
        blobs = b"".join(row[5] for row in rows)
        vectors = np.frombuffer(blobs, dtype=np.float32).reshape(len(rows), -1 if rows else 0)
        return _Snapshot(chunks, vectors, np.array([c.doc_id for c in chunks], dtype=object))


def indexed_sources(index_dir: Path) -> set[str]:
    """File names of the documents in an index folder, without opening it as a store: also
    from an index of an earlier version (ChromaDB), so a rebuild can check that every
    document has an original to be rebuilt from."""
    for name, sql in (
        (INDEX_FILE, "SELECT DISTINCT source FROM chunks"),
        (CHROMA_FILE, "SELECT DISTINCT string_value FROM embedding_metadata WHERE key = 'source'"),
    ):
        path = index_dir / name
        if not path.exists():
            continue
        db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            return {str(row[0]) for row in db.execute(sql)}
        except sqlite3.OperationalError:  # no table yet: an empty index
            return set()
        finally:
            db.close()
    return set()


def _unit_rows(vectors: Sequence[Sequence[float]]) -> np.ndarray:
    """The vectors as float32 rows of length 1, so a dot product is the cosine similarity."""
    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return np.divide(matrix, norms, out=np.zeros_like(matrix), where=norms > 0)


def _position(chunk_id: str) -> tuple[str, int] | None:
    """("x.pdf::p3", 2) for "x.pdf::p3::2"; None for an id in another form."""
    prefix, _, index = chunk_id.rpartition("::")
    return (prefix, int(index)) if prefix and index.isdigit() else None


def _index(chunk_id: str) -> int:
    position = _position(chunk_id)
    return position[1] if position else 0
