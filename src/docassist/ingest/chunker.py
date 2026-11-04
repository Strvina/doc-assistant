"""Split pages into overlapping chunks.

Chunks never cross page boundaries, so every chunk can be cited with a single page number.
Inside a page the text is split at the most natural boundary that keeps pieces under the
size limit: paragraphs, then lines, then sentences, then words, and only as a last resort
in the middle of a word. Neighbouring chunks share up to `overlap` characters, so a fact
that sits on a chunk boundary is still complete in at least one chunk.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from docassist.models import Chunk, Page

# Stored with the index: chunks made by another version need `index --rebuild` (ADR-023).
CHUNKING = "sections-v2"
# 500/100 measured best with the local embedding model, which reads ~128 tokens (ADR-019).
DEFAULT_CHUNK_SIZE = 500
DEFAULT_OVERLAP = 100

_SEPARATORS = ("\n\n", "\n", ". ", " ")


def chunk_pages(
    pages: Iterable[Page],
    doc_id: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if not 0 <= overlap < chunk_size:
        raise ValueError("overlap must be >= 0 and smaller than chunk_size")

    chunks = []
    for page in pages:
        for index, text in enumerate(split_sections(page.text, chunk_size, overlap)):
            chunks.append(
                Chunk(
                    chunk_id=f"{page.source}::p{page.page_no}::{index}",
                    doc_id=doc_id,
                    source=page.source,
                    page=page.page_no,
                    text=text,
                )
            )
    return chunks


def split_sections(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split a page so that a section stays together with its heading (ADR-023).

    Whole sections are packed into chunks while they fit. A section longer than a chunk is
    split with `split_text`, and every part after the first starts with the section heading,
    so a table row is never separated from the heading that says what the table is about.
    Text without recognisable headings is split with `split_text` as before.
    """
    sections = _sections(text)
    if not any(heading for heading, _ in sections):
        return split_text(text, chunk_size, overlap)

    chunks: list[str] = []
    current = ""
    for heading, body in sections:
        section = body.strip()
        if not section:
            continue
        if len(section) > chunk_size:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long_section(heading, section, chunk_size, overlap))
        elif current and len(current) + 2 + len(section) > chunk_size:
            chunks.append(current)
            current = section
        else:
            current = f"{current}\n\n{section}" if current else section
    if current:
        chunks.append(current)
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def _split_long_section(heading: str, section: str, chunk_size: int, overlap: int) -> list[str]:
    if not heading or len(heading) > chunk_size // 4:
        return split_text(section, chunk_size, overlap)
    body = section[len(heading) :].lstrip()
    room = chunk_size - len(heading) - 1
    parts = split_text(body, room, min(overlap, room - 1))
    return [f"{heading}\n{part}" for part in parts]


# A section heading: a numbered title ("6. Anzugsmomente …", "2.1 Scope") or a short question
# (FAQ entries). Numbered lines that end like a sentence are list items, not headings.
_NUMBERED_HEADING = re.compile(r"^\d{1,2}(\.\d{1,2})*\.?\s+\S.{0,78}$")
_QUESTION_HEADING = re.compile(r"^\S.{3,148}\?$")


def _is_heading(line: str) -> bool:
    line = line.strip()
    if _NUMBERED_HEADING.match(line):
        return not line.endswith((".", ",", ";", ":"))
    return bool(_QUESTION_HEADING.match(line))


def _sections(text: str) -> list[tuple[str, str]]:
    """(heading, section text including the heading) pairs; the first may have no heading."""
    sections: list[tuple[str, str]] = []
    heading, lines = "", []
    for line in text.splitlines(keepends=True):
        if _is_heading(line) and "".join(lines).strip():
            sections.append((heading, "".join(lines)))
            heading, lines = "", []
        if _is_heading(line) and not "".join(lines).strip():
            heading = line.strip()
        lines.append(line)
    sections.append((heading, "".join(lines)))
    return sections


def split_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split `text` into stripped chunks of at most `chunk_size` characters."""
    pieces = _split(text, chunk_size, _SEPARATORS)

    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for piece in pieces:
        if current and length + len(piece) > chunk_size:
            chunks.append("".join(current))
            current, length = _tail(current, overlap)
            # Drop overlap from the front if it would not leave room for the new piece.
            while current and length + len(piece) > chunk_size:
                length -= len(current.pop(0))
        current.append(piece)
        length += len(piece)
    if current:
        chunks.append("".join(current))

    return [chunk.strip() for chunk in chunks if chunk.strip()]


def _split(text: str, size: int, separators: tuple[str, ...]) -> list[str]:
    """Cut text into pieces of at most `size` characters.

    Separators stay attached to the end of the piece before them, so joining all pieces
    gives back the original text exactly.
    """
    if len(text) <= size:
        return [text]
    if not separators:
        return [text[i : i + size] for i in range(0, len(text), size)]

    separator, finer = separators[0], separators[1:]
    parts = text.split(separator)
    if len(parts) == 1:
        return _split(text, size, finer)

    pieces = []
    for i, part in enumerate(parts):
        piece = part + separator if i < len(parts) - 1 else part
        if len(piece) <= size:
            pieces.append(piece)
        else:
            pieces.extend(_split(piece, size, finer))
    return pieces


def _tail(pieces: list[str], max_length: int) -> tuple[list[str], int]:
    """Return the longest run of trailing pieces whose total length fits in `max_length`."""
    tail: list[str] = []
    length = 0
    for piece in reversed(pieces):
        if length + len(piece) > max_length:
            break
        tail.insert(0, piece)
        length += len(piece)
    return tail, length
