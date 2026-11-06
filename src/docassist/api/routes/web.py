"""The browser UI page, its configuration and the health check."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from docassist import __version__
from docassist.api.deps import ConversationsDep, SettingsDep, get_library
from docassist.api.security import ensure_session
from docassist.ingest.loaders import SUPPORTED_EXTENSIONS

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
PROVIDER_LABELS = {
    "claude": "Claude",
    "openai": "ChatGPT",
    "gemini": "Gemini",
    "groq": "Groq",
    "fake": "Offline demo",
}
OPTIONAL_PROVIDERS = ("openai", "gemini", "groq")  # listed only when configured (ADR-018/21/22)

router = APIRouter()


@router.get("/", include_in_schema=False)
def index(request: Request, settings: SettingsDep, conversations: ConversationsDep) -> Response:
    response = HTMLResponse(_index_html(), headers={"Cache-Control": "no-cache"})
    ensure_session(request, response, settings, conversations)
    return response


@router.get("/api/config")
def config(
    request: Request,
    response: Response,
    settings: SettingsDep,
    conversations: ConversationsDep,
) -> dict[str, Any]:
    """What the web UI needs to know: available models, limits, demo mode."""
    ensure_session(request, response, settings, conversations)
    available = {
        "claude": bool(settings.anthropic_api_key),
        "openai": bool(settings.openai_api_key and settings.openai_model),
        "gemini": bool(settings.gemini_api_key),
        "groq": bool(settings.groq_api_key),
        "fake": True,
    }
    providers = [
        {"id": p, "label": PROVIDER_LABELS[p], "available": available[p]}
        for p in PROVIDER_LABELS
        if p not in OPTIONAL_PROVIDERS or available[p]
    ]
    return {
        "version": __version__,
        "providers": providers,
        "default_provider": settings.llm_provider,
        "models": {
            "claude": settings.anthropic_model,
            "openai": settings.openai_model,
            "gemini": settings.gemini_model,
            "groq": settings.groq_model,
        },
        "allow_uploads": settings.allow_uploads,
        "max_upload_mb": settings.max_upload_mb,
        "extensions": list(SUPPORTED_EXTENSIONS),
        # Reading pictures with an AI model sends them to Google; off in the public demo.
        "vision": bool(settings.gemini_api_key) and settings.allow_uploads,
        "max_questions_per_session": settings.max_questions_per_session,
        "top_k": settings.top_k,
        "embedding_model": settings.embedding_model,
        "escalation_teams": [
            {"id": team.id, "label": team.label} for team in settings.escalation_teams
        ],
        # The folder whose files are imported automatically (ADR-036), or None when off.
        "inbox": str(settings.inbox_dir) if request.app.state.inbox is not None else None,
    }


@router.get("/health")
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


def _index_html() -> str:
    """index.html with versioned asset URLs, so browsers never mix a new app.js with an old
    app.css after an update (the static files themselves are served without cache headers)."""
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    for name in ("app.css", "app.js"):
        digest = hashlib.sha256((WEB_DIR / name).read_bytes()).hexdigest()[:10]
        html = html.replace(f"/static/{name}", f"/static/{name}?v={digest}")
    return html
