"""Read text from images in PDFs and image files with Tesseract OCR (ADR-024).

Scanned pages, screenshots pasted into a PDF and photographed documents have no text layer.
For a page whose images cover a noticeable part of it, the page is rendered and Tesseract
reads it; every recognised word comes back with its position, so OCR text is grouped into
blocks like PDF text and the viewer can still highlight a cited passage. OCR lines that
overlap the page's own text are dropped (no duplicates). Tesseract runs locally:
documents do not leave the server.

Tesseract is called directly (its command-line program): PyMuPDF's built-in OCR skipped
whole tables on test scans that Tesseract itself reads correctly. Ordinary text PDFs are not
slowed down, and without Tesseract installed pages are read without OCR.

Tesseract is good at printed text (scans, screenshots); on photos of products or logos it
also produces noise, which `clean_ocr_text` filters out line by line.

With Serbian Cyrillic among the languages, Tesseract reads some words of a Latin page as
Cyrillic look-alikes ("је", "од", "5ргесамапје" for "Sprečavanje"); search cannot find those
words. A mostly Latin page with such words is read again without Cyrillic (ADR-030).
"""

from __future__ import annotations

import csv
import io
import itertools
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import pymupdf

# Serbian (Latin and Cyrillic), German and English — the languages of the documents.
DEFAULT_LANGUAGES = "srp_latn+srp+deu+eng"
DEFAULT_DPI = 200  # 300 was slower and not better on the test pages
# OCR a page when images cover at least this share of it.
MIN_IMAGE_SHARE = 0.10
# An OCR line is a duplicate when this share of it lies inside the page's own text blocks.
OVERLAP_SHARE = 0.5
TIMEOUT_S = 120
# Tesseract languages written in Cyrillic (left out when a Latin page is read again).
CYRILLIC_LANGUAGES = frozenset({"srp", "rus", "ukr", "bel", "bul", "mkd"})
# A page counts as Latin with stray Cyrillic when Cyrillic letters are at most this share.
MAX_STRAY_CYRILLIC = 0.2
# Pages read at the same time (one Tesseract process each); leave a core for the web server.
WORKERS = max(1, (os.cpu_count() or 2) - 1)

_TESSERACT_CANDIDATES = (
    Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
    Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
)


@dataclass(frozen=True)
class OcrSettings:
    enabled: bool = True
    languages: str = DEFAULT_LANGUAGES
    dpi: int = DEFAULT_DPI


def settings_from_env() -> OcrSettings:
    """OCR=false turns it off; OCR_LANGUAGES / OCR_DPI tune it."""
    enabled = os.getenv("OCR", "true").strip().lower() not in ("0", "false", "no", "off")
    languages = os.getenv("OCR_LANGUAGES", "").strip() or DEFAULT_LANGUAGES
    dpi = int(os.getenv("OCR_DPI", "").strip() or DEFAULT_DPI)
    return OcrSettings(enabled, languages, dpi)


@cache
def tesseract_command() -> str | None:
    """Path of the tesseract program, or None when it is not installed."""
    found = shutil.which("tesseract")
    if found:
        return found
    return next((str(p) for p in _TESSERACT_CANDIDATES if p.is_file()), None)


@cache
def available_languages() -> frozenset[str]:
    command = tesseract_command()
    if command is None:
        return frozenset()
    try:
        result = _run([command, "--list-langs"], b"")
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    lines = result.stdout.decode("utf-8", "replace").splitlines()
    return frozenset(line.strip() for line in lines[1:] if line.strip())


def usable_languages(languages: str) -> str | None:
    """The requested languages that are installed ("srp+deu"), or None if none is."""
    installed = available_languages()
    usable = [lang for lang in languages.split("+") if lang in installed]
    return "+".join(usable) or None


def image_share(page: pymupdf.Page) -> float:
    """Share of the page area covered by images (overlaps counted once per image)."""
    page_area = abs(page.rect) or 1.0
    covered = 0.0
    for image in page.get_images(full=True):
        for rect in page.get_image_rects(image[0]):
            covered += abs(rect & page.rect)
    return min(covered / page_area, 1.0)


