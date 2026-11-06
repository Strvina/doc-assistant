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

# Windows keeps a window opened by a background program (the server) behind the window the
# user works in. So the dialog gets an owner window that is shown, invisible and always on
# top (a dialog owned by a topmost window is on top too), and the script borrows the input of
# the foreground window for a moment (AttachThreadInput), which lets it take the focus.
_WINDOWS_PICKER = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class DocAssistFront {
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hWnd, IntPtr pid);
    [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] static extern bool AttachThreadInput(uint from, uint to, bool attach);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] static extern bool BringWindowToTop(IntPtr hWnd);
    public static void Bring(IntPtr window) {
        uint front = GetWindowThreadProcessId(GetForegroundWindow(), IntPtr.Zero);
        uint mine = GetCurrentThreadId();
        bool attached = front != mine && AttachThreadInput(mine, front, true);
        BringWindowToTop(window);
        SetForegroundWindow(window);
        if (attached) { AttachThreadInput(mine, front, false); }
    }
}
'@
$owner = New-Object System.Windows.Forms.Form -Property @{
    TopMost = $true; ShowInTaskbar = $false; FormBorderStyle = 'None'; Opacity = 0
    StartPosition = 'CenterScreen'; Size = New-Object System.Drawing.Size(1, 1)
}
$owner.Show()
[DocAssistFront]::Bring($owner.Handle)
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Izaberi folder iz kog se dokumenti sami dodaju u biblioteku'
$dialog.ShowNewFolderButton = $true
if ($env:DOCASSIST_START -and (Test-Path -LiteralPath $env:DOCASSIST_START)) {
    $dialog.SelectedPath = $env:DOCASSIST_START
}
$result = $dialog.ShowDialog($owner)
$owner.Close()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) { $dialog.SelectedPath }
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
