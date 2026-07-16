"""Windows-safe subprocess helpers.

``CREATE_NO_WINDOW`` + console ``python.exe`` often fails with
``0xc0000142`` (STATUS_DLL_INIT_FAILED) on some Windows / Anaconda setups,
and the OS pops a blocking error dialog. Prefer ``STARTF_USESHOWWINDOW`` +
``SW_HIDE`` to hide the console without that flag.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any


def hidden_run_kwargs() -> dict[str, Any]:
    """Kwargs for ``subprocess.run`` / short probes (keep capture_output usable)."""
    if sys.platform != "win32":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {
        "startupinfo": si,
        "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    }


def detach_popen_kwargs() -> dict[str, Any]:
    """Kwargs for long-lived background services (outlive the CLI, no flash)."""
    kw: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        kw["startupinfo"] = si
        # Do NOT use CREATE_NO_WINDOW — triggers 0xc0000142 dialogs with python.exe
        kw["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        kw["close_fds"] = True
    else:
        kw["start_new_session"] = True
    return kw
