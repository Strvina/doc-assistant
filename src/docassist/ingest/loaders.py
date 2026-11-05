"""Load PDF, DOCX, XLSX, PPTX and plain-text files into a list of `Page` objects.

Page numbers are what the user will see in citations, so each loader keeps them as close
to the original document as the format allows:

- PDF: real pages. Text in images (scans, screenshots) is read with OCR (ADR-024).
- Images (PNG, JPG, TIFF, BMP): one page per image (several for a multi-page TIFF), via OCR.
- DOCX: Word files have no stored page layout, so a "page" is the text between explicit
  page breaks (hard page break or "page break before" on a paragraph). See ADR-010.
- XLSX: one page per visible worksheet, in workbook order (ADR-028).
- PPTX: one page per slide, speaker notes included (ADR-028).
- TXT/MD: the whole file is page 1, unless it contains form feeds (``\\f``), which are
  treated as page breaks.

Blank pages are skipped, but numbering is never shifted.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import docx
import openpyxl
import pptx
import pymupdf
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph
from pptx.enum.shapes import MSO_SHAPE_TYPE

from docassist.ingest import ocr
from docassist.models import Page


class DocumentLoadError(Exception):
    """A file could not be turned into text."""


class UnsupportedFileTypeError(DocumentLoadError):
    """The file extension is not one we know how to read."""


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W_T = f"{{{_W_NS}}}t"
_W_TAB = f"{{{_W_NS}}}tab"
_W_BR = f"{{{_W_NS}}}br"
_W_CR = f"{{{_W_NS}}}cr"
_W_TYPE = f"{{{_W_NS}}}type"

# Expand ligatures (ﬁ → fi) and join words hyphenated at line ends, so text is searchable.
_PDF_TEXT_FLAGS = (pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_LIGATURES) | (
    pymupdf.TEXT_DEHYPHENATE
)

# Damaged PDFs make MuPDF print the same repair message for every page; they still load.
pymupdf.TOOLS.mupdf_display_errors(False)

_MULTI_BLANK_LINES = re.compile(r"\n{3,}")
_BULLETS = frozenset("•◦▪▫●○■□‣⁃")


def load_document(path: str | Path) -> list[Page]:
    """Load a supported document and return its non-blank pages."""
    path = Path(path)
    loader = _LOADERS.get(path.suffix.lower())
    if loader is None:
        supported = ", ".join(SUPPORTED_EXTENSIONS)
        raise UnsupportedFileTypeError(
            f"Unsupported file type {path.suffix!r} for {path.name}; supported: {supported}"
        )
    if not path.is_file():
        raise FileNotFoundError(path)

    try:
        raw_pages = loader(path)
    except DocumentLoadError:
        raise
    except Exception as exc:  # parser libraries raise many different error types
        raise DocumentLoadError(f"Could not read {path.name}: {exc}") from exc

    return _to_pages(path.name, raw_pages)


IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")


def open_pdf(path: Path) -> pymupdf.Document:
    """Open a PDF, or an image converted to a PDF, so both are read and shown the same way."""
    # Open from memory: on Windows a failed open from disk keeps the file locked.
    data = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        with pymupdf.open(stream=data, filetype=suffix.lstrip(".")) as image:
            data = image.convert_to_pdf()
    return pymupdf.open(stream=data, filetype="pdf")


# Documents being read right now, for the UI: file name -> (pages read by OCR, pages to read).
PROGRESS: dict[str, tuple[int, int]] = {}


def load_pdf(path: Path) -> list[str]:
    """Page texts with one paragraph per line, paragraphs separated by a blank line.

    PDFs store visual lines, so a sentence is often cut by a line wrap. PyMuPDF groups lines
    into blocks (paragraphs, table rows, list items); joining the lines of a block gives
    whole sentences and keeps a table row like "M8 20 Nm 13 mm" on one line.

    Pages that need OCR are rendered one by one, then read by Tesseract in parallel (a
    96-page scan: ~4 s per page one at a time); `PROGRESS` shows how far it is.
    """
    settings = ocr.settings_from_env()
    natives, jobs = [], {}
    with open_pdf(path) as pdf:
        if pdf.needs_pass:
            raise DocumentLoadError(f"{path.name} is password-protected")
        for index, page in enumerate(pdf):
            blocks = _native_blocks(page)
            natives.append(blocks)
            job = ocr.prepare(page, settings, _text_rects(blocks))
            if job is not None:
                jobs[index] = job
    if jobs:
        PROGRESS[path.name] = (0, len(jobs))
    try:
        found = ocr.run_jobs(jobs, lambda done, total: PROGRESS.update({path.name: (done, total)}))
    finally:
        PROGRESS.pop(path.name, None)
    return [
        "\n\n".join(text for _rect, text in _paragraph_blocks(_merge(blocks, found.get(i, []))))
        for i, blocks in enumerate(natives)
    ]


def pdf_page_blocks(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, str]]:
    """Text blocks of a PDF page with their position, lines joined as in the loaded text.

    Text in the page's images is added with OCR when images cover enough of the page; OCR
    blocks are cleaned of picture noise. The viewer uses the same blocks to highlight a cited
    passage on the original page, so it finds OCR text there too.
    """
    blocks = _native_blocks(page)
    found = ocr.ocr_blocks(page, ocr.settings_from_env(), _text_rects(blocks))
    return _paragraph_blocks(_merge(blocks, found))


def _native_blocks(page: pymupdf.Page) -> list[tuple]:
    return page.get_text("blocks", flags=_PDF_TEXT_FLAGS, sort=False)


def _text_rects(blocks: list[tuple]) -> list[pymupdf.Rect]:
    return [pymupdf.Rect(b[:4]) for b in blocks if b[6] == 0 and b[4].strip()]


def _merge(blocks: list[tuple], found: list[tuple[pymupdf.Rect, str]]) -> list[tuple]:
    """Add OCR paragraphs to the page's own text blocks, in reading order."""
    if not found:
        return blocks
    merged = [b for b in blocks if b[6] == 0]
    merged += [(*rect, text, -1, 0) for rect, text in found]
    merged.sort(key=lambda b: (round(b[1]), b[0]))  # top to bottom, left to right
    return merged


