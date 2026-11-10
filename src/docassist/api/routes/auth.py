"""Signing in and out with a user account (ADR-048)."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request, Response

from docassist.api.deps import ConversationsDep, SettingsDep, UsersDep
from docassist.api.schemas import LoginRequest
from docassist.api.security import SESSION_COOKIE, start_session

router = APIRouter(prefix="/auth", tags=["accounts"])

# Wrong passwords for one user name from one address before sign-in pauses: slow enough to
# stop guessing, short enough that a person who forgot their password is not locked out.
LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_S = 15 * 60
MAX_TRACKED = 1000  # (address, name) pairs remembered; expired ones are dropped beyond it


@router.post("/login")
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    settings: SettingsDep,
    users: UsersDep,
    conversations: ConversationsDep,
) -> dict[str, str]:
    if not settings.login_required:
        raise HTTPException(404, "This server has no user accounts (LOGIN_REQUIRED=false)")
    wrong = failures(request, body.username)
    if len(wrong) >= LOGIN_ATTEMPTS:
        raise HTTPException(429, "Too many wrong passwords. Try again in 15 minutes.")
    user = users.verify(body.username, body.password)
    if user is None:
        wrong.append(time.monotonic())
        raise HTTPException(401, "Wrong user name or password")
    wrong.clear()

    before = request.cookies.get(SESSION_COOKIE)
    session_id = start_session(request, response, settings, conversations)
    users.sign_in(session_id, user.id)
    if before:
        users.sign_out(before)  # someone else was signed in in this browser
        # What was asked in this browser before there were accounts stays with the person.
        conversations.claim(f"session:{before}", f"user:{user.id}")
    return {"username": user.username, "role": user.role}


@router.post("/logout")
def logout(request: Request, response: Response, users: UsersDep) -> dict[str, bool]:
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        users.sign_out(cookie)
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


def failures(request: Request, username: str) -> list[float]:
    """Times of the recent wrong passwords for this user name from this address (at sign-in
    and when changing one's own password)."""
    tracked = request.app.state.login_failures
    now = time.monotonic()
    if len(tracked) > MAX_TRACKED:
        for key in [
            k for k, times in tracked.items() if not times or now - times[-1] > LOGIN_WINDOW_S
        ]:
            del tracked[key]
    address = request.client.host if request.client else "?"
    times: list[float] = tracked[(address, username.strip().lower())]
    while times and now - times[0] > LOGIN_WINDOW_S:
        times.pop(0)
    return times
