"""Assemble the FastAPI app: shared state, middleware, static files, the routers and the
background work that runs while the server is up (the inbox folder, ADR-036)."""

from __future__ import annotations

import logging
import threading
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from docassist import __version__, inbox
from docassist.api.deps import LLMFactory, VisionFactory, app_settings_of, library_of
from docassist.api.routes import ask, conversations, documents, monitoring, reports, web
from docassist.api.security import security_headers
from docassist.config import Settings
from docassist.factory import build_llm, build_vision
from docassist.library import Library
from docassist.retrieval import VectorStore

log = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    store: VectorStore | None = None,
    llm_factory: LLMFactory = build_llm,
    vision_factory: VisionFactory = build_vision,
) -> FastAPI:
    """Build the app. Arguments are for tests; by default everything comes from `.env`."""
    app = FastAPI(
        title="Doc Assistant API",
        description="Answers questions from company documents, with citations.",
        version=__version__,
        lifespan=_lifespan,
    )
    state = app.state
    state.settings = settings
    state.store = store
    state.llm_factory = llm_factory
    state.vision_factory = vision_factory
    state.library = None
    state.log = None
    state.conversations = None
    state.sessions = {}  # session id -> questions asked
    state.cancelled = set()  # request ids of questions the user cancelled
    state.ip_hits = defaultdict(deque)  # client ip -> timestamps of recent questions
    state.vision_progress = {}  # source -> (pages read, pages with pictures)
    state.rebuild = None  # reindex.Rebuild while a stale index is rebuilt (ADR-032)
    state.rebuild_lock = threading.Lock()
    state.inbox = None  # inbox.Inbox while the server watches the inbox folder (ADR-036)

    app.middleware("http")(security_headers)
    app.include_router(web.router)
    app.mount("/static", StaticFiles(directory=web.WEB_DIR), name="static")
    for module in (documents, ask, conversations, monitoring, reports):
        app.include_router(module.router)
    return app


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    stop = threading.Event()
    _watch_inbox(app.state, stop)
    yield
    stop.set()


def _watch_inbox(state: Any, stop: threading.Event) -> None:
    """Import files dropped into INBOX_DIR while the server runs; off in the public demo
    (no uploads) and with INBOX_POLL_S=0."""
    settings = app_settings_of(state)
    if not settings.allow_uploads or settings.inbox_poll_s <= 0:
        return
    state.inbox = inbox.Inbox(settings.inbox_dir)
    state.inbox.folder.mkdir(parents=True, exist_ok=True)

    def library() -> Library | None:
        try:
            return library_of(state, settings)
        except HTTPException:  # e.g. the index is being rebuilt: try again next time
            return None

    inbox.watch(state.inbox, library, settings.inbox_poll_s, stop)
    log.info("Watching %s for new documents", settings.inbox_dir)
