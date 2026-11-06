"""OCR (ADR-024, ADR-030). Unit tests run everywhere; the end-to-end ones need Tesseract installed
(CI installs it) and are skipped otherwise."""

from types import SimpleNamespace

import pymupdf
import pytest

from docassist.ingest import load_document, ocr
from docassist.ingest.loaders import _paragraph_blocks, joins_cut_word, pdf_page_blocks

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


# --- parallel pages and progress -----------------------------------------------------------


def test_jobs_run_in_parallel_and_report_progress(monkeypatch):
    seen = []
    monkeypatch.setattr(ocr, "run", lambda job: [(pymupdf.Rect(0, 0, 1, 1), job.languages)])
    jobs = {
        index: ocr.OcrJob(b"", 1.0, pymupdf.Rect(0, 0, 10, 10), (), "tesseract", f"p{index}")
        for index in (0, 3, 7)
    }

    results = ocr.run_jobs(jobs, lambda done, total: seen.append((done, total)))

    assert {index: blocks[0][1] for index, blocks in results.items()} == {
        0: "p0",
        3: "p3",
        7: "p7",
    }
    assert seen == [(1, 3), (2, 3), (3, 3)]
    assert ocr.run_jobs({}) == {}


@needs_tesseract
def test_multi_page_scan_reports_progress_while_reading(tmp_path, monkeypatch):
    from docassist.ingest import loaders

    monkeypatch.setenv("OCR_LANGUAGES", "deu+eng")
    path, pixmap = scanned_pdf(tmp_path / "one.pdf")
    scan = pymupdf.open()
    for _ in range(3):
        scan.new_page(width=595, height=842).insert_image(
            pymupdf.Rect(0, 0, 595, 842), pixmap=pixmap
        )
    scan.save(tmp_path / "three.pdf")
    progress = []
    original = ocr.run_jobs
    monkeypatch.setattr(
        ocr,
        "run_jobs",
        lambda jobs, on_done=None: original(
            jobs, lambda d, t: (on_done(d, t), progress.append(dict(loaders.PROGRESS)))
        ),
    )

    pages = load_document(tmp_path / "three.pdf")

    assert [p.page_no for p in pages] == [1, 2, 3]
    assert all("M8 20 Nm" in p.text for p in pages)
    assert progress[-1] == {"three.pdf": (3, 3)}
    assert "three.pdf" not in loaders.PROGRESS  # cleared when done


# --- Latin pages read again without Cyrillic (ADR-030) -------------------------------------


@pytest.mark.parametrize(
    ("languages", "expected"),
    [
        ("srp_latn+srp+deu+eng", "srp_latn+deu+eng"),
        ("srp_latn+eng", None),  # nothing to leave out
        ("srp", None),  # nothing would be left
    ],
)
def test_latin_languages(languages, expected):
    assert ocr.latin_languages(languages) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Aktuelni zakon о zaštiti stanovništva од zaraznih bolesti је donet 2016.", True),
        ("Aktuelni zakon o zaštiti stanovništva od zaraznih bolesti je donet 2016.", False),
        ("Правилник о раду, члан 12: годишњи одмор траје 20 дана.", False),  # Cyrillic page
        ("", False),
    ],
)
def test_has_stray_cyrillic(text, expected):
    assert ocr.has_stray_cyrillic(text) is expected


def fake_tesseract(monkeypatch, read):
    """`read(languages)` gives the words Tesseract "reads"; returns the languages asked for."""
    asked = []

    def run(args, _data):
        languages = args[args.index("-l") + 1]
        asked.append(languages)
        words = [word(1, 1, 1, i, 100 + 50 * i, 100, w) for i, w in enumerate(read(languages))]
        return SimpleNamespace(returncode=0, stdout="\n".join([HEADER, *words]).encode())

    monkeypatch.setattr(ocr, "_run", run)
    return asked


def job(languages="srp_latn+srp+eng"):
    return ocr.OcrJob(b"", 0.5, pymupdf.Rect(0, 0, 595, 842), (), "tesseract", languages)


def test_a_latin_page_with_cyrillic_look_alikes_is_read_again_without_cyrillic(monkeypatch):
    def read(languages):
        return ["Zakon", "је", "donet"] if "srp+" in languages else ["Zakon", "je", "donet"]

    asked = fake_tesseract(monkeypatch, read)

    assert [text for _, text in ocr.run(job())] == ["Zakon je donet"]
    assert asked == ["srp_latn+srp+eng", "srp_latn+eng"]


@pytest.mark.parametrize("words", [["Zakon", "je", "donet"], ["Закон", "је", "донет"]])
def test_latin_and_cyrillic_pages_are_read_once(monkeypatch, words):
    asked = fake_tesseract(monkeypatch, lambda _: words)

    assert [text for _, text in ocr.run(job())] == [" ".join(words)]
    assert asked == ["srp_latn+srp+eng"]


# --- words cut at a line end in OCR text (ADR-030) -----------------------------------------


@pytest.mark.parametrize(
    ("paragraph", "line", "joined"),
    [
        ("antraks, botuli-", "zam, kampilobakterioza", True),
        ("vektorske bole-", "sti", True),
        ("Sicherheits-", "hinweise beachten", True),
        ("mere sanitarno-", "higijenske zaštite", False),  # compound
        ("vakcinacija polio-", "vakcinom", False),
        ("mindestens 1000 mm Bedien-", "und Wartungsgang", False),  # shared ending
        ("hemoragijska groz-", "Nica", False),  # next line starts with a capital
        ("broj 12-", "15", False),
        ("Q-", "groznica", False),
    ],
)
def test_words_cut_at_a_line_end_are_joined(paragraph, line, joined):
    assert joins_cut_word(paragraph, line) is joined


def test_only_ocr_blocks_are_dehyphenated():
    rect = (0, 0, 100, 20)
    ocr_block = (*rect, "infekcije uzrokovane bole-\nsti", -1, 0)
    pdf_block = (*rect, "Bedien-\nund", 3, 0)

    assert [t for _, t in _paragraph_blocks([ocr_block, pdf_block])] == [
        "infekcije uzrokovane bolesti",
        "Bedien-und",
    ]
