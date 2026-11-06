"""Browse the folders of the computer the app runs on, for choosing the inbox in the web UI
(ADR-038).

A dialog inside the page instead of the system folder dialog: Windows keeps a window opened
by a background program (the server) behind the browser, and a server without a desktop
(Docker, a service) cannot show one at all. Only folder names are listed, never files.
"""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_FOLDERS = 500  # a folder with more subfolders is cut (and says so)


@dataclass(frozen=True)
class Folder:
    name: str
    path: str


@dataclass(frozen=True)
class Listing:
    path: str | None  # None: the starting view (places and drives)
    parent: str | None  # None: no level above (a drive, "/")
    folders: list[Folder]
    truncated: bool = False


def places() -> list[Folder]:
    """Well-known folders of the user that exist here: Desktop, Documents, Downloads, OneDrive."""
    home = Path.home()
    onedrives = [
        Path(p) for p in (os.environ.get(v) for v in ("OneDrive", "OneDriveCommercial")) if p
    ]
    candidates = [
        ("Radna površina", [d / "Desktop" for d in (*onedrives, home)]),  # often moved to OneDrive
        ("Dokumenti", [d / "Documents" for d in (*onedrives, home)]),
        ("Preuzimanja", [home / "Downloads"]),
        *((f"OneDrive ({d.name})" if len(onedrives) > 1 else "OneDrive", [d]) for d in onedrives),
        ("Početni folder", [home]),
    ]
    found: list[Folder] = []
    for label, paths in candidates:
        path = next((p for p in paths if p.is_dir()), None)
        if path is not None and all(str(path) != f.path for f in found):
            found.append(Folder(label, str(path)))
    return found


def drives() -> list[Folder]:
    """The drives (Windows: C:\\, D:\\ …) or the file system root."""
    if sys.platform == "win32":  # a check the type checker knows: listdrives is Windows-only
        roots = [Folder(f"Disk {d.rstrip(chr(92))}", d) for d in os.listdrives()]
    else:
        roots = [Folder("/", "/")]
    return roots


def list_folders(path: str | Path) -> Listing:
    """The subfolders of `path`, by name; hidden and system folders and those without access
    are left out. Raises NotADirectoryError / FileNotFoundError for a bad path."""
    folder = Path(path).expanduser().resolve()
    if not folder.is_dir():
        raise NotADirectoryError(str(folder))
    try:
        with os.scandir(folder) as entries:
            names = [entry.name for entry in entries if _visible_folder(entry)]
    except PermissionError:
        names = []
    names.sort(key=str.lower)
    parent = folder.parent if folder.parent != folder else None
    return Listing(
        path=str(folder),
        parent=str(parent) if parent else None,
        folders=[Folder(name, str(folder / name)) for name in names[:MAX_FOLDERS]],
        truncated=len(names) > MAX_FOLDERS,
    )


def _visible_folder(entry: os.DirEntry[str]) -> bool:
    if entry.name.startswith((".", "$")) or entry.name in ("System Volume Information",):
        return False
    try:
        if not entry.is_dir():
            return False
        attributes = getattr(entry.stat(), "st_file_attributes", 0)  # Windows only
    except OSError:
        return False
    hidden = stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM
    return not attributes & hidden
