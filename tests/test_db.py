"""The app's SQLite file under many server threads at once (ADR-046)."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

from docassist.conversations import ConversationStore
from docassist.logs import QueryLog
from docassist.rag import Answer


def answer(question: str) -> Answer:
    return Answer(
        question=question,
        text="20 dana [pravilnik.pdf, str. 2]",
        citations=[],
        retrieved=[],
        provider="fake",
        model="fake",
        latency_s=0.1,
    )


def test_the_log_file_uses_wal(tmp_path):
    path = tmp_path / "logs.sqlite"
    QueryLog(path).stats()

    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_many_threads_store_answers_at_once(tmp_path):
    """Before WAL, 40 threads writing at once failed with "database is locked"."""
    path = tmp_path / "logs.sqlite"
    log, conversations = QueryLog(path), ConversationStore(path)

    def one_question(i: int) -> None:
        log.log(answer(f"Pitanje {i}"), channel="web")
        cid = conversations.create(f"session:{i % 5}", f"Pitanje {i}")
        conversations.add_turn(f"session:{i % 5}", cid, f"Pitanje {i}", "rag", {"answer": "x"})
        log.stats()

    with ThreadPoolExecutor(40) as pool:
        list(pool.map(one_question, range(200)))

    assert log.stats().queries == 200
    assert sum(len(conversations.summaries(f"session:{s}", limit=100)) for s in range(5)) == 200


def test_a_deleted_log_file_is_created_again(tmp_path):
    path = tmp_path / "logs.sqlite"
    log = QueryLog(path)
    log.log(answer("Prvo?"), channel="web")
    for leftover in tmp_path.glob("logs.sqlite*"):  # the file and its WAL files
        leftover.unlink()

    log.log(answer("Drugo?"), channel="web")

    assert [row["question"] for row in log.recent()] == ["Drugo?"]
