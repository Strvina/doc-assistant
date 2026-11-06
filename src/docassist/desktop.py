"""Open a folder in the file manager ("Otvori folder" in the "Automatski uvoz" panel, ADR-037).

Only when the browser runs on the same computer as the server (the usual setup on one PC) and
that computer has a desktop. Choosing a folder happens in the page itself (`folders`, ADR-038):
Windows keeps a dialog opened by the server behind the browser.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# As a plain string: the type checker would treat the other platforms as unreachable code.
_SYSTEM: str = sys.platform


def available() -> bool:
    """True when this computer has a desktop to open a folder on."""
    if _SYSTEM in ("win32", "darwin"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) and bool(
        shutil.which("xdg-open")
    )


def open_folder(path: Path) -> None:
    """Show the folder in Explorer / Finder / the file manager."""
    if sys.platform == "win32":  # a check the type checker knows: startfile is Windows-only
        os.startfile(path)  # a folder the app itself checked (inbox.check_folder)
    else:
        subprocess.Popen(["open" if _SYSTEM == "darwin" else "xdg-open", str(path)])
