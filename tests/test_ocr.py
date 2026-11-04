"""OCR (ADR-024). Unit tests run everywhere; the end-to-end ones need Tesseract installed
(CI installs it) and are skipped otherwise."""

import pymupdf
import pytest

from docassist.ingest import load_document, ocr
from docassist.ingest.loaders import pdf_page_blocks

HAS_TESSERACT = ocr.usable_languages("deu+eng") == "deu+eng"
needs_tesseract = pytest.mark.skipif(not HAS_TESSERACT, reason="Tesseract is not installed")

TEXT = [
    "6. Anzugsmomente fuer Sammelschienen",
    "Gewinde  Anzugsmoment  Schluesselweite",
    "M8  20 Nm  13 mm",
    "Kantina radi od 10:30 do 13:30.",
]


# --- cleaning ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    ["M8 20 Nm 13 mm", "Pravilnik o radu", "1. Opšte odredbe", "Viski 700ml 12420", "QP-07"],
)
def test_text_lines_are_kept_whole(line):
    assert ocr.clean_ocr_text(line) == line


@pytest.mark.parametrize("line", ["ry)", "I krYzYzYıh", "©", "wi", "| = —", ""])
def test_picture_noise_is_dropped(line):
    assert ocr.clean_ocr_text(line) == ""


# --- Tesseract output → paragraphs ---------------------------------------------------------

HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
)


def word(block, par, line, num, left, top, text, conf="95"):
    return f"5\t1\t{block}\t{par}\t{line}\t{num}\t{left}\t{top}\t40\t20\t{conf}\t{text}"


def test_words_are_grouped_into_lines_and_paragraphs_with_page_positions():
    tsv = "\n".join(
        [
            HEADER,
            "4\t1\t1\t1\t1\t0\t100\t100\t300\t20\t-1\t",  # line row: ignored
            word(1, 1, 1, 1, 100, 100, "Kantina"),
            word(1, 1, 1, 2, 150, 100, "radi"),
            word(1, 1, 2, 1, 100, 130, "od"),
            word(1, 1, 2, 2, 150, 130, "10:30"),
            word(2, 1, 1, 1, 100, 300, "M8"),
            word(2, 1, 1, 2, 150, 300, "20"),
            word(2, 1, 1, 3, 200, 300, "Nm"),
        ]
    )
    # Rendered at 144 dpi: half a PDF point per pixel.
    paragraphs = ocr._paragraphs(tsv, 0.5, pymupdf.Rect(0, 0, 595, 842), [])

    assert [text for _, text in paragraphs] == ["Kantina radi\nod 10:30", "M8 20 Nm"]
    assert paragraphs[0][0] == pymupdf.Rect(50, 50, 95, 75)


def test_lines_over_the_pages_own_text_are_not_duplicated():
    tsv = "\n".join(
        [HEADER, word(1, 1, 1, 1, 100, 100, "Kantina"), word(1, 1, 2, 1, 100, 200, "Parking")]
    )
    own_text = [pymupdf.Rect(40, 40, 120, 70)]  # covers the first line only

    paragraphs = ocr._paragraphs(tsv, 0.5, pymupdf.Rect(0, 0, 595, 842), own_text)

    assert [text for _, text in paragraphs] == ["Parking"]


# --- when OCR runs -------------------------------------------------------------------------


def text_pdf():
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "Plain text page")
    return pdf


def test_pages_without_images_are_not_sent_to_ocr(monkeypatch):
    calls = []
    monkeypatch.setattr(ocr, "_run", lambda *args: calls.append(args))
    page = text_pdf()[0]

    assert ocr.image_share(page) == 0
    assert ocr.ocr_blocks(page, ocr.OcrSettings(), []) == []
    assert calls == []


def test_ocr_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("OCR", "false")
    assert ocr.settings_from_env().enabled is False


# --- end to end with Tesseract -------------------------------------------------------------


def scanned_pdf(path):
    """A PDF whose only content is a picture of text (like a scan): no text layer."""
    source = pymupdf.open()
    page = source.new_page()
    for i, line in enumerate(TEXT):
        page.insert_text((60, 90 + 30 * i), line, fontsize=13)
    pixmap = page.get_pixmap(dpi=200, colorspace=pymupdf.csGRAY)
    scan = pymupdf.open()
    scan.new_page(width=page.rect.width, height=page.rect.height).insert_image(
        page.rect, pixmap=pixmap
    )
    scan.save(path)
    assert scan[0].get_text().strip() == ""
    return path, pixmap


@needs_tesseract
def test_scanned_pdf_is_read_with_ocr(tmp_path, monkeypatch):
    monkeypatch.setenv("OCR_LANGUAGES", "deu+eng")
    path, _ = scanned_pdf(tmp_path / "scan.pdf")

    [page] = load_document(path)

    for expected in ("Anzugsmomente", "M8 20 Nm 13 mm", "Kantina radi"):
        assert expected in page.text


@needs_tesseract
def test_ocr_blocks_have_positions_for_the_viewer(tmp_path, monkeypatch):
    monkeypatch.setenv("OCR_LANGUAGES", "deu+eng")
    path, _ = scanned_pdf(tmp_path / "scan.pdf")

    with pymupdf.open(path) as pdf:
        blocks = pdf_page_blocks(pdf[0])

    [(rect, _)] = [(r, t) for r, t in blocks if "Kantina" in t]
    assert 60 < rect.y0 < 200 and rect.x0 < 120  # where the line was drawn


@needs_tesseract
def test_image_file_is_a_one_page_document(tmp_path, monkeypatch):
    monkeypatch.setenv("OCR_LANGUAGES", "deu+eng")
    _, pixmap = scanned_pdf(tmp_path / "scan.pdf")
    image = tmp_path / "photo.png"
    pixmap.save(image)

    [page] = load_document(image)

    assert page.page_no == 1
    assert "M8 20 Nm" in page.text
