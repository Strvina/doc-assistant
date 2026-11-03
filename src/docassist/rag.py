"""answer(question): retrieve relevant chunks, ask the LLM, return the answer with citations."""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from docassist.llm import NOT_FOUND_SR, SYSTEM_PROMPT, LLMProvider, Message, build_user_message
from docassist.llm.base import ToolCall
from docassist.models import Chunk
from docassist.retrieval import SearchResult, VectorStore, retrieve

# [pravilnik_o_radu.pdf, str. 2] — also tolerate "p." / "S." / "page" in case the model translates.
_CITATION = re.compile(
    r"\[\s*(?P<source>[^\[\],]+?\.(?:pdf|docx|txt|md))\s*,\s*"
    r"(?:str\.?|p\.|pp\.|page|s\.|seite)\s*(?P<page>\d+)\s*\]",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Citation:
    source: str
    page: int
    chunks: tuple[Chunk, ...]  # retrieved chunks from that page, to show the quoted text


@dataclass(frozen=True)
class Answer:
    question: str
    text: str
    citations: list[Citation]
    retrieved: list[SearchResult]
    provider: str
    model: str
    latency_s: float
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = "end"
    invalid_citations: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)  # agent mode only

    @property
    def found(self) -> bool:
        """True when the answer is backed by at least one valid citation."""
        return bool(self.citations)


def answer(
    question: str,
    store: VectorStore,
    llm: LLMProvider,
    *,
    k: int = 5,
    min_score: float = 0.0,
    history: Sequence[tuple[str, str]] = (),
) -> Answer:
    """Answer a question from the indexed documents.

    `history` is a list of earlier (question, answer) pairs for follow-up questions; only
    the current question gets document context.
    """
    started = time.perf_counter()
    results = retrieve(store, question, k=k, min_score=min_score)
    if not results:
        return Answer(
            question=question,
            text=NOT_FOUND_SR,
            citations=[],
            retrieved=[],
            provider=llm.name,
            model="",
            latency_s=time.perf_counter() - started,
        )

    messages: list[Message] = []
    for previous_question, previous_answer in history:
        messages.append({"role": "user", "content": previous_question})
        messages.append({"role": "assistant", "content": previous_answer})
    messages.append(
        {"role": "user", "content": build_user_message(question, [r.chunk for r in results])}
    )

    result = llm.complete(SYSTEM_PROMPT, messages)
    citations, invalid = extract_citations(result.text, results)
    text = result.text
    if result.stop_reason == "refusal" and not text:
        text = "The model declined to answer this question."

    return Answer(
        question=question,
        text=text,
        citations=citations,
        retrieved=results,
        provider=llm.name,
        model=result.model,
        latency_s=time.perf_counter() - started,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        stop_reason=result.stop_reason,
        invalid_citations=invalid,
    )


def extract_citations(
    text: str, results: Sequence[SearchResult]
) -> tuple[list[Citation], list[str]]:
    """Citations in `text` that point to a retrieved page, and the ones that don't.

    A citation to a page that was not in the context means the model made it up, so it is
    reported separately instead of being shown as a source.
    """
    by_page: dict[tuple[str, int], list[Chunk]] = {}
    for result in results:
        key = (result.chunk.source.lower(), result.chunk.page)
        by_page.setdefault(key, []).append(result.chunk)

    citations: list[Citation] = []
    invalid: list[str] = []
    seen: set[tuple[str, int]] = set()
    for match in _CITATION.finditer(text):
        key = (match["source"].strip().lower(), int(match["page"]))
        if key in seen:
            continue
        seen.add(key)
        chunks = by_page.get(key)
        if chunks is None:
            invalid.append(match.group(0))
        else:
            citations.append(Citation(chunks[0].source, key[1], tuple(chunks)))
    return citations, invalid
