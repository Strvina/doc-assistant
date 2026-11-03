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
