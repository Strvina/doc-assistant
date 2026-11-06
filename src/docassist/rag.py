"""answer(question): retrieve relevant chunks, ask the LLM, return the answer with citations."""

from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

from docassist.citations import find_citations
from docassist.llm import NOT_FOUND_SR, SYSTEM_PROMPT, LLMProvider, Message, build_user_message
from docassist.llm.base import LLMError, LLMResult, ToolCall, stream_complete
from docassist.llm.prompts import REWRITE_PROMPT
from docassist.models import Chunk
from docassist.retrieval import SearchResult, VectorStore, retrieve
from docassist.retrieval.store import NEIGHBORS


@dataclass(frozen=True, slots=True)
class Citation:
    source: str
    page: int
    chunks: tuple[Chunk, ...]  # retrieved chunks from that page, to show the quoted text

    @property
    def passage(self) -> str:
        """The cited text of this page, without the overlap neighbouring chunks share."""
        return merge_passages(self.chunks)


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
    search_query: str = ""  # what was searched, when a follow-up question was rewritten

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
    neighbors: int = NEIGHBORS,
) -> Answer:
    """Answer a question from the indexed documents.

    `history` is a list of earlier (question, answer) pairs for follow-up questions; only
    the current question gets document context. The `k` best chunks go to the model with
    the chunks around them (`VectorStore.with_neighbors`, `neighbors` before and after).
    """
    final = None
    events = answer_stream(
        question, store, llm, k=k, min_score=min_score, history=history, neighbors=neighbors
    )
    for event in events:
        if isinstance(event, Answer):
            final = event
    assert final is not None
    return final


def answer_stream(
    question: str,
    store: VectorStore,
    llm: LLMProvider,
    *,
    k: int = 5,
    min_score: float = 0.0,
    history: Sequence[tuple[str, str]] = (),
    neighbors: int = NEIGHBORS,
) -> Iterator[str | Answer]:
    """Like `answer`, but yields the text as the model writes it, then the `Answer` last."""
    started = time.perf_counter()
    query, rewrite = search_query(question, history, llm)
    results = store.with_neighbors(retrieve(store, query, k=k, min_score=min_score), neighbors)
    if not results:
        yield NOT_FOUND_SR
        yield Answer(
            question=question,
            text=NOT_FOUND_SR,
            citations=[],
            retrieved=[],
            provider=llm.name,
            model=rewrite.model if rewrite else "",
            latency_s=time.perf_counter() - started,
            input_tokens=rewrite.input_tokens if rewrite else 0,
            output_tokens=rewrite.output_tokens if rewrite else 0,
            search_query=query if rewrite else "",
        )
        return

    messages: list[Message] = []
    for previous_question, previous_answer in history:
        messages.append({"role": "user", "content": previous_question})
        messages.append({"role": "assistant", "content": previous_answer})
    messages.append(
        {"role": "user", "content": build_user_message(question, context_passages(results))}
    )

    result = None
    for piece in stream_complete(llm, SYSTEM_PROMPT, messages):
        if isinstance(piece, str):
            yield piece
        else:
            result = piece
    assert result is not None
    citations, invalid = extract_citations(result.text, results)
    text = result.text
    if result.stop_reason == "refusal" and not text:
        text = "The model declined to answer this question."

    yield Answer(
        question=question,
        text=text,
        citations=citations,
        retrieved=results,
        provider=llm.name,
        model=result.model,
        latency_s=time.perf_counter() - started,
        input_tokens=result.input_tokens + (rewrite.input_tokens if rewrite else 0),
        output_tokens=result.output_tokens + (rewrite.output_tokens if rewrite else 0),
        stop_reason=result.stop_reason,
        invalid_citations=invalid,
        search_query=query if rewrite else "",
    )


# Follow-up rewriting sees only the last turns, with long answers cut (fewer tokens).
REWRITE_TURNS = 3
REWRITE_ANSWER_CHARS = 1500
MAX_QUERY_CHARS = 300


def search_query(
    question: str, history: Sequence[tuple[str, str]], llm: LLMProvider
) -> tuple[str, LLMResult | None]:
    """What to search for: a follow-up question ("a u petu?") rewritten by the model into a
    standalone query using the conversation (ADR-031), or the question itself.

    Without history there is no extra model call. When the call fails or returns something
    that is not a query (empty, too long), the question itself is searched.
    """
    if not history:
        return question, None
    messages: list[Message] = []
    for previous_question, previous_answer in history[-REWRITE_TURNS:]:
        messages.append({"role": "user", "content": previous_question})
        messages.append({"role": "assistant", "content": previous_answer[:REWRITE_ANSWER_CHARS]})
    messages.append({"role": "user", "content": question})
    try:
        result = llm.complete(REWRITE_PROMPT, messages)
    except LLMError:
        return question, None
    query = result.text.strip().strip("\"'„“”«»").strip()
    if not query or len(query) > MAX_QUERY_CHARS:
        return question, result
    return query, result


def merge_passages(chunks: Sequence[Chunk]) -> str:
    """Join chunks of one page in reading order, dropping the text neighbours share (overlap)
    and the section heading a chunk repeats from the one before it. "[…]" marks left-out
    text between chunks that are not next to each other."""
    ordered = sorted({c.chunk_id: c for c in chunks}.values(), key=_chunk_index)
    merged, previous = "", None
    for chunk in ordered:
        text = chunk.text
        adjacent = previous is not None and _chunk_index(chunk) == _chunk_index(previous) + 1
        if adjacent:
            heading = _first_line(previous.text)
            if _first_line(text) == heading and len(heading) < len(text):
                text = text[len(heading) :].lstrip("\n")
        overlap = next(
            (n for n in range(min(len(merged), len(text)), 0, -1) if merged.endswith(text[:n])),
            0,
        )
        if overlap >= 20:  # real overlap, not an accidental one-word match
            merged += text[overlap:]
        elif not merged:
            merged = text
        else:
            merged += ("\n\n" if adjacent else "\n\n[…]\n\n") + text
        previous = chunk
    return merged


def context_passages(results: Sequence[SearchResult]) -> list[Chunk]:
    """One passage per page for the model, in the order of the page's best hit. The chunks
    of a page are merged (`merge_passages`), so overlapping text is not sent twice and a
    list cut into several chunks reads as one text."""
    by_page: dict[tuple[str, int], list[Chunk]] = {}
    for result in results:
        by_page.setdefault((result.chunk.source, result.chunk.page), []).append(result.chunk)
    return [
        Chunk(chunks[0].chunk_id, chunks[0].doc_id, source, page, merge_passages(chunks))
        for (source, page), chunks in by_page.items()
    ]


def _chunk_index(chunk: Chunk) -> int:
    index = chunk.chunk_id.rsplit("::", 1)[-1]
    return int(index) if index.isdigit() else 0


def _first_line(text: str) -> str:
    return text.split("\n", 1)[0].strip()


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
    for ref in find_citations(text):
        key = (ref.source.lower(), ref.page)
        if key in seen:
            continue
        seen.add(key)
        chunks = by_page.get(key)
        if chunks is None:
            invalid.append(ref.text)
        else:
            citations.append(Citation(chunks[0].source, key[1], tuple(chunks)))
    return citations, invalid
