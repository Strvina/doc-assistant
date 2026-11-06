"""Saved conversations and cancelling a question (ADR-025)."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from docassist.api import SESSION_COOKIE, create_app
from docassist.config import Settings
from docassist.conversations import ConversationStore, make_title
from docassist.llm import FakeProvider
from docassist.llm.fake import quote_first_document
from docassist.llm.prompts import REWRITE_PROMPT
from docassist.retrieval import FakeEmbedder, VectorStore


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    return Settings.from_env(load_dotenv_file=False)


def browser(settings, tmp_path, llm_factory=None, visit=True):
    """A web client with a session cookie, like the UI after opening the page."""
    store = VectorStore(tmp_path / "index", FakeEmbedder())
    app = create_app(
        settings=replace(settings, api_token="secret"),
        store=store,
        llm_factory=llm_factory or (lambda s, p: FakeProvider()),
    )
    client = TestClient(app)
    if visit:
        client.get("/")
    return client


def ask(client, question, **extra):
    response = client.post("/ask", json={"question": question, **extra})
    assert response.status_code == 200, response.text
    return response.json()


# --- store -------------------------------------------------------------------------------


def test_title_is_the_first_question_shortened():
    assert make_title("  Kada   radi\nkantina? ") == "Kada radi kantina?"
    long = make_title("x" * 200)
    assert len(long) == 70 and long.endswith("…")


def test_store_keeps_turns_in_order_per_owner(tmp_path):
    store = ConversationStore(tmp_path / "log.sqlite")
    cid = store.create("a", "Prvo pitanje")
    store.add_turn("a", cid, "Prvo pitanje", "rag", {"answer": "Prvi odgovor"})
    store.add_turn("a", cid, "Drugo", "agent", {"answer": "Drugi"})

    assert store.history("a", cid) == [("Prvo pitanje", "Prvi odgovor"), ("Drugo", "Drugi")]
    assert [c["turns"] for c in store.summaries("a")] == [2]
    assert store.summaries("b") == [] and store.get("b", cid) is None
    assert store.add_turn("b", cid, "q", "rag", {}) is False
    assert store.delete("b", cid) is False and store.delete("a", cid) is True
    assert store.get("a", cid) is None


# --- API -----------------------------------------------------------------------------------


def test_a_question_starts_a_conversation_and_follow_ups_continue_it(settings, tmp_path):
    client = browser(settings, tmp_path)

    first = ask(client, "Kada radi kantina?")
    second = ask(client, "A parking?", conversation_id=first["conversation_id"])

    assert second["conversation_id"] == first["conversation_id"]
    [listed] = client.get("/conversations").json()
    assert listed["title"] == "Kada radi kantina?" and listed["turns"] == 2
    saved = client.get(f"/conversations/{first['conversation_id']}").json()
    assert [t["question"] for t in saved["turns"]] == ["Kada radi kantina?", "A parking?"]
    assert saved["turns"][0]["response"]["answer"] == first["answer"]  # shown as it was


def test_follow_up_gets_the_saved_history(settings, tmp_path):
    seen = []

    class Recording(FakeProvider):
        def complete(self, system, messages):
            seen.append(messages)
            return super().complete(system, messages)

    client = browser(settings, tmp_path, llm_factory=lambda s, p: Recording())
    client.post("/documents/demo")
    first = ask(client, "Kada radi kantina?")
    ask(client, "A parking?", conversation_id=first["conversation_id"])

    assert seen[-1][0]["content"] == "Kada radi kantina?"  # earlier turn sent along


def test_a_follow_up_is_searched_as_the_rewritten_question(settings, tmp_path):
    def respond(system, messages):
        if system == REWRITE_PROMPT:
            return "Gde mogu da parkiram?"
        return quote_first_document(system, messages)

    client = browser(settings, tmp_path, llm_factory=lambda s, p: FakeProvider(respond))
    client.post("/documents/demo")
    first = ask(client, "Kada radi kantina?")
    second = ask(client, "A parking?", conversation_id=first["conversation_id"])

    assert first["search_query"] == ""
    assert second["search_query"] == "Gde mogu da parkiram?"
    saved = client.get(f"/conversations/{first['conversation_id']}").json()
    assert saved["turns"][1]["response"]["search_query"] == "Gde mogu da parkiram?"


def test_conversations_are_private_to_their_browser(settings, tmp_path):
    alice = browser(settings, tmp_path)
    bob = browser(settings, tmp_path)
    cid = ask(alice, "Moje pitanje")["conversation_id"]

    assert bob.get("/conversations").json() == []
    assert bob.get(f"/conversations/{cid}").status_code == 404
    assert bob.delete(f"/conversations/{cid}").status_code == 404
    assert bob.post("/ask", json={"question": "x", "conversation_id": cid}).status_code == 404
    assert len(alice.get("/conversations").json()) == 1


def test_delete_a_conversation(settings, tmp_path):
    client = browser(settings, tmp_path)
    cid = ask(client, "Za brisanje")["conversation_id"]

    assert client.delete(f"/conversations/{cid}").json() == {"ok": True}
    assert client.get("/conversations").json() == []
    assert client.get(f"/conversations/{cid}").status_code == 404


def test_a_cancelled_question_is_not_saved_or_logged(settings, tmp_path):
    def answer_after_cancel(s, p):
        client.post("/ask/cancel", json={"request_id": "r-1"})  # user clicked Stop meanwhile
        return FakeProvider()

    client = browser(settings, tmp_path, llm_factory=answer_after_cancel)

    response = client.post("/ask", json={"question": "Pogrešno pitanje", "request_id": "r-1"})

    assert response.status_code == 409
    assert client.get("/conversations").json() == []
    assert client.get("/stats").json()["queries"] == 0


def test_session_and_its_conversations_survive_a_server_restart(settings, tmp_path):
    client = browser(settings, tmp_path)
    cid = ask(client, "Pre restarta")["conversation_id"]
    cookie = client.cookies[SESSION_COOKIE]

    restarted = browser(settings, tmp_path, visit=False)  # new app: no sessions in memory
    restarted.cookies.set(SESSION_COOKIE, cookie)
    restarted.get("/")

    assert [c["id"] for c in restarted.get("/conversations").json()] == [cid]


def test_session_cookie_is_long_lived(settings, tmp_path):
    store = VectorStore(tmp_path / "index", FakeEmbedder())
    client = TestClient(create_app(settings=settings, store=store))
    header = client.get("/").headers["set-cookie"]
    assert "Max-Age=31536000" in header and "HttpOnly" in header


# --- search and rename ---------------------------------------------------------------------


def test_search_finds_titles_questions_and_answers_without_diacritics(tmp_path):
    store = ConversationStore(tmp_path / "log.sqlite")
    canteen = store.create("a", "Kada radi kantina?")
    store.add_turn("a", canteen, "Kada radi kantina?", "rag", {"answer": "Od 10:30 do 13:30."})
    store.add_turn("a", canteen, "A čaj?", "rag", {"answer": "Čaj je besplatan."})
    leave = store.create("a", "Godišnji odmor")
    store.add_turn("a", leave, "Godišnji odmor", "rag", {"answer": "Najmanje 20 radnih dana."})

    def ids(query):
        return [c["id"] for c in store.summaries("a", query=query)]

    assert ids("KANTINA") == [canteen]
    assert ids("caj besplatan") == [canteen]  # words from a later turn, without diacritics
    assert ids("godisnji 20") == [leave]
    assert ids("parking") == []
    assert len(ids("")) == 2
    assert store.summaries("b", query="kantina") == []


def test_rename_a_conversation(settings, tmp_path):
    alice = browser(settings, tmp_path)
    bob = browser(settings, tmp_path)
    cid = ask(alice, "Kada radi kantina?")["conversation_id"]

    renamed = alice.patch(f"/conversations/{cid}", json={"title": "  Kantina\n i ručak  "})
    assert renamed.json() == {"ok": True}
    assert alice.get("/conversations").json()[0]["title"] == "Kantina i ručak"
    assert alice.get("/conversations", params={"q": "rucak"}).json()[0]["id"] == cid

    assert bob.patch(f"/conversations/{cid}", json={"title": "Moje"}).status_code == 404
    assert alice.patch(f"/conversations/{cid}", json={"title": ""}).status_code == 422
