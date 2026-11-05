from pathlib import Path

import pytest

from docassist.config import Settings
from docassist.eval import (
    Question,
    QuestionResult,
    evaluate,
    fact_recall,
    load_questions,
    summarize,
    to_markdown,
)
from docassist.ingest import ingest
from docassist.llm import NOT_FOUND_SR, FakeProvider
from docassist.logs import QueryLog
from docassist.rag import answer
from docassist.retrieval import FakeEmbedder, VectorStore

SAMPLE_DOCS = Path(__file__).resolve().parent.parent / "data" / "sample_docs"


@pytest.fixture
def store(tmp_path):
    store = VectorStore(tmp_path / "index", FakeEmbedder())
    for path in sorted(SAMPLE_DOCS.iterdir()):
        store.add_document(ingest(path))
    return store


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("TOP_K", "5")
    return Settings.from_env(load_dotenv_file=False)


# --- QueryLog ----------------------------------------------------------------------------


def test_log_feedback_and_stats(tmp_path, store):
    log = QueryLog(tmp_path / "logs" / "q.sqlite")
    found = answer("Kada radi kantina?", store, FakeProvider())
    missing = answer("Bonus?", store, FakeProvider(NOT_FOUND_SR))

    first = log.log(found, channel="ui")
    second = log.log(missing, channel="api", mode="agent")

    assert log.set_feedback(first, 1)
    assert log.set_feedback(second, -1)
    assert not log.set_feedback(999, 1)
    with pytest.raises(ValueError):
        log.set_feedback(first, 5)

    stats = log.stats()
    assert stats.queries == 2
    assert stats.found_rate == 0.5
    assert (stats.thumbs_up, stats.thumbs_down) == (1, 1)
    recent = log.recent()
    assert recent[0]["channel"] == "api" and recent[0]["mode"] == "agent"
    assert recent[1]["citations"] == '["faq.txt p. 1"]'


def test_log_survives_file_deletion(tmp_path, store):
    path = tmp_path / "q.sqlite"
    log = QueryLog(path)
    log.log(answer("Kantina?", store, FakeProvider()), channel="ui")
    path.unlink()

    log.log(answer("Kantina?", store, FakeProvider()), channel="ui")

    assert log.stats().queries == 1


def test_empty_log_stats(tmp_path):
    stats = QueryLog(tmp_path / "q.sqlite").stats()

    assert stats.queries == 0
    assert stats.found_rate is None


# --- eval --------------------------------------------------------------------------------


def test_question_file_is_valid():
    questions = load_questions()

    assert 25 <= len(questions) <= 40
    assert len({q.id for q in questions}) == len(questions)
    assert {q.lang for q in questions} == {"sr", "de", "en"}
    assert sum(not q.answerable for q in questions) >= 3
    sources = {p.name for p in SAMPLE_DOCS.iterdir()}
    for q in questions:
        if q.answerable:
            assert q.source in sources, q.id
            assert q.page >= 1


def test_expected_facts_are_on_the_expected_page():
    """Guards the eval set against typos: every fact must appear on its page."""
    pages = {}
    for path in SAMPLE_DOCS.iterdir():
        for chunk in ingest(path):
            key = (chunk.source, chunk.page)
            pages[key] = pages.get(key, "") + " " + chunk.text

    for q in load_questions():
        if q.answerable and q.facts:
            assert fact_recall(pages[(q.source, q.page)], q.facts) == 1.0, q.id


def test_fact_recall():
    assert fact_recall("Moment je 20 Nm.", ["20 nm"]) == 1.0
    assert fact_recall("Radi od 10:30 do 13:00", ["10:30", "13:30"]) == 0.5
    assert fact_recall("zwei Tage", ["2|zwei"]) == 1.0
    assert fact_recall("anything", []) == 1.0


def test_evaluate_with_fake_provider(store, settings):
    questions = [
        Question("q1", "sr", "Kada radi kantina?", "faq.txt", 1, ["10:30", "13:30"]),
        Question("q2", "sr", "Koliki je bonus?", None, None),
    ]
    llm = FakeProvider(
        lambda system, messages: (
            NOT_FOUND_SR
            if "bonus" in messages[-1]["content"]
            else "Kantina radi od 10:30 do 13:30 [faq.txt, str. 1]."
        )
    )

    results = evaluate(questions, store, llm, settings=settings)
    summary = summarize(results, model="claude-sonnet-5-5")

    assert [r.correct for r in results] == [True, True]
    assert summary["correct"] == 1.0
    assert summary["not_found_accuracy"] == 1.0
    assert summary["false_not_found"] == 0.0
    assert summary["cost_usd"] is not None
    assert "| Correct overall | 100 % |" in to_markdown(summary, results, "test")


def test_retrieval_only_mode(store, settings):
    questions = [Question("q1", "sr", "Kada radi kantina?", "faq.txt", 1, [])]

    [result] = evaluate(questions, store, None, settings=settings)

    assert result.retrieved_hit
    assert result.answer == ""


def test_wrong_not_found_is_counted():
    results = [
        QuestionResult("a", "sr", answerable=True, found=False),
        QuestionResult("b", "sr", answerable=True, found=True, page_hit=True, fact_recall=1.0),
    ]

    assert summarize(results)["false_not_found"] == 0.5
