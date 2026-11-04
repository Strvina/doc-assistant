"""Exporting a saved conversation as PDF or Word (ADR-027)."""

import io
from dataclasses import replace

import pymupdf
import pytest
from docx import Document
from fastapi.testclient import TestClient

from docassist import export
from docassist.api import create_app
from docassist.config import Settings
from docassist.export import Heading, ListBlock, Paragraph, Table, inline_runs, parse_markdown
from docassist.llm import FakeProvider
from docassist.retrieval import FakeEmbedder, VectorStore

ANSWER = """## Momenti pritezanja
Za sabirnice važi **tabela 4** [Montageanleitung.pdf, str. 7]:

| Vijak | Moment |
|---|---:|
| M8 | 20 Nm |
| M10 | `40 Nm` |

- proveriti moment ključ
- obeležiti vijak

1. prvo
2. drugo"""

CONVERSATION = {
    "id": "abc",
    "title": "Koliki je moment za M8 vijke? Čćšđž",
    "created_at": "2026-11-04T10:00:00+00:00",
    "updated_at": "2026-11-04T10:01:00+00:00",
    "turns": [
        {
            "question": "Koliki je moment za M8 vijke?",
            "mode": "rag",
            "created_at": "2026-11-04T10:00:00+00:00",
            "response": {
                "answer": ANSWER,
                "model": "openai/gpt-oss-120b",
                "citations": [
                    {
                        "source": "Montageanleitung.pdf",
                        "page": 7,
                        "text": "M8 Sammelschienen: Anzugsmoment 20 Nm, Prüfung nach Montage.",
                    }
                ],
            },
        },
        {
            "question": "Ko proverava?",
            "mode": "agent",
            "created_at": "2026-11-04T10:01:00+00:00",
            "response": {"answer": "Kontrola kvaliteta.", "citations": []},
        },
    ],
}


# --- Markdown ----------------------------------------------------------------------------


def test_markdown_blocks_match_the_web_ui():
    blocks = parse_markdown(ANSWER)
    assert blocks == [
        Heading("Momenti pritezanja"),
        Paragraph("Za sabirnice važi **tabela 4** [Montageanleitung.pdf, str. 7]:"),
        Table(head=["Vijak", "Moment"], rows=[["M8", "20 Nm"], ["M10", "`40 Nm`"]]),
        ListBlock(False, ["proveriti moment ključ", "obeležiti vijak"]),
        ListBlock(True, ["prvo", "drugo"]),
    ]


def test_inline_bold_and_code():
    assert inline_runs("a **b** c `d`") == [("", "a "), ("bold", "b"), ("", " c "), ("code", "d")]
    assert inline_runs("plain") == [("", "plain")]


def test_ragged_table_rows_are_padded():
    (table,) = parse_markdown("| a | b | c |\n| 1 |")
    assert table.head is None and table.width == 3
    turn = {"question": "q", "response": {"answer": "| a | b | c |\n| 1 |"}}
    html = export.to_html({"title": "t", "turns": [turn]})
    assert "<td>1</td><td></td><td></td>" in html


# --- files -------------------------------------------------------------------------------


def test_pdf_has_questions_answers_tables_and_sources():
    data = export.to_pdf(CONVERSATION)
    doc = pymupdf.open(stream=data, filetype="pdf")
    text = " ".join(page.get_text() for page in doc)
    assert doc.metadata["title"] == CONVERSATION["title"]
    for expected in [
        "Čćšđž",  # Serbian letters survive
        "1. Koliki je moment za M8 vijke?",
        "Momenti pritezanja",
        "M10",
        "40 Nm",
        "Izvori",
        "Montageanleitung.pdf, str. 7",
        "Prüfung nach Montage",
        "2. Ko proverava?",
        "Agent",
        "1 / 1",  # page number
    ]:
        assert expected in text, expected
    assert "**" not in text and "|---" not in text


def test_long_conversation_spans_pages_with_numbers():
    turn = CONVERSATION["turns"][0]
    long = CONVERSATION | {"turns": [turn] * 12}
    doc = pymupdf.open(stream=export.to_pdf(long), filetype="pdf")
    assert doc.page_count > 1
    assert f"{doc.page_count} / {doc.page_count}" in doc[-1].get_text()


