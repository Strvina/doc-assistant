import datetime as dt
from pathlib import Path

import docx
import openpyxl
import pptx
import pymupdf
import pytest
from docx.enum.text import WD_BREAK
from pptx.util import Inches

from docassist.ingest import (
    SUPPORTED_EXTENSIONS,
    DocumentLoadError,
    UnsupportedFileTypeError,
    load_document,
)
from docassist.models import Page

SAMPLE_DOCS = Path(__file__).resolve().parent.parent / "data" / "sample_docs"


def make_pdf(path: Path, page_texts: list[str], **save_options) -> Path:
    with pymupdf.open() as pdf:
        for text in page_texts:
            page = pdf.new_page()
            if text:
                page.insert_htmlbox(pymupdf.Rect(50, 50, 550, 800), text)
        pdf.save(path, **save_options)
    return path


# --- PDF ---------------------------------------------------------------------------------


def test_pdf_returns_one_page_per_pdf_page(tmp_path):
    path = make_pdf(tmp_path / "manual.pdf", ["First page", "Second page"])

    pages = load_document(path)

    assert pages == [
        Page(source="manual.pdf", page_no=1, text="First page"),
        Page(source="manual.pdf", page_no=2, text="Second page"),
    ]


def test_pdf_skips_blank_pages_but_keeps_numbering(tmp_path):
    path = make_pdf(tmp_path / "doc.pdf", ["Intro", "", "Appendix"])

    pages = load_document(path)

    assert [(p.page_no, p.text) for p in pages] == [(1, "Intro"), (3, "Appendix")]


def test_pdf_keeps_serbian_and_german_characters(tmp_path):
    text = "Čačak, Đorđe, šuma, žaba, ćup — Größe, Übergabe, Maß"
    path = make_pdf(tmp_path / "chars.pdf", [text])

    assert load_document(path)[0].text == text


def test_pdf_joins_wrapped_lines_into_paragraphs(tmp_path):
    long_sentence = " ".join(["Schaltschrank"] * 60) + "."
    html = (
        f"<p>{long_sentence}</p><p>Second paragraph.</p>"
        "<ul><li>first item</li><li>second item</li></ul>"
    )
    path = make_pdf(tmp_path / "wrap.pdf", [html])

    paragraphs = load_document(path)[0].text.split("\n\n")

    assert paragraphs[0] == long_sentence  # several visual lines → one line
    assert paragraphs[1] == "Second paragraph."
    assert paragraphs[2].splitlines() == ["• first item", "• second item"]


def test_password_protected_pdf_raises(tmp_path):
    path = make_pdf(
        tmp_path / "secret.pdf",
        ["Top secret"],
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="user",
        owner_pw="owner",
    )

    with pytest.raises(DocumentLoadError, match="password"):
        load_document(path)


def test_corrupted_pdf_raises_document_load_error(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"this is not a pdf")

    with pytest.raises(DocumentLoadError, match=r"broken\.pdf"):
        load_document(path)


# --- DOCX --------------------------------------------------------------------------------


def test_docx_splits_pages_on_hard_page_breaks(tmp_path):
    document = docx.Document()
    document.add_heading("Safety rules", level=1)
    document.add_paragraph("Wear safety shoes.")
    document.add_page_break()
    document.add_paragraph("Report every injury.")
    path = tmp_path / "rules.docx"
    document.save(str(path))

    pages = load_document(path)

    assert [(p.page_no, p.text) for p in pages] == [
        (1, "Safety rules\nWear safety shoes."),
        (2, "Report every injury."),
    ]


def test_docx_page_break_inside_paragraph(tmp_path):
    document = docx.Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("before")
    paragraph.add_run().add_break(WD_BREAK.PAGE)
    paragraph.add_run("after")
    path = tmp_path / "inline.docx"
    document.save(str(path))

    pages = load_document(path)

    assert [(p.page_no, p.text) for p in pages] == [(1, "before"), (2, "after")]


def test_docx_page_break_before(tmp_path):
    document = docx.Document()
    document.add_paragraph("Starts on page one").paragraph_format.page_break_before = True
    document.add_paragraph("Chapter 1 text")
    document.add_paragraph("Chapter 2").paragraph_format.page_break_before = True
    path = tmp_path / "chapters.docx"
    document.save(str(path))

    pages = load_document(path)

    # The first paragraph already starts page 1, so it must not create an empty page.
    assert [(p.page_no, p.text) for p in pages] == [
        (1, "Starts on page one\nChapter 1 text"),
        (2, "Chapter 2"),
    ]


