"""Query log in SQLite: every answer, its sources, latency, tokens and user feedback."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from docassist.rag import Answer

_SCHEMA = """
CREATE TABLE IF NOT EXISTS queries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT    NOT NULL,
    channel       TEXT    NOT NULL,          -- ui | api | cli
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
    feedback      INTEGER                    -- 1 = 👍, -1 = 👎, NULL = none
);
"""


@dataclass(frozen=True)
class UsageStats:
    queries: int
    found_rate: float | None
    avg_latency_s: float | None
    input_tokens: int
    output_tokens: int
    thumbs_up: int
    thumbs_down: int


class QueryLog:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        # Create the file and table on demand, so a deleted log file is simply recreated.
        self._path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self._path)
        db.executescript(_SCHEMA)
        return db

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
        )
        with closing(self._connect()) as db, db:
            cursor = db.execute(
                "INSERT INTO queries (created_at, channel, mode, provider, model, question,"
                " answer, found, citations, tool_calls, latency_ms, input_tokens, output_tokens)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )
            return int(cursor.lastrowid)

    def set_feedback(self, query_id: int, rating: int | None) -> bool:
        """Store 👍 (1), 👎 (-1) or clear it (None). Returns False for an unknown id."""
        if rating not in (1, -1, None):
            raise ValueError("rating must be 1, -1 or None")
        with closing(self._connect()) as db, db:
            cursor = db.execute("UPDATE queries SET feedback = ? WHERE id = ?", (rating, query_id))
            return cursor.rowcount == 1

    def stats(self) -> UsageStats:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT COUNT(*), AVG(found), AVG(latency_ms), COALESCE(SUM(input_tokens), 0),"
                " COALESCE(SUM(output_tokens), 0), COALESCE(SUM(feedback = 1), 0),"
                " COALESCE(SUM(feedback = -1), 0) FROM queries"
            ).fetchone()
        count, found, latency, tokens_in, tokens_out, up, down = row
        return UsageStats(
            queries=count,
            found_rate=round(found, 3) if found is not None else None,
            avg_latency_s=round(latency / 1000, 2) if latency is not None else None,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            thumbs_up=up,
            thumbs_down=down,
        )

    def recent(self, limit: int = 20) -> list[dict[str, object]]:
        with closing(self._connect()) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM queries ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(row) for row in rows]
