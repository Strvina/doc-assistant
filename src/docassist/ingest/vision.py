"""Read pages with pictures using an AI vision model, when the user asks for it (ADR-029).

Tesseract (ADR-024) reads scans well, but not photos of products, price cards with small
superscript decimals or text over pictures. For such a document the user can ask, per
document, that its picture pages are read by a vision model (Gemini on the free tier). The
pages then leave the server, which is why it is never automatic.

The model's text is stored next to the original as ``<file>.vision.json`` with the file's
hash, page number -> text. The loader uses it instead of OCR for those pages, so re-indexing
(also ``index --rebuild``) needs no new calls; a changed file under the same name ignores the
old text. Pages are saved one by one, so when the free-tier limit is reached halfway, the
next attempt continues where it stopped.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from docassist.ingest import ocr

SIDECAR_SUFFIX = ".vision.json"
MAX_SIDE_PX = 2000  # the longer side of a rendered page; enough for small print
MAX_DPI = 200

PROMPT = """\
Transcribe all text on this page of a company document, in reading order.
- Tables: one Markdown row per line, e.g. "| Product | Weight | Price |".
- Product cards, price lists and catalogues: one line per item with its name, details and \
price, e.g. "Crema e Aroma 1 kg beans — 2.490,00 RSD".
- Copy numbers, units, codes and names exactly. Prices printed with small raised decimals \
("1099⁰⁰") are written with a decimal comma ("1099,00").
- Keep the document's language and script; do not translate.
- Do not describe pictures, logos or layout, and add no comments. If the page has no text, \
answer with nothing."""

ReadImage = Callable[[bytes], str]  # PNG bytes -> the page's text


class VisionError(Exception):
    """The document cannot be read with the vision model (wrong type, no original)."""


@dataclass(frozen=True)
class VisionResult:
    pages_total: int  # pages with pictures
    pages_read: int  # of those, read by the model (now or earlier)
    error: str | None = None  # why it stopped early, e.g. the free-tier limit


def sidecar_path(path: Path) -> Path:
    return path.with_name(path.name + SIDECAR_SUFFIX)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_pages(path: Path) -> dict[int, str]:
    """Page number -> text read by the model, if it was read for this exact file."""
    sidecar = sidecar_path(path)
    if not sidecar.is_file():
        return {}
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        if data.get("sha256") != _sha256(path):
            return {}
        return {int(page): str(text) for page, text in data.get("pages", {}).items()}
    except (ValueError, AttributeError, TypeError):
        return {}


def _save_pages(path: Path, pages: dict[int, str], model: str) -> None:
    data = {
        "sha256": _sha256(path),
        "model": model,
        "pages": {str(page): text for page, text in sorted(pages.items())},
    }
    sidecar_path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")


def picture_pages(path: Path) -> dict[int, bytes]:
    """Page number -> PNG of each page where pictures cover a noticeable part (as for OCR)."""
    from docassist.ingest.loaders import open_pdf  # loaders imports this module

    pages = {}
    with open_pdf(path) as pdf:
        for index, page in enumerate(pdf):
            if ocr.image_share(page) < ocr.MIN_IMAGE_SHARE:
                continue
            longest_pt = max(page.rect.width, page.rect.height) or 1
            dpi = min(MAX_DPI, int(MAX_SIDE_PX / longest_pt * 72))
            pages[index + 1] = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB).tobytes("png")
    return pages


def read_document(
    path: Path,
    read_image: ReadImage,
    model: str,
    on_done: Callable[[int, int], None] | None = None,
) -> VisionResult:
    """Read the picture pages of a PDF or image that the model has not read yet.

    Stops at the first error (usually the free-tier limit) and keeps what was read.
    """
    from docassist.ingest.loaders import IMAGE_EXTENSIONS

    if path.suffix.lower() not in (".pdf", *IMAGE_EXTENSIONS):
        raise VisionError(f"{path.name}: only PDFs and images have pages to read as pictures")
    pictures = picture_pages(path)
    done = {page: text for page, text in load_pages(path).items() if page in pictures}
    if on_done:
        on_done(len(done), len(pictures))
    for page, png in pictures.items():
        if page in done:
            continue
        try:
            done[page] = read_image(png).strip()
        except Exception as exc:  # LLMError from the provider, network errors
            return VisionResult(len(pictures), len(done), str(exc))
        _save_pages(path, done, model)
        if on_done:
            on_done(len(done), len(pictures))
    return VisionResult(len(pictures), len(done))


def llm_reader(provider) -> ReadImage:
    """`read_image` for a Chat Completions provider (Gemini): one page per request."""

    def read_image(png: bytes) -> str:
        url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        message = {
            "role": "user",
            "content": [
                {"type": "text", "text": "Transcribe this page."},
                {"type": "image_url", "image_url": {"url": url}},
            ],
        }
        return provider.complete(PROMPT, [message]).text

    return read_image
