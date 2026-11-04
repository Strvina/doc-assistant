from dataclasses import replace
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from docassist.api import SESSION_COOKIE, create_app
from docassist.config import Settings
from docassist.factory import MissingAPIKeyError
from docassist.llm import FakeProvider, LLMError
from docassist.retrieval import FakeEmbedder, VectorStore

SAMPLE_DOCS = Path(__file__).resolve().parent.parent / "data" / "sample_docs"


def fake_llm(settings, provider):
    if provider == "claude":
        raise MissingAPIKeyError("ANTHROPIC_API_KEY is not set")
    if provider == "broken":
        raise LLMError("Claude API error 400: credit balance is too low")
    return FakeProvider()


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    return Settings.from_env(load_dotenv_file=False)


def make_client(settings, tmp_path, **overrides):
    store = VectorStore(tmp_path / "index", FakeEmbedder())
    app = create_app(settings=replace(settings, **overrides), store=store, llm_factory=fake_llm)
    return TestClient(app)


@pytest.fixture
def client(settings, tmp_path):
    return make_client(settings, tmp_path)


def upload(client, *names, headers=None):
    files = [("files", (n, (SAMPLE_DOCS / n).read_bytes())) for n in names]
    return client.post("/documents", files=files, headers=headers or {})


def doc_id(client, source):
    return next(d["doc_id"] for d in client.get("/documents").json() if d["source"] == source)


# --- web UI and config -------------------------------------------------------------------


