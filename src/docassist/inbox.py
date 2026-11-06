"""Import documents dropped into a folder (ADR-036, ADR-037).

A scanner that saves to a network folder, a colleague copying files, a synced OneDrive folder
or an n8n workflow that saves e-mail attachments: every supported file that lands in the watched
folder is indexed like an upload (OCR included). The folder is chosen in the web UI ("Automatski
uvoz"); the choice is kept in `inbox.json` next to the query log.

Nothing is deleted: an imported file moves to the subfolder "Uvezeno", one that cannot be read
to "Nije uspelo" next to a `.txt` with the reason, and is not tried again until the user asks.
A file is imported only once it has not changed for `SETTLE_S` seconds, so a file that is
still being written (a large scan arriving over the network) is not read half-way.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from docassist.ingest import DocumentLoadError
from docassist.ingest.loaders import SUPPORTED_EXTENSIONS
from docassist.library import Library

log = logging.getLogger(__name__)

SETTLE_S = 5.0  # seconds without a change before a file counts as complete
IMPORTED_DIR = "Uvezeno"  # names the user sees in the folder
FAILED_DIR = "Nije uspelo"
RECENT = 20

# Reasons a file could not be imported, for people (the technical message stays in the log).
_REASONS = (
    ("password-protected", "Dokument je zaštićen lozinkom."),
    ("no text found", "U dokumentu nema teksta, ni OCR ga nije pročitao (možda je prazan sken)."),
    ("unsupported file type", "Ova vrsta fajla nije podržana."),
    ("cannot open", "Fajl je oštećen ili nije pravi dokument te vrste."),
    ("permission", "Aplikacija nema pravo da čita ili premesti ovaj fajl."),
)


def reason_for_people(error: str) -> str:
    lower = error.lower()
    for marker, reason in _REASONS:
        if marker in lower:
            return reason
    return f"Fajl nije mogao da se pročita ({error})."


@dataclass(frozen=True)
class Imported:
    source: str
    status: str  # "indexed" | "unchanged" | "failed"
    chunks_added: int = 0
    error: str | None = None  # for people (see `reason_for_people`)
    at: str = ""  # ISO timestamp


@dataclass(frozen=True)
class Failed:
    name: str
    reason: str
    at: str  # ISO timestamp of the failure


# --- what was imported (kept in the query log database) ---------------------------------


class ImportLog:
    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS imports (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        at           TEXT    NOT NULL,
        source       TEXT    NOT NULL,
        status       TEXT    NOT NULL,
        chunks_added INTEGER NOT NULL,
        error        TEXT
    );
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self._path)
        db.executescript(self._SCHEMA)
        return db

    def add(self, items: Iterable[Imported]) -> None:
        rows = [(i.at, i.source, i.status, i.chunks_added, i.error) for i in items]
        if not rows:
            return
        with closing(self._connect()) as db, db:
            db.executemany(
                "INSERT INTO imports (at, source, status, chunks_added, error)"
                " VALUES (?, ?, ?, ?, ?)",
                rows,
            )

    def recent(self, limit: int = RECENT) -> list[Imported]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT source, status, chunks_added, error, at FROM imports"
                " ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [Imported(*row) for row in rows]  # columns in the order of the fields


# --- which folder (chosen in the UI) -------------------------------------------------------


@dataclass(frozen=True)
class InboxConfig:
    folder: Path
    enabled: bool = True

    @classmethod
    def load(cls, path: Path, default_folder: Path) -> InboxConfig:
        """The saved choice, else `default_folder`; the folder as a full path, because people
        see it (a relative "data/inbox" says nothing about where it is)."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        folder = Path(data.get("folder") or default_folder).absolute()
        return cls(folder, bool(data.get("enabled", True)))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"folder": str(self.folder), "enabled": self.enabled}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class FolderError(ValueError):
    """A folder that cannot be the inbox; the message is for people (shown in the UI)."""


def check_folder(folder: str | Path, protected: Iterable[Path], exact: Iterable[Path] = ()) -> Path:
    """The folder as an absolute path, created if needed, or FolderError saying why not.

    The inbox may not be one of the app's own folders: not `protected` ones (library, index)
    or a folder inside them, and not exactly one of `exact` (the app's root and data folder,
    whose own files such as README.md would be imported). Only the files directly in the
    inbox are read, so a folder that merely contains the app (the Desktop) is fine."""
    text = str(folder).strip().strip('"')
    if not text:
        raise FolderError("Upiši ili izaberi folder.")
    path = Path(text).expanduser().resolve()
    inside_own = any(path == own or own in path.parents for own in _resolved(protected))
    if inside_own or path in _resolved(exact):
        raise FolderError("To je folder same aplikacije. Izaberi neki drugi folder.")
    if path.exists() and not path.is_dir():
        raise FolderError("To je fajl, a ne folder.")
    if not path.exists():
        if not path.parent.is_dir():
            raise FolderError("Taj folder ne postoji, a ne može ni da se napravi.")
        path.mkdir()
    if not os.access(path, os.R_OK | os.W_OK):
        raise FolderError("Aplikacija nema pravo da čita i menja taj folder.")
    return path


# --- the inbox ---------------------------------------------------------------------------


