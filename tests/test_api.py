import json
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
    assert "/static/app.css?v=" in response.text  # versioned: no stale CSS after an update
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert client.get("/static/app.js").status_code == 200


def test_config(client):
    body = client.get("/api/config").json()

    assert [p["id"] for p in body["providers"]] == ["claude", "fake"]  # ChatGPT hidden
    assert body["providers"][0]["available"] is False
    assert body["default_provider"] == "fake"
    assert body["allow_uploads"] is True
    assert {".pdf", ".docx", ".xlsx", ".pptx", ".png"} <= set(body["extensions"])


def test_config_lists_gemini_when_key_is_set(settings, tmp_path):
    client = make_client(
        settings, tmp_path, gemini_api_key="g-key", gemini_model="gemini-x", groq_api_key="gsk"
    )
    body = client.get("/api/config").json()

    gemini = next(p for p in body["providers"] if p["id"] == "gemini")
    assert gemini == {"id": "gemini", "label": "Gemini", "available": True}
    assert body["models"]["gemini"] == "gemini-x"
    assert [p["id"] for p in body["providers"]] == ["claude", "gemini", "groq", "fake"]


def test_upload_progress_lists_documents_being_read(client, monkeypatch):
    from docassist.ingest import loaders

    assert client.get("/documents/progress").json() == []
    monkeypatch.setitem(loaders.PROGRESS, "scan.pdf", (23, 96))

    assert client.get("/documents/progress").json() == [
        {"source": "scan.pdf", "done": 23, "total": 96, "kind": "ocr"}
    ]


# --- reading pictures with the AI model (ADR-029) ------------------------------------------


def picture_pdf_bytes():
    picture = pymupdf.open()
    picture.new_page(width=200, height=100).insert_text((20, 50), "picture")
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "Cenovnik 2026")
    pdf.new_page().insert_image(pdf[1].rect, pixmap=picture[0].get_pixmap(dpi=72))
    return pdf.tobytes()


def test_read_pictures_with_the_vision_model(settings, tmp_path, monkeypatch):
    monkeypatch.setenv("OCR", "false")
    seen = []

    def vision_factory(_settings):
        return (lambda png: seen.append(png) or "Ristretto | 1.099,00 RSD"), "fake-vision"

    store = VectorStore(tmp_path / "index", FakeEmbedder())
    app = create_app(
        settings=replace(settings, gemini_api_key="g-key"),
        store=store,
        llm_factory=fake_llm,
        vision_factory=vision_factory,
    )
    client = TestClient(app)
    assert client.get("/api/config").json()["vision"] is True
    client.post("/documents", files=[("files", ("cenovnik.pdf", picture_pdf_bytes()))])
    doc = doc_id(client, "cenovnik.pdf")

    body = client.post(f"/documents/{doc}/pictures").json()

    assert body == {
        "source": "cenovnik.pdf",
        "pages_total": 1,
        "pages_read": 1,
        "chunks": 2,
        "model": "fake-vision",
        "error": None,
    }
    assert len(seen) == 1
    assert client.get("/documents/progress").json() == []
    assert client.post(f"/documents/{doc}/pictures").json()["pages_read"] == 1
    assert len(seen) == 1  # already read: nothing is sent again


def test_read_pictures_needs_a_gemini_key_and_is_off_in_the_demo(settings, tmp_path):
    client = make_client(settings, tmp_path)
    assert client.get("/api/config").json()["vision"] is False
    upload(client, "faq.txt")
    doc = doc_id(client, "faq.txt")

    response = client.post(f"/documents/{doc}/pictures")
    assert response.status_code == 400
    assert "GEMINI_API_KEY" in response.json()["detail"]
    assert client.post("/documents/nope/pictures").status_code == 404

    demo = make_client(settings, tmp_path / "demo", allow_uploads=False, gemini_api_key="k")
    assert demo.get("/api/config").json()["vision"] is False
    assert demo.post(f"/documents/{doc}/pictures").status_code == 403


