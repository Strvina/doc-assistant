"""Export a saved conversation as a PDF or Word file (ADR-027).

Answers are Markdown, as the web UI shows them (`renderAnswerText` in app.js): headings,
bullet and numbered lists, tables, **bold** and `code`. They are parsed once into blocks and
written by two small renderers: python-docx for Word, and PyMuPDF's HTML layout (`Story`) for
PDF — both libraries are already used to read documents, so export needs no new dependency.

The file is in Serbian, like the UI: the question, the answer with its inline citations
("[Pravilnik.pdf, str. 3]"), and the cited passages under "Izvori".
"""

from __future__ import annotations

import html
import io
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pymupdf
from docx import Document
from docx.shared import Pt, RGBColor

from docassist.conversations import fold

FORMATS = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
QUOTE_LENGTH = 300  # characters of each cited passage under "Izvori"

# --- Markdown → blocks -------------------------------------------------------------------


@dataclass
class Heading:
    text: str


@dataclass
class Paragraph:
    text: str


@dataclass
class ListBlock:
    ordered: bool
    items: list[str] = field(default_factory=list)


@dataclass
class Table:
    head: list[str] | None = None
    rows: list[list[str]] = field(default_factory=list)

    @property
    def width(self) -> int:
        return max(len(r) for r in [self.head or [], *self.rows])


Block = Heading | Paragraph | ListBlock | Table

