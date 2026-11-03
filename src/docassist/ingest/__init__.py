"""Document ingestion: loading files and splitting them into chunks."""

from docassist.ingest.loaders import (
    SUPPORTED_EXTENSIONS,
    DocumentLoadError,
    UnsupportedFileTypeError,
    load_document,
)

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "DocumentLoadError",
    "UnsupportedFileTypeError",
    "load_document",
]