def test_read_pictures_only_for_pdfs_and_images(settings, tmp_path):
    def vision_factory(_settings):
        return (lambda png: "x"), "fake-vision"

    store = VectorStore(tmp_path / "index", FakeEmbedder())
    client = TestClient(
        create_app(
            settings=settings, store=store, llm_factory=fake_llm, vision_factory=vision_factory
        )
    )
    upload(client, "faq.txt")

    response = client.post(f"/documents/{doc_id(client, 'faq.txt')}/pictures")

    assert response.status_code == 400
    assert "only PDFs and images" in response.json()["detail"]


def test_delete_one_question_and_all_questions(client):
    first = client.post("/ask", json={"question": "Prvo pitanje"}).json()
    client.post("/ask", json={"question": "Drugo pitanje"})
    client.post("/ask", json={"question": "Treće pitanje"})

    assert client.delete(f"/queries/{first['log_id']}").json() == {"ok": True}
    assert client.delete(f"/queries/{first['log_id']}").status_code == 404
    recent = [q["question"] for q in client.get("/insights").json()["recent"]]
    assert "Prvo pitanje" not in recent and len(recent) == 2

    assert client.delete("/queries").json() == {"deleted": 2}
    assert client.get("/insights").json()["stats"]["queries"] == 0
    assert len(client.get("/conversations").json()) == 3  # saved conversations are kept


def test_questions_cannot_be_deleted_in_the_public_demo(settings, tmp_path):
    demo = make_client(settings, tmp_path, allow_uploads=False)
    log_id = demo.post("/ask", json={"question": "Pitanje"}).json()["log_id"]

    assert demo.delete(f"/queries/{log_id}").status_code == 403
    assert demo.delete("/queries").status_code == 403
    assert demo.get("/insights").json()["stats"]["queries"] == 1


def test_app_can_be_embedded_only_where_allowed(settings, tmp_path):
    plain = make_client(settings, tmp_path).get("/")
    assert plain.headers["x-frame-options"] == "SAMEORIGIN"
    assert "samesite=strict" in plain.headers["set-cookie"].lower()

    space = make_client(settings, tmp_path, embed_origins=("https://huggingface.co",)).get("/")
    assert "x-frame-options" not in space.headers
    assert space.headers["content-security-policy"] == (
        "frame-ancestors 'self' https://huggingface.co"
    )
    cookie = space.headers["set-cookie"].lower()
    assert "samesite=none" in cookie and "secure" in cookie and "partitioned" in cookie


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
        ("files", ("a.xls", b"x")),
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
    assert len(client.get("/documents").json()) == 7


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


