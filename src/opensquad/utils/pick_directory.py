"""Native OS folder picker for the local Launcher (Agent Web Open Folder).

Browsers cannot expose absolute paths from ``webkitdirectory``. The Launcher
runs on the same machine as the workspace, so it can open a real folder dialog
and return the absolute path to the frontend.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from typing import Any

logger = logging.getLogger(__name__)

_pick_lock = threading.Lock()


def pick_directory(initial_dir: str | None = None) -> dict[str, Any]:
    """Open a native folder dialog on this machine.

    Returns:
        ``{"path": "<abs path>"}`` on success,
        ``{"path": None, "cancelled": True}`` when the user dismisses,
        ``{"path": None, "error": "..."}`` on failure.
    """
    start = (initial_dir or "").strip() or None
    if start and not os.path.isdir(start):
        start = os.path.dirname(start) if start else None
        if start and not os.path.isdir(start):
            start = None

    if not _pick_lock.acquire(blocking=False):
        return {"path": None, "error": "A folder picker is already open"}

    try:
        path = _pick_via_tkinter(start)
        if path is not None:
            return {"path": path, "cancelled": False} if path else {"path": None, "cancelled": True}

        if sys.platform == "win32":
            path = _pick_via_powershell(start)
            if path is not None:
                return {"path": path, "cancelled": False} if path else {"path": None, "cancelled": True}

        if sys.platform == "darwin":
            path = _pick_via_osascript(start)
            if path is not None:
                return {"path": path, "cancelled": False} if path else {"path": None, "cancelled": True}

        if sys.platform.startswith("linux"):
            path = _pick_via_zenity(start)
            if path is not None:
                return {"path": path, "cancelled": False} if path else {"path": None, "cancelled": True}

        return {
            "path": None,
            "error": "No native folder dialog available on this host (need tkinter / OS picker)",
        }
    except Exception as e:
        logger.exception("[pick_directory] failed")
        return {"path": None, "error": str(e)}
    finally:
        _pick_lock.release()


def _pick_via_tkinter(initial_dir: str | None) -> str | bool | None:
    """Return path string, empty string if cancelled, or None if unavailable."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as e:
        logger.debug("[pick_directory] tkinter unavailable: %s", e)
        return None

    root = tk.Tk()
    root.withdraw()
    try:
        try:
            root.wm_attributes("-topmost", True)
        except Exception:
            pass
        kwargs: dict[str, Any] = {
            "mustexist": True,
            "title": "Select project folder",
        }
        if initial_dir:
            kwargs["initialdir"] = initial_dir
        chosen = filedialog.askdirectory(**kwargs)
        return chosen if chosen else ""
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def _pick_via_powershell(initial_dir: str | None) -> str | bool | None:
    """Windows Forms FolderBrowserDialog via PowerShell."""
    initial = (initial_dir or "").replace("'", "''")
    script = f"""
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Select project folder'
$dialog.ShowNewFolderButton = $true
{f"$dialog.SelectedPath = '{initial}'" if initial else ""}
$result = $dialog.ShowDialog()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {{
  Write-Output $dialog.SelectedPath
}}
"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except Exception as e:
        logger.debug("[pick_directory] powershell picker failed: %s", e)
        return None
    out = (proc.stdout or "").strip()
    if out:
        return out
    # Empty stdout with exit 0 → cancelled; non-zero → unavailable/error
    if proc.returncode == 0:
        return ""
    return None


def _pick_via_osascript(initial_dir: str | None) -> str | bool | None:
    """macOS choose folder dialog."""
    if initial_dir:
        script = f'POSIX path of (choose folder with prompt "Select project folder" default location POSIX file "{initial_dir}")'
    else:
        script = 'POSIX path of (choose folder with prompt "Select project folder")'
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except Exception as e:
        logger.debug("[pick_directory] osascript failed: %s", e)
        return None
    out = (proc.stdout or "").strip()
    if out:
        return out.rstrip("/")
    if proc.returncode != 0:
        # User cancel is typically -128
        err = (proc.stderr or "").lower()
        if "user canceled" in err or "user cancelled" in err or proc.returncode == 1:
            return ""
        return None
    return ""


def _pick_via_zenity(initial_dir: str | None) -> str | bool | None:
    """Linux zenity directory picker."""
    cmd = ["zenity", "--file-selection", "--directory", "--title=Select project folder"]
    if initial_dir:
        cmd.append(f"--filename={initial_dir}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.debug("[pick_directory] zenity failed: %s", e)
        return None
    out = (proc.stdout or "").strip()
    if out:
        return out
    # zenity returns 1 on cancel
    return "" if proc.returncode in (0, 1) else None