def _paragraph_blocks(blocks: list[tuple]) -> list[tuple[pymupdf.Rect, str]]:
    result = []
    for x0, y0, x1, y1, text, _block_no, block_type in blocks:
        if block_type != 0:  # 1 = image block
            continue
        paragraph = ""
        for line in text.split("\n"):
            line = " ".join(line.split())
            if not line:
                continue
            if paragraph and line[0] in _BULLETS:  # each list item on its own line
                paragraph += "\n"
            elif paragraph and not paragraph.endswith("-"):
                paragraph += " "
            paragraph += line
        if paragraph:
            result.append((pymupdf.Rect(x0, y0, x1, y1), paragraph))
    return result


def load_docx(path: Path) -> list[str]:
    document = docx.Document(str(path))
    pages: list[list[str]] = [[]]

    for block in document.iter_inner_content():
        if isinstance(block, Table):
            pages[-1].append(_table_text(block))
        else:
            _add_paragraph(block, pages)

    return ["\n".join(lines) for lines in pages]


def load_text(path: Path) -> list[str]:
    data = path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Older Serbian/Central European Windows files are usually Windows-1250.
        text = data.decode("cp1250", errors="replace")
    return text.split("\f")


# --- XLSX ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Sheet:
    name: str
    rows: list[tuple[str, ...]]  # non-empty rows, trailing empty cells removed

    @property
    def header(self) -> tuple[str, ...] | None:
        """The first row when it names the columns: two or more cells, none of them a number."""
        if len(self.rows) < 2:
            return None
        first = self.rows[0]
        filled = [cell for cell in first if cell]
        if len(filled) < 2 or any(_NUMBER.fullmatch(cell) for cell in filled):
            return None
        return first

    def row_text(self, index: int) -> str:
        """A row as it is indexed: "Column: value | Column: value" under a header row.

        Every row names its columns, so a row is still understood when a long sheet is split
        into many chunks far away from the header.
        """
        row, header = self.rows[index], self.header
        if header is None or index == 0:
            return " | ".join(cell for cell in row if cell)
        parts = []
        for i, cell in enumerate(row):
            if not cell:
                continue
            name = header[i] if i < len(header) else ""
            parts.append(f"{name}: {cell}" if name else cell)
        return " | ".join(parts)

    @property
    def text(self) -> str:
        lines = [self.name, *(self.row_text(i) for i in range(len(self.rows)))]
        return "\n".join(lines) if self.rows else ""


_NUMBER = re.compile(r"[-+]?[\d.,\s]+%?")


def xlsx_sheets(path: Path) -> list[Sheet]:
    """Visible worksheets with the values Excel last calculated (not the formulas)."""
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheets = []
        for worksheet in workbook.worksheets:
            if worksheet.sheet_state != "visible":
                continue
            rows = []
            for values in worksheet.iter_rows(values_only=True):
                cells = [_cell_text(value) for value in values]
                while cells and not cells[-1]:
                    cells.pop()
                if cells:
                    rows.append(tuple(cells))
            sheets.append(Sheet(worksheet.title, rows))
        return sheets
    finally:
        workbook.close()


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{value:.12g}"
    if isinstance(value, dt.datetime):
        if value.time() == dt.time():
            return value.date().isoformat()
        return value.isoformat(sep=" ", timespec="minutes")
    if isinstance(value, dt.date | dt.time):
        return value.isoformat()
    return " ".join(str(value).split())  # line breaks inside a cell