def test_escalate_unanswered_question_once(client, settings):
    body = client.post("/ask", json={"question": "Koja je cena akcija na berzi?"}).json()
    assert body["found"] is False

    first = client.post("/escalate", json={"log_id": body["log_id"]})
    again = client.post("/escalate", json={"log_id": body["log_id"]})

    assert first.status_code == 200
    reference = first.json()["reference"]
    assert reference.startswith("ESC-")
    assert again.json()["reference"] == reference
    assert again.json()["already_sent"] is True
    lines = (settings.outbox_dir / "escalations.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1  # the second click did not send a second e-mail
    record = json.loads(lines[0])
    assert record["question"] == "Koja je cena akcija na berzi?"
    assert record["id"] == reference
    assert first.json()["team"] == record["team"] == "hr"  # first team is the default


def test_escalate_to_a_chosen_team(client, settings):
    teams = client.get("/api/config").json()["escalation_teams"]
    assert {"id": "maintenance", "label": "Održavanje"} in teams
    log_id = client.post("/ask", json={"question": "Koja je cena akcija?"}).json()["log_id"]

    assert client.post("/escalate", json={"log_id": log_id, "team": "sales"}).status_code == 422
    response = client.post("/escalate", json={"log_id": log_id, "team": "maintenance"})

    assert response.json()["team"] == "maintenance"
    record = json.loads((settings.outbox_dir / "escalations.jsonl").read_text(encoding="utf-8"))
    assert (record["team_label"], record["team_email"]) == ("Održavanje", "odrzavanje@example.com")


def test_escalate_unknown_log_id(client):
    assert client.post("/escalate", json={"log_id": 999}).status_code == 404


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
    assert len(client.get("/documents").json()) == 7
    assert upload(client, "faq.txt").status_code == 403
    assert client.post("/documents/demo").status_code == 403
    doc = client.get("/documents").json()[0]["doc_id"]
    assert client.delete(f"/documents/{doc}").status_code == 403
    assert client.get("/api/config").json()["allow_uploads"] is False


# --- search in chosen documents ------------------------------------------------------------


def test_ask_only_in_the_chosen_documents(client):
    upload(client, "faq.txt", "montageanleitung_pv400.pdf")
    faq = doc_id(client, "faq.txt")
    question = {"question": "Anzugsmoment M8 Sammelschienen"}

    everywhere = client.post("/ask", json=question).json()
    chosen = client.post("/ask", json={**question, "doc_ids": [faq]}).json()

    assert everywhere["citations"][0]["source"] == "montageanleitung_pv400.pdf"
    assert everywhere["searched_documents"] == []
    assert {c["source"] for c in chosen["citations"]} <= {"faq.txt"}
    assert chosen["searched_documents"] == ["faq.txt"]
    saved = client.get(f"/conversations/{chosen['conversation_id']}").json()
    assert saved["turns"][0]["response"]["searched_documents"] == ["faq.txt"]


def test_ask_rejects_unknown_or_empty_document_choices(client):
    upload(client, "faq.txt")

    unknown = client.post("/ask", json={"question": "Kantina?", "doc_ids": ["nema"]})
    empty = client.post("/ask", json={"question": "Kantina?", "doc_ids": []})

    assert unknown.status_code == 422
    assert "No document with id nema" in unknown.json()["detail"]
    assert empty.status_code == 422


def test_streamed_and_agent_answers_use_the_chosen_documents(client):
    upload(client, "faq.txt", "montageanleitung_pv400.pdf")
    faq = doc_id(client, "faq.txt")
    body = {"question": "Anzugsmoment M8 Sammelschienen", "doc_ids": [faq]}

    lines = client.post("/ask/stream", json=body).text.strip().splitlines()
    streamed = json.loads(lines[-1])["response"]
    agent = client.post("/ask", json={**body, "agent": True}).json()

    assert streamed["searched_documents"] == agent["searched_documents"] == ["faq.txt"]
    assert {c["source"] for c in streamed["citations"] + agent["citations"]} <= {"faq.txt"}


# --- monitoring lists, page by page --------------------------------------------------------


def test_monitoring_lists_come_page_by_page(client):
    upload(client, "faq.txt")
    for n in range(5):
        client.post("/ask", json={"question": f"Kada radi kantina {n}?"})
    rated = client.post("/ask", json={"question": "Kvantna gravitacija?"}).json()
    client.post("/feedback", json={"log_id": rated["log_id"], "rating": -1})  # 👎: a gap

    first = client.get("/queries", params={"kind": "recent", "limit": 4}).json()
    rest = client.get("/queries", params={"kind": "recent", "limit": 4, "before": first["next"]})

    questions = [q["question"] for q in first["items"] + rest.json()["items"]]
    assert questions[0] == "Kvantna gravitacija?" and len(questions) == 6
    assert len(set(questions)) == 6  # no question twice across pages
    assert first["next"] == first["items"][-1]["id"] and rest.json()["next"] is None
    gaps = client.get("/queries", params={"kind": "gaps"}).json()
    assert "Kvantna gravitacija?" in [q["question"] for q in gaps["items"]]
    assert client.get("/queries", params={"kind": "sve"}).status_code == 422
