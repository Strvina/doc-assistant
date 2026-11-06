"""Documents dropped into the inbox folder are imported (ADR-036, ADR-037)."""

import os
import shutil
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from docassist import desktop
from docassist.api import create_app
from docassist.config import Settings
from docassist.inbox import (
    FAILED_DIR,
    IMPORTED_DIR,
    FolderError,
    ImportLog,
    Inbox,
    InboxConfig,
    check_folder,
    reason_for_people,
    waiting_in,
    watch,
)
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


def drop(folder, name, data=None):
    path = folder / name
    if data is None:
        shutil.copyfile(SAMPLE_DOCS / name, path)
    else:
        path.write_bytes(data)
    return path


def settle(path):
    old = time.time() - 60
    os.utime(path, (old, old))


# --- importing ---------------------------------------------------------------------------


def test_a_dropped_file_is_indexed_and_moves_to_imported(inbox, library):
    path = drop(inbox.folder, "faq.txt")

    [result] = inbox.scan(library, now=LATER)

    assert (result.source, result.status) == ("faq.txt", "indexed") and result.chunks_added > 0
    assert [d.source for d in library.documents()] == ["faq.txt"]
    assert not path.exists() and (inbox.folder / IMPORTED_DIR / "faq.txt").is_file()
    assert inbox.recent == [result]


def test_a_file_still_being_written_waits(inbox, library):
    drop(inbox.folder, "faq.txt")

    assert inbox.scan(library, now=time.time()) == []  # changed just now
    assert library.documents() == []


def test_other_files_are_left_alone(inbox, library):
    drop(inbox.folder, "notes.zip", b"PK")
    drop(inbox.folder, ".hidden.txt", b"temporary")
    drop(inbox.folder, "~$Pravilnik.docx", b"Word lock file")
    (inbox.folder / "sub").mkdir()

    assert inbox.scan(library, now=LATER) == []
    names = {p.name for p in inbox.folder.iterdir()}
    assert names == {"notes.zip", ".hidden.txt", "~$Pravilnik.docx", "sub"}


def test_the_same_name_twice_keeps_both_copies(inbox, library):
    for _ in range(2):
        drop(inbox.folder, "faq.txt")
        inbox.scan(library, now=LATER)

    imported = {p.name for p in (inbox.folder / IMPORTED_DIR).iterdir()}
    assert imported == {"faq.txt", "faq (2).txt"}
    assert [r.status for r in inbox.recent] == ["unchanged", "indexed"]


# --- failures ----------------------------------------------------------------------------


def test_a_file_that_cannot_be_read_is_set_aside_with_a_reason_for_people(inbox, library):
    drop(inbox.folder, "prazno.txt", b"   ")
    drop(inbox.folder, "faq.txt")

    results = inbox.scan(library, now=LATER)

    assert [(r.source, r.status) for r in results] == [
        ("faq.txt", "indexed"),
        ("prazno.txt", "failed"),
    ]
    [failed] = inbox.failed()
    assert failed.name == "prazno.txt"  # a .txt document, next to its reason "prazno.txt.txt"
    assert failed.reason.startswith("U dokumentu nema teksta")
    assert results[1].error == failed.reason
    assert inbox.scan(library, now=LATER) == []  # not tried again by itself


def test_retry_puts_the_file_back_and_discard_deletes_it(inbox, library):
    drop(inbox.folder, "a.txt", b" ")
    drop(inbox.folder, "b.txt", b" ")
    inbox.scan(library, now=LATER)

    assert inbox.retry("a.txt") and (inbox.folder / "a.txt").is_file()
    assert [p.name for p in inbox.waiting()] == ["a.txt"]  # complete at once
    assert inbox.discard("b.txt")
    assert inbox.failed() == []
    assert not any((inbox.folder / FAILED_DIR).iterdir())


@pytest.mark.parametrize("name", ["nema.pdf", "../faq.txt", "a.txt.txt"])
def test_retry_and_discard_only_take_a_failed_file_name(inbox, library, name):
    drop(inbox.folder, "a.txt", b" ")
    inbox.scan(library, now=LATER)

    assert not inbox.retry(name) and not inbox.discard(name)


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        ("scan.pdf is password-protected", "Dokument je zaštićen lozinkom."),
        ("Unsupported file type '.zip' for a.zip", "Ova vrsta fajla nije podržana."),
        ("something odd", "Fajl nije mogao da se pročita (something odd)."),
    ],
)
def test_reasons_for_people(error, reason):
    assert reason_for_people(error) == reason


# --- the folder --------------------------------------------------------------------------


