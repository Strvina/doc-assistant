"""Connections to the app's SQLite file (query log, conversations, imports), ready for many
server threads at once (ADR-046).

- WAL journal: a write no longer blocks readers, and readers no longer block a write.
- `synchronous=NORMAL`: no disk flush on every commit (safe against an app crash; a power
  cut can lose only the last commits).
- A locked file is waited for up to `BUSY_TIMEOUT_S` instead of failing at once.
- The tables are created once per `Database` object, not on every connection; a deleted file
  is still simply created again.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

BUSY_TIMEOUT_S = 30.0


class Database:
    """One SQLite file and the tables a store keeps in it (`create` makes them)."""

    def __init__(self, path: Path, create: Callable[[sqlite3.Connection], object]) -> None:
        self.path = path
        self._create = create
        self._ready = False
        self._lock = threading.Lock()

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not self.path.exists()
        db = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_S)
        db.execute("PRAGMA synchronous = NORMAL")
        if fresh or not self._ready:
            with self._lock:
                db.execute("PRAGMA journal_mode = WAL")  # kept in the file from now on
                self._create(db)
                db.commit()
                self._ready = True
        return db
