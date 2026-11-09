"""Query log in SQLite: every answer, its sources, latency, tokens, cost and user feedback."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from docassist.db import Database
from docassist.pricing import cost_usd
from docassist.rag import Answer

_SCHEMA = """
CREATE TABLE IF NOT EXISTS queries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT    NOT NULL,
    channel       TEXT    NOT NULL,          -- web | ui | api | cli
    mode          TEXT    NOT NULL,          -- rag | agent
    provider      TEXT    NOT NULL,
    model         TEXT    NOT NULL,
    question      TEXT    NOT NULL,
    answer        TEXT    NOT NULL,
    found         INTEGER NOT NULL,
    citations     TEXT    NOT NULL,          -- JSON list of "source p. N"
    tool_calls    TEXT    NOT NULL,          -- JSON list of tool names
    latency_ms    INTEGER NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    feedback      INTEGER,                   -- 1 = 👍, -1 = 👎, NULL = none
    cost_usd      REAL,                      -- NULL when the model price is unknown
    escalation    TEXT,                      -- reference when forwarded to a person
    escalation_team TEXT,                    -- id of the team it was forwarded to
    search_query  TEXT                       -- a follow-up rewritten to stand alone (ADR-031)
);
"""

# Columns added after the first version; older log files get them on first use.
_MIGRATIONS = {
    "cost_usd": "ALTER TABLE queries ADD COLUMN cost_usd REAL",
    "escalation": "ALTER TABLE queries ADD COLUMN escalation TEXT",
    "escalation_team": "ALTER TABLE queries ADD COLUMN escalation_team TEXT",
    "search_query": "ALTER TABLE queries ADD COLUMN search_query TEXT",
}


def _create(db: sqlite3.Connection) -> None:
    db.executescript(_SCHEMA)
    columns = {row[1] for row in db.execute("PRAGMA table_info(queries)")}
    for column, statement in _MIGRATIONS.items():
        if column not in columns:
            db.execute(statement)


_NO_LIMIT = 2**63 - 1  # larger than any SQLite row id: the first page


@dataclass(frozen=True)
class UsageStats:
    queries: int
    found_rate: float | None
    avg_latency_s: float | None
    input_tokens: int
    output_tokens: int
    thumbs_up: int
    thumbs_down: int
    cost_usd: float = 0.0


class QueryLog:
    def __init__(self, path: str | Path) -> None:
        # The table is created on demand, so a deleted log file is simply recreated.
        self._db = Database(Path(path), _create)

    def _connect(self) -> sqlite3.Connection:
        return self._db.connect()

    def log(self, result: Answer, *, channel: str, mode: str = "rag") -> int:
        row = (
            datetime.now(UTC).isoformat(timespec="seconds"),
            channel,
            mode,
            result.provider,
            result.model,
            result.question,
            result.text,
            int(result.found),
            json.dumps([f"{c.source} p. {c.page}" for c in result.citations], ensure_ascii=False),
            json.dumps([call.name for call in result.tool_calls]),
            round(result.latency_s * 1000),
            result.input_tokens,
            result.output_tokens,
            cost_usd(result.model, result.input_tokens, result.output_tokens),
            result.search_query or None,
        )
        with closing(self._connect()) as db, db:
            cursor = db.execute(
                "INSERT INTO queries (created_at, channel, mode, provider, model, question,"
                " answer, found, citations, tool_calls, latency_ms, input_tokens, output_tokens,"
                " cost_usd, search_query) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )
            row_id = cursor.lastrowid
            assert row_id is not None  # set by every INSERT
            return row_id

    def set_feedback(self, query_id: int, rating: int | None) -> bool:
        """Store 👍 (1), 👎 (-1) or clear it (None). Returns False for an unknown id."""
        if rating not in (1, -1, None):
            raise ValueError("rating must be 1, -1 or None")
        with closing(self._connect()) as db, db:
            cursor = db.execute("UPDATE queries SET feedback = ? WHERE id = ?", (rating, query_id))
            return cursor.rowcount == 1

    def get(self, query_id: int) -> dict[str, object] | None:
        rows = self._select("SELECT * FROM queries WHERE id = ?", query_id)
        return rows[0] if rows else None

    def set_escalation(self, query_id: int, reference: str, team: str | None = None) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "UPDATE queries SET escalation = ?, escalation_team = ? WHERE id = ?",
                (reference, team, query_id),
            )

    def delete(self, query_id: int) -> bool:
        """Remove one question from the log. False for an unknown id."""
        with closing(self._connect()) as db, db:
            return db.execute("DELETE FROM queries WHERE id = ?", (query_id,)).rowcount == 1

    def clear(self) -> int:
        """Remove every question from the log; returns how many were removed."""
        with closing(self._connect()) as db, db:
            return db.execute("DELETE FROM queries").rowcount

    def stats(self) -> UsageStats:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT COUNT(*), AVG(found), AVG(latency_ms), COALESCE(SUM(input_tokens), 0),"
                " COALESCE(SUM(output_tokens), 0), COALESCE(SUM(feedback = 1), 0),"
                " COALESCE(SUM(feedback = -1), 0), COALESCE(SUM(cost_usd), 0) FROM queries"
            ).fetchone()
        count, found, latency, tokens_in, tokens_out, up, down, cost = row
        return UsageStats(
            queries=count,
            found_rate=round(found, 3) if found is not None else None,
            avg_latency_s=round(latency / 1000, 2) if latency is not None else None,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            thumbs_up=up,
            thumbs_down=down,
            cost_usd=round(cost, 4),
        )

    def recent(self, limit: int = 20, before: int | None = None) -> list[dict[str, object]]:
        """The latest questions, newest first; with `before`, those older than that id (the
        next page when scrolling)."""
        return self._page("1 = 1", limit, before)

    def gaps(self, limit: int = 20, before: int | None = None) -> list[dict[str, object]]:
        """Questions the documents did not answer, or answers users rated 👎, newest first
        (pages as in `recent`).

        This is the to-do list for whoever maintains the documents.
        """
        return self._page("(found = 0 OR feedback = -1)", limit, before)

    def _page(self, where: str, limit: int, before: int | None) -> list[dict[str, object]]:
        # `where` is one of the fixed conditions above, never user input.
        return self._select(
            f"SELECT * FROM queries WHERE {where} AND id < ? ORDER BY id DESC LIMIT ?",
            _NO_LIMIT if before is None else before,
            limit,
        )

    def gaps_since(self, since: str) -> list[dict[str, object]]:
        """Every gap (see `gaps`) logged at or after the ISO timestamp `since`, oldest first:
        the input of the weekly report (docassist.reports)."""
        return self._select(
            "SELECT * FROM queries WHERE (found = 0 OR feedback = -1) AND created_at >= ?"
            " ORDER BY id",
            since,
        )

    def _select(self, sql: str, *params: object) -> list[dict[str, object]]:
        with closing(self._connect()) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(sql, params)]
