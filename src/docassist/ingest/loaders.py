"""Load PDF, DOCX and plain-text files into a list of `Page` objects.

Page numbers are what the user will see in citations, so each loader keeps them as close
to the original document as the format allows:

- PDF: real pages.
- DOCX: Word files have no stored page layout, so a "page" is the text between explicit
  page breaks (hard page break or "page break before" on a paragraph). See ADR-010.
- TXT/MD: the whole file is page 1, unless it contains form feeds (``\\f``), which are
  treated as page breaks.

Blank pages are skipped, but numbering is never shifted.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from pathlib import Path

import docx
import pymupdf
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph

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


def load_pdf(path: Path) -> list[str]:
    with pymupdf.open(path) as pdf:
        if pdf.needs_pass:
            raise DocumentLoadError(f"{path.name} is password-protected")
        return [_pdf_page_text(page) for page in pdf]


def _pdf_page_text(page: pymupdf.Page) -> str:
    """Page text with one paragraph per line, paragraphs separated by a blank line.

    PDFs store visual lines, so a sentence is often cut by a line wrap. PyMuPDF groups lines
    into blocks (paragraphs, table rows, list items); joining the lines of a block gives
    whole sentences and keeps a table row like "M8 20 Nm 13 mm" on one line.
    """
    blocks = page.get_text("blocks", flags=_PDF_TEXT_FLAGS, sort=False)
    paragraphs = []
    for *_, text, _block_no, block_type in blocks:
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
            paragraphs.append(paragraph)
    return "\n\n".join(paragraphs)


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


_LOADERS: dict[str, Callable[[Path], list[str]]] = {
    ".pdf": load_pdf,
    ".docx": load_docx,
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
