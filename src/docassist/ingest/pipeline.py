"""File → chunks: the full ingestion step for one document."""

from __future__ import annotations

import hashlib
from pathlib import Path

from docassist.ingest.chunker import DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP, chunk_pages
from docassist.ingest.loaders import load_document
from docassist.models import Chunk


def document_id(path: str | Path) -> str:
    """Stable ID derived from the file content (same file → same ID, wherever it lives)."""
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return digest[:16]


def ingest(
    path: str | Path,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """Load a document and split it into chunks with citation metadata."""
    pages = load_document(path)
    return chunk_pages(pages, document_id(path), chunk_size=chunk_size, overlap=overlap)
