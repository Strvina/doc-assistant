"""Core data types shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Page:
    """Text of one page of a source document.

    `page_no` is 1-based and always refers to the position in the original document,
    so blank pages that were skipped still leave a gap in the numbering.
    """

    source: str
    page_no: int
    text: str


@dataclass(frozen=True, slots=True)
class Chunk:
    """A piece of one page, small enough to embed and to put into a prompt.

    `chunk_id` looks like ``pravilnik_o_radu.pdf::p3::2`` (third chunk of page 3).
    `doc_id` is a hash of the file content and is shared by all chunks of a document,
    so a document can be replaced or deleted as a whole.
    """

    chunk_id: str
    doc_id: str
    source: str
    page: int
    text: str
