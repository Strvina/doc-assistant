"""Rebuild a stale index in the background, from the stored originals (ADR-032).

An index built with another embedder or another chunking (`CHUNKING`), or in the format of an
earlier version (ChromaDB, ADR-044), cannot be searched.
The web app then rebuilds it by itself instead of failing until someone runs
`index --rebuild`: every original in DOCUMENTS_DIR is indexed again, and the UI shows how far
it is. Nothing is deleted when an indexed document has no original to rebuild it from.

A marker file in the index folder says a rebuild has started and not finished, so a rebuild
cut short (server restart, `--reload`) is started again instead of leaving half an index.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from docassist.config import Settings
from docassist.factory import build_library
from docassist.ingest import DocumentLoadError
from docassist.ingest.loaders import SUPPORTED_EXTENSIONS
from docassist.library import Library
from docassist.retrieval.store import indexed_sources

log = logging.getLogger(__name__)

MARKER = "rebuilding"


@dataclass
class Rebuild:
    """Progress of one rebuild, read by the API while the thread works."""

    total: int = 0
    done: int = 0
    current: str = ""
    failed: list[str] = field(default_factory=list)
    error: str | None = None
    finished: bool = False
    # The index being rebuilt: the documents done so far can be listed and opened (ADR-047).
    library: Library | None = None

    @property
    def message(self) -> str:
        if self.error:
            return f"Rebuilding the index failed: {self.error}"
        step = f" (document {min(self.done + 1, self.total)} of {self.total})" if self.total else ""
        return f"The index is being rebuilt after an update{step}. Try again in a minute."


def is_unfinished(index_dir: Path) -> bool:
    """True when a rebuild started and did not finish."""
    return (index_dir / MARKER).exists()


def mark_started(index_dir: Path) -> None:
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / MARKER).write_text("A rebuild of this index started and has not finished.\n")


def mark_finished(index_dir: Path) -> None:
    (index_dir / MARKER).unlink(missing_ok=True)


def originals(documents_dir: Path) -> list[Path]:
    """The stored original files the index is built from, by name."""
    if not documents_dir.is_dir():
        return []
    files = (p for p in documents_dir.iterdir() if p.is_file())
    return sorted(
        (p for p in files if p.suffix.lower() in SUPPORTED_EXTENSIONS), key=lambda p: p.name.lower()
    )


def missing_originals(settings: Settings) -> list[str]:
    """Indexed documents without a stored original: a rebuild would lose them."""
    sources = indexed_sources(settings.index_dir)  # also from an index of an earlier version
    stored = {p.name for p in originals(settings.documents_dir)}
    return sorted(sources - stored, key=str.lower)


def rebuild(settings: Settings, status: Rebuild) -> Library:
    """Delete the index and index every stored original again; `status` follows along."""
    mark_started(settings.index_dir)
    library = build_library(settings, rebuild=True)
    status.library = library
    files = originals(settings.documents_dir)
    status.total = len(files)
    for path in files:
        status.current = path.name
        try:
            library.add_file(path)
        except (DocumentLoadError, OSError) as exc:
            log.warning("Rebuild: %s was not indexed: %s", path.name, exc)
            status.failed.append(f"{path.name}: {exc}")
        status.done += 1
    status.current = ""
    mark_finished(settings.index_dir)
    return library


def start(settings: Settings, on_done: Callable[[Library], None]) -> Rebuild:
    """Run `rebuild` in a background thread; `on_done` gets the new library."""
    status = Rebuild()

    def run() -> None:
        try:
            on_done(rebuild(settings, status))
        except Exception as exc:  # shown to the user instead of a silent dead thread
            log.exception("Rebuilding the index failed")
            status.error = str(exc)
        finally:
            status.finished = True

    threading.Thread(target=run, name="rebuild-index", daemon=True).start()
    return status