_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_RULE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")
_HEADING = re.compile(r"^\s*#{1,4}\s+(.*)$")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*)$")
_INLINE = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`")


def parse_markdown(text: str) -> list[Block]:
    """The Markdown subset models use in answers, line by line like the web UI."""
    blocks: list[Block] = []
    current_list: ListBlock | None = None
    table: Table | None = None
    for raw in text.split("\n"):
        line = raw.rstrip()
        if _TABLE_ROW.match(line):
            current_list = None
            if _TABLE_RULE.match(line) and "-" in line:  # |---|:---:| under the header row
                if table and len(table.rows) == 1 and table.head is None:
                    table.head = table.rows.pop()
                continue
            if table is None:
                table = Table()
                blocks.append(table)
            table.rows.append([c.strip() for c in line.strip().strip("|").split("|")])
            continue
        table = None
        if heading := _HEADING.match(line):
            current_list = None
            blocks.append(Heading(re.sub(r"\s*#+\s*$", "", heading[1])))
            continue
        if bullet := _BULLET.match(line):
            ordered = line.lstrip()[0].isdigit()
            if current_list is None or current_list.ordered != ordered:
                current_list = ListBlock(ordered)
                blocks.append(current_list)
            current_list.items.append(bullet[1])
            continue
        current_list = None
        if line.strip():
            blocks.append(Paragraph(line.strip()))
    return blocks


def inline_runs(text: str) -> list[tuple[str, str]]:
    """Split a line into (style, text) runs; style is "", "bold" or "code"."""
    runs: list[tuple[str, str]] = []
    position = 0
    for match in _INLINE.finditer(text):
        if match.start() > position:
            runs.append(("", text[position : match.start()]))
        runs.append(("bold", match[1]) if match[1] is not None else ("code", match[2]))
        position = match.end()
    if position < len(text):
        runs.append(("", text[position:]))
    return runs


# --- shared content ----------------------------------------------------------------------


def _date(iso: str) -> str:
    """Stored times are UTC; the file shows the server's local time."""
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%d.%m.%Y. %H:%M")
    except ValueError:
        return iso


def _quote(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= QUOTE_LENGTH else text[: QUOTE_LENGTH - 1].rstrip() + "…"


def _sources(response: dict[str, Any]) -> list[tuple[str, str]]:
    """("Pravilnik.pdf, str. 3", "cited passage…") for each citation of an answer."""
    return [
        (f"{c['source']}, str. {c['page']}", _quote(c.get("text", "")))
        for c in response.get("citations", [])
    ]


def _meta(turn: dict[str, Any]) -> str:
    response = turn["response"]
    parts = [_date(turn.get("created_at", ""))]
    if turn.get("mode") == "agent":
        parts.append("Agent")
    if response.get("model"):
        parts.append(f"model: {response['model']}")
    return " · ".join(p for p in parts if p)


def filename(conversation: dict[str, Any], fmt: str) -> str:
    """ASCII file name from the title: "Godišnji odmor?" -> "godisnji-odmor.pdf"."""
    slug = re.sub(r"[^a-z0-9]+", "-", fold(conversation["title"])).strip("-")[:60].strip("-")
    return f"{slug or 'razgovor'}.{fmt}"


def content_disposition(conversation: dict[str, Any], fmt: str) -> str:
    """attachment; ASCII name for old clients, the real title (UTF-8) for the rest."""
    title = " ".join(re.sub(r"[\\/:*?\"<>|]+", " ", conversation["title"]).split()) or "razgovor"
    utf8 = urllib.parse.quote(f"{title}.{fmt}")
    return f"attachment; filename=\"{filename(conversation, fmt)}\"; filename*=UTF-8''{utf8}"


def export(conversation: dict[str, Any], fmt: str) -> bytes:
    if fmt == "pdf":
        return to_pdf(conversation)
    if fmt == "docx":
        return to_docx(conversation)
    raise ValueError(f"Unknown export format {fmt!r}; use one of: {', '.join(FORMATS)}")


# --- Word --------------------------------------------------------------------------------

_MUTED = RGBColor(0x6B, 0x72, 0x80)


def _add_runs(paragraph: Any, text: str) -> None:
    for style, part in inline_runs(text):
        run = paragraph.add_run(part)
        if style == "bold":
            run.bold = True
        elif style == "code":
            run.font.name = "Consolas"


def to_docx(conversation: dict[str, Any]) -> bytes:
    doc = Document()
    doc.core_properties.title = conversation["title"]
    doc.add_heading(conversation["title"], level=1)
    note = doc.add_paragraph(f"Doc Assistant · izvezeno {_date(datetime.now().isoformat())}")
    note.runs[0].font.color.rgb = _MUTED

    for number, turn in enumerate(conversation["turns"], start=1):
        doc.add_heading(f"{number}. {turn['question']}", level=2)
        meta = doc.add_paragraph(_meta(turn))
        meta.runs[0].font.size = Pt(9)
        meta.runs[0].font.color.rgb = _MUTED
        for block in parse_markdown(turn["response"].get("answer", "")):
            if isinstance(block, Heading):
                _add_runs(doc.add_heading(level=3), block.text)
            elif isinstance(block, Paragraph):
                _add_runs(doc.add_paragraph(), block.text)
            elif isinstance(block, ListBlock):
                style = "List Number" if block.ordered else "List Bullet"
                for item in block.items:
                    _add_runs(doc.add_paragraph(style=style), item)
            else:
                rows = [block.head] if block.head else []
                rows += block.rows
                table = doc.add_table(rows=len(rows), cols=block.width)
                table.style = "Table Grid"
                for r, row in enumerate(rows):
                    for c, cell in enumerate(row):
                        paragraph = table.cell(r, c).paragraphs[0]
                        _add_runs(paragraph, cell)
                        if block.head and r == 0:
                            for run in paragraph.runs:
                                run.bold = True
        sources = _sources(turn["response"])
        if sources:
            doc.add_paragraph().add_run("Izvori").bold = True
            for where, quote in sources:
                item = doc.add_paragraph(style="List Bullet")
                item.add_run(where).bold = True
                if quote:
                    item.add_run(f" — „{quote}“").italic = True

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


# --- PDF ---------------------------------------------------------------------------------

_CSS = """
body { font-family: sans-serif; font-size: 10.5pt; line-height: 1.4; color: #1f2937; }
h1 { font-size: 18pt; margin: 0 0 4pt 0; color: #0f2340; }
h2 { font-size: 12.5pt; margin: 18pt 0 2pt 0; color: #0f2340; }
h3 { font-size: 11pt; margin: 8pt 0 2pt 0; }
p { margin: 0 0 6pt 0; }
.muted { color: #6b7280; font-size: 8.5pt; }
code { font-family: monospace; }
table { border-collapse: collapse; margin: 4pt 0 8pt 0; }
th, td { border: 0.6pt solid #9ca3af; padding: 3pt 5pt; text-align: left; }
th { background-color: #eef1f5; }
.sources { font-size: 9pt; color: #374151; }
.sources b { color: #1f2937; }
"""


def _inline_html(text: str) -> str:
    out = []
    for style, part in inline_runs(text):
        escaped = html.escape(part)
        out.append(
            f"<b>{escaped}</b>"
            if style == "bold"
            else f"<code>{escaped}</code>"
            if style == "code"
            else escaped
        )
    return "".join(out)


def _block_html(block: Block) -> str:
    if isinstance(block, Heading):
        return f"<h3>{_inline_html(block.text)}</h3>"
    if isinstance(block, Paragraph):
        return f"<p>{_inline_html(block.text)}</p>"
    if isinstance(block, ListBlock):
        tag = "ol" if block.ordered else "ul"
        items = "".join(f"<li>{_inline_html(i)}</li>" for i in block.items)
        return f"<{tag}>{items}</{tag}>"

    def row(cells: list[str], tag: str) -> str:
        padded = cells + [""] * (block.width - len(cells))
        return "<tr>" + "".join(f"<{tag}>{_inline_html(c)}</{tag}>" for c in padded) + "</tr>"

    head = f"<thead>{row(block.head, 'th')}</thead>" if block.head else ""
    return f"<table>{head}<tbody>{''.join(row(r, 'td') for r in block.rows)}</tbody></table>"


def to_html(conversation: dict[str, Any]) -> str:
    parts = [
        f"<h1>{html.escape(conversation['title'])}</h1>",
        f"<p class='muted'>Doc Assistant · izvezeno {_date(datetime.now().isoformat())}</p>",
    ]
    for number, turn in enumerate(conversation["turns"], start=1):
        parts.append(f"<h2>{number}. {html.escape(turn['question'])}</h2>")
        parts.append(f"<p class='muted'>{html.escape(_meta(turn))}</p>")
        parts += [_block_html(b) for b in parse_markdown(turn["response"].get("answer", ""))]
        sources = _sources(turn["response"])
        if sources:
            items = "".join(
                f"<li><b>{html.escape(where)}</b>"
                + (f" — <i>„{html.escape(quote)}“</i>" if quote else "")
                + "</li>"
                for where, quote in sources
            )
            parts.append(f"<div class='sources'><p><b>Izvori</b></p><ul>{items}</ul></div>")
    return "".join(parts)


def to_pdf(conversation: dict[str, Any]) -> bytes:
    page = pymupdf.paper_rect("a4")
    content = page + (56, 56, -56, -56)  # 2 cm margins
    story = pymupdf.Story(html=to_html(conversation), user_css=_CSS)
    out = io.BytesIO()
    writer = pymupdf.DocumentWriter(out)
    more = True
    while more:
        device = writer.begin_page(page)
        more, _ = story.place(content)
        story.draw(device)
        writer.end_page()
    writer.close()

    # Page numbers and the document title, added after layout.
    doc = pymupdf.open(stream=out.getvalue(), filetype="pdf")
    doc.set_metadata({"title": conversation["title"], "creator": "Doc Assistant"})
    for number, pdf_page in enumerate(doc, start=1):
        footer = pymupdf.Rect(56, page.height - 40, page.width - 56, page.height - 24)
        pdf_page.insert_textbox(
            footer, f"{number} / {doc.page_count}", fontsize=8, color=(0.42, 0.45, 0.5), align=1
        )
    return doc.tobytes(garbage=3, deflate=True)