def load_xlsx(path: Path) -> list[str]:
    return [sheet.text for sheet in xlsx_sheets(path)]


# --- PPTX ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class SlideItem:
    kind: str  # title | p | table | notes
    text: str = ""
    rows: tuple[tuple[str, ...], ...] = ()

    @property
    def plain(self) -> str:
        if self.kind == "table":
            return "\n".join(" | ".join(row) for row in self.rows)
        return self.text


def pptx_slides(path: Path) -> list[list[SlideItem]]:
    """The text of each slide: title, then text boxes and tables top to bottom, then notes."""
    presentation = pptx.Presentation(str(path))
    slides = []
    for slide in presentation.slides:
        title = slide.shapes.title
        items: list[SlideItem] = []
        if title is not None and title.text_frame.text.strip():
            items.append(SlideItem("title", " ".join(title.text_frame.text.split())))
        for shape in _shapes_in_reading_order(slide.shapes):
            if title is not None and shape.shape_id == title.shape_id:
                continue
            if shape.has_table:
                rows = tuple(
                    tuple(" ".join(cell.text.split()) for cell in row.cells)
                    for row in shape.table.rows
                )
                rows = tuple(row for row in rows if any(row))
                if rows:
                    items.append(SlideItem("table", rows=rows))
            elif shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    text = paragraph.text.replace("\v", "\n").strip()
                    if text:
                        items.append(SlideItem("p", text))
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame
            if notes is not None and notes.text.strip():
                items.append(SlideItem("notes", notes.text.strip()))
        slides.append(items)
    return slides


def _shapes_in_reading_order(shapes) -> Iterator:
    def position(shape) -> tuple[int, int]:
        return (shape.top or 0, shape.left or 0)

    for shape in sorted(shapes, key=position):
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _shapes_in_reading_order(shape.shapes)
        else:
            yield shape


def load_pptx(path: Path) -> list[str]:
    """Slide texts; every slide after the first starts with the presentation's title.

    A slide rarely repeats what the deck is about ("Warranty and service" — of which
    product?), so without the title its chunks are not found for a question that names it.
    """
    slides = pptx_slides(path)
    deck = next((i.text for items in slides for i in items if i.kind == "title"), "")

    def text(item: SlideItem) -> str:
        return f"Notes: {item.text}" if item.kind == "notes" else item.plain

    pages = []
    for items in slides:
        lines = [text(item) for item in items]
        if lines and deck and not any(i.kind == "title" and i.text == deck for i in items):
            lines.insert(0, deck)
        pages.append("\n".join(lines))
    return pages


_LOADERS: dict[str, Callable[[Path], list[str]]] = {
    ".pdf": load_pdf,
    **dict.fromkeys(IMAGE_EXTENSIONS, load_pdf),
    ".docx": load_docx,
    ".xlsx": load_xlsx,
    ".pptx": load_pptx,
    ".txt": load_text,
    ".md": load_text,
}

SUPPORTED_EXTENSIONS: tuple[str, ...] = tuple(_LOADERS)


def _add_paragraph(paragraph: Paragraph, pages: list[list[str]]) -> None:
    """Append a paragraph's text to `pages`, starting new pages at explicit breaks."""
    # Like Word, ignore "page break before" when the paragraph already starts a page.
    if paragraph.paragraph_format.page_break_before and any(s.strip() for s in pages[-1]):
        pages.append([])

    parts: list[str] = []
    for element in paragraph._p.iter(_W_T, _W_TAB, _W_BR, _W_CR):
        if element.tag == _W_T:
            parts.append(element.text or "")
        elif element.tag == _W_TAB:
            parts.append("\t")
        elif element.tag == _W_BR and element.get(_W_TYPE) == "page":
            pages[-1].append("".join(parts))
            pages.append([])
            parts = []
        else:  # line break, column break, carriage return
            parts.append("\n")

    pages[-1].append("".join(parts))


def _table_text(table: Table) -> str:
    rows = []
    for row in table.rows:
        cells = (cell.text.strip() for cell in _unique_cells(row.cells))
        rows.append(" | ".join(cells))
    return "\n".join(rows)


def _unique_cells(cells: Iterable[_Cell]) -> list[_Cell]:
    # python-docx repeats the same cell for every grid column a merged cell spans.
    result: list[_Cell] = []
    for cell in cells:
        if not result or cell._tc is not result[-1]._tc:
            result.append(cell)
    return result


def _to_pages(source: str, raw_pages: list[str]) -> list[Page]:
    pages = []
    for page_no, raw in enumerate(raw_pages, start=1):
        text = _clean(raw)
        if text:
            pages.append(Page(source=source, page_no=page_no, text=text))
    return pages


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace(" ", " ").replace("­", "")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _MULTI_BLANK_LINES.sub("\n\n", text)
    return text.strip()
