"""Import documents dropped into a folder (ADR-036).

A scanner that saves to a network folder, a colleague copying files, or an n8n workflow that
saves e-mail attachments: every supported file that lands in INBOX_DIR is indexed like an
upload (OCR included). The library keeps its own copy of the original, so the file is removed
from the inbox afterwards; a file that cannot be read moves to `failed/` next to a `.txt`
with the reason, and is not tried again.

A file is imported only once it has not changed for `SETTLE_S` seconds, so a file that is
still being written (a large scan arriving over the network) is not read half-way.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from docassist.ingest import DocumentLoadError
from docassist.ingest.loaders import SUPPORTED_EXTENSIONS
from docassist.library import Library

log = logging.getLogger(__name__)

SETTLE_S = 5.0  # seconds without a change before a file counts as complete
FAILED_DIR = "failed"
RECENT = 20  # imports kept for GET /inbox


@dataclass(frozen=True)
class Imported:
    source: str
    status: str  # "indexed" | "unchanged" | "failed"
    chunks_added: int = 0
    error: str | None = None
    at: str = ""  # ISO timestamp


class Inbox:
    """Watches one folder and adds what arrives to a library (`scan` does one pass)."""

    def __init__(self, folder: Path, settle_s: float = SETTLE_S) -> None:
        self.folder = folder
        self.settle_s = settle_s
        self.recent: deque[Imported] = deque(maxlen=RECENT)

    def ready(self, now: float | None = None) -> list[Path]:
        """Supported files in the folder that are complete (unchanged for `settle_s`)."""
        if not self.folder.is_dir():
            return []
        now = time.time() if now is None else now
        files = (p for p in self.folder.iterdir() if p.is_file() and not p.name.startswith("."))
        return sorted(
            (
                p
                for p in files
                if p.suffix.lower() in SUPPORTED_EXTENSIONS
                and now - p.stat().st_mtime >= self.settle_s
            ),
            key=lambda p: p.name.lower(),
        )

    def scan(self, library: Library, now: float | None = None) -> list[Imported]:
        """Import every complete file once; returns what happened to each."""
        self.folder.mkdir(parents=True, exist_ok=True)
        results = [self._import(path, library) for path in self.ready(now)]
        self.recent.extend(results)
        return results

    def _import(self, path: Path, library: Library) -> Imported:
        at = datetime.now(UTC).isoformat(timespec="seconds")
        try:
            added = library.add_file(path)
        except (DocumentLoadError, OSError) as exc:
            log.warning("Inbox: %s was not imported: %s", path.name, exc)
            self._set_aside(path, str(exc))
            return Imported(path.name, "failed", error=str(exc), at=at)
        path.unlink(missing_ok=True)  # the library keeps the original
        log.info("Inbox: %s %s (%d chunks)", path.name, added.status, added.chunks_added)
        return Imported(path.name, added.status, added.chunks_added, at=at)

    def _set_aside(self, path: Path, reason: str) -> None:
        failed = self.folder / FAILED_DIR
        failed.mkdir(exist_ok=True)
        target = failed / path.name
        if target.exists():
            target = failed / f"{path.stem}-{int(time.time())}{path.suffix}"
        shutil.move(path, target)
        target.with_name(target.name + ".txt").write_text(reason + "\n", encoding="utf-8")


def watch(
    inbox: Inbox,
    library: Callable[[], Library | None],
    every_s: float,
    stop: threading.Event,
) -> threading.Thread:
    """Scan the inbox every `every_s` seconds in a background thread until `stop` is set.
    `library()` returns None while the library is not available (e.g. being rebuilt)."""

    def run() -> None:
        while not stop.wait(every_s):
            try:
                current = library()
                if current is not None:
                    inbox.scan(current)
            except Exception:  # keep watching: one bad pass must not end the thread
                log.exception("Inbox scan failed")

    thread = threading.Thread(target=run, name="inbox", daemon=True)
    thread.start()
    return thread