@dataclass(frozen=True)
class OcrJob:
    """One page rendered for Tesseract. Rendering uses the PDF (one thread at a time); the
    job itself has no PDF objects, so several jobs can run in parallel (`run_jobs`)."""

    png: bytes
    scale: float  # PDF points per pixel
    page_rect: pymupdf.Rect
    text_rects: tuple[pymupdf.Rect, ...]
    command: str
    languages: str


def prepare(
    page: pymupdf.Page, settings: OcrSettings, text_rects: list[pymupdf.Rect]
) -> OcrJob | None:
    """An OCR job for the page, or None when OCR is off or not installed or the page has too
    few images to need it."""
    if not settings.enabled or image_share(page) < MIN_IMAGE_SHARE:
        return None
    command, languages = tesseract_command(), usable_languages(settings.languages)
    if command is None or languages is None:
        return None
    pixmap = page.get_pixmap(dpi=settings.dpi, colorspace=pymupdf.csGRAY)
    return OcrJob(
        pixmap.tobytes("png"),
        page.rect.width / pixmap.width,
        pymupdf.Rect(page.rect),
        tuple(text_rects),
        command,
        languages,
    )


def run(job: OcrJob) -> list[tuple[pymupdf.Rect, str]]:
    """Paragraphs Tesseract reads in the job's page, with their position on the page; lines
    over the page's own text are left out. Empty if Tesseract fails.

    A mostly Latin page that came back with some Cyrillic words is read again without the
    Cyrillic languages (see the module docstring)."""
    paragraphs = _read(job, job.languages)
    latin = latin_languages(job.languages)
    if latin and has_stray_cyrillic("\n".join(text for _, text in paragraphs)):
        paragraphs = _read(job, latin) or paragraphs
    return paragraphs


