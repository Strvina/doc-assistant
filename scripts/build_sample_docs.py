"""Build the demo documents in data/sample_docs/ from the text sources in sample_docs_src/.

Usage:
    uv run python scripts/build_sample_docs.py

The sources use a tiny Markdown subset:
    # Title            ## Heading          ### Subheading
    - bullet item      | table | row |     (first table row is the header)
    <<<PAGE>>>         explicit page break
Anything else is a paragraph; paragraphs are separated by blank lines.
"""

from __future__ import annotations

import html
import io
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import docx
import pymupdf
from docx.shared import Pt

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = Path(__file__).resolve().parent / "sample_docs_src"
OUT_DIR = ROOT / "data" / "sample_docs"

AUTHOR = "Panelvolt d.o.o. (fictional demo company)"
PAGE_BREAK = "<<<PAGE>>>"

# source file -> output file (the extension decides the format)
DOCUMENTS = {
    "pravilnik_o_radu.md": "pravilnik_o_radu.pdf",
    "montageanleitung_pv400.md": "montageanleitung_pv400.pdf",
    "quality_control_procedure.md": "quality_control_procedure.docx",
    "bezbednost_na_radu.md": "bezbednost_na_radu.docx",
    "faq.md": "faq.txt",
}

PDF_CSS = """
body { font-family: sans-serif; font-size: 10.5pt; line-height: 1.35; }
h1 { font-size: 18pt; margin-bottom: 2pt; }
h2 { font-size: 13pt; margin-top: 12pt; margin-bottom: 4pt; }
h3 { font-size: 11pt; margin-top: 8pt; }
p.meta { color: #555555; font-size: 9pt; margin-bottom: 10pt; }
table { border-collapse: collapse; margin: 6pt 0; }
th, td { border: 1px solid #888888; padding: 3pt 6pt; text-align: left; }
th { background-color: #e6e6e6; }
"""


@dataclass
class Block:
    kind: str  # title | h2 | h3 | meta | p | ul | table | pagebreak
    text: str = ""
    items: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


def parse(source: str) -> list[Block]:
    blocks: list[Block] = []
    for chunk in source.strip().split("\n\n"):
        lines = [line.strip() for line in chunk.strip().splitlines() if line.strip()]
        if not lines:
            continue
        first = lines[0]
        if first == PAGE_BREAK:
            blocks.append(Block("pagebreak"))
        elif first.startswith("# "):
            blocks.append(Block("title", first[2:]))
        elif first.startswith("## "):
            blocks.append(Block("h2", first[3:]))
        elif first.startswith("### "):
            blocks.append(Block("h3", first[4:]))
        elif first.startswith("- "):
            blocks.append(Block("ul", items=[line[2:] for line in lines]))
        elif first.startswith("|"):
            rows = [[cell.strip() for cell in line.strip("|").split("|")] for line in lines]
            blocks.append(Block("table", rows=rows))
        else:
            kind = "meta" if blocks and blocks[-1].kind == "title" else "p"
            blocks.append(Block(kind, " ".join(lines)))
    return blocks


def build_pdf(blocks: list[Block], title: str, out: Path) -> None:
    pages_html = [[]]
    for block in blocks:
        if block.kind == "pagebreak":
            pages_html.append([])
        else:
            pages_html[-1].append(_block_html(block))

    page_rect = pymupdf.paper_rect("a4")
    content_rect = page_rect + (60, 60, -60, -60)
    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    for parts in pages_html:
        story = pymupdf.Story("".join(parts), user_css=PDF_CSS)
        more = True
        while more:  # a section longer than one page simply flows onto the next one
            device = writer.begin_page(page_rect)
            more, _ = story.place(content_rect)
            story.draw(device)
            writer.end_page()
    writer.close()

    with pymupdf.open("pdf", buffer.getvalue()) as pdf:
        pdf.set_metadata({"title": title, "author": AUTHOR, "creator": "build_sample_docs.py"})
        pdf.save(out, garbage=3, deflate=True)


def _block_html(block: Block) -> str:
    esc = html.escape
    match block.kind:
        case "title":
            return f"<h1>{esc(block.text)}</h1>"
        case "h2" | "h3":
            return f"<{block.kind}>{esc(block.text)}</{block.kind}>"
        case "meta":
            return f'<p class="meta">{esc(block.text)}</p>'
        case "ul":
            return "<ul>" + "".join(f"<li>{esc(item)}</li>" for item in block.items) + "</ul>"
        case "table":
            header, *body = block.rows
            head = "".join(f"<th>{esc(cell)}</th>" for cell in header)
            rows = "".join(
                "<tr>" + "".join(f"<td>{esc(cell)}</td>" for cell in row) + "</tr>" for row in body
            )
            return f"<table><tr>{head}</tr>{rows}</table>"
        case _:
            return f"<p>{esc(block.text)}</p>"


def build_docx(blocks: list[Block], title: str, out: Path) -> None:
    document = docx.Document()
    document.styles["Normal"].font.name = "Calibri"
    document.styles["Normal"].font.size = Pt(11)
    document.core_properties.title = title
    document.core_properties.author = AUTHOR

    for block in blocks:
        match block.kind:
            case "pagebreak":
                document.add_page_break()
            case "title":
                document.add_heading(block.text, level=0)
            case "h2":
                document.add_heading(block.text, level=1)
            case "h3":
                document.add_heading(block.text, level=2)
            case "meta":
                document.add_paragraph(block.text, style="Subtitle")
            case "ul":
                for item in block.items:
                    document.add_paragraph(item, style="List Bullet")
            case "table":
                header, *body = block.rows
                table = document.add_table(rows=0, cols=len(header))
                table.style = "Table Grid"
                for row_no, row in enumerate([header, *body]):
                    cells = table.add_row().cells
                    for cell, value in zip(cells, row, strict=True):
                        cell.text = value
                        if row_no == 0:
                            cell.paragraphs[0].runs[0].bold = True
            case _:
                document.add_paragraph(block.text)

    document.save(str(out))


def build_txt(source: str, out: Path) -> None:
    text = source.replace(f"\n{PAGE_BREAK}\n", "\f")
    out.write_text(text.strip() + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for src_name, out_name in DOCUMENTS.items():
        source = (SRC_DIR / src_name).read_text(encoding="utf-8")
        out = OUT_DIR / out_name
        blocks = parse(source)
        title = next((b.text for b in blocks if b.kind == "title"), out.stem)

        match out.suffix:
            case ".pdf":
                build_pdf(blocks, title, out)
            case ".docx":
                build_docx(blocks, title, out)
            case ".txt":
                build_txt(source, out)
            case _:
                shutil.copyfile(SRC_DIR / src_name, out)
        print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