def test_docx_line_breaks_and_tabs(tmp_path):
    document = docx.Document()
    paragraph = document.add_paragraph()
    run = paragraph.add_run("Name:\tAna")
    run.add_break()
    paragraph.add_run("Role:\tQuality Manager")
    path = tmp_path / "breaks.docx"
    document.save(str(path))

    assert load_document(path)[0].text == "Name:\tAna\nRole:\tQuality Manager"


def test_docx_tables_in_document_order(tmp_path):
    document = docx.Document()
    document.add_paragraph("Torque values:")
    rows = [("Thread", "Torque"), ("M8", "20 Nm"), ("Yes", "Yes"), ("", "")]
    table = document.add_table(rows=len(rows), cols=2)
    for row, values in zip(table.rows, rows, strict=True):
        for cell, value in zip(row.cells, values, strict=True):
            cell.text = value
    merged = table.cell(3, 0).merge(table.cell(3, 1))
    merged.text = "Bolts class 8.8"
    document.add_paragraph("End of table.")
    path = tmp_path / "table.docx"
    document.save(str(path))

    text = load_document(path)[0].text

    # Equal neighbouring cells stay; a merged cell spanning two columns appears once.
    assert text == (
        "Torque values:\nThread | Torque\nM8 | 20 Nm\nYes | Yes\nBolts class 8.8\nEnd of table."
    )


# --- TXT / MD ----------------------------------------------------------------------------


def test_txt_is_a_single_page(tmp_path):
    path = tmp_path / "faq.txt"
    path.write_text("Pitanje?\nOdgovor.\n", encoding="utf-8")

    assert load_document(path) == [Page(source="faq.txt", page_no=1, text="Pitanje?\nOdgovor.")]


def test_txt_form_feed_starts_new_page(tmp_path):
    path = tmp_path / "pages.txt"
    path.write_text("one\fthree is next\f\fthree", encoding="utf-8")

    pages = load_document(path)

    assert [(p.page_no, p.text) for p in pages] == [(1, "one"), (2, "three is next"), (4, "three")]


def test_txt_utf8_with_bom(tmp_path):
    path = tmp_path / "bom.txt"
    path.write_bytes("﻿Šifra za Wi-Fi".encode())

    assert load_document(path)[0].text == "Šifra za Wi-Fi"


def test_txt_falls_back_to_windows_1250(tmp_path):
    path = tmp_path / "old.txt"
    path.write_bytes("Godišnji odmor: 20 dana, ćao, đak".encode("cp1250"))

    assert load_document(path)[0].text == "Godišnji odmor: 20 dana, ćao, đak"


