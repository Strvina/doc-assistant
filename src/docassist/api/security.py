"""Who is calling and what they may do: API key, browser session or signed-in user and their
role, question limits, the session cookie, requests from other websites (CSRF) and the
security headers."""

from __future__ import annotations

import secrets
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from docassist.api.deps import ConversationsDep, SettingsDep, UsersDep, app_settings
from docassist.config import Settings
from docassist.conversations import ConversationStore
from docassist.users import User, has_role

SESSION_COOKIE = "da_session"
SESSION_MAX_AGE_S = 365 * 24 * 3600  # saved conversations belong to the session
IP_WINDOW_S = 3600


def current_user(request: Request, settings: SettingsDep, users: UsersDep) -> User | None:
    """Who is signed in in this browser. None when nobody is, and always without accounts
    (LOGIN_REQUIRED=false)."""
    if not settings.login_required:
        return None
    cookie = request.cookies.get(SESSION_COOKIE)
    return users.signed_in(cookie) if cookie else None


UserDep = Annotated[User | None, Depends(current_user)]


def client_kind(
    request: Request,
    settings: SettingsDep,
    user: UserDep,
    conversations: ConversationsDep,
    x_api_key: Annotated[str | None, Header()] = None,
) -> str:
    """'api' (valid X-API-Key), 'web' (a browser: with LOGIN_REQUIRED a signed-in user,
    otherwise a session) or 'open' (neither API_TOKEN nor accounts configured)."""
    expected = settings.api_token
    if expected and x_api_key and secrets.compare_digest(x_api_key, expected):
        return "api"
    if settings.login_required:
        if user is None:
            raise HTTPException(401, "Sign in first")
        return "web"
    if known_session(request, conversations):
        return "web"
    if not expected:
        return "open"
    raise HTTPException(401, "Missing or invalid X-API-Key header")


ClientDep = Annotated[str, Depends(client_kind)]


def owner(request: Request, kind: ClientDep, user: UserDep) -> str:
    """Whose conversations: the signed-in user (on any device), the browser session, or one
    shared owner for API clients."""
    if kind == "web":
        return f"user:{user.id}" if user else f"session:{request.cookies[SESSION_COOKIE]}"
    return kind  # "api" (valid X-API-Key) or "open" (no API_TOKEN configured)


OwnerDep = Annotated[str, Depends(owner)]


def role(kind: ClientDep, user: UserDep) -> str:
    """What the caller may do (one of users.ROLES). Without accounts, and for an integration
    with the API key, that is everything the server allows, as before accounts existed."""
    return user.role if kind == "web" and user else "admin"


RoleDep = Annotated[str, Depends(role)]


def at_least(minimum: str) -> Any:
    """A route dependency: the caller needs the rights of `minimum` (or more)."""

    def check(role: RoleDep) -> None:
        if not has_role(role, minimum):
            raise HTTPException(403, f"This needs the {minimum} role")

    return Depends(check)


def require_uploads(settings: SettingsDep) -> None:
    """Changing shared data (documents, the question log) is off in the public demo."""
    if not settings.allow_uploads:
        raise HTTPException(403, "Uploading and deleting is disabled in this demo")


# Route dependencies. ACCESS: any known client. EDITOR / ADMIN: that role or more.
# UPLOADS: an editor changing the documents; ADMIN_CHANGES: an administrator changing shared
# data (the question log, the inbox). The last two are off in the public demo.
ACCESS = [Depends(client_kind)]
EDITOR = [at_least("editor")]
ADMIN = [at_least("admin")]
UPLOADS = [*ACCESS, Depends(require_uploads), at_least("editor")]
ADMIN_CHANGES = [*ACCESS, Depends(require_uploads), at_least("admin")]


def permissions(role: str, settings: Settings) -> dict[str, bool]:
    """What the web UI shows for a role; the server checks each of them again."""
    editor, admin = has_role(role, "editor"), has_role(role, "admin")
    return {
        "edit_documents": editor and settings.allow_uploads,
        "see_gaps": editor,
        "see_usage": admin,
        "delete_questions": admin and settings.allow_uploads,
        "manage_inbox": admin and settings.allow_uploads,
        "manage_users": admin and settings.login_required,
    }


def known_session(request: Request, conversations: ConversationStore) -> str | None:
    """The browser's session id, if it is one of ours. A session survives a server restart:
    it is stored with the conversations."""
    sessions = request.app.state.sessions
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return None
    if cookie not in sessions:
        if not conversations.has_session(cookie):
            return None
        sessions[cookie] = 0  # question limits restart, the conversations stay
    return cookie


def ensure_session(
    request: Request,
    response: Response,
    settings: Settings,
    conversations: ConversationStore,
) -> None:
    """Give the browser a session, unless it has one."""
    if known_session(request, conversations) is None:
        start_session(request, response, settings, conversations)


def start_session(
    request: Request,
    response: Response,
    settings: Settings,
    conversations: ConversationStore,
) -> str:
    """A new session and its cookie (also on sign-in, so a session id that was known before
    someone signed in is never the one that is signed in)."""
    session_id = secrets.token_urlsafe(24)
    request.app.state.sessions[session_id] = 0
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
        return session_id
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=SESSION_MAX_AGE_S,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",  # behind the HTTPS proxy: never sent in clear
    )
    return session_id


def check_limits(request: Request, settings: Settings, user: User | None = None) -> None:
    """Protect the API budget: per browser session and per IP address for anonymous
    visitors (a public demo), per person for signed-in users."""
    state = request.app.state
    session_id = request.cookies.get(SESSION_COOKIE)
    if user is None and session_id in state.sessions:
        if state.sessions[session_id] >= settings.max_questions_per_session:
            raise HTTPException(429, "Question limit for this session reached")
        state.sessions[session_id] += 1

    # The address of the connection, never a request header a client can make up: behind a
    # proxy (Caddy, Docker) uvicorn takes it from X-Forwarded-For, but only when the proxy
    # is trusted (`--forwarded-allow-ips`). A whole company can share one address, so
    # signed-in users are counted each on their own.
    address = request.client.host if request.client else "?"
    hits = state.ip_hits[f"user:{user.id}" if user else address]
    now = time.monotonic()
    while hits and now - hits[0] > IP_WINDOW_S:
        hits.popleft()
    if len(hits) >= settings.max_questions_per_ip_hour:
        raise HTTPException(429, "Too many questions in the last hour, try again later")
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
