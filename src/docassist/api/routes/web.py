"""The browser UI page, its configuration and the health check."""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from docassist import __version__
from docassist.api.deps import ConversationsDep, SettingsDep, get_library
from docassist.api.security import UserDep, ensure_session, page_policy, permissions
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
def index(
    request: Request, settings: SettingsDep, conversations: ConversationsDep, user: UserDep
) -> Response:
    """The app, or the sign-in page when accounts are on and nobody is signed in."""
    sign_in = settings.login_required and user is None
    page = _page("login.html", "login.js") if sign_in else _page("index.html", "app.js")
    headers = {
        "Cache-Control": "no-cache",
        "Content-Security-Policy": page_policy(settings, inline_script_hashes(page)),
    }
    response = HTMLResponse(page, headers=headers)
    if not sign_in:
        ensure_session(request, response, settings, conversations)
    return response


_INLINE_SCRIPT = re.compile(r"<script>(.*?)</script>", re.DOTALL)


def inline_script_hashes(page: str) -> list[str]:
    """The CSP hashes (sha256, base64) of the page's inline scripts: only those may run."""
    return [
        base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        for script in _INLINE_SCRIPT.findall(page)
    ]


@router.get("/api/config")
def config(
    request: Request,
    response: Response,
    settings: SettingsDep,
    conversations: ConversationsDep,
    user: UserDep,
) -> dict[str, Any]:
    """What the web UI needs to know: available models, limits, demo mode, and who is signed
    in and what they may do."""
    if settings.login_required and user is None:
        raise HTTPException(401, "Sign in first")
    ensure_session(request, response, settings, conversations)
    can = permissions(user.role if user else "admin", settings)
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
        "login": settings.login_required,
        "user": {"username": user.username, "role": user.role} if user else None,
        "can": can,
        "max_upload_mb": settings.max_upload_mb,
        "extensions": list(SUPPORTED_EXTENSIONS),
        # Reading pictures with an AI model sends them to Google; off in the public demo.
        "vision": bool(settings.gemini_api_key) and can["edit_documents"],
        "max_questions_per_session": settings.max_questions_per_session,
        "top_k": settings.top_k,
        "embedding_model": settings.embedding_model,
        "escalation_teams": [
            {"id": team.id, "label": team.label} for team in settings.escalation_teams
        ],
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


def _page(name: str, script: str) -> str:
    """A page with versioned asset URLs, so browsers never mix a new script with an old
    app.css after an update (the static files themselves are served without cache headers)."""
    html = (WEB_DIR / name).read_text(encoding="utf-8")
    for name in ("app.css", script):
        digest = hashlib.sha256((WEB_DIR / name).read_bytes()).hexdigest()[:10]
        html = html.replace(f"/static/{name}", f"/static/{name}?v={digest}")
    return html
