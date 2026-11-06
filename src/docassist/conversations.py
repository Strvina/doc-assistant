"""Saved conversations, per browser session, in the same SQLite file as the query log (ADR-025).

A conversation is an ordered list of turns; a turn keeps the question and the full answer
as the API returned it (citations, tool calls…), so an old conversation is shown exactly as
it was. Every conversation belongs to an owner — the browser session, or "api" for
integrations — and is only listed, opened or deleted by that owner: on a public demo,
visitors never see each other's conversations.

Browser sessions are stored here too, so a session (and its conversations) survives a server
restart.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import unicodedata
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,
    title       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS conversations_owner ON conversations (owner, updated_at);
CREATE TABLE IF NOT EXISTS turns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    question        TEXT NOT NULL,
    mode            TEXT NOT NULL,          -- rag | agent
    response        TEXT NOT NULL,          -- the /ask response as JSON
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS turns_conversation ON turns (conversation_id, id);
"""

TITLE_LENGTH = 70


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def fold(text: str) -> str:
    """Lower case without diacritics, for search: "Česta Pitanja" -> "cesta pitanja"."""
    plain = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in plain if not unicodedata.combining(ch)).replace("đ", "dj")


def make_title(question: str) -> str:
    """The first question, on one line and shortened, names the conversation."""
    title = " ".join(question.split())
    return title if len(title) <= TITLE_LENGTH else title[: TITLE_LENGTH - 1].rstrip() + "…"


class ConversationStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        # Created on demand, like the query log: a deleted file is simply recreated.
        self._path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self._path)
        db.execute("PRAGMA foreign_keys = ON")
        db.executescript(_SCHEMA)
        return db

    # --- sessions ------------------------------------------------------------------------

    def add_session(self, session_id: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO sessions (id, created_at) VALUES (?, ?)",
                (session_id, _now()),
            )

    def has_session(self, session_id: str) -> bool:
        with closing(self._connect()) as db:
            row = db.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return row is not None

    # --- conversations -------------------------------------------------------------------

    def create(self, owner: str, first_question: str) -> str:
        conversation_id = secrets.token_hex(8)
        now = _now()
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO conversations (id, owner, title, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (conversation_id, owner, make_title(first_question), now, now),
            )
        return conversation_id

    def exists(self, owner: str, conversation_id: str) -> bool:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT 1 FROM conversations WHERE id = ? AND owner = ?", (conversation_id, owner)
            ).fetchone()
        return row is not None

    def add_turn(
        self, owner: str, conversation_id: str, question: str, mode: str, response: dict[str, Any]
    ) -> bool:
        """Append a turn. False if the conversation does not exist for this owner."""
        now = _now()
        with closing(self._connect()) as db, db:
            updated = db.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ? AND owner = ?",
                (now, conversation_id, owner),
            ).rowcount
            if not updated:
                return False
            db.execute(
                "INSERT INTO turns (conversation_id, question, mode, response, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (conversation_id, question, mode, json.dumps(response, ensure_ascii=False), now),
            )
        return True

    def summaries(
        self, owner: str, limit: int = 100, query: str = "", offset: int = 0
    ) -> list[dict[str, Any]]:
        """The owner's conversations, most recently used first; `offset` skips that many
        (the next page when scrolling the list).

        `query` keeps the conversations whose title, questions or answers contain every word
        of it, ignoring case and diacritics ("cesta" finds "česta"). Done in Python: SQLite's
        LIKE only folds ASCII letters, and one person's conversations are few.
        """
        words = fold(query).split()
        with closing(self._connect()) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT c.id, c.title, c.created_at, c.updated_at, COUNT(t.id) AS turns,"
                " GROUP_CONCAT(t.question || ' ' || json_extract(t.response, '$.answer'), ' ')"
                " AS content"
                " FROM conversations c LEFT JOIN turns t ON t.conversation_id = c.id"
                " WHERE c.owner = ? GROUP BY c.id ORDER BY c.updated_at DESC, c.rowid DESC",
                (owner,),
            ).fetchall()
        found = []
        skipped = 0
        for row in rows:
            text = fold(f"{row['title']} {row['content'] or ''}")
            if not all(word in text for word in words):
                continue
            if skipped < offset:
                skipped += 1
                continue
            found.append({k: row[k] for k in ("id", "title", "created_at", "updated_at", "turns")})
            if len(found) == limit:
                break
        return found

    def rename(self, owner: str, conversation_id: str, title: str) -> bool:
        """Give a conversation a new title. False if it is not the owner's."""
        with closing(self._connect()) as db, db:
            renamed = db.execute(
                "UPDATE conversations SET title = ? WHERE id = ? AND owner = ?",
                (make_title(title), conversation_id, owner),
            ).rowcount
        return renamed == 1

    def get(self, owner: str, conversation_id: str) -> dict[str, Any] | None:
        """A conversation with all its turns, or None (unknown, or someone else's)."""
        with closing(self._connect()) as db:
            db.row_factory = sqlite3.Row
            conversation = db.execute(
                "SELECT id, title, created_at, updated_at FROM conversations"
                " WHERE id = ? AND owner = ?",
                (conversation_id, owner),
            ).fetchone()
            if conversation is None:
                return None
            turns = db.execute(
                "SELECT question, mode, response, created_at FROM turns"
                " WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        return {
            **dict(conversation),
            "turns": [
                {
                    "question": t["question"],
                    "mode": t["mode"],
                    "response": json.loads(t["response"]),
                    "created_at": t["created_at"],
                }
                for t in turns
            ],
        }

    def history(self, owner: str, conversation_id: str, last: int = 3) -> list[tuple[str, str]]:
        """The last (question, answer) pairs, for follow-up questions."""
        conversation = self.get(owner, conversation_id)
        if conversation is None:
            return []
        return [(t["question"], t["response"].get("answer", "")) for t in conversation["turns"]][
            -last:
        ]

    def delete(self, owner: str, conversation_id: str) -> bool:
        with closing(self._connect()) as db, db:
            deleted = db.execute(
                "DELETE FROM conversations WHERE id = ? AND owner = ?", (conversation_id, owner)
            ).rowcount
        return deleted == 1
