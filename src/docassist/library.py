"""The document library: indexed chunks plus a copy of every original file.

The copy makes it possible to open a cited page in the viewer or download the original,
and deleting a document removes both (see README, "Data protection").
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from docassist.ingest import (
    SUPPORTED_EXTENSIONS,
    DocumentLoadError,
    UnsupportedFileTypeError,
    ingest,
    vision,
)
from docassist.retrieval import DocumentInfo, VectorStore

_UNSAFE = re.compile(r"[^\w.\- ()]+", re.UNICODE)
_WORD = re.compile(r"\w+", re.UNICODE)
_STOPWORDS = {
    "sr": {
        "je",
        "i",
        "se",
        "na",
        "u",
        "za",
        "da",
        "od",
        "su",
        "sa",
        "ili",
        "koji",
        "kod",
        "iz",
        "po",
    },
    "de": {
        "der",
        "die",
        "und",
        "das",
        "ist",
        "zu",
        "mit",
        "von",
        "den",
        "für",
        "nicht",
        "auf",
        "im",
    },
    "en": {
        "the",
        "and",
        "of",
        "to",
        "is",
        "in",
        "for",
        "with",
        "are",
        "be",
        "on",
        "or",
        "by",
        "must",
    },
}


def guess_language(text: str) -> str | None:
    """'sr', 'de' or 'en' by counting common short words; None when unsure."""
    words = [w.lower() for w in _WORD.findall(text[:5000])]
    scores = {lang: sum(w in stop for w in words) for lang, stop in _STOPWORDS.items()}
    best = max(scores, key=lambda lang: scores[lang])
    return best if scores[best] >= 5 else None


@dataclass(frozen=True)
class AddResult:
    source: str
    chunks_added: int  # 0 = same content was already indexed

    @property
    def status(self) -> str:
        return "indexed" if self.chunks_added else "unchanged"


def safe_name(name: str) -> str:
    """A file name that is safe to store: no directories, no control or odd characters."""
    name = Path(name.replace("\\", "/")).name
    stem, suffix = Path(name).stem, Path(name).suffix.lower()
    stem = _UNSAFE.sub("_", stem).strip(" ._") or "document"
    return f"{stem[:120]}{suffix}"


class Library:
    def __init__(self, store: VectorStore, files_dir: str | Path) -> None:
        self.store = store
        self.files_dir = Path(files_dir)

    def add_file(self, path: str | Path) -> AddResult:
        """Index a file and keep a copy of it (no copy if it already lives in files_dir)."""
        path = Path(path)
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise UnsupportedFileTypeError(f"Unsupported file type {path.suffix!r} for {path.name}")
        chunks = ingest(path)
        if not chunks:
            raise DocumentLoadError(
                f"{path.name}: no text found, also not with OCR (is Tesseract installed?)"
            )
        added = self.store.add_document(chunks)
        target = self.files_dir / path.name
        if path.resolve() != target.resolve():
            self.files_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        return AddResult(path.name, added)

    def add_bytes(self, name: str, data: bytes) -> AddResult:
        """Store an uploaded file under a safe name and index it."""
        name = safe_name(name)
        if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise UnsupportedFileTypeError(f"Unsupported file type for {name}")
        self.files_dir.mkdir(parents=True, exist_ok=True)
        target = self.files_dir / name
        previous = target.read_bytes() if target.exists() else None
        target.write_bytes(data)
        try:
            return self.add_file(target)
        except Exception:
            # Keep the library consistent: restore or remove the file we just wrote.
            if previous is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(previous)
            raise

    def documents(self) -> list[DocumentInfo]:
        return self.store.documents()

    def language(self, source: str) -> str | None:
        chunks = self.store.document_chunks(source)[:6]
        return guess_language(" ".join(c.text for c in chunks))

    def get(self, doc_id: str) -> DocumentInfo | None:
        return next((d for d in self.store.documents() if d.doc_id == doc_id), None)

    def original(self, doc_id: str) -> tuple[DocumentInfo, Path] | None:
        """The document and its original file, if both exist."""
        doc = self.get(doc_id)
        if doc is None:
            return None
        path = self.files_dir / doc.source
        return (doc, path) if path.is_file() else None

    def delete(self, doc_id: str) -> int:
        """Remove the chunks and the stored original. Returns the number of chunks removed."""
        doc = self.get(doc_id)
        removed = self.store.delete_document(doc_id)
        if doc is not None:
            original = self.files_dir / doc.source
            original.unlink(missing_ok=True)
            vision.sidecar_path(original).unlink(missing_ok=True)
        return removed

    def read_pictures(
        self,
        doc_id: str,
        read_image: vision.ReadImage,
        model: str,
        on_done: Callable[[int, int], None] | None = None,
    ) -> vision.VisionResult:
        """Have a vision model read the picture pages of a document, then index it again with
        that text (ADR-029). Pages read earlier are not sent again."""
        found = self.original(doc_id)
        if found is None:
            raise vision.VisionError("Document or its original file not found")
        _doc, path = found
        result = vision.read_document(path, read_image, model, on_done)
        if result.pages_read:
            chunks = ingest(path)
            self.store.delete_document(doc_id)
            self.store.add_document(chunks)
        return result
