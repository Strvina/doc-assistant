"""The signed-in user's own account: their name and their password (the account menu)."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request

from docassist.api.deps import UsersDep
from docassist.api.routes.auth import LOGIN_ATTEMPTS, failures
from docassist.api.routes.users import refused
from docassist.api.schemas import AccountOut, AccountUpdate, PasswordChange
from docassist.api.security import SESSION_COOKIE, MeDep, require_accounts
from docassist.users import User, UserError

router = APIRouter(prefix="/account", tags=["accounts"], dependencies=[Depends(require_accounts)])


@router.get("")
def my_account(me: MeDep) -> AccountOut:
    return _out(me)


@router.patch("")
def change_my_account(body: AccountUpdate, me: MeDep, users: UsersDep) -> AccountOut:
    try:
        users.set_full_name(me.id, body.full_name)
    except UserError as exc:
        raise refused(exc) from exc
    changed = users.get(me.id)
    if changed is None:  # removed by an administrator meanwhile
        raise HTTPException(401, "Sign in first")
    return _out(changed)


@router.post("/password")
def change_my_password(
    body: PasswordChange, request: Request, me: MeDep, users: UsersDep
) -> dict[str, bool]:
    """A new password, given the current one. Other devices are signed out; this browser
    stays signed in."""
    # Someone at an unlocked computer could otherwise guess the password here without limit.
    wrong = failures(request, me.username)
    if len(wrong) >= LOGIN_ATTEMPTS:
        raise HTTPException(429, "Too many wrong passwords. Try again in 15 minutes.")
    try:
        users.change_own_password(
            me.id,
            body.current_password,
            body.new_password,
            keep_session=request.cookies[SESSION_COOKIE],
        )
    except UserError as exc:
        if exc.code == "wrong_password":
            wrong.append(time.monotonic())
        raise refused(exc) from exc
    wrong.clear()
    return {"ok": True}


def _out(user: User) -> AccountOut:
    return AccountOut(
        username=user.username,
        full_name=user.full_name,
        role=user.role,
        created_at=user.created_at,
    )
