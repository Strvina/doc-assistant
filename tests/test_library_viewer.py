import os
import shutil
from pathlib import Path

import docx
import pymupdf
import pytest

from docassist import viewer
from docassist.ingest import DocumentLoadError, UnsupportedFileTypeError
from docassist.library import Library, guess_language, safe_name
from docassist.retrieval import FakeEmbedder, VectorStore
from docassist.viewer import is_cited, pdf_with_highlights, render_html

SAMPLE_DOCS = Path(__file__).resolve().parent.parent / "data" / "sample_docs"


@pytest.fixture
def library(tmp_path):
    return Library(VectorStore(tmp_path / "index", FakeEmbedder()), tmp_path / "files")


# --- Library -----------------------------------------------------------------------------


def test_add_file_indexes_and_keeps_a_copy(library):
    result = library.add_file(SAMPLE_DOCS / "faq.txt")

    assert (result.source, result.status) == ("faq.txt", "indexed")
    assert (library.files_dir / "faq.txt").read_bytes() == (SAMPLE_DOCS / "faq.txt").read_bytes()
    assert library.add_file(SAMPLE_DOCS / "faq.txt").status == "unchanged"


def test_original_and_delete(library):
    library.add_file(SAMPLE_DOCS / "pravilnik_o_radu.pdf")
    [doc] = library.documents()

    found = library.original(doc.doc_id)
    assert found is not None and found[1].name == "pravilnik_o_radu.pdf"

    assert library.delete(doc.doc_id) > 0
    assert not (library.files_dir / "pravilnik_o_radu.pdf").exists()
    assert library.original(doc.doc_id) is None
    assert library.delete(doc.doc_id) == 0


def test_add_bytes_uses_a_safe_name(library):
    result = library.add_bytes("../../etc/Nešto čudno?.txt", b"Kantina radi do 13:30.")

    assert result.source == "Nešto čudno.txt"
    assert (library.files_dir / result.source).exists()
    assert not any(p.name == "etc" for p in library.files_dir.parents)


def test_failed_upload_leaves_no_file(library):
    with pytest.raises(DocumentLoadError):
        library.add_bytes("broken.pdf", b"not a pdf")
    with pytest.raises(UnsupportedFileTypeError):
        library.add_bytes("tabela.xls", b"x")
    with pytest.raises(DocumentLoadError, match="no text"):
        library.add_bytes("prazan.txt", b"   ")

    assert not library.files_dir.exists() or not any(library.files_dir.iterdir())


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a/b/c.pdf", "c.pdf"),
        ("..\\x.DOCX", "x.docx"),
        ("<>.txt", "document.txt"),
        ("ok (1).md", "ok (1).md"),
    ],
)
def test_safe_name(name, expected):
    assert safe_name(name) == expected


def test_language_of_sample_documents(library):
    for path in SAMPLE_DOCS.iterdir():
        library.add_file(path)

    languages = {d.source: library.language(d) for d in library.documents()}

    assert languages == {
        "bezbednost_na_radu.docx": "sr",
        "faq.txt": "sr",
        "montageanleitung_pv400.pdf": "de",
        "pravilnik_o_radu.pdf": "sr",
        "pv400_sales_training.pptx": "en",
        "quality_control_procedure.docx": "en",
        "rezervni_delovi.xlsx": "sr",
    }


def test_guess_language_is_unsure_on_little_text():
    assert guess_language("M8 20 Nm") is None


# --- viewer ------------------------------------------------------------------------------


def test_is_cited():
    passage = (
        "6. Anzugsmomente\n\nDie folgenden Anzugsmomente gelten für Schrauben.\n\nM8 20 Nm 13 mm"
    )

    assert is_cited("M8 20 Nm 13 mm", passage)
    assert is_cited("Die folgenden  Anzugsmomente gelten für Schrauben.", passage)
    assert is_cited("M8 | 20 Nm | 13 mm", passage)  # DOCX table row
    assert not is_cited("M10 40 Nm 17 mm", passage)
    assert not is_cited("x", passage)


def test_pdf_highlights_only_the_cited_blocks():
    pdf_path = SAMPLE_DOCS / "montageanleitung_pv400.pdf"
    passage = "M8 20 Nm 13 mm"

    data = pdf_with_highlights(pdf_path, 2, passage)

    with pymupdf.open("pdf", data) as pdf:
        page = pdf[1]
        found = [(a.type[1], page.get_textbox(a.rect)) for a in page.annots()]
        assert len(found) == 1
        kind, text = found[0]
        assert kind == "Highlight"
        assert "M8" in text
        assert not any(True for _ in pdf[0].annots())


