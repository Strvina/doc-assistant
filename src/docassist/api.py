"""REST API for integrations (n8n, other apps).

uv run uvicorn docassist.api:app --reload
→ http://localhost:8000/docs (interactive OpenAPI documentation)
"""

from __future__ import annotations

import secrets
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from docassist.agent import run_agent
from docassist.config import Settings
from docassist.factory import MissingAPIKeyError, build_llm, build_store, build_tool_context
from docassist.ingest import SUPPORTED_EXTENSIONS, DocumentLoadError, ingest
from docassist.llm import LLMError, LLMProvider
from docassist.logs import QueryLog
from docassist.rag import Answer, answer
from docassist.retrieval import EmbedderMismatchError, VectorStore

# --- request / response models -----------------------------------------------------------


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    provider: str | None = Field(default=None, description="claude | openai | fake")
    agent: bool = Field(default=False, description="Let the model use tools (search, escalate…)")
    history: list[tuple[str, str]] = Field(
        default_factory=list, description="Earlier (question, answer) pairs", max_length=10
    )


class CitationOut(BaseModel):
    source: str
    page: int
    text: str


class ToolCallOut(BaseModel):
    name: str
    input: dict[str, Any]
    output: str
    is_error: bool


class AskResponse(BaseModel):
    log_id: int | None = None
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


class FeedbackRequest(BaseModel):
    log_id: int
    rating: int = Field(description="1 = helpful, -1 = not helpful, 0 = clear")


class DocumentOut(BaseModel):
    doc_id: str
    source: str
    pages: int
    chunks: int


class IndexResult(BaseModel):
    source: str
    chunks_added: int
    status: str  # "indexed" | "unchanged" | "failed"
    error: str | None = None


# --- app ---------------------------------------------------------------------------------


# Dependencies live at module level: with `from __future__ import annotations`, FastAPI
# resolves annotation strings in module globals, so they cannot be local to create_app().


def get_settings(request: Request) -> Settings:
    state = request.app.state
    if state.settings is None:
        state.settings = Settings.from_env()
    return state.settings


SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_store(request: Request, settings: SettingsDep) -> VectorStore:
    state = request.app.state
    if state.store is None:
        try:
            state.store = build_store(settings)
        except (MissingAPIKeyError, EmbedderMismatchError) as exc:
            raise HTTPException(503, str(exc)) from exc
    return state.store


StoreDep = Annotated[VectorStore, Depends(get_store)]


def get_log(request: Request, settings: SettingsDep) -> QueryLog:
    state = request.app.state
    if state.log is None:
        state.log = QueryLog(settings.log_db)
    return state.log


LogDep = Annotated[QueryLog, Depends(get_log)]


def check_token(settings: SettingsDep, x_api_key: Annotated[str | None, Header()] = None) -> None:
    expected = settings.api_token
    if expected and not secrets.compare_digest(x_api_key or "", expected):
        raise HTTPException(401, "Missing or invalid X-API-Key header")


def create_app(
    settings: Settings | None = None,
    store: VectorStore | None = None,
    llm_factory: Callable[[Settings, str | None], LLMProvider] = build_llm,
) -> FastAPI:
    """Build the API. Arguments are for tests; by default everything comes from `.env`."""
    app = FastAPI(
        title="Doc Assistant API",
        description="Answers questions from company documents, with citations.",
        version="0.1.0",
    )
    app.state.settings = settings
    app.state.store = store
    app.state.log = None
    auth = [Depends(check_token)]

    @app.get("/health")
    def health(request: Request, settings: SettingsDep) -> dict[str, Any]:
        try:
            store = get_store(request, settings)
        except HTTPException as exc:
            return {"status": "degraded", "detail": exc.detail}
        return {"status": "ok", "documents": len(store.documents()), "chunks": store.count()}

    @app.get("/documents", dependencies=auth)
    def list_documents(store: StoreDep) -> list[DocumentOut]:
        return [DocumentOut(**vars_of(d)) for d in store.documents()]

    @app.post("/documents", dependencies=auth)
    def upload_documents(
        store: StoreDep, files: Annotated[list[UploadFile], File()]
    ) -> list[IndexResult]:
        results = []
        for upload in files:
            name = Path(upload.filename or "upload").name
            if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
                results.append(
                    IndexResult(
                        source=name, chunks_added=0, status="failed", error="unsupported file type"
                    )
                )
                continue
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / name  # keep the original name: it is the citation source
                path.write_bytes(upload.file.read())
                try:
                    added = store.add_document(ingest(path))
                except DocumentLoadError as exc:
                    results.append(
                        IndexResult(source=name, chunks_added=0, status="failed", error=str(exc))
                    )
                    continue
            status = "indexed" if added else "unchanged"
            results.append(IndexResult(source=name, chunks_added=added, status=status))
        return results

    @app.delete("/documents/{doc_id}", dependencies=auth)
    def delete_document(doc_id: str, store: StoreDep) -> dict[str, int]:
        removed = store.delete_document(doc_id)
        if not removed:
            raise HTTPException(404, f"No document with id {doc_id}")
        return {"chunks_removed": removed}

    @app.post("/ask", dependencies=auth)
    def ask(
        request: AskRequest, settings: SettingsDep, store: StoreDep, log: LogDep
    ) -> AskResponse:
        try:
            llm = llm_factory(settings, request.provider)
            if request.agent:
                ctx = build_tool_context(settings, store)
                result = run_agent(request.question, ctx, llm, history=request.history)
            else:
                result = answer(
                    request.question, store, llm, k=settings.top_k, history=request.history
                )
        except ValueError as exc:  # unknown provider name
            raise HTTPException(422, str(exc)) from exc
        except MissingAPIKeyError as exc:
            raise HTTPException(503, str(exc)) from exc
        except LLMError as exc:
            raise HTTPException(502, str(exc)) from exc
        response = to_response(result)
        response.log_id = log.log(result, channel="api", mode="agent" if request.agent else "rag")
        return response

    @app.post("/feedback", dependencies=auth)
    def feedback(request: FeedbackRequest, log: LogDep) -> dict[str, bool]:
        if request.rating not in (1, -1, 0):
            raise HTTPException(422, "rating must be 1, -1 or 0")
        if not log.set_feedback(request.log_id, request.rating or None):
            raise HTTPException(404, f"No query with log_id {request.log_id}")
        return {"ok": True}

    @app.get("/stats", dependencies=auth)
    def stats(log: LogDep) -> dict[str, Any]:
        return vars_of(log.stats())

    return app


def to_response(result: Answer) -> AskResponse:
    return AskResponse(
        answer=result.text,
        found=result.found,
        citations=[
            CitationOut(source=c.source, page=c.page, text=c.passage) for c in result.citations
        ],
        invalid_citations=result.invalid_citations,
        tool_calls=[ToolCallOut(**vars_of(call)) for call in result.tool_calls],
        provider=result.provider,
        model=result.model,
        latency_s=round(result.latency_s, 3),
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )


def vars_of(obj: Any) -> dict[str, Any]:
    """Fields of a (slotted) dataclass as a dict."""
    return {name: getattr(obj, name) for name in obj.__dataclass_fields__}


app = create_app()