def test_docx_has_headings_lists_tables_and_sources():
    doc = Document(io.BytesIO(export.to_docx(CONVERSATION)))
    paragraphs = [(p.style.name, p.text) for p in doc.paragraphs]
    assert ("Heading 1", CONVERSATION["title"]) in paragraphs
    assert ("Heading 2", "1. Koliki je moment za M8 vijke?") in paragraphs
    assert ("Heading 3", "Momenti pritezanja") in paragraphs
    assert ("List Bullet", "proveriti moment ključ") in paragraphs
    assert ("List Number", "drugo") in paragraphs
    assert any(t.startswith("Montageanleitung.pdf, str. 7 — „M8") for _, t in paragraphs)
    bold = [r.text for p in doc.paragraphs for r in p.runs if r.bold]
    assert "tabela 4" in bold

    (table,) = doc.tables
    assert [[c.text for c in row.cells] for row in table.rows] == [
        ["Vijak", "Moment"],
        ["M8", "20 Nm"],
        ["M10", "40 Nm"],
    ]


def test_file_names_are_ascii_with_the_real_title_for_browsers():
    assert export.filename({"title": "Godišnji odmor — đaci?"}, "pdf") == "godisnji-odmor-djaci.pdf"
    assert export.filename({"title": "???"}, "docx") == "razgovor.docx"
    header = export.content_disposition({"title": "Čaj / kafa"}, "pdf")
    assert header.startswith('attachment; filename="caj-kafa.pdf"')
    assert "filename*=UTF-8''%C4%8Caj%20kafa.pdf" in header


def test_unknown_format_is_rejected():
    with pytest.raises(ValueError, match="Unknown export format"):
        export.export(CONVERSATION, "xlsx")


# --- API ---------------------------------------------------------------------------------


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    return Settings.from_env(load_dotenv_file=False)


def browser(settings, tmp_path):
    app = create_app(
        settings=replace(settings, api_token="secret"),
        store=VectorStore(tmp_path / "index", FakeEmbedder()),
        llm_factory=lambda s, p: FakeProvider(),
    )
    client = TestClient(app)
    client.get("/")  # session cookie, like the UI
    return client


def test_export_endpoint_returns_files_only_to_the_owner(settings, tmp_path):
    alice = browser(settings, tmp_path)
    bob = browser(settings, tmp_path)
    asked = alice.post("/ask", json={"question": "Kada radi kantina?"})
    cid = asked.json()["conversation_id"]

    pdf = alice.get(f"/conversations/{cid}/export")  # PDF by default
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert 'filename="kada-radi-kantina.pdf"' in pdf.headers["content-disposition"]
    assert "Kada radi kantina?" in pymupdf.open(stream=pdf.content, filetype="pdf")[0].get_text()

    word = alice.get(f"/conversations/{cid}/export", params={"format": "docx"})
    assert word.headers["content-type"] == export.FORMATS["docx"]
    assert Document(io.BytesIO(word.content)).paragraphs[0].text == "Kada radi kantina?"

    assert bob.get(f"/conversations/{cid}/export").status_code == 404
    assert alice.get(f"/conversations/{cid}/export", params={"format": "xlsx"}).status_code == 422


def test_table_citations_are_shortened_full_names_stay_in_sources():
    long_name = "Lavazza Belgrade Training Center Cenovnik 2026. (optimized).pdf"
    assert (
        export.short_citations(f"x [{long_name}, str. 6]") == "x [Lavazza Belgrade Traini…, str. 6]"
    )
    assert export.short_citations("[kratko.pdf, p. 2]") == "[kratko, str. 2]"

    answer = f"| Kafa | Cena | Izvor |\n|---|---|---|\n| Cuba | 3.605,00 | [{long_name}, str. 6] |"
    turn = {
        "question": "q",
        "response": {"answer": answer, "citations": [{"source": long_name, "page": 6}]},
    }
    html = export.to_html({"title": "t", "turns": [turn]})
    assert "<td>[Lavazza Belgrade Traini…, str. 6]</td>" in html
    assert f"<b>{long_name}, str. 6</b>" in html  # the full name under "Izvori"
