from pathlib import Path

import pytest

from docassist.ingest import chunk_pages, document_id, ingest, split_text
from docassist.ingest.chunker import split_sections
from docassist.models import Page

SAMPLE_DOCS = Path(__file__).resolve().parent.parent / "data" / "sample_docs"


def sentences(n: int) -> str:
    return " ".join(f"Sentence number {i} talks about torque values." for i in range(n))


# --- split_text --------------------------------------------------------------------------


def test_short_text_is_one_chunk():
    assert split_text("Kratak tekst.", chunk_size=800, overlap=150) == ["Kratak tekst."]


def test_chunks_respect_size_limit():
    chunks = split_text(sentences(100), chunk_size=200, overlap=50)

    assert len(chunks) > 1
    assert all(len(chunk) <= 200 for chunk in chunks)


def test_chunks_cover_the_whole_text():
    text = sentences(60)
    chunks = split_text(text, chunk_size=200, overlap=50)

    for i in range(60):
        assert any(f"Sentence number {i} " in chunk for chunk in chunks)
    assert all(chunk in text for chunk in chunks)


def test_neighbouring_chunks_overlap():
    chunks = split_text(sentences(30), chunk_size=200, overlap=60)

    for previous, current in zip(chunks, chunks[1:], strict=False):
        first_sentence = current.split(". ")[0]
        assert first_sentence in previous


def test_zero_overlap_has_no_repetition():
    text = sentences(30)
    chunks = split_text(text, chunk_size=200, overlap=0)

    assert sum(len(chunk) for chunk in chunks) <= len(text)


def test_prefers_paragraph_boundaries():
    first = "A" * 50 + " first paragraph."
    second = "B" * 50 + " second paragraph."
    chunks = split_text(f"{first}\n\n{second}", chunk_size=100, overlap=0)

    assert chunks == [first, second]


def test_does_not_cut_words_when_spaces_exist():
    text = " ".join(["Schaltschrank"] * 40)
    chunks = split_text(text, chunk_size=50, overlap=10)

    for chunk in chunks:
        assert set(chunk.split()) == {"Schaltschrank"}


def test_hard_split_of_one_huge_word():
    chunks = split_text("x" * 250, chunk_size=100, overlap=0)

    assert [len(c) for c in chunks] == [100, 100, 50]


def test_blank_text_gives_no_chunks():
    assert split_text(" \n\n ", chunk_size=100, overlap=10) == []


# --- chunk_pages -------------------------------------------------------------------------


def test_chunk_metadata():
    pages = [Page("manual.pdf", 1, "Page one."), Page("manual.pdf", 3, sentences(20))]

    chunks = chunk_pages(pages, doc_id="abc123", chunk_size=200, overlap=40)

    assert chunks[0].chunk_id == "manual.pdf::p1::0"
    assert chunks[0].text == "Page one."
    assert [c.chunk_id for c in chunks[1:3]] == ["manual.pdf::p3::0", "manual.pdf::p3::1"]
    assert {c.doc_id for c in chunks} == {"abc123"}
    assert {c.source for c in chunks} == {"manual.pdf"}
    assert len({c.chunk_id for c in chunks}) == len(chunks)


def test_chunks_never_cross_pages():
    pages = [Page("a.txt", 1, "alpha " * 30), Page("a.txt", 2, "beta " * 30)]

    chunks = chunk_pages(pages, doc_id="d", chunk_size=100, overlap=30)

    for chunk in chunks:
        words = set(chunk.text.split())
        assert words == ({"alpha"} if chunk.page == 1 else {"beta"})


@pytest.mark.parametrize(("size", "overlap"), [(0, 0), (100, 100), (100, -1), (100, 150)])
def test_invalid_parameters(size, overlap):
    with pytest.raises(ValueError):
        chunk_pages([Page("a.txt", 1, "text")], doc_id="d", chunk_size=size, overlap=overlap)


# --- ingest ------------------------------------------------------------------------------


def test_document_id_depends_on_content_only(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    c = tmp_path / "c.txt"
    a.write_text("same", encoding="utf-8")
    b.write_text("same", encoding="utf-8")
    c.write_text("different", encoding="utf-8")

    assert document_id(a) == document_id(b) != document_id(c)
    assert len(document_id(a)) == 16


def test_ingest_sample_pdf():
    path = SAMPLE_DOCS / "montageanleitung_pv400.pdf"

    chunks = ingest(path)

    assert {c.page for c in chunks} == {1, 2, 3}
    assert {c.doc_id for c in chunks} == {document_id(path)}
    assert all(len(c.text) <= 800 for c in chunks)
    torque = [c for c in chunks if "M8 20 Nm 13 mm" in c.text]
    assert torque and torque[0].page == 2


@pytest.mark.parametrize("name", sorted(p.name for p in SAMPLE_DOCS.iterdir()))
def test_ingest_all_sample_docs(name):
    chunks = ingest(SAMPLE_DOCS / name, chunk_size=500, overlap=100)

    assert chunks
    assert all(c.source == name and 0 < len(c.text) <= 500 for c in chunks)


# --- sections (ADR-023) ------------------------------------------------------------------

TORQUE_PAGE = """5. Befestigung am Boden

Jeder Schrank wird mit 4 Schwerlastankern M10 am Betonboden befestigt.
Das Anzugsmoment der Bodenanker beträgt 35 Nm.

6. Anzugsmomente für Sammelschienenverbindungen

Die folgenden Anzugsmomente gelten für Schrauben der Festigkeitsklasse 8.8 an Kupfer-Sammelschienen.

Gewinde Anzugsmoment Schlüsselweite

M6 10 Nm 10 mm

M8 20 Nm 13 mm

M10 40 Nm 17 mm"""


def test_a_table_stays_with_its_section_heading():
    chunks = split_sections(TORQUE_PAGE, 300, 60)

    [table] = [c for c in chunks if "M8 20 Nm" in c]
    assert table.startswith("6. Anzugsmomente für Sammelschienenverbindungen")
    assert "M6 10 Nm" in table and "M10 40 Nm" in table


def test_small_sections_are_packed_together():
    text = "1. Prvi\nKratko.\n\n2. Drugi\nTakođe kratko.\n\n3. Treći\nI ovo."
    assert split_sections(text, 500, 100) == [text]


def test_parts_of_a_long_section_repeat_its_heading():
    body = " ".join(f"Rečenica broj {i} o zaštitnoj opremi." for i in range(30))
    chunks = split_sections(f"2. Lična zaštitna oprema\n{body}", 200, 40)

    assert len(chunks) > 1
    assert all(c.startswith("2. Lična zaštitna oprema\n") for c in chunks)
    assert all(len(c) <= 200 for c in chunks)


def test_faq_questions_start_sections():
    text = (
        "Kada radi kantina?\nKantina radi od 10:30 do 13:30.\n\n"
        "Gde mogu da parkiram?\nIza hale B.\n\n"
        "Kada je isplata plate?\nDo 10. u mesecu."
    )
    chunks = split_sections(text, 60, 10)
    assert chunks[0] == "Kada radi kantina?\nKantina radi od 10:30 do 13:30."
    assert all(c.splitlines()[0].endswith("?") for c in chunks)


@pytest.mark.parametrize(
    "line",
    ["1. Ugradite ormar na ravnu podlogu.", "2. Proverite:", "M8 20 Nm 13 mm", "Napomena"],
)
def test_list_items_and_table_rows_are_not_headings(line):
    text = f"Uvod.\n{line}\nJoš teksta."
    assert split_sections(text, 500, 100) == split_text(text, 500, 100)
