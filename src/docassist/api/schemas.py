"""Request and response models of the REST API, and the conversion of an `Answer`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from docassist.pricing import cost_usd
from docassist.rag import Answer


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    provider: str | None = Field(default=None, description="claude | openai | gemini | groq | fake")
    agent: bool = Field(default=False, description="Let the model use tools (search, escalate…)")
    top_k: int | None = Field(default=None, ge=1, le=10, description="Passages for the answer")
    history: list[tuple[str, str]] = Field(
        default_factory=list, description="Earlier (question, answer) pairs", max_length=10
    )
    conversation_id: str | None = Field(
        default=None,
        description="Continue a saved conversation; empty starts a new one (see /conversations)",
    )
    request_id: str | None = Field(
        default=None, max_length=64, description="Client id for POST /ask/cancel"
    )
    doc_ids: list[str] | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="Search only these documents (ids from GET /documents); empty: all",
    )


class CitationOut(BaseModel):
    source: str
    page: int
    doc_id: str
    chunk_ids: list[str]
    text: str


class ToolCallOut(BaseModel):
    name: str
    input: dict[str, Any]
    output: str
    is_error: bool


class AskResponse(BaseModel):
    log_id: int | None = None
    conversation_id: str | None = None
    answer: str
    found: bool
    citations: list[CitationOut]
    invalid_citations: list[str]
    tool_calls: list[ToolCallOut]
    provider: str
    model: str
    latency_s: float
    input_tokens: int
    output_tokens: int
    cost_usd: float | None = None
    search_query: str = ""  # a follow-up question rewritten for the search (ADR-031)
    searched_documents: list[str] = Field(
        default_factory=list, description="File names the search was limited to; empty: all"
    )


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class CancelRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=64)


class FeedbackRequest(BaseModel):
    log_id: int
    rating: int = Field(description="1 = helpful, -1 = not helpful, 0 = clear")


class EscalateRequest(BaseModel):
    log_id: int
    team: str | None = Field(default=None, description="Team id from /api/config; default: first")
    contact: str = Field(default="", max_length=200, description="How to reach the person asking")


class EscalateResponse(BaseModel):
    reference: str
    team: str | None = None
    already_sent: bool = False


class DocumentOut(BaseModel):
    doc_id: str
    source: str
    pages: int
    chunks: int
    has_file: bool = False
    language: str | None = None


class IndexResult(BaseModel):
    source: str
    chunks_added: int
    status: str  # "indexed" | "unchanged" | "failed"
    error: str | None = None

    @classmethod
    def failed(cls, name: str, error: str) -> IndexResult:
        return cls(source=Path(name).name, chunks_added=0, status="failed", error=error)


class PicturesResult(BaseModel):
    source: str
    pages_total: int  # pages with pictures
    pages_read: int  # of those, read by the vision model (now or earlier)
    chunks: int  # chunks of the document after indexing it again
    model: str
    error: str | None = None  # why it stopped early, e.g. the free-tier limit


def to_response(result: Answer) -> AskResponse:
    return AskResponse(
        answer=result.text,
        found=result.found,
        citations=[
            CitationOut(
                source=c.source,
                page=c.page,
                doc_id=c.chunks[0].doc_id,
                chunk_ids=[chunk.chunk_id for chunk in c.chunks],
                text=c.passage,
            )
            for c in result.citations
        ],
        invalid_citations=result.invalid_citations,
        tool_calls=[ToolCallOut(**fields_of(call)) for call in result.tool_calls],
        provider=result.provider,
        model=result.model,
        latency_s=round(result.latency_s, 3),
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=cost_usd(result.model, result.input_tokens, result.output_tokens),
        search_query=result.search_query,
    )


def fields_of(obj: Any) -> dict[str, Any]:
    """Fields of a (slotted) dataclass as a dict."""
    return {name: getattr(obj, name) for name in obj.__dataclass_fields__}
