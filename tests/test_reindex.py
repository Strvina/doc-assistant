"""A stale index is rebuilt by the web app from the stored originals (ADR-032)."""

import shutil
import sqlite3
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import docassist.retrieval.store as store_module
from docassist import reindex
from docassist.api import create_app
from docassist.config import Settings
from docassist.library import Library
from docassist.llm import FakeProvider
from docassist.models import Chunk
from docassist.retrieval import FakeEmbedder, VectorStore

SAMPLE_DOCS = Path(__file__).resolve().parent.parent / "data" / "sample_docs"


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    return Settings.from_env(load_dotenv_file=False)


def stale_index(settings, monkeypatch, sources=("faq.txt",)):
    """An index made with an older chunking, holding one chunk per source."""
    monkeypatch.setattr(store_module, "CHUNKING", "old-chunking")
    store = VectorStore(settings.index_dir, FakeEmbedder())
    for source in sources:
        store.add_document([Chunk(f"{source}::p1::0", source, source, 1, "old text")])
    monkeypatch.undo()


def store_originals(settings, *names):
    settings.documents_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copyfile(SAMPLE_DOCS / name, settings.documents_dir / name)


def app_client(settings):
    app = create_app(settings=settings, llm_factory=lambda s, p: FakeProvider())
    return app, TestClient(app)


def wait_for_rebuild(app, timeout=60.0):
    deadline = time.monotonic() + timeout
    while not (app.state.rebuild and app.state.rebuild.finished):
        assert time.monotonic() < deadline, "rebuild did not finish"
        time.sleep(0.02)


def chunk_count(settings):
    db = sqlite3.connect(settings.index_dir / store_module.INDEX_FILE)
    try:
        return db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    finally:
        db.close()


def chroma_index(settings, *sources):
    """What an index of an earlier version (ChromaDB) looks like on disk: its SQLite file
    names the documents in `embedding_metadata`."""
    settings.index_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(settings.index_dir / store_module.CHROMA_FILE)
    db.execute("CREATE TABLE embedding_metadata (id INTEGER, key TEXT, string_value TEXT)")
    db.executemany(
        "INSERT INTO embedding_metadata VALUES (1, 'source', ?)", [(s,) for s in sources]
    )
    db.commit()
    db.close()


def test_an_index_of_an_earlier_version_is_rebuilt_from_the_originals(settings):
    chroma_index(settings, "faq.txt")
    store_originals(settings, "faq.txt")
    app, client = app_client(settings)

    assert client.get("/documents").status_code == 503  # the rebuild starts
    wait_for_rebuild(app)

    assert [d["source"] for d in client.get("/documents").json()] == ["faq.txt"]
    assert chunk_count(settings) > 0
    assert (settings.index_dir / store_module.CHROMA_FILE).exists()  # the old files stay


def test_an_index_of_an_earlier_version_is_kept_while_originals_are_missing(settings):
    chroma_index(settings, "faq.txt", "only_indexed.pdf")
    store_originals(settings, "faq.txt")
    _app, client = app_client(settings)

    response = client.get("/documents")

    assert response.status_code == 503
    assert "only_indexed.pdf" in response.json()["detail"]
    assert not (settings.index_dir / store_module.INDEX_FILE).exists()  # nothing replaced


def test_a_stale_index_is_rebuilt_from_the_originals(settings, monkeypatch):
    stale_index(settings, monkeypatch)
    store_originals(settings, "faq.txt", "pravilnik_o_radu.pdf")
    app, client = app_client(settings)

    first = client.get("/documents")
    assert first.status_code == 503
    assert "being rebuilt" in first.json()["detail"]

    wait_for_rebuild(app)
    documents = client.get("/documents")
    assert documents.status_code == 200
    assert [d["source"] for d in documents.json()] == ["faq.txt", "pravilnik_o_radu.pdf"]
    assert app.state.rebuild.done == app.state.rebuild.total == 2
    assert not reindex.is_unfinished(settings.index_dir)
    answer = client.post("/ask", json={"question": "Kada radi kantina?"}).json()
    assert answer["citations"][0]["source"] == "faq.txt"


def test_nothing_is_deleted_when_an_original_is_missing(settings, monkeypatch):
    stale_index(settings, monkeypatch, sources=("faq.txt", "stari_dokument.pdf"))
    store_originals(settings, "faq.txt")
    app, client = app_client(settings)

    response = client.get("/documents")

    assert response.status_code == 503
    assert "No stored original for: stari_dokument.pdf" in response.json()["detail"]
    assert app.state.rebuild is None
    assert chunk_count(settings) == 2  # the old index is left as it was


def test_an_interrupted_rebuild_is_started_again(settings):
    store_originals(settings, "faq.txt")
    library = Library(VectorStore(settings.index_dir, FakeEmbedder()), settings.documents_dir)
    library.add_file(settings.documents_dir / "faq.txt")
    reindex.mark_started(settings.index_dir)  # e.g. the server restarted in the middle
    app, client = app_client(settings)

    assert client.get("/documents").status_code == 503

    wait_for_rebuild(app)
    assert client.get("/documents").status_code == 200
    assert not reindex.is_unfinished(settings.index_dir)


