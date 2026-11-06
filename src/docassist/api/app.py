"""Assemble the FastAPI app: shared state, middleware, static files and the routers."""

from __future__ import annotations

import threading
from collections import defaultdict, deque

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from docassist import __version__
from docassist.api.deps import LLMFactory, VisionFactory
from docassist.api.routes import ask, conversations, documents, monitoring, web
from docassist.api.security import security_headers
from docassist.config import Settings
from docassist.factory import build_llm, build_vision
from docassist.retrieval import VectorStore


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

    app.middleware("http")(security_headers)
    app.include_router(web.router)
    app.mount("/static", StaticFiles(directory=web.WEB_DIR), name="static")
    for module in (documents, ask, conversations, monitoring):
        app.include_router(module.router)
    return app
