"""Open a folder in the file manager and let the user pick one with the system dialog.

Used by the "Automatski uvoz" panel (ADR-037) when the browser runs on the same computer as
the server (the usual setup on one PC). On a server without a desktop (Docker, Linux without a
display) both are unavailable and the UI shows a text field instead.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

PICK_TIMEOUT_S = 600  # the dialog waits for the user
# As a plain string: the type checker would treat the other platforms as unreachable code.
_SYSTEM: str = sys.platform

_WINDOWS_PICKER = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Izaberi folder iz kog se dokumenti sami dodaju u biblioteku'
$dialog.ShowNewFolderButton = $true
if ($env:DOCASSIST_START -and (Test-Path -LiteralPath $env:DOCASSIST_START)) {
    $dialog.SelectedPath = $env:DOCASSIST_START
}
$owner = New-Object System.Windows.Forms.Form -Property @{ TopMost = $true }
if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { $dialog.SelectedPath }
"""


def available() -> bool:
    """True when this computer has a desktop to open a folder or a dialog on."""
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


def pick_folder(start: Path | None = None) -> Path | None:
    """The folder the user picks in the system dialog, or None when cancelled/unavailable."""
    env = {**os.environ, "DOCASSIST_START": str(start or "")}
    if _SYSTEM == "win32":
        command = ["powershell", "-NoProfile", "-STA", "-Command", _WINDOWS_PICKER]
    elif _SYSTEM == "darwin":
        prompt = 'POSIX path of (choose folder with prompt "Izaberi folder za automatski uvoz")'
        command = ["osascript", "-e", prompt]
    elif shutil.which("zenity"):
        command = ["zenity", "--file-selection", "--directory", "--title=Automatski uvoz"]
    else:
        return None
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=PICK_TIMEOUT_S,
            env=env,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    chosen = result.stdout.decode("utf-8", "replace").strip()
    return Path(chosen) if result.returncode == 0 and chosen else None