def test_page_blocks_are_read_once_per_page_and_file_version(tmp_path, monkeypatch):
    """OCR of a scanned page takes ~1.5 s: a second citation on the page must not wait."""
    pdf_path = tmp_path / "manual.pdf"
    shutil.copyfile(SAMPLE_DOCS / "montageanleitung_pv400.pdf", pdf_path)
    reads = []
    original = viewer.pdf_page_blocks
    monkeypatch.setattr(viewer, "pdf_page_blocks", lambda page: reads.append(1) or original(page))

    first = pdf_with_highlights(pdf_path, 2, "M8 20 Nm 13 mm")
    again = pdf_with_highlights(pdf_path, 2, "M10 40 Nm 17 mm")  # another citation, same page
    assert len(reads) == 1
    with pymupdf.open("pdf", first) as pdf, pymupdf.open("pdf", again) as other:
        assert "M8" in pdf[1].get_textbox(next(pdf[1].annots()).rect)
        assert "M10" in other[1].get_textbox(next(other[1].annots()).rect)

    pdf_with_highlights(pdf_path, 1, "Montageanleitung")  # another page is read
    assert len(reads) == 2

    shutil.copyfile(SAMPLE_DOCS / "pravilnik_o_radu.pdf", pdf_path)  # a new file, same name
    os.utime(pdf_path, ns=(1, 1))
    pdf_with_highlights(pdf_path, 2, "odmor")
    assert len(reads) == 3  # never the blocks of the old file


def test_pdf_without_passage_is_unchanged_in_content():
    data = pdf_with_highlights(SAMPLE_DOCS / "pravilnik_o_radu.pdf", None, None)

    with pymupdf.open("pdf", data) as pdf:
        assert pdf.page_count == 3
        assert all(not list(page.annots()) for page in pdf)


def test_docx_html_keeps_structure_and_marks_citation():
    html = render_html(
        SAMPLE_DOCS / "quality_control_procedure.docx",
        2,
        "Insulation resistance | 500 V DC for 60 s | ≥ 1 MΩ",
    )

    assert html.count('class="sheet"') == 3
    assert '<h1 class="title">Quality Control Procedure</h1>' in html
    assert "<h1>5. Final inspection and routine tests</h1>" in html
    assert '<tr class="cited"><td>Insulation resistance</td>' in html
    assert html.count("cited") >= 1
    assert "scrollIntoView" in html


def test_txt_html_escapes_content(tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("Prva strana <script>alert(1)</script>\fDruga strana", encoding="utf-8")

    html = render_html(path, None, None)

    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
    assert "Strana 2 / 2" in html


def test_docx_page_break_before_matches_loader(tmp_path):
    document = docx.Document()
    document.add_heading("Naslov", level=1)
    document.add_paragraph("Prva strana.")
    document.add_paragraph("Druga strana.").paragraph_format.page_break_before = True
    path = tmp_path / "x.docx"
    document.save(str(path))

    html = render_html(path, 2, "Druga strana.")

    assert html.count('class="sheet"') == 2
    assert '<p class="cited">Druga strana.</p>' in html


def test_xlsx_html_one_sheet_per_page_marks_cited_row():
    passage = "Šifra: PV-400-MR | Naziv: Montažni okvir za duple podove | Cena (RSD): 23900"

    html = render_html(SAMPLE_DOCS / "rezervni_delovi.xlsx", 2, passage)

    assert html.count('class="sheet"') == 3
    assert "<h1>Delovi ormana</h1>" in html
    assert '<tr class="cited"><td>PV-400-MR</td>' in html
    assert html.count('<tr class="cited">') == 1


def test_pptx_html_one_slide_per_page_with_notes():
    html = render_html(
        SAMPLE_DOCS / "pv400_sales_training.pptx",
        3,
        "Do not promise discounts above 8 % without approval from the sales director.",
    )

    assert html.count('class="sheet"') == 4
    assert "<h1>Warranty and service</h1>" in html
    assert '<p class="notes cited">Do not promise discounts above 8 %' in html
    assert "<td>PV-400-M</td>" in html  # the variants table on slide 2


def test_language_is_guessed_once_per_content(library, monkeypatch):
    library.add_file(SAMPLE_DOCS / "faq.txt")
    [doc] = library.documents()
    reads = []
    real = library.store.document_chunks
    monkeypatch.setattr(library.store, "document_chunks", lambda s: reads.append(s) or real(s))

    assert library.language(doc) == library.language(doc) == "sr"
    assert reads == ["faq.txt"]  # the document list asks often; the text is read once