def test_markdown_is_loaded_as_text(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("# Title\n\nBody", encoding="utf-8")

    assert load_document(path)[0].text == "# Title\n\nBody"


def test_empty_file_returns_no_pages(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("  \n\n ", encoding="utf-8")

    assert load_document(path) == []


def test_text_is_normalized(tmp_path):
    path = tmp_path / "messy.txt"
    decomposed = "Živko"  # "Ž" as Z + combining caron
    path.write_bytes(f"{decomposed} Ilic  \r\n\r\n\r\n\r\nnext­line\r\n".encode())

    assert load_document(path)[0].text == "Živko Ilic\n\nnextline"


# --- XLSX --------------------------------------------------------------------------------


def make_xlsx(path: Path, sheets: dict[str, list[list]], hidden: tuple[str, ...] = ()) -> Path:
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(row)
        if name in hidden:
            sheet.sheet_state = "hidden"
    workbook.save(path)
    return path


def test_xlsx_one_page_per_sheet_rows_name_their_columns(tmp_path):
    path = make_xlsx(
        tmp_path / "parts.xlsx",
        {
            "Prices": [["Code", "Name", "Price"], ["A-1", "Breaker", 1250], ["B-2", None, 9.5]],
            "Notes": [["Prices without VAT."], [], ["Call 230."]],
        },
    )

    pages = load_document(path)

    assert [p.page_no for p in pages] == [1, 2]
    assert pages[0].text == (
        "Prices\n"
        "Code | Name | Price\n"
        "Code: A-1 | Name: Breaker | Price: 1250\n"
        "Code: B-2 | Price: 9.5"
    )
    assert pages[1].text == "Notes\nPrices without VAT.\nCall 230."  # no header: one column


def test_xlsx_numbers_dates_and_hidden_sheets(tmp_path):
    path = make_xlsx(
        tmp_path / "x.xlsx",
        {
            "Data": [[2026, 3.0, dt.datetime(2026, 1, 15), "two\nlines", None, None]],
            "Helper": [["secret"]],
        },
        hidden=("Helper",),
    )

    pages = load_document(path)

    assert [(p.page_no, p.text) for p in pages] == [(1, "Data\n2026 | 3 | 2026-01-15 | two lines")]


def test_xlsx_empty_sheet_keeps_numbering(tmp_path):
    path = make_xlsx(tmp_path / "x.xlsx", {"Empty": [], "Full": [["value"]]})

    assert [(p.page_no, p.text) for p in load_document(path)] == [(2, "Full\nvalue")]


# --- PPTX --------------------------------------------------------------------------------


def make_pptx(path: Path) -> Path:
    deck = pptx.Presentation()
    title_slide = deck.slides.add_slide(deck.slide_layouts[0])
    title_slide.shapes.title.text = "Training"
    title_slide.placeholders[1].text = "Sales team"

    slide = deck.slides.add_slide(deck.slide_layouts[5])  # title only
    slide.shapes.title.text = "Prices"
    lower = slide.shapes.add_textbox(Inches(1), Inches(5), Inches(6), Inches(1))
    lower.text_frame.text = "Below the table"
    upper = slide.shapes.add_textbox(Inches(1), Inches(1.5), Inches(6), Inches(1))
    upper.text_frame.text = "First line"
    upper.text_frame.add_paragraph().text = "Second line"
    table = slide.shapes.add_table(2, 2, Inches(1), Inches(3), Inches(6), Inches(1)).table
    for r, row in enumerate([["Item", "Price"], ["S", "1,950"]]):
        for c, value in enumerate(row):
            table.cell(r, c).text = value
    slide.notes_slide.notes_text_frame.text = "Max discount 8 %."

    deck.slides.add_slide(deck.slide_layouts[6])  # blank slide
    deck.save(str(path))
    return path


def test_pptx_one_page_per_slide_in_reading_order_with_notes(tmp_path):
    pages = load_document(make_pptx(tmp_path / "deck.pptx"))

    assert [p.page_no for p in pages] == [1, 2]  # the blank 3rd slide is skipped
    assert pages[0].text == "Training\nSales team"
    assert pages[1].text == (
        "Training\n"  # the deck's title, so the slide is found by what the deck is about
        "Prices\n"
        "First line\n"
        "Second line\n"
        "Item | Price\n"
        "S | 1,950\n"
        "Below the table\n"
        "Notes: Max discount 8 %."
    )


# --- dispatch & errors -------------------------------------------------------------------


def test_supported_extensions():
    assert set(SUPPORTED_EXTENSIONS) == {
        ".pdf",
        ".docx",
        ".xlsx",
        ".pptx",
        ".txt",
        ".md",
        ".png",
        ".jpg",
        ".jpeg",
        ".tif",
        ".tiff",
        ".bmp",
    }


def test_extension_is_case_insensitive(tmp_path):
    path = tmp_path / "LOUD.TXT"
    path.write_text("hello", encoding="utf-8")

    assert load_document(path)[0].text == "hello"


@pytest.mark.parametrize("name", ["old.xls", "old.doc", "no_extension"])
def test_unsupported_file_type(tmp_path, name):
    path = tmp_path / name
    path.write_text("data", encoding="utf-8")

    with pytest.raises(UnsupportedFileTypeError):
        load_document(path)


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_document(tmp_path / "missing.pdf")


def test_accepts_string_path(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("text", encoding="utf-8")

    assert load_document(str(path))[0].source == "a.txt"


# --- demo documents ----------------------------------------------------------------------

SAMPLE_FILES = sorted(p.name for p in SAMPLE_DOCS.iterdir())


def test_sample_docs_cover_all_formats():
    assert {Path(name).suffix for name in SAMPLE_FILES} == {
        ".pdf",
        ".docx",
        ".xlsx",
        ".pptx",
        ".txt",
    }


@pytest.mark.parametrize("name", SAMPLE_FILES)
def test_sample_docs_load(name):
    pages = load_document(SAMPLE_DOCS / name)

    assert pages, f"{name} produced no text"
    assert all(page.source == name for page in pages)


@pytest.mark.parametrize(
    ("name", "page_no", "fact"),
    [
        ("pravilnik_o_radu.pdf", 2, "najmanje 20 radnih dana"),
        ("montageanleitung_pv400.pdf", 2, "M8 20 Nm 13 mm"),
        ("quality_control_procedure.docx", 2, "500 V DC for 60 s | ≥ 1 MΩ"),
        ("bezbednost_na_radu.docx", 3, "Hitnu pomoć na broj 194"),
        ("faq.txt", 1, "Kantina radi od 10:30 do 13:30"),
        ("rezervni_delovi.xlsx", 2, "Šifra: PV-400-MR | Naziv: Montažni okvir"),
        ("pv400_sales_training.pptx", 3, "Notes: Do not promise discounts above 8 %"),
    ],
)
def test_sample_docs_facts_are_on_expected_page(name, page_no, fact):
    pages = {page.page_no: page.text for page in load_document(SAMPLE_DOCS / name)}

    assert fact in pages[page_no]
