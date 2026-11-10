"""The administrator's "Korisnici" panel: who may sign in and with which role (ADR-049)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from docassist.api.deps import ConversationsDep, UsersDep
from docassist.api.schemas import UserCreate, UserOut, UserUpdate, fields_of
from docassist.api.security import ADMIN, UserDep, require_accounts
from docassist.users import User, UserError

router = APIRouter(
    prefix="/users", tags=["accounts"], dependencies=[Depends(require_accounts), *ADMIN]
)


@router.get("")
def list_users(users: UsersDep, me: UserDep) -> list[UserOut]:
    return [_out(user, me) for user in users.all()]


@router.post("", status_code=201)
def add_user(body: UserCreate, users: UsersDep, me: UserDep) -> UserOut:
    try:
        created = users.add(body.username, body.password, body.role)
    except UserError as exc:
        raise refused(exc) from exc
    return _out(created, me)


@router.patch("/{user_id}")
def change_user(user_id: int, body: UserUpdate, users: UsersDep, me: UserDep) -> UserOut:
    """Another role, a new password (signs the user out everywhere), or disabled / enabled."""
    target = users.get(user_id)
    if target is None:
        raise refused(UserError("No such user", "missing"))
    demotes = body.role is not None and body.role != target.role
    if me is not None and me.id == user_id and (demotes or body.disabled):
        # A slip here would lock the administrator out of this very panel.
        raise refused(UserError("You cannot change your own role or disable yourself", "self"))
    try:
        if body.role is not None and body.role != target.role:
            users.set_role(user_id, body.role)
        if body.disabled is not None and body.disabled != target.disabled:
            users.set_disabled(user_id, body.disabled)
        if body.password is not None:
            users.set_password(user_id, body.password)
    except UserError as exc:
        raise refused(exc) from exc
    changed = users.get(user_id)
    if changed is None:  # another administrator removed it meanwhile
        raise refused(UserError("No such user", "missing"))
    return _out(changed, me)


@router.delete("/{user_id}")
def remove_user(
    user_id: int, users: UsersDep, conversations: ConversationsDep, me: UserDep
) -> dict[str, int]:
    """Delete an account and the conversations saved under it. To keep them, disable the
    account instead."""
    if me is not None and me.id == user_id:
        raise refused(UserError("You cannot delete your own account", "self"))
    try:
        users.remove(user_id)
    except UserError as exc:
        raise refused(exc) from exc
    return {"conversations_deleted": conversations.delete_owner(f"user:{user_id}")}


def _out(user: User, me: User | None) -> UserOut:
    return UserOut(**fields_of(user), me=me is not None and me.id == user.id)


def refused(error: UserError) -> HTTPException:
    """404 for an unknown user, otherwise 422; the UI words the reason by its `code`."""
    status = 404 if error.code == "missing" else 422
    return HTTPException(status, {"code": error.code, "message": str(error)})
