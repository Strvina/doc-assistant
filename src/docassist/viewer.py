"""Render an original document for the in-app viewer, with the cited passage highlighted.

- PDF: the original file with highlight annotations on the text blocks of the cited passage;
  the browser's PDF viewer opens it (the UI adds ``#page=N``).
- DOCX / XLSX / PPTX / TXT / MD: an HTML page that looks like paper sheets, one per document
  page (worksheet, slide), with headings, lists and tables preserved and the cited paragraphs
  or table rows marked.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

from docassist.ingest.loaders import open_pdf, pdf_page_blocks, pptx_slides, xlsx_sheets

HIGHLIGHT_COLOR = (1.0, 0.82, 0.25)  # amber, same accent as the web UI


@dataclass(frozen=True)
class Block:
    kind: str  # title | h1 | h2 | h3 | p | li | table | notes
    text: str = ""
    rows: tuple[tuple[str, ...], ...] = ()
    # How each table row reads in the index, when that differs from "a | b | c" (XLSX rows
    # name their columns); used to find the cited rows.
    row_text: tuple[str, ...] = ()

    @property
    def plain(self) -> str:
        if self.kind == "table":
            return "\n".join(" | ".join(row) for row in self.rows)
        return self.text


# --- matching a passage to blocks ---------------------------------------------------------


def _norm(text: str) -> str:
    return " ".join(text.replace("|", " ").lower().split())


def is_cited(block_text: str, passage: str) -> bool:
    """True when a block of the original belongs to the cited passage.

    Chunks are cut from the same blocks, so a block is either inside the passage, contains
    it, or (where a chunk boundary splits it) shares a long beginning or end with it.
    """
    block, cited = _norm(block_text), _norm(passage)
    if len(block) < 3 or not cited:
        return False
    if block in cited or (len(cited) >= 20 and cited in block):
        return True
    edge = 40
    return len(block) > edge and (block[:edge] in cited or block[-edge:] in cited)


# --- PDF -----------------------------------------------------------------------------------


def pdf_with_highlights(path: Path, page_no: int | None, passage: str | None) -> bytes:
    with open_pdf(path) as pdf:  # an uploaded image is shown as a one-page PDF
        if passage and page_no and 1 <= page_no <= pdf.page_count:
            page = pdf[page_no - 1]
            for rect, text in pdf_page_blocks(page):
                if is_cited(text, passage):
                    annot = page.add_highlight_annot(rect)
                    annot.set_colors(stroke=HIGHLIGHT_COLOR)
                    annot.set_opacity(0.45)
                    annot.update()
        return pdf.tobytes(garbage=1, deflate=True)


# --- DOCX / TXT / MD → pages of blocks -----------------------------------------------------

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = {"t": f"{{{_W_NS}}}t", "tab": f"{{{_W_NS}}}tab", "br": f"{{{_W_NS}}}br"}
_TYPE = f"{{{_W_NS}}}type"


def docx_pages(path: Path) -> list[list[Block]]:
    document = docx.Document(str(path))
    pages: list[list[Block]] = [[]]
    for item in document.iter_inner_content():
        if isinstance(item, Table):
            rows = []
            for row in item.rows:
                cells: list[str] = []
                previous = None
                for cell in row.cells:
                    if cell._tc is not previous:  # merged cells repeat; show them once
                        cells.append(cell.text.strip())
                    previous = cell._tc
                rows.append(tuple(cells))
            pages[-1].append(Block("table", rows=tuple(rows)))
            continue
        _docx_paragraph(item, pages)
    return pages


def _docx_paragraph(paragraph: Paragraph, pages: list[list[Block]]) -> None:
    style = (paragraph.style.name if paragraph.style is not None else "").lower()
    if style == "title":
        kind = "title"
    elif style.startswith("heading"):
        level = style.removeprefix("heading").strip() or "1"
        kind = f"h{min(int(level) if level.isdigit() else 1, 3)}"
    elif "list" in style:
        kind = "li"
    else:
        kind = "p"

    if paragraph.paragraph_format.page_break_before and pages[-1]:
        pages.append([])
    parts: list[str] = []
    for element in paragraph._p.iter(*_W.values()):
        if element.tag == _W["t"]:
            parts.append(element.text or "")
        elif element.tag == _W["tab"]:
            parts.append("\t")
        elif element.get(_TYPE) == "page":
            text = "".join(parts).strip()
            if text:
                pages[-1].append(Block(kind, text))
            pages.append([])
            parts = []
        else:
            parts.append("\n")
    text = "".join(parts).strip()
    if text:
        pages[-1].append(Block(kind, text))


def xlsx_pages(path: Path) -> list[list[Block]]:
    pages = []
    for sheet in xlsx_sheets(path):
        rows = tuple(sheet.rows)
        width = max((len(row) for row in rows), default=0)
        rows = tuple(row + ("",) * (width - len(row)) for row in rows)
        texts = tuple(sheet.row_text(i) for i in range(len(rows)))
        blocks = [Block("h1", sheet.name)]
        if rows:
            blocks.append(Block("table", rows=rows, row_text=texts))
        pages.append(blocks)
    return pages


def pptx_pages(path: Path) -> list[list[Block]]:
    kinds = {"title": "h1", "p": "p", "table": "table", "notes": "notes"}
    return [
        [Block(kinds[item.kind], item.text, item.rows) for item in items]
        for items in pptx_slides(path)
    ]


def text_pages(path: Path) -> list[list[Block]]:
    raw = path.read_bytes()
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = raw.decode("cp1250", errors="replace")
    markdown = path.suffix.lower() == ".md"
    pages = []
    for page_text in content.replace("\r\n", "\n").split("\f"):
        blocks = []
        for paragraph in re.split(r"\n\s*\n", page_text):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            heading = re.match(r"^(#{1,3})\s+(.*)", paragraph) if markdown else None
            if heading:
                blocks.append(Block(f"h{len(heading[1])}", heading[2]))
            elif markdown and paragraph.startswith(("- ", "* ")):
                blocks.extend(Block("li", line[2:].strip()) for line in paragraph.splitlines())
            else:
                blocks.append(Block("p", paragraph))
        pages.append(blocks)
    return pages


# --- HTML ----------------------------------------------------------------------------------

_PAGE_CSS = """
:root { --ink:#1b2430; --muted:#6b7686; --line:#e3e8ef; --paper:#fff; --bg:#eef1f5;
        --mark:#ffd35c; --accent:#0f2a47; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font: 15px/1.6 "Segoe UI", system-ui, -apple-system, Roboto, sans-serif; }
.sheet { background:var(--paper); max-width:760px; margin:24px auto; padding:56px 64px 48px;
         border-radius:6px; box-shadow:0 1px 2px rgba(16,24,40,.06),0 8px 24px rgba(16,24,40,.08);
         position:relative; }
.pageno { position:absolute; top:18px; right:24px; font:600 11px/1 ui-monospace,Consolas,monospace;
          color:var(--muted); letter-spacing:.08em; text-transform:uppercase; }
h1.title { font-size:26px; line-height:1.25; margin:0 0 6px; color:var(--accent); }
h1,h2,h3 { color:var(--accent); line-height:1.3; }
h1 { font-size:20px; margin:26px 0 8px; } h2 { font-size:17px; margin:22px 0 6px; }
h3 { font-size:15px; margin:18px 0 4px; }
p { margin:0 0 10px; white-space:pre-wrap; }
ul { margin:0 0 10px; padding-left:22px; } li { margin:2px 0; }
table { border-collapse:collapse; margin:10px 0 14px; width:100%; font-size:14px; }
td,th { border:1px solid var(--line); padding:6px 10px; text-align:left; vertical-align:top; }
tr:first-child td { background:#f5f7fa; font-weight:600; }
.cited { background:linear-gradient(transparent 0, var(--mark) 0); border-radius:3px;
         box-shadow:0 0 0 3px var(--mark); }
tr.cited td { background:var(--mark); }
.empty { color:var(--muted); font-style:italic; }
.notes { margin-top:18px; padding:10px 14px; border-left:3px solid var(--line);
         color:var(--muted); font-size:14px; }
.notes.cited { color:var(--ink); }
.notes::before { content:"Beleške"; display:block; font-weight:600; font-size:12px;
                 text-transform:uppercase; letter-spacing:.06em; }
.table-scroll { overflow-x:auto; }
@media (max-width:700px) { .sheet { margin:10px; padding:40px 22px 28px; } }
"""


def blocks_to_html(
    title: str, pages: list[list[Block]], page_no: int | None, passage: str | None
) -> str:
    sheets = []
    for number, blocks in enumerate(pages, start=1):
        cite_here = passage if page_no == number else None
        body = _render_blocks(blocks, cite_here) or '<p class="empty">(prazna strana)</p>'
        sheets.append(
            f'<section class="sheet" id="page-{number}">'
            f'<div class="pageno">Strana {number} / {len(pages)}</div>{body}</section>'
        )
    target = f"page-{page_no}" if page_no else ""
    script = (
        "<script>window.addEventListener('load',()=>{"
        "const t=document.querySelector('.cited')||document.getElementById("
        f"{target!r});if(t)t.scrollIntoView({{block:'center'}});}});</script>"
        if target
        else ""
    )
    return (
        '<!doctype html><html lang="sr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{_PAGE_CSS}</style></head>"
        f"<body>{''.join(sheets)}{script}</body></html>"
    )


def _render_blocks(blocks: list[Block], passage: str | None) -> str:
    out: list[str] = []
    in_list = False
    for block in blocks:
        cited = bool(passage) and is_cited(block.plain, passage or "")
        cls = ' class="cited"' if cited else ""
        if block.kind == "li" and not in_list:
            out.append("<ul>")
            in_list = True
        elif block.kind != "li" and in_list:
            out.append("</ul>")
            in_list = False
        text = html.escape(block.text)
        if block.kind == "title":
            out.append(f'<h1 class="title{" cited" if cited else ""}">{text}</h1>')
        elif block.kind in ("h1", "h2", "h3"):
            out.append(f"<{block.kind}{cls}>{text}</{block.kind}>")
        elif block.kind == "li":
            out.append(f"<li{cls}>{text}</li>")
        elif block.kind == "table":
            rows = []
            for i, row in enumerate(block.rows):
                match = block.row_text[i] if i < len(block.row_text) else " | ".join(row)
                row_cited = bool(passage) and is_cited(match, passage or "")
                row_cls = ' class="cited"' if row_cited else ""
                cells = "".join(f"<td>{html.escape(cell)}</td>" for cell in row)
                rows.append(f"<tr{row_cls}>{cells}</tr>")
            out.append(f'<div class="table-scroll"><table>{"".join(rows)}</table></div>')
        elif block.kind == "notes":
            out.append(f'<p class="notes{" cited" if cited else ""}">{text}</p>')
        else:
            out.append(f"<p{cls}>{text}</p>")
    if in_list:
        out.append("</ul>")
    return "".join(out)


def render_html(path: Path, page_no: int | None, passage: str | None) -> str:
    readers = {".docx": docx_pages, ".xlsx": xlsx_pages, ".pptx": pptx_pages}
    pages = readers.get(path.suffix.lower(), text_pages)(path)
    return blocks_to_html(path.name, pages, page_no, passage)
