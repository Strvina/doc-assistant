"""Assemble the FastAPI app: shared state, middleware, static files, the routers and the
background work that runs while the server is up (the inbox folder, ADR-036)."""

from __future__ import annotations

import logging
import threading
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from docassist import __version__, inbox
from docassist.api.deps import (
    LLMFactory,
    VisionFactory,
    app_settings_of,
    inbox_config_file,
    library_of,
)
from docassist.api.routes import (
    ask,
    auth,
    conversations,
    documents,
    monitoring,
    reports,
    users,
    web,
)
from docassist.api.routes import inbox as inbox_routes
from docassist.api.security import block_cross_site, security_headers
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
    state.users = None
    state.login_failures = defaultdict(list)  # (address, user name) -> times of wrong passwords
    state.sessions = {}  # session id -> questions asked
    state.cancelled = set()  # request ids of questions the user cancelled
    state.ip_hits = defaultdict(deque)  # client ip -> timestamps of recent questions
    state.vision_progress = {}  # source -> (pages read, pages with pictures)
    state.rebuild = None  # reindex.Rebuild while a stale index is rebuilt (ADR-032)
    state.rebuild_lock = threading.Lock()
    state.inbox = None  # inbox.Inbox while the server watches the inbox folder (ADR-036)

    app.middleware("http")(security_headers)
    app.middleware("http")(block_cross_site)
    app.include_router(web.router)
    app.mount("/static", StaticFiles(directory=web.WEB_DIR), name="static")
    for module in (auth, users, documents, inbox_routes, ask, conversations, monitoring, reports):
        app.include_router(module.router)
    return app


LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging() -> None:
    """Show the app's own log lines (imports, rebuilds, failed scans) next to uvicorn's.

    uvicorn configures only its own loggers: without a handler, the app's INFO lines were
    dropped and warnings came out without a time. Left alone when logging is already
    configured (e.g. `uvicorn --log-config`).
    """
    logger = logging.getLogger("docassist")
    if logger.handlers or logging.getLogger().handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    stop = threading.Event()
    _watch_inbox(app.state, stop)
    yield
    stop.set()


def _watch_inbox(state: Any, stop: threading.Event) -> None:
    """Import files dropped into the inbox folder while the server runs: the folder chosen in
    the UI (`inbox.json`), else INBOX_DIR. Off in the public demo (no uploads) and with
    INBOX_POLL_S=0."""
    settings = app_settings_of(state)
    if not settings.allow_uploads or settings.inbox_poll_s <= 0:
        return
    config = inbox.InboxConfig.load(inbox_config_file(settings), settings.inbox_dir)
    with suppress(OSError):  # e.g. a network drive that is not connected: the panel says so
        config.folder.mkdir(parents=True, exist_ok=True)
    state.inbox = inbox.Inbox(
        config.folder, enabled=config.enabled, history=inbox.ImportLog(settings.log_db)
    )

    def library() -> Library | None:
        try:
            return library_of(state, settings)
        except HTTPException:  # e.g. the index is being rebuilt: try again next time
            return None

    inbox.watch(state.inbox, library, settings.inbox_poll_s, stop)
    log.info("Watching %s for new documents", config.folder)