def test_check_folder_creates_a_missing_folder(tmp_path):
    folder = check_folder(f'  "{tmp_path / "Skenirano"}" ', protected=[])

    assert folder == (tmp_path / "Skenirano").resolve() and folder.is_dir()


def test_check_folder_refuses_the_apps_own_folders(tmp_path):
    library = tmp_path / "data" / "documents"
    library.mkdir(parents=True)

    for folder in (library, library / "sub", tmp_path / "data"):
        with pytest.raises(FolderError, match="folder same aplikacije"):
            check_folder(folder, protected=[library], exact=[tmp_path / "data"])
    assert check_folder(tmp_path, protected=[library], exact=[tmp_path / "data"]) == tmp_path


@pytest.mark.parametrize("make", ["file", "no-parent", "empty"])
def test_check_folder_explains_what_is_wrong(tmp_path, make):
    target = {"file": tmp_path / "a.pdf", "no-parent": tmp_path / "x" / "y", "empty": "  "}[make]
    if make == "file":
        target.write_bytes(b"%PDF")

    with pytest.raises(FolderError):
        check_folder(target, protected=[])


def test_waiting_in_counts_documents_already_there(tmp_path):
    drop(tmp_path, "faq.txt")
    drop(tmp_path, "notes.zip", b"PK")

    assert waiting_in(tmp_path) == 1


def test_the_chosen_folder_is_kept(tmp_path):
    path = tmp_path / "inbox.json"

    assert InboxConfig.load(path, tmp_path / "default") == InboxConfig(tmp_path / "default")
    InboxConfig(tmp_path / "Skenirano", enabled=False).save(path)
    assert InboxConfig.load(path, tmp_path / "default") == InboxConfig(
        tmp_path / "Skenirano", enabled=False
    )


def test_import_history_survives_a_restart(tmp_path, library):
    folder = tmp_path / "inbox"
    folder.mkdir()
    drop(folder, "faq.txt")
    Inbox(folder, history=ImportLog(tmp_path / "logs.sqlite")).scan(library, now=LATER)

    [item] = Inbox(folder, history=ImportLog(tmp_path / "logs.sqlite")).recent

    assert (item.source, item.status) == ("faq.txt", "indexed")


# --- the watcher -------------------------------------------------------------------------


def test_the_watcher_imports_while_enabled(inbox, library):
    path = drop(inbox.folder, "faq.txt")
    settle(path)
    stop = threading.Event()
    inbox.enabled = False
    thread = watch(inbox, lambda: library, every_s=0.02, stop=stop)

    time.sleep(0.2)
    assert path.exists()  # turned off: nothing happens
    inbox.enabled = True
    deadline = time.monotonic() + 10
    while path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    stop.set()
    thread.join(timeout=5)

    assert not path.exists() and [d.source for d in library.documents()] == ["faq.txt"]
    assert not thread.is_alive()


def test_the_watcher_waits_while_the_library_is_unavailable(inbox):
    drop(inbox.folder, "faq.txt")
    stop = threading.Event()
    calls = []

    def unavailable():
        calls.append(1)

    thread = watch(inbox, unavailable, every_s=0.02, stop=stop)
    while len(calls) < 3:
        time.sleep(0.01)
    stop.set()
    thread.join(timeout=5)

    assert (inbox.folder / "faq.txt").exists()


# --- the API (the "Automatski uvoz" panel) -----------------------------------------------


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    return replace(Settings.from_env(load_dotenv_file=False), inbox_poll_s=3600)


@pytest.fixture
def client(settings, tmp_path):
    store = VectorStore(tmp_path / "idx", FakeEmbedder())
    app = create_app(settings=settings, store=store, llm_factory=lambda s, p: FakeProvider())
    with TestClient(app) as client:  # `with`: the app starts up and watches the folder
        client.app.state.inbox.settle_s = 0.0  # files copied by the tests are complete
        yield client


REMOTE = {"X-Forwarded-For": "10.0.0.7"}  # a browser on another computer


def test_status_of_the_panel(client, settings):
    drop(settings.inbox_dir, "faq.txt")

    status = client.get("/inbox").json()

    assert status["watched"] and status["enabled"] and status["available"]
    assert status["folder"] == str(settings.inbox_dir) and status["waiting"] == 1
    assert status["local"] and status["recent"] == [] and status["failed"] == []
    assert client.get("/inbox", headers=REMOTE).json()["local"] is False