def _read(job: OcrJob, languages: str) -> list[tuple[pymupdf.Rect, str]]:
    try:
        result = _run(
            [job.command, "stdin", "stdout", "-l", languages, "--psm", "3", "tsv"], job.png
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    tsv = result.stdout.decode("utf-8", "replace")
    return _paragraphs(tsv, job.scale, job.page_rect, list(job.text_rects))


def latin_languages(languages: str) -> str | None:
    """`languages` without the Cyrillic ones ("srp_latn+srp+eng" -> "srp_latn+eng"), or None
    when nothing would change or nothing would be left."""
    parts = languages.split("+")
    latin = [lang for lang in parts if lang not in CYRILLIC_LANGUAGES]
    return "+".join(latin) if latin and len(latin) < len(parts) else None


# The Cyrillic Unicode block, U+0400 to U+04FF.
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def has_stray_cyrillic(text: str) -> bool:
    """True for mostly Latin text with some Cyrillic letters in it (not for Cyrillic text)."""
    cyrillic = len(_CYRILLIC.findall(text))
    letters = len(_LETTERS.findall(text))
    return 0 < cyrillic <= letters * MAX_STRAY_CYRILLIC


def run_jobs(
    jobs: dict[int, OcrJob], on_done: Callable[[int, int], None] | None = None
) -> dict[int, list[tuple[pymupdf.Rect, str]]]:
    """Run OCR jobs (keyed by page index) in parallel; `on_done(done, total)` after each."""
    results: dict[int, list[tuple[pymupdf.Rect, str]]] = {}
    if not jobs:
        return results
    with ThreadPoolExecutor(max_workers=min(WORKERS, len(jobs))) as pool:
        futures = {pool.submit(run, job): index for index, job in jobs.items()}
        for done, future in enumerate(as_completed(futures), start=1):
            results[futures[future]] = future.result()
            if on_done:
                on_done(done, len(jobs))
    return results


def ocr_blocks(
    page: pymupdf.Page, settings: OcrSettings, text_rects: list[pymupdf.Rect]
) -> list[tuple[pymupdf.Rect, str]]:
    """OCR paragraphs of one page (the viewer, one page at a time). Empty if not needed."""
    job = prepare(page, settings, text_rects)
    return run(job) if job else []


def _run(args: list[str], data: bytes) -> subprocess.CompletedProcess[bytes]:
    # A platform check as a statement, so the type checker knows the flag exists (Windows).
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW  # no console popup
    else:
        flags = 0
    # One thread per Tesseract process: pages already run in parallel (run_jobs).
    env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
    return subprocess.run(
        args,
        input=data,
        capture_output=True,
        timeout=TIMEOUT_S,
        check=False,
        creationflags=flags,
        env=env,
    )


def _paragraphs(
    tsv: str, scale: float, page_rect: pymupdf.Rect, text_rects: list[pymupdf.Rect]
) -> list[tuple[pymupdf.Rect, str]]:
    """Group Tesseract's words (TSV output) into lines and paragraphs."""
    lines: dict[tuple[int, int, int], list[tuple[pymupdf.Rect, str]]] = defaultdict(list)
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        word = (row.get("text") or "").strip()
        if row.get("level") != "5" or not word or float(row.get("conf") or -1) < 0:
            continue
        left, top = int(row["left"]) * scale, int(row["top"]) * scale
        right = left + int(row["width"]) * scale
        bottom = top + int(row["height"]) * scale
        rect = pymupdf.Rect(left, top, right, bottom) + (page_rect.x0, page_rect.y0) * 2
        lines[(int(row["block_num"]), int(row["par_num"]), int(row["line_num"]))].append(
            (rect, word)
        )

    paragraphs: dict[tuple[int, int], list[tuple[pymupdf.Rect, str]]] = defaultdict(list)
    for (block, par, _line), words in sorted(lines.items()):
        rect = pymupdf.Rect(words[0][0])
        for word_rect, _ in words[1:]:
            rect |= word_rect
        if _overlaps(rect, text_rects):
            continue
        paragraphs[(block, par)].append((rect, " ".join(word for _, word in words)))

    result = []
    for line_list in paragraphs.values():
        text = clean_ocr_text("\n".join(text for _, text in line_list))
        if not text:
            continue
        rect = pymupdf.Rect(line_list[0][0])
        for line_rect, _ in line_list[1:]:
            rect |= line_rect
        result.append((rect, text))
    return result


def _overlaps(rect: pymupdf.Rect, others: list[pymupdf.Rect]) -> bool:
    area = abs(rect)
    return bool(area) and any(abs(rect & other) >= area * OVERLAP_SHARE for other in others)


# --- cleaning ------------------------------------------------------------------------------

_TOKEN = re.compile(r"\S+")
_LETTERS = re.compile(r"[^\W\d_]")
_DIGITS = re.compile(r"\d")


def _token_kind(token: str) -> int:
    """0 = noise, 1 = readable (a short word, a unit, "M8"), 2 = strong (a word of 3+
    letters or a number of 2+ digits)."""
    core = token.strip(".,:;!?()[]{}\"'«»„“”‘’-–—/|*")
    letters = len(_LETTERS.findall(core))
    digits = len(_DIGITS.findall(core))
    if not core or letters + digits < len(core) * 0.7 or (letters + digits < 2 and not digits):
        return 0
    # Noise often mixes cases at random ("krYzYzYıh"); real words change case at most twice.
    changes = sum(
        1
        for a, b in itertools.pairwise(core)
        if a.isalpha() and b.isalpha() and a.islower() != b.islower()
    )
    if changes > 2:
        return 0
    return 2 if letters >= 3 or digits >= 2 else 1


def clean_ocr_text(text: str) -> str:
    """Keep the lines of OCR output that look like text; drop picture noise.

    A line stays (whole) when at least half of its tokens are readable and one is strong:
    "M8 20 Nm 13 mm" and "Pravilnik o radu" stay, "ry)" and "I krYzYzYıh" go.
    """
    kept = []
    for line in text.splitlines():
        tokens = _TOKEN.findall(line)
        kinds = [_token_kind(t) for t in tokens]
        readable = sum(1 for kind in kinds if kind)
        if 2 in kinds and readable * 2 >= len(tokens):
            kept.append(" ".join(tokens))
    return "\n".join(kept)