def test_progress_and_questions_while_rebuilding(settings, monkeypatch):
    stale_index(settings, monkeypatch)
    store_originals(settings, "faq.txt")
    release = threading.Event()

    def slow_rebuild(settings, status):
        status.total, status.current = 2, "faq.txt"
        release.wait(10)
        status.done = 2
        return Library(VectorStore(settings.index_dir, FakeEmbedder(), rebuild=True), "x")

    monkeypatch.setattr(reindex, "rebuild", slow_rebuild)
    app, client = app_client(settings)
    client.get("/documents")

    progress = client.get("/documents/progress").json()
    asked = client.post("/ask", json={"question": "Kada radi kantina?"})

    assert progress == [{"source": "faq.txt", "done": 0, "total": 2, "kind": "reindex"}]
    assert asked.status_code == 503
    assert "document 1 of 2" in asked.json()["detail"]
    release.set()
    wait_for_rebuild(app)
    assert client.get("/documents/progress").json() == []


def test_documents_appear_one_by_one_while_rebuilding(settings, monkeypatch):
    """The list is not empty until the last (slowest) document is done (ADR-047)."""
    stale_index(settings, monkeypatch, sources=("faq.txt", "pravilnik_o_radu.pdf"))
    store_originals(settings, "faq.txt", "pravilnik_o_radu.pdf")
    release = threading.Event()
    add_file = Library.add_file

    def slow_after_the_first(self, path):
        if path.name != "faq.txt":
            release.wait(10)  # the second document is still being read
        return add_file(self, path)

    monkeypatch.setattr(Library, "add_file", slow_after_the_first)
    app, client = app_client(settings)
    assert client.get("/documents").status_code == 503  # the rebuild starts

    deadline = time.monotonic() + 10
    listed = []
    while not listed and time.monotonic() < deadline:
        response = client.get("/documents")
        listed = [d["source"] for d in response.json()] if response.status_code == 200 else []
        time.sleep(0.02)
    assert listed == ["faq.txt"]
    faq = client.get("/documents").json()[0]["doc_id"]
    assert client.get(f"/documents/{faq}/view").status_code == 200  # it can be opened
    assert client.post("/ask", json={"question": "Kantina?"}).status_code == 503  # not asked yet

    release.set()
    wait_for_rebuild(app)
    assert [d["source"] for d in client.get("/documents").json()] == [
        "faq.txt",
        "pravilnik_o_radu.pdf",
    ]


def test_a_failed_rebuild_is_reported(settings, monkeypatch):
    stale_index(settings, monkeypatch)
    store_originals(settings, "faq.txt")

    def broken(settings, status):
        raise OSError("disk full")

    monkeypatch.setattr(reindex, "rebuild", broken)
    app, client = app_client(settings)
    client.get("/documents")
    wait_for_rebuild(app)

    response = client.get("/documents")
    assert response.status_code == 503
    assert response.json()["detail"] == "Rebuilding the index failed: disk full"


def test_rebuild_skips_files_that_fail_and_other_file_types(settings):
    store_originals(settings, "faq.txt")
    (settings.documents_dir / "faq.txt.vision.json").write_text("{}")
    (settings.documents_dir / "prazno.txt").write_text("   ")
    status = reindex.Rebuild()

    library = reindex.rebuild(settings, status)

    assert [d.source for d in library.documents()] == ["faq.txt"]
    assert status.total == status.done == 2
    assert status.failed and status.failed[0].startswith("prazno.txt")


# --- citations saved before a rebuild ------------------------------------------------------


def test_old_chunk_ids_do_not_highlight_another_passage(settings, tmp_path):
    store = VectorStore(tmp_path / "idx", FakeEmbedder())
    app = create_app(
        settings=replace(settings, documents_dir=tmp_path / "docs"),
        store=store,
        llm_factory=lambda s, p: FakeProvider(),
    )
    client = TestClient(app)
    faq = (SAMPLE_DOCS / "faq.txt").read_bytes()
    client.post("/documents", files=[("files", ("faq.txt", faq))])
    doc = client.get("/documents").json()[0]["doc_id"]
    chunks = store.document_chunks("faq.txt")
    kantina = next(c for c in chunks if "Kantina radi" in c.text)
    other = next(c for c in chunks if "Kantina" not in c.text)

    def view(chunk_id, quote):
        params = {"page": 1, "chunk": [chunk_id], "q": quote}
        return client.get(f"/documents/{doc}/view", params=params)

    # The ids now name another chunk; the saved text says which passage it was.
    moved = view(other.chunk_id, kantina.text[:60])
    gone = view(other.chunk_id, "tekst kog više nema")
    fresh = view(kantina.chunk_id, kantina.text[:60])

    cited = 'class="cited"'
    assert cited in moved.text and "Kantina radi" in moved.text.split(cited, 1)[1][:300]
    assert cited not in gone.text
    assert cited in fresh.text
