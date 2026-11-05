"""Find citations like ``[pravilnik_o_radu.pdf, str. 2]`` in an answer.

Any file extension is accepted, so a new document type needs no change here. Models sometimes
put several sources in one bracket, ``[a.docx, str. 2; b.pdf, str. 3]``; each is a citation.
"page" / "p." / "S." are accepted too, in case the model translates "str.".

The web UI (`app.js`, ``CITE_PART``) reads citations the same way; change both together.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

_BRACKET = re.compile(r"\[([^\[\]\n]+)\]")
_PART = re.compile(
    r"\s*(?P<source>[^\[\],;\n]+?\.[A-Za-z0-9]{2,5})\s*,\s*"
    r"(?:str\.?|p\.|pp\.|page|s\.|seite)\s*(?P<page>\d+)\s*",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class CitationRef:
    source: str  # file name as the model wrote it, stripped
    page: int
    text: str  # the citation as one bracket, e.g. "[a.pdf, str. 2]"


def _parts(inner: str) -> list[CitationRef] | None:
    """The citations inside one bracket, or None when the bracket is not (only) citations."""
    refs = []
    for part in inner.split(";"):
        match = _PART.fullmatch(part)
        if match is None:
            return None
        source = match["source"].strip()
        refs.append(CitationRef(source, int(match["page"]), f"[{part.strip()}]"))
    return refs


def find_citations(text: str) -> Iterator[CitationRef]:
    """Every citation in `text`, in order (duplicates included)."""
    for bracket in _BRACKET.finditer(text):
        yield from _parts(bracket[1]) or ()


def replace_citations(text: str, render: Callable[[list[CitationRef]], str]) -> str:
    """Replace each citation bracket with ``render(citations in that bracket)``."""

    def replace(bracket: re.Match[str]) -> str:
        refs = _parts(bracket[1])
        return bracket[0] if refs is None else render(refs)

    return _BRACKET.sub(replace, text)
