"""Doc Assistant web app: the browser UI and the REST API for integrations (n8n, other apps).

uv run uvicorn docassist.api:app --reload
→ http://localhost:8000        web UI
→ http://localhost:8000/docs   interactive API documentation

Access: without API_TOKEN everything is open (internal network). With API_TOKEN set,
integrations send ``X-API-Key`` and browsers get a session cookie when they open the UI.
"""

from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from docassist import __version__
from docassist.agent import run_agent
from docassist.config import Settings
from docassist.factory import MissingAPIKeyError, build_llm, build_store, build_tool_context
from docassist.ingest import DocumentLoadError
from docassist.library import Library
from docassist.llm import LLMError, LLMProvider
from docassist.logs import QueryLog
from docassist.pricing import cost_usd
from docassist.rag import Answer, answer, merge_passages
from docassist.retrieval import EmbedderMismatchError, VectorStore
from docassist.viewer import pdf_with_highlights, render_html

WEB_DIR = Path(__file__).parent / "web"
SAMPLE_DOCS = Path(__file__).resolve().parents[2] / "data" / "sample_docs"
SESSION_COOKIE = "da_session"
PROVIDER_LABELS = {"claude": "Claude", "openai": "ChatGPT", "fake": "Offline demo"}

# --- request / response models -----------------------------------------------------------


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    provider: str | None = Field(default=None, description="claude | openai | fake")
    agent: bool = Field(default=False, description="Let the model use tools (search, escalate…)")
    top_k: int | None = Field(default=None, ge=1, le=10, description="Passages for the answer")
    history: list[tuple[str, str]] = Field(
        default_factory=list, description="Earlier (question, answer) pairs", max_length=10
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


class FeedbackRequest(BaseModel):
    log_id: int
    rating: int = Field(description="1 = helpful, -1 = not helpful, 0 = clear")


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


# --- dependencies ------------------------------------------------------------------------
# They live at module level: with `from __future__ import annotations`, FastAPI resolves
# annotation strings in module globals, so they cannot be local to create_app().


def get_settings(request: Request) -> Settings:
    state = request.app.state
    if state.settings is None:
        state.settings = Settings.from_env()
    return state.settings


SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_library(request: Request, settings: SettingsDep) -> Library:
    state = request.app.state
    if state.library is None:
        try:
            store = state.store or build_store(settings)
        except (MissingAPIKeyError, EmbedderMismatchError) as exc:
            raise HTTPException(503, str(exc)) from exc
        state.library = Library(store, settings.documents_dir)
        if not settings.allow_uploads and store.count() == 0:
            # Public demo: the document set is fixed, so index the demo set on first use.
            for path in sorted(SAMPLE_DOCS.glob("*.*")):
                state.library.add_file(path)
    return state.library


LibraryDep = Annotated[Library, Depends(get_library)]


def get_store(library: LibraryDep) -> VectorStore:
    return library.store


StoreDep = Annotated[VectorStore, Depends(get_store)]


def get_log(request: Request, settings: SettingsDep) -> QueryLog:
    state = request.app.state
    if state.log is None:
        state.log = QueryLog(settings.log_db)
    return state.log


LogDep = Annotated[QueryLog, Depends(get_log)]


def client_kind(
    request: Request, settings: SettingsDep, x_api_key: Annotated[str | None, Header()] = None
) -> str:
    """'api' (valid X-API-Key), 'web' (browser session) or 'open' (no API_TOKEN configured)."""
    expected = settings.api_token
    if expected and x_api_key and secrets.compare_digest(x_api_key, expected):
        return "api"
    if request.cookies.get(SESSION_COOKIE) in request.app.state.sessions:
        return "web"
    if not expected:
        return "open"
    raise HTTPException(401, "Missing or invalid X-API-Key header")


ClientDep = Annotated[str, Depends(client_kind)]


def require_uploads(settings: SettingsDep) -> None:
    if not settings.allow_uploads:
        raise HTTPException(403, "Uploading and deleting documents is disabled in this demo")


# --- app ---------------------------------------------------------------------------------


def create_app(
    settings: Settings | None = None,
    store: VectorStore | None = None,
    llm_factory: Callable[[Settings, str | None], LLMProvider] = build_llm,
) -> FastAPI:
    """Build the app. Arguments are for tests; by default everything comes from `.env`."""
    app = FastAPI(
        title="Doc Assistant API",
        description="Answers questions from company documents, with citations.",
        version=__version__,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.library = None
    app.state.log = None
    app.state.sessions = {}  # session id -> questions asked
    app.state.ip_hits = defaultdict(deque)  # client ip -> timestamps of recent questions
    access = [Depends(client_kind)]
    uploads = [Depends(client_kind), Depends(require_uploads)]

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    # --- web UI ---------------------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    def index(request: Request) -> Response:
        response = FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})
        _ensure_session(request, response)
        return response

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/api/config")
    def config(request: Request, response: Response, settings: SettingsDep) -> dict[str, Any]:
        """What the web UI needs to know: available models, limits, demo mode."""
        _ensure_session(request, response)
        available = {
            "claude": bool(settings.anthropic_api_key),
            "openai": bool(settings.openai_api_key and settings.openai_model),
            "fake": True,
        }
        providers = [
            {"id": p, "label": PROVIDER_LABELS[p], "available": available[p]}
            for p in ("claude", "openai", "fake")
            if p != "openai" or available["openai"]  # ChatGPT is optional (ADR-018)
        ]
        return {
            "version": __version__,
            "providers": providers,
            "default_provider": settings.llm_provider,
            "models": {"claude": settings.anthropic_model, "openai": settings.openai_model},
            "allow_uploads": settings.allow_uploads,
            "max_upload_mb": settings.max_upload_mb,
            "max_questions_per_session": settings.max_questions_per_session,
            "top_k": settings.top_k,
            "embedding_model": settings.embedding_model,
        }

    # --- documents ------------------------------------------------------------------------

    @app.get("/health")
    def health(request: Request, settings: SettingsDep) -> dict[str, Any]:
        try:
            library = get_library(request, settings)
        except HTTPException as exc:
            return {"status": "degraded", "detail": exc.detail}
        return {
            "status": "ok",
            "version": __version__,
            "documents": len(library.documents()),
            "chunks": library.store.count(),
        }

    @app.get("/documents", dependencies=access)
    def list_documents(library: LibraryDep) -> list[DocumentOut]:
        return [
            DocumentOut(
                **vars_of(d),
                has_file=(library.files_dir / d.source).is_file(),
                language=library.language(d.source),
            )
            for d in library.documents()
        ]

    @app.post("/documents", dependencies=uploads)
    def upload_documents(
        library: LibraryDep, settings: SettingsDep, files: Annotated[list[UploadFile], File()]
    ) -> list[IndexResult]:
        limit = settings.max_upload_mb * 1024 * 1024
        results = []
        for upload in files:
            name = upload.filename or "upload"
            data = upload.file.read(limit + 1)
            if len(data) > limit:
                results.append(_failed(name, f"file is larger than {settings.max_upload_mb} MB"))
                continue
            try:
                added = library.add_bytes(name, data)
            except DocumentLoadError as exc:  # includes unsupported file types
                results.append(_failed(name, str(exc)))
                continue
            results.append(IndexResult(**vars_of(added), status=added.status))
        return results

    @app.post("/documents/demo", dependencies=uploads)
    def load_demo_documents(library: LibraryDep) -> list[IndexResult]:
        results = []
        for path in sorted(SAMPLE_DOCS.glob("*.*")):
            added = library.add_file(path)
            results.append(IndexResult(**vars_of(added), status=added.status))
        return results

    @app.delete("/documents/{doc_id}", dependencies=uploads)
    def delete_document(doc_id: str, library: LibraryDep) -> dict[str, int]:
        removed = library.delete(doc_id)
        if not removed:
            raise HTTPException(404, f"No document with id {doc_id}")
        return {"chunks_removed": removed}

    @app.get("/documents/{doc_id}/view", dependencies=access)
    def view_document(
        doc_id: str,
        library: LibraryDep,
        page: Annotated[int | None, Query(ge=1)] = None,
        chunk: Annotated[list[str] | None, Query()] = None,
    ) -> Response:
        """The original document for the viewer, cited passage highlighted on `page`."""
        found = library.original(doc_id)
        if found is None:
            raise HTTPException(404, "Document or its original file not found")
        doc, path = found
        passage = None
        if chunk:
            wanted = set(chunk)
            chunks = [c for c in library.store.document_chunks(doc.source) if c.chunk_id in wanted]
            passage = merge_passages(chunks) or None
        disposition = {"Content-Disposition": f"inline; filename*=UTF-8''{quote(doc.source)}"}
        if path.suffix.lower() == ".pdf":
            data = pdf_with_highlights(path, page, passage)
            return Response(data, media_type="application/pdf", headers=disposition)
        return HTMLResponse(render_html(path, page, passage))

    @app.get("/documents/{doc_id}/file", dependencies=access)
    def download_document(doc_id: str, library: LibraryDep) -> FileResponse:
        found = library.original(doc_id)
        if found is None:
            raise HTTPException(404, "Document or its original file not found")
        doc, path = found
        return FileResponse(path, filename=doc.source)

    # --- questions ------------------------------------------------------------------------

    @app.post("/ask")
    def ask(
        body: AskRequest,
        request: Request,
        kind: ClientDep,
        settings: SettingsDep,
        store: StoreDep,
        log: LogDep,
    ) -> AskResponse:
        if kind != "api":
            _check_limits(request, settings)
        try:
            llm = llm_factory(settings, body.provider)
            top_k = body.top_k or settings.top_k
            if body.agent:
                ctx = build_tool_context(settings, store)
                ctx.top_k = top_k
                result = run_agent(body.question, ctx, llm, history=body.history)
            else:
                result = answer(body.question, store, llm, k=top_k, history=body.history)
        except ValueError as exc:  # unknown provider name
            raise HTTPException(422, str(exc)) from exc
        except MissingAPIKeyError as exc:
            raise HTTPException(503, str(exc)) from exc
        except LLMError as exc:
            raise HTTPException(502, str(exc)) from exc
        response = to_response(result)
        channel = "web" if kind == "web" else "api"
        response.log_id = log.log(result, channel=channel, mode="agent" if body.agent else "rag")
        return response

    @app.post("/feedback", dependencies=access)
    def feedback(body: FeedbackRequest, log: LogDep) -> dict[str, bool]:
        if body.rating not in (1, -1, 0):
            raise HTTPException(422, "rating must be 1, -1 or 0")
        if not log.set_feedback(body.log_id, body.rating or None):
            raise HTTPException(404, f"No query with log_id {body.log_id}")
        return {"ok": True}

    @app.get("/stats", dependencies=access)
    def stats(log: LogDep) -> dict[str, Any]:
        return vars_of(log.stats())

    @app.get("/insights", dependencies=access)
    def insights(log: LogDep) -> dict[str, Any]:
        """Monitoring: usage numbers, latest questions and the gaps in the documentation."""
        fields = ("id", "created_at", "question", "found", "feedback", "latency_ms", "model")
        extra = ("mode", "cost_usd", "input_tokens", "output_tokens")

        def slim(rows: list[dict[str, object]]) -> list[dict[str, object]]:
            return [{key: row.get(key) for key in fields + extra} for row in rows]

        return {
            "stats": vars_of(log.stats()),
            "recent": slim(log.recent(15)),
            "gaps": slim(log.gaps(15)),
        }

    return app