class Inbox:
    """Watches one folder and adds what arrives to a library (`scan` does one pass)."""

    def __init__(
        self,
        folder: Path,
        *,
        enabled: bool = True,
        settle_s: float = SETTLE_S,
        history: ImportLog | None = None,
    ) -> None:
        self.folder = folder
        self.enabled = enabled
        self.settle_s = settle_s
        self._history = history
        self._recent: list[Imported] = []  # without a history database (tests)
        self._lock = threading.Lock()  # one scan at a time: the watcher or "Proveri sada"

    @property
    def recent(self) -> list[Imported]:
        """The latest imports, newest first."""
        if self._history is not None:
            return self._history.recent()
        return self._recent[::-1][:RECENT]

    def waiting(self, now: float | None = None) -> list[Path]:
        """Supported files in the folder that are complete (unchanged for `settle_s`)."""
        return _ready(self.folder, self.settle_s, now)

    def scan(self, library: Library, now: float | None = None) -> list[Imported]:
        """Import every complete file once; returns what happened to each."""
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            results = [self._import(path, library) for path in self.waiting(now)]
        if self._history is not None:
            self._history.add(results)
        else:
            self._recent.extend(results)
        return results

    def failed(self) -> list[Failed]:
        """Files that could not be imported, newest first."""
        folder = self.folder / FAILED_DIR
        if not folder.is_dir():
            return []
        names = {p.name for p in folder.iterdir() if p.is_file()}
        items = []
        for name in names:
            if _is_note(name, names):
                continue
            path = folder / name
            note = _note(path)
            reason = note.read_text(encoding="utf-8").strip() if note.exists() else ""
            # The reason is written when the import fails; the file keeps its own date.
            when = (note if note.exists() else path).stat().st_mtime
            at = datetime.fromtimestamp(when, UTC).isoformat(timespec="seconds")
            items.append(Failed(name, reason, at))
        return sorted(items, key=lambda f: f.at, reverse=True)

    def retry(self, name: str) -> bool:
        """Put a failed file back, so the next scan tries it again. False if there is none."""
        path = self._failed_file(name)
        if path is None:
            return False
        target = _free_name(self.folder / name)
        shutil.move(path, target)
        old = time.time() - self.settle_s - 1  # counts as complete right away
        os.utime(target, (old, old))
        _note(path).unlink(missing_ok=True)
        return True

    def discard(self, name: str) -> bool:
        """Delete a failed file and its reason. False if there is none."""
        path = self._failed_file(name)
        if path is None:
            return False
        path.unlink()
        _note(path).unlink(missing_ok=True)
        return True

    def _failed_file(self, name: str) -> Path | None:
        folder = self.folder / FAILED_DIR
        if Path(name).name != name or not folder.is_dir():  # a bare file name only
            return None
        if _is_note(name, {p.name for p in folder.iterdir()}):
            return None
        path = folder / name
        return path if path.is_file() else None

    def _import(self, path: Path, library: Library) -> Imported:
        at = datetime.now(UTC).isoformat(timespec="seconds")
        try:
            added = library.add_file(path)
        except (DocumentLoadError, OSError) as exc:
            log.warning("Inbox: %s was not imported: %s", path.name, exc)
            reason = reason_for_people(str(exc))
            self._move(path, FAILED_DIR, note=reason)
            return Imported(path.name, "failed", error=reason, at=at)
        self._move(path, IMPORTED_DIR)  # the library has its own copy as well
        log.info("Inbox: %s %s (%d chunks)", path.name, added.status, added.chunks_added)
        return Imported(path.name, added.status, added.chunks_added, at=at)

    def _move(self, path: Path, subfolder: str, note: str | None = None) -> None:
        target_dir = self.folder / subfolder
        target_dir.mkdir(exist_ok=True)
        target = _free_name(target_dir / path.name)
        shutil.move(path, target)
        if note is not None:
            _note(target).write_text(note + "\n", encoding="utf-8")


def _ready(folder: Path, settle_s: float, now: float | None) -> list[Path]:
    if not folder.is_dir():
        return []
    now = time.time() if now is None else now
    files = (p for p in folder.iterdir() if p.is_file() and not p.name.startswith((".", "~$")))
    return sorted(
        (
            p
            for p in files
            if p.suffix.lower() in SUPPORTED_EXTENSIONS and now - p.stat().st_mtime >= settle_s
        ),
        key=lambda p: p.name.lower(),
    )


def _resolved(paths: Iterable[Path]) -> list[Path]:
    return [p.resolve() for p in paths]


def _note(path: Path) -> Path:
    """The reason file next to a failed file: "scan.pdf" → "scan.pdf.txt"."""
    return path.with_name(path.name + ".txt")


def _is_note(name: str, names: set[str]) -> bool:
    """True for "scan.pdf.txt" when "scan.pdf" is there too: a reason file, not a document."""
    return name.endswith(".txt") and name.removesuffix(".txt") in names


def _free_name(path: Path) -> Path:
    """`path`, or "name (2).pdf", "name (3).pdf" … when it is taken."""
    if not path.exists():
        return path
    for n in range(2, 10_000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise OSError(f"No free file name for {path.name}")


def waiting_in(folder: Path) -> int:
    """How many supported files lie in a folder right now (shown before choosing it)."""
    return len(_ready(folder, 0.0, None))


def watch(
    inbox: Inbox,
    library: Callable[[], Library | None],
    every_s: float,
    stop: threading.Event,
) -> threading.Thread:
    """Scan the inbox every `every_s` seconds in a background thread until `stop` is set,
    while it is enabled. `library()` returns None while the library is not available."""

    def run() -> None:
        while not stop.wait(every_s):
            if not inbox.enabled:
                continue
            try:
                current = library()
                if current is not None:
                    inbox.scan(current)
            except Exception:  # keep watching: one bad pass must not end the thread
                log.exception("Inbox scan failed")

    thread = threading.Thread(target=run, name="inbox", daemon=True)
    thread.start()
    return thread