def test_check_now_imports_and_reports(client, settings):
    drop(settings.inbox_dir, "faq.txt")
    drop(settings.inbox_dir, "prazno.txt", b" ")

    result = client.post("/inbox/scan").json()

    assert [i["status"] for i in result["imported"]] == ["indexed", "failed"]
    assert [f["name"] for f in result["failed"]] == ["prazno.txt"]
    assert [r["source"] for r in result["recent"]] == ["prazno.txt", "faq.txt"]
    assert [d["source"] for d in client.get("/documents").json()] == ["faq.txt"]


def test_retry_and_remove_a_failed_file(client, settings):
    drop(settings.inbox_dir, "a.txt", b" ")
    drop(settings.inbox_dir, "b.txt", b" ")
    client.post("/inbox/scan")

    assert client.post("/inbox/failed/a.txt/retry").json() == {"ok": True}
    assert client.delete("/inbox/failed/b.txt").json() == {"ok": True}
    assert client.delete("/inbox/failed/b.txt").status_code == 404
    assert (settings.inbox_dir / "a.txt").is_file()


def test_choose_another_folder_and_turn_the_import_off(client, settings, tmp_path):
    other = tmp_path / "Skenirano"
    other.mkdir()
    drop(other, "faq.txt")

    checked = client.post("/inbox/check", json={"folder": str(other)}).json()
    changed = client.put("/inbox", json={"folder": str(other), "enabled": False}).json()

    assert checked == {"folder": str(other.resolve()), "waiting": 1}
    assert changed["folder"] == str(other.resolve()) and changed["enabled"] is False
    saved = InboxConfig.load(settings.log_db.parent / "inbox.json", settings.inbox_dir)
    assert saved == InboxConfig(other.resolve(), enabled=False)


def test_the_apps_own_folder_is_refused_with_a_reason(client, settings):
    response = client.post("/inbox/check", json={"folder": str(settings.documents_dir)})

    assert response.status_code == 422
    assert "folder same aplikacije" in response.json()["detail"]


def test_only_this_computer_changes_the_folder(client, tmp_path):
    for method, path, body in [
        ("put", "/inbox", {"enabled": False}),
        ("post", "/inbox/check", {"folder": str(tmp_path)}),
        ("post", "/inbox/pick", None),
        ("post", "/inbox/open", None),
    ]:
        response = getattr(client, method)(path, json=body, headers=REMOTE)
        assert response.status_code == 403, path
    assert client.post("/inbox/scan", headers=REMOTE).status_code == 200  # anyone may check


def test_pick_and_open_use_the_desktop(client, monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(desktop, "available", lambda: True)
    monkeypatch.setattr(desktop, "pick_folder", lambda start: tmp_path / "Izabrano")
    monkeypatch.setattr(desktop, "open_folder", opened.append)

    picked = client.post("/inbox/pick").json()
    client.post("/inbox/open")

    assert picked == {"folder": str(tmp_path / "Izabrano")}
    assert opened == [client.app.state.inbox.folder]


def test_no_desktop_no_dialog(client, monkeypatch):
    monkeypatch.setattr(desktop, "available", lambda: False)

    assert client.post("/inbox/pick").status_code == 409
    assert client.get("/inbox").json()["desktop"] is False


@pytest.mark.parametrize("change", [{"inbox_poll_s": 0}, {"allow_uploads": False}])
def test_no_inbox_when_turned_off_or_in_the_public_demo(settings, tmp_path, change):
    store = VectorStore(tmp_path / "i", FakeEmbedder())
    app = create_app(settings=replace(settings, **change), store=store)

    with TestClient(app) as client:
        assert client.get("/inbox").json() == {"watched": False, "local": True}
        assert client.post("/inbox/scan").status_code in (403, 409)


def test_a_relative_default_folder_is_shown_as_a_full_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    config = InboxConfig.load(tmp_path / "inbox.json", Path("data/inbox"))

    assert config.folder == tmp_path / "data" / "inbox"


def test_only_one_folder_dialog_at_a_time(client, monkeypatch):
    monkeypatch.setattr(desktop, "available", lambda: True)
    opened, release = threading.Event(), threading.Event()

    def slow_dialog(start):
        opened.set()
        release.wait(10)

    monkeypatch.setattr(desktop, "pick_folder", slow_dialog)
    first = threading.Thread(target=lambda: client.post("/inbox/pick"))
    first.start()
    opened.wait(10)

    second = client.post("/inbox/pick")
    release.set()
    first.join(10)

    assert second.status_code == 409
    assert second.json()["detail"] == "Prozor za izbor foldera je već otvoren."
    assert client.post("/inbox/pick").status_code == 200  # free again after closing
