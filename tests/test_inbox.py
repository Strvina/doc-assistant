"""Documents dropped into the inbox folder are imported (ADR-036)."""

import os
import shutil
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from docassist.api import create_app
from docassist.config import Settings
from docassist.inbox import FAILED_DIR, Inbox, watch
from docassist.library import Library
from docassist.llm import FakeProvider
from docassist.retrieval import FakeEmbedder, VectorStore

SAMPLE_DOCS = Path(__file__).resolve().parent.parent / "data" / "sample_docs"
LATER = time.time() + 3600  # "now" for a scan, well after every file settled


@pytest.fixture
def library(tmp_path):
    return Library(VectorStore(tmp_path / "index", FakeEmbedder()), tmp_path / "documents")


@pytest.fixture
def inbox(tmp_path):
    folder = tmp_path / "inbox"
    folder.mkdir()
    return Inbox(folder)


def drop(inbox, name, data=None):
    path = inbox.folder / name
    if data is None:
        shutil.copyfile(SAMPLE_DOCS / name, path)
    else:
        path.write_bytes(data)
    return path


def test_a_dropped_file_is_indexed_and_leaves_the_inbox(inbox, library):
    path = drop(inbox, "faq.txt")

    [result] = inbox.scan(library, now=LATER)

    assert (result.source, result.status) == ("faq.txt", "indexed") and result.chunks_added > 0
    assert [d.source for d in library.documents()] == ["faq.txt"]
    assert (library.files_dir / "faq.txt").is_file()  # the library keeps the original
    assert not path.exists()
    assert list(inbox.recent) == [result]


def test_a_file_still_being_written_waits(inbox, library):
    drop(inbox, "faq.txt")

    assert inbox.scan(library, now=time.time()) == []  # changed just now
    assert library.documents() == []


def test_other_files_are_left_alone(inbox, library):
    drop(inbox, "notes.zip", b"PK")
    drop(inbox, ".hidden.txt", b"temporary")
    (inbox.folder / "sub").mkdir()

    assert inbox.scan(library, now=LATER) == []
    assert {p.name for p in inbox.folder.iterdir()} == {"notes.zip", ".hidden.txt", "sub"}


def test_a_file_that_cannot_be_read_is_set_aside_with_the_reason(inbox, library):
    drop(inbox, "prazno.txt", b"   ")
    drop(inbox, "faq.txt")

    results = inbox.scan(library, now=LATER)

    assert [(r.source, r.status) for r in results] == [
        ("faq.txt", "indexed"),
        ("prazno.txt", "failed"),
    ]
    failed = inbox.folder / FAILED_DIR
    assert (failed / "prazno.txt").is_file()
    assert "no text found" in (failed / "prazno.txt.txt").read_text(encoding="utf-8")
    assert inbox.scan(library, now=LATER) == []  # not tried again


def test_a_second_failure_with_the_same_name_keeps_both(inbox, library):
    for _ in range(2):
        drop(inbox, "prazno.txt", b" ")
        inbox.scan(library, now=LATER)

    names = {p.name for p in (inbox.folder / FAILED_DIR).iterdir()}
    assert len([n for n in names if n.endswith(".txt") and not n.endswith(".txt.txt")]) == 2


def test_the_same_file_again_is_unchanged(inbox, library):
    drop(inbox, "faq.txt")
    inbox.scan(library, now=LATER)
    drop(inbox, "faq.txt")

    [result] = inbox.scan(library, now=LATER)

    assert result.status == "unchanged"
    assert not (inbox.folder / "faq.txt").exists()


def test_the_watcher_scans_until_stopped(inbox, library):
    path = drop(inbox, "faq.txt")
    old = time.time() - 60
    os.utime(path, (old, old))
    stop = threading.Event()

    thread = watch(inbox, lambda: library, every_s=0.05, stop=stop)
    deadline = time.monotonic() + 10
    while path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    stop.set()
    thread.join(timeout=5)

    assert not path.exists() and [d.source for d in library.documents()] == ["faq.txt"]
    assert not thread.is_alive()


def test_the_watcher_waits_while_the_library_is_unavailable(inbox):
    drop(inbox, "faq.txt")
    stop = threading.Event()
    calls = []

    def unavailable():
        calls.append(1)
        return

    thread = watch(inbox, unavailable, every_s=0.02, stop=stop)
    while len(calls) < 3:
        time.sleep(0.01)
    stop.set()
    thread.join(timeout=5)

    assert (inbox.folder / "faq.txt").exists()


# --- the app -----------------------------------------------------------------------------


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    return replace(Settings.from_env(load_dotenv_file=False), inbox_poll_s=1)


def app_for(settings, tmp_path):
    store = VectorStore(tmp_path / "idx", FakeEmbedder())
    return create_app(settings=settings, store=store, llm_factory=lambda s, p: FakeProvider())


def test_the_running_app_imports_from_the_inbox(settings, tmp_path):
    app = app_for(settings, tmp_path)

    with TestClient(app) as client:  # `with`: the app starts up and watches the folder
        assert client.get("/api/config").json()["inbox"] == str(settings.inbox_dir)
        app.state.inbox.settle_s = 0.0  # the copy below is complete at once
        shutil.copyfile(SAMPLE_DOCS / "faq.txt", settings.inbox_dir / "faq.txt")
        deadline = time.monotonic() + 15
        while not client.get("/documents").json() and time.monotonic() < deadline:
            time.sleep(0.1)

        assert [d["source"] for d in client.get("/documents").json()] == ["faq.txt"]
        status = client.get("/documents/inbox").json()
        assert status["enabled"] and status["recent"][0]["status"] == "indexed"


@pytest.mark.parametrize("change", [{"inbox_poll_s": 0}, {"allow_uploads": False}])
def test_no_inbox_when_turned_off_or_in_the_public_demo(settings, tmp_path, change):
    app = app_for(replace(settings, **change), tmp_path)

    with TestClient(app) as client:
        assert client.get("/api/config").json()["inbox"] is None
        assert client.get("/documents/inbox").json()["enabled"] is False
