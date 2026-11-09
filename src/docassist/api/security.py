"""Who is calling and how much they may ask: API key or browser session, question limits,
the session cookie, requests from other websites (CSRF) and the security headers."""

from __future__ import annotations

import secrets
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from docassist.api.deps import SettingsDep, app_settings
from docassist.config import Settings
from docassist.conversations import ConversationStore

SESSION_COOKIE = "da_session"
SESSION_MAX_AGE_S = 365 * 24 * 3600  # saved conversations belong to the session
IP_WINDOW_S = 3600


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


def owner(request: Request, kind: ClientDep) -> str:
    """Whose conversations: the browser session, or one shared owner for API clients."""
    if kind == "web":
        return f"session:{request.cookies[SESSION_COOKIE]}"
    return kind  # "api" (valid X-API-Key) or "open" (no API_TOKEN configured)


OwnerDep = Annotated[str, Depends(owner)]


def require_uploads(settings: SettingsDep) -> None:
    """Changing shared data (documents, the question log) is off in the public demo."""
    if not settings.allow_uploads:
        raise HTTPException(403, "Uploading and deleting is disabled in this demo")


# Route dependencies: any known client, or a client that may also change shared data.
ACCESS = [Depends(client_kind)]
UPLOADS = [Depends(client_kind), Depends(require_uploads)]


def ensure_session(
    request: Request,
    response: Response,
    settings: Settings,
    conversations: ConversationStore,
) -> None:
    """Give the browser a session; a known session survives a server restart (it is stored
    with the conversations), so the user keeps their saved conversations."""
    sessions = request.app.state.sessions
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie in sessions:
        return
    if cookie and conversations.has_session(cookie):
        sessions[cookie] = 0  # question limits restart, the conversations stay
        return
    session_id = secrets.token_urlsafe(24)
    sessions[session_id] = 0
    conversations.add_session(session_id)
    if settings.embed_origins:
        # Inside an iframe on another site (a Hugging Face Space) a "strict" cookie is never
        # sent; a partitioned cookie works there and is still separate per embedding site.
        # Written by hand: Starlette's `partitioned=True` needs Python 3.14 (Docker/CI: 3.12).
        cookie = (
            f"{SESSION_COOKIE}={session_id}; Max-Age={SESSION_MAX_AGE_S}; Path=/; HttpOnly; "
            "Secure; SameSite=None; Partitioned"
        )
        response.raw_headers.append((b"set-cookie", cookie.encode("latin-1")))
        return
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=SESSION_MAX_AGE_S,
        httponly=True,
        samesite="strict",
    )


def check_limits(request: Request, settings: Settings) -> None:
    """Protect the API budget of a public demo: per browser session and per IP address."""
    state = request.app.state
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id in state.sessions:
        if state.sessions[session_id] >= settings.max_questions_per_session:
            raise HTTPException(429, "Question limit for this session reached")
        state.sessions[session_id] += 1

    # The address of the connection, never a request header a client can make up: behind a
    # proxy (Caddy, Docker) uvicorn takes it from X-Forwarded-For, but only when the proxy
    # is trusted (`--forwarded-allow-ips`).
    ip = request.client.host if request.client else "?"
    hits = state.ip_hits[ip]
    now = time.monotonic()
    while hits and now - hits[0] > IP_WINDOW_S:
        hits.popleft()
    if len(hits) >= settings.max_questions_per_ip_hour:
        raise HTTPException(429, "Too many questions from this address, try again later")
    hits.append(now)


def frame_ancestors(settings: Settings) -> str:
    """The CSP directive for who may show the app in an iframe (EMBED_ORIGINS, ADR-020)."""
    return "frame-ancestors " + " ".join(("'self'", *settings.embed_origins))


def page_policy(settings: Settings, script_hashes: Iterable[str]) -> str:
    """Content-Security-Policy of the web UI page: scripts, styles and requests only from
    this server, plus the inline scripts with these sha256 hashes (base64).

    The page shows text written by the AI model and found in documents; it is escaped, and
    this policy is the second line: even text that slipped through as HTML cannot run code.
    """
    scripts = " ".join(f"'sha256-{digest}'" for digest in script_hashes)
    return (
        f"default-src 'self'; script-src 'self' {scripts}; style-src 'self'; "
        "img-src 'self' data: blob:; frame-src 'self' blob:; base-uri 'self'; "
        f"form-action 'self'; {frame_ancestors(settings)}"
    )


UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def is_cross_site(request: Request) -> bool:
    """True when another website's page sent this request through the user's browser.

    Browsers say where a request comes from (`Sec-Fetch-Site`, older ones only `Origin`);
    API clients such as n8n or curl send neither, so they are never blocked.
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        return site not in ("same-origin", "none")  # "none": typed in the address bar
    origin = request.headers.get("origin")
    if origin is None:
        return False
    return urlsplit(origin).netloc != request.headers.get("host")


async def block_cross_site(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """HTTP middleware against cross-site request forgery (CSRF).

    Without API_TOKEN the API answers anyone, and a form or `fetch` on another site can send
    a POST (an upload, "scan the inbox", a question that costs quota) without the page ever
    reading the answer. Changes are accepted only from the app's own pages.
    """
    if request.method in UNSAFE_METHODS and is_cross_site(request):
        return JSONResponse({"detail": "Requests from other websites are not allowed"}, 403)
    return await call_next(request)


async def security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """HTTP middleware: no MIME sniffing, no framing by other sites (except EMBED_ORIGINS)."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    settings = app_settings(request)
    if settings.embed_origins:  # e.g. a Hugging Face Space shows the app in an iframe
        response.headers.setdefault("Content-Security-Policy", frame_ancestors(settings))
    else:
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response
