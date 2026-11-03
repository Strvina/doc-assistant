from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from docassist.api import create_app
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
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.setenv("OUTBOX_DIR", str(tmp_path / "outbox"))
    monkeypatch.setenv("API_TOKEN", "")
    return Settings.from_env(load_dotenv_file=False)


def make_client(settings, tmp_path):
    store = VectorStore(tmp_path / "index", FakeEmbedder())
    return TestClient(create_app(settings=settings, store=store, llm_factory=fake_llm))


@pytest.fixture
def client(settings, tmp_path):
    return make_client(settings, tmp_path)


def upload(client, *names, headers=None):
    files = [("files", (n, (SAMPLE_DOCS / n).read_bytes())) for n in names]
    return client.post("/documents", files=files, headers=headers or {})


def test_health_and_empty_index(client):
    assert client.get("/health").json() == {"status": "ok", "documents": 0, "chunks": 0}
    assert client.get("/documents").json() == []


def test_upload_list_delete(client):
    response = upload(client, "faq.txt", "pravilnik_o_radu.pdf")

    assert response.status_code == 200
    assert [r["status"] for r in response.json()] == ["indexed", "indexed"]
    assert upload(client, "faq.txt").json()[0]["status"] == "unchanged"

    docs = client.get("/documents").json()
    assert [d["source"] for d in docs] == ["faq.txt", "pravilnik_o_radu.pdf"]

    doc_id = docs[0]["doc_id"]
    assert client.delete(f"/documents/{doc_id}").json()["chunks_removed"] > 0
    assert client.delete(f"/documents/{doc_id}").status_code == 404


def test_upload_rejects_unsupported_and_broken_files(client):
    files = [("files", ("a.xlsx", b"x")), ("files", ("b.pdf", b"not a pdf"))]

    results = client.post("/documents", files=files).json()

    assert [r["status"] for r in results] == ["failed", "failed"]
    assert results[0]["error"] == "unsupported file type"


def test_ask_returns_answer_with_citations(client):
    upload(client, "pravilnik_o_radu.pdf")

    response = client.post("/ask", json={"question": "Koliko dana traje godišnji odmor?"})

    assert response.status_code == 200
    body = response.json()
    assert body["found"] is True
    assert body["citations"][0]["source"] == "pravilnik_o_radu.pdf"
    assert body["citations"][0]["page"] == 2
    assert "20 radnih dana" in body["citations"][0]["text"]
    assert body["provider"] == "fake"
    assert body["tool_calls"] == []


def test_ask_agent_mode_reports_tool_calls(client):
    upload(client, "faq.txt")

    body = client.post("/ask", json={"question": "Kada radi kantina?", "agent": True}).json()

    assert [c["name"] for c in body["tool_calls"]] == ["search_documents"]
    assert body["found"] is True


@pytest.mark.parametrize(
    ("provider", "status"), [("claude", 503), ("broken", 502), ("gemini", 200)]
)
def test_ask_provider_errors(client, provider, status):
    upload(client, "faq.txt")

    response = client.post("/ask", json={"question": "?", "provider": provider})

    assert response.status_code == status


def test_ask_validates_input(client):
    assert client.post("/ask", json={"question": ""}).status_code == 422
    assert client.post("/ask", json={"question": "x" * 2001}).status_code == 422


def test_api_token(settings, tmp_path):
    client = make_client(replace(settings, api_token="s3cret"), tmp_path)

    assert client.get("/documents").status_code == 401
    assert client.get("/documents", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/documents", headers={"X-API-Key": "s3cret"}).status_code == 200
    assert client.get("/health").status_code == 200  # health stays open for monitoring
