"""The "Automatski uvoz" panel: the watched folder, its imports and failures (ADR-037).

Anyone who may upload sees the panel, checks the folder now and retries or removes failed
files. Choosing another folder, opening it in the file manager and the folder dialog work only
from the computer the app runs on: a folder is a path on that computer, and a remote user must
not be able to point the app at any folder of the server.
"""

from __future__ import annotations

import ipaddress
import threading
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from docassist import desktop
from docassist.api.deps import (
    LibraryDep,
    SettingsDep,
    app_folders,
    inbox_config_file,
)
from docassist.api.schemas import FolderCheck, InboxUpdate
from docassist.api.security import ACCESS, UPLOADS
from docassist.config import Settings
from docassist.inbox import FolderError, Inbox, InboxConfig, check_folder, waiting_in

router = APIRouter(prefix="/inbox", tags=["inbox"])


def is_local(request: Request) -> bool:
    """True when the browser runs on the computer the app runs on (not through a proxy)."""
    if request.headers.get("x-forwarded-for") or request.client is None:
        return False
    host = request.client.host
    if host in ("localhost", "testclient"):  # "testclient": FastAPI's TestClient
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def require_local(request: Request) -> None:
    if not is_local(request):
        raise HTTPException(403, "The folder can be changed only on the computer the app runs on")


def get_inbox(request: Request) -> Inbox:
    watched: Inbox | None = request.app.state.inbox
    if watched is None:
        raise HTTPException(409, "Automatic import is turned off on this server (INBOX_POLL_S=0)")
    return watched


InboxDep = Annotated[Inbox, Depends(get_inbox)]
LOCAL = [*UPLOADS, Depends(require_local)]


@router.get("", dependencies=ACCESS)
def inbox_status(request: Request, settings: SettingsDep) -> dict[str, Any]:
    """The watched folder, what waits in it, the latest imports and the failed files."""
    watched: Inbox | None = request.app.state.inbox
    local = is_local(request)
    if watched is None:
        return {"watched": False, "local": local}
    return _status(watched, settings, local)


@router.put("", dependencies=LOCAL)
def update_inbox(
    body: InboxUpdate, request: Request, settings: SettingsDep, watched: InboxDep
) -> dict[str, Any]:
    """Turn the import on or off, or watch another folder (kept in `inbox.json`)."""
    if body.folder is not None:
        watched.folder = _checked(body.folder, settings)
    if body.enabled is not None:
        watched.enabled = body.enabled
    InboxConfig(watched.folder, watched.enabled).save(inbox_config_file(settings))
    return _status(watched, settings, is_local(request))


@router.post("/check", dependencies=LOCAL)
def check(body: FolderCheck, settings: SettingsDep) -> dict[str, Any]:
    """Whether a folder can be the inbox, and how many documents already wait in it."""
    folder = _checked(body.folder, settings)
    return {"folder": str(folder), "waiting": waiting_in(folder)}


_dialog_open = threading.Lock()  # one folder dialog at a time (a double click opens one)


@router.post("/pick", dependencies=LOCAL)
def pick(watched: InboxDep) -> dict[str, Any]:
    """Let the user choose a folder in the system dialog (nothing is saved yet)."""
    if not desktop.available():
        raise HTTPException(409, "This computer has no desktop to show a folder dialog on")
    if not _dialog_open.acquire(blocking=False):
        raise HTTPException(409, "Prozor za izbor foldera je već otvoren.")
    try:
        chosen = desktop.pick_folder(watched.folder)
    finally:
        _dialog_open.release()
    return {"folder": str(chosen) if chosen else None}


@router.post("/open", dependencies=LOCAL)
def open_folder(watched: InboxDep) -> dict[str, bool]:
    """Show the watched folder in Explorer / Finder."""
    if not desktop.available():
        raise HTTPException(409, "This computer has no desktop to open the folder on")
    watched.folder.mkdir(parents=True, exist_ok=True)
    desktop.open_folder(watched.folder)
    return {"ok": True}


@router.post("/scan", dependencies=UPLOADS)
def scan_now(
    request: Request, library: LibraryDep, settings: SettingsDep, watched: InboxDep
) -> dict[str, Any]:
    """Import what waits in the folder now, without waiting for the next check."""
    results = watched.scan(library)
    return {
        "imported": [vars(item) for item in results],
        **_status(watched, settings, is_local(request)),
    }


@router.post("/failed/{name}/retry", dependencies=UPLOADS)
def retry(name: str, watched: InboxDep) -> dict[str, bool]:
    """Put a failed file back into the folder; the next check tries it again."""
    if not watched.retry(name):
        raise HTTPException(404, f"No failed file {name!r}")
    return {"ok": True}


@router.delete("/failed/{name}", dependencies=UPLOADS)
def discard(name: str, watched: InboxDep) -> dict[str, bool]:
    """Delete a failed file and its reason."""
    if not watched.discard(name):
        raise HTTPException(404, f"No failed file {name!r}")
    return {"ok": True}


def _checked(folder: str, settings: Settings) -> Path:
    protected, exact = app_folders(settings)
    try:
        return check_folder(folder, protected, exact)
    except FolderError as exc:  # the message is written for the person choosing
        raise HTTPException(422, str(exc)) from exc


def _status(watched: Inbox, settings: Settings, local: bool) -> dict[str, Any]:
    available = watched.folder.is_dir()
    return {
        "watched": True,
        "enabled": watched.enabled,
        "folder": str(watched.folder),
        "available": available,
        "waiting": len(watched.waiting()) if available else 0,
        "every_s": settings.inbox_poll_s,
        "local": local,
        "desktop": local and desktop.available(),
        "recent": [vars(item) for item in watched.recent],
        "failed": [vars(item) for item in watched.failed()] if available else [],
    }
