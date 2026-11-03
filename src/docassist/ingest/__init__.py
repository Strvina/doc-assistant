"""Document ingestion: loading files and splitting them into chunks."""

from docassist.ingest.chunker import chunk_pages, split_text
from docassist.ingest.loaders import (
    SUPPORTED_EXTENSIONS,
    DocumentLoadError,
    UnsupportedFileTypeError,
    load_document,
)
from docassist.ingest.pipeline import document_id, ingest

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "DocumentLoadError",
    "UnsupportedFileTypeError",
    "chunk_pages",
    "document_id",
    "ingest",
    "load_document",
    "split_text",
]
