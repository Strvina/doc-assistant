"""Dependencies shared by the routes: settings, the library (index), logs, conversations and
the model factories. Everything lives on `app.state`, so tests can pass their own.

The dependencies live at module level: with `from __future__ import annotations`, FastAPI
resolves annotation strings in module globals.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request

from docassist import reindex
from docassist.config import Settings
from docassist.conversations import ConversationStore
from docassist.factory import MissingAPIKeyError, build_store
from docassist.ingest.vision import ReadImage
from docassist.library import Library
from docassist.llm import LLMProvider
from docassist.logs import QueryLog
from docassist.retrieval import EmbedderMismatchError, VectorStore

SAMPLE_DOCS = Path(__file__).resolve().parents[3] / "data" / "sample_docs"

LLMFactory = Callable[[Settings, str | None], LLMProvider]
VisionFactory = Callable[[Settings], tuple[ReadImage, str]]


def app_settings(request: Request) -> Settings:
    """The app's settings, read from `.env` on first use (also usable in a middleware)."""
    return app_settings_of(request.app.state)


def app_settings_of(state: Any) -> Settings:
    if state.settings is None:
        state.settings = Settings.from_env()
    settings: Settings = state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(app_settings)]


def get_library(request: Request, settings: SettingsDep) -> Library:
    return library_of(request.app.state, settings)


def library_of(state: Any, settings: Settings) -> Library:
    """The app's library, opened on first use. Raises HTTPException 503 while it cannot be
    used (stale index being rebuilt, missing originals, missing API key)."""
    if state.library is None:
        with state.rebuild_lock:
            _open_library(state, settings)
    library: Library = state.library
    return library


def _open_library(state: Any, settings: Settings) -> None:
    """Open the index, or start rebuilding it when it is stale (ADR-032). Raises 503 while
    a rebuild runs."""
    if state.library is not None:
        return
    rebuilding = state.rebuild
    if rebuilding is not None and (not rebuilding.finished or rebuilding.error):
        raise HTTPException(503, rebuilding.message)
    try:
        if state.store is None and reindex.is_unfinished(settings.index_dir):
            raise EmbedderMismatchError("An earlier rebuild of the index did not finish.")
        store = state.store or build_store(settings)
    except MissingAPIKeyError as exc:
        raise HTTPException(503, str(exc)) from exc
    except EmbedderMismatchError as exc:
        missing = reindex.missing_originals(settings)
        if missing:  # rebuilding would lose these documents: leave the index alone
            names = ", ".join(missing)
            raise HTTPException(
                503,
                f"{exc} No stored original for: {names}. Upload them again or run "
                "`index --rebuild` with their files.",
            ) from exc

        def done(library: Library) -> None:
            state.library = library

        state.rebuild = reindex.start(settings, done)
        raise HTTPException(503, state.rebuild.message) from exc
    state.library = Library(store, settings.documents_dir)
    if not settings.allow_uploads and store.count() == 0:
        # Public demo: the document set is fixed, so index the demo set on first use.
        for path in sorted(SAMPLE_DOCS.glob("*.*")):
            state.library.add_file(path)


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


def get_conversations(request: Request, settings: SettingsDep) -> ConversationStore:
    state = request.app.state
    if state.conversations is None:
        state.conversations = ConversationStore(settings.log_db)
    return state.conversations


ConversationsDep = Annotated[ConversationStore, Depends(get_conversations)]


def get_llm_factory(request: Request) -> LLMFactory:
    factory: LLMFactory = request.app.state.llm_factory
    return factory


LLMFactoryDep = Annotated[LLMFactory, Depends(get_llm_factory)]


def get_vision_factory(request: Request) -> VisionFactory:
    factory: VisionFactory = request.app.state.vision_factory
    return factory


VisionFactoryDep = Annotated[VisionFactory, Depends(get_vision_factory)]