# --- helpers -----------------------------------------------------------------------------


def _ensure_session(request: Request, response: Response) -> None:
    sessions = request.app.state.sessions
    if request.cookies.get(SESSION_COOKIE) in sessions:
        return
    session_id = secrets.token_urlsafe(24)
    sessions[session_id] = 0
    response.set_cookie(SESSION_COOKIE, session_id, httponly=True, samesite="strict")


def _check_limits(request: Request, settings: Settings) -> None:
    """Protect the API budget of a public demo: per browser session and per IP address."""
    state = request.app.state
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id in state.sessions:
        if state.sessions[session_id] >= settings.max_questions_per_session:
            raise HTTPException(429, "Question limit for this session reached")
        state.sessions[session_id] += 1

    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() or (request.client.host if request.client else "?")
    hits = state.ip_hits[ip]
    now = time.monotonic()
    while hits and now - hits[0] > 3600:
        hits.popleft()
    if len(hits) >= settings.max_questions_per_ip_hour:
        raise HTTPException(429, "Too many questions from this address, try again later")
    hits.append(now)


def _failed(name: str, error: str) -> IndexResult:
    return IndexResult(source=Path(name).name, chunks_added=0, status="failed", error=error)


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
        tool_calls=[ToolCallOut(**vars_of(call)) for call in result.tool_calls],
        provider=result.provider,
        model=result.model,
        latency_s=round(result.latency_s, 3),
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=cost_usd(result.model, result.input_tokens, result.output_tokens),
    )


def vars_of(obj: Any) -> dict[str, Any]:
    """Fields of a (slotted) dataclass as a dict."""
    return {name: getattr(obj, name) for name in obj.__dataclass_fields__}


app = create_app()