def test_index_page_sets_a_session_cookie(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "<title>Doc Assistant</title>" in response.text
    assert SESSION_COOKIE in response.cookies
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert client.get("/static/app.js").status_code == 200


def test_config(client):
    body = client.get("/api/config").json()

    assert [p["id"] for p in body["providers"]] == ["claude", "fake"]  # ChatGPT hidden
    assert body["providers"][0]["available"] is False
    assert body["default_provider"] == "fake"
    assert body["allow_uploads"] is True


def test_config_lists_gemini_when_key_is_set(settings, tmp_path):
    client = make_client(settings, tmp_path, gemini_api_key="g-key", gemini_model="gemini-x")
    body = client.get("/api/config").json()

    gemini = next(p for p in body["providers"] if p["id"] == "gemini")
    assert gemini == {"id": "gemini", "label": "Gemini", "available": True}
    assert body["models"]["gemini"] == "gemini-x"


def test_health_and_empty_index(client):
    body = client.get("/health").json()

    assert (body["status"], body["documents"], body["chunks"]) == ("ok", 0, 0)
    assert client.get("/documents").json() == []


# --- documents ---------------------------------------------------------------------------


def test_upload_list_delete(client, tmp_path):
    response = upload(client, "faq.txt", "pravilnik_o_radu.pdf")

    assert [r["status"] for r in response.json()] == ["indexed", "indexed"]
    assert upload(client, "faq.txt").json()[0]["status"] == "unchanged"

    docs = client.get("/documents").json()
    assert [(d["source"], d["language"], d["has_file"]) for d in docs] == [
        ("faq.txt", "sr", True),
        ("pravilnik_o_radu.pdf", "sr", True),
    ]

    faq = docs[0]["doc_id"]
    assert client.delete(f"/documents/{faq}").json()["chunks_removed"] > 0
    assert client.delete(f"/documents/{faq}").status_code == 404
    assert not (tmp_path / "documents" / "faq.txt").exists()


def test_upload_rejects_unsupported_broken_and_large_files(settings, tmp_path):
    client = make_client(settings, tmp_path, max_upload_mb=1)
    files = [
        ("files", ("a.xlsx", b"x")),
        ("files", ("b.pdf", b"not a pdf")),
        ("files", ("c.txt", b"a" * (1024 * 1024 + 1))),
    ]

    results = client.post("/documents", files=files).json()

    assert [r["status"] for r in results] == ["failed"] * 3
    assert "Unsupported" in results[0]["error"]
    assert "larger than 1 MB" in results[2]["error"]
    assert client.get("/documents").json() == []


def test_load_demo_documents(client):
    results = client.post("/documents/demo").json()

    assert {r["source"] for r in results} == {p.name for p in SAMPLE_DOCS.iterdir()}
    assert len(client.get("/documents").json()) == 5


def test_view_pdf_with_highlight_and_download(client):
    upload(client, "montageanleitung_pv400.pdf")
    doc = doc_id(client, "montageanleitung_pv400.pdf")
    body = client.post("/ask", json={"question": "Anzugsmoment M8 Sammelschienen"}).json()
    citation = body["citations"][0]

    view = client.get(
        f"/documents/{doc}/view",
        params={"page": citation["page"], "chunk": citation["chunk_ids"]},
    )

    assert view.headers["content-type"] == "application/pdf"
    assert "inline" in view.headers["content-disposition"]
    with pymupdf.open("pdf", view.content) as pdf:
        assert any(True for _ in pdf[citation["page"] - 1].annots())

    download = client.get(f"/documents/{doc}/file")
    assert download.content == (SAMPLE_DOCS / "montageanleitung_pv400.pdf").read_bytes()
    assert "attachment" in download.headers["content-disposition"]


def test_view_docx_as_html(client):
    upload(client, "bezbednost_na_radu.docx")
    doc = doc_id(client, "bezbednost_na_radu.docx")

    view = client.get(f"/documents/{doc}/view", params={"page": 3})

    assert view.headers["content-type"].startswith("text/html")
    assert "Povreda na radu" in view.text
    assert client.get("/documents/unknown/view").status_code == 404


# --- questions ---------------------------------------------------------------------------


def test_ask_returns_answer_with_citations(client):
    upload(client, "pravilnik_o_radu.pdf")

    response = client.post("/ask", json={"question": "Koliko dana traje godišnji odmor?"})

    assert response.status_code == 200
    body = response.json()
    assert body["found"] is True
    citation = body["citations"][0]
    assert (citation["source"], citation["page"]) == ("pravilnik_o_radu.pdf", 2)
    assert citation["doc_id"] == doc_id(client, "pravilnik_o_radu.pdf")
    assert citation["chunk_ids"] and "radnih dana" in citation["text"]
    assert body["provider"] == "fake"
    assert body["cost_usd"] is None  # the fake model has no price
    assert body["tool_calls"] == []


def test_ask_agent_mode_reports_tool_calls(client):
    upload(client, "faq.txt")

    body = client.post(
        "/ask", json={"question": "Kada radi kantina?", "agent": True, "top_k": 2}
    ).json()

    assert [c["name"] for c in body["tool_calls"]] == ["search_documents"]
    assert body["found"] is True


@pytest.mark.parametrize(("provider", "status"), [("claude", 503), ("broken", 502)])
def test_ask_provider_errors(client, provider, status):
    upload(client, "faq.txt")

    response = client.post("/ask", json={"question": "?", "provider": provider})

    assert response.status_code == status


def test_ask_validates_input(client):
    assert client.post("/ask", json={"question": ""}).status_code == 422
    assert client.post("/ask", json={"question": "x" * 2001}).status_code == 422
    assert client.post("/ask", json={"question": "x", "top_k": 50}).status_code == 422


def test_feedback_stats_and_insights(client):
    upload(client, "faq.txt")
    client.get("/")  # browser session
    first = client.post("/ask", json={"question": "Kada radi kantina?"}).json()

    assert first["log_id"] == 1
    assert client.post("/feedback", json={"log_id": 1, "rating": -1}).json() == {"ok": True}
    assert client.post("/feedback", json={"log_id": 99, "rating": 1}).status_code == 404
    assert client.post("/feedback", json={"log_id": 1, "rating": 3}).status_code == 422

    assert client.get("/stats").json()["thumbs_down"] == 1
    insights = client.get("/insights").json()
    assert insights["stats"]["queries"] == 1
    assert [q["question"] for q in insights["gaps"]] == ["Kada radi kantina?"]
    assert insights["recent"][0]["feedback"] == -1


# --- access control and limits -----------------------------------------------------------


def test_api_token_or_browser_session(settings, tmp_path):
    client = make_client(settings, tmp_path, api_token="s3cret")

    assert client.get("/documents").status_code == 401
    assert client.get("/documents", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/documents", headers={"X-API-Key": "s3cret"}).status_code == 200
    assert client.get("/health").status_code == 200  # monitoring stays open

    client.get("/")  # the web UI gets a session cookie ...
    assert client.get("/documents").status_code == 200  # ... and may use the API


def test_question_limit_per_session(settings, tmp_path):
    client = make_client(settings, tmp_path, max_questions_per_session=2)
    upload(client, "faq.txt")
    client.get("/")

    codes = [client.post("/ask", json={"question": "Kantina?"}).status_code for _ in range(3)]

    assert codes == [200, 200, 429]


def test_question_limit_per_ip_but_not_for_api_keys(settings, tmp_path):
    client = make_client(settings, tmp_path, max_questions_per_ip_hour=1, api_token="k")
    key = {"X-API-Key": "k"}
    upload(client, "faq.txt", headers=key)
    client.get("/")

    assert client.post("/ask", json={"question": "Kantina?"}).status_code == 200
    assert client.post("/ask", json={"question": "Kantina?"}).status_code == 429
    keyed = client.post("/ask", json={"question": "Kantina?"}, headers=key)
    assert keyed.status_code == 200  # integrations are not rate limited


def test_public_demo_mode(settings, tmp_path):
    client = make_client(settings, tmp_path, allow_uploads=False)

    # Demo documents are indexed on first use; changing the library is forbidden.
    assert len(client.get("/documents").json()) == 5
    assert upload(client, "faq.txt").status_code == 403
    assert client.post("/documents/demo").status_code == 403
    doc = client.get("/documents").json()[0]["doc_id"]
    assert client.delete(f"/documents/{doc}").status_code == 403
    assert client.get("/api/config").json()["allow_uploads"] is False
