"""An interactive shell the user drives from the agent-web right panel.

What this is: a long-lived shell child (``cmd.exe`` on Windows, a POSIX shell elsewhere)
whose **stdin the panel writes to** and whose output is streamed back over the existing
``job_stdout`` / ``job_status`` events. That reuse is deliberate — the websocket already
forwards arbitrary ``{type: "command"}`` frames to the agent, and already relays those two
event types to the browser, so an interactive terminal needs no new event type, no
``protocol_version`` entry and no gateway route. The events are keyed by
``call_id = job_id = "terminal:<id>"`` so they cannot collide with an agent's jobs.

What this is **not**: a TTY. There is no pty/ConPTY in this repository, so ``isatty()`` is
false: full-screen programs (vim, top/htop), colour escape sequences and interactive
password prompts do not work properly. Line-oriented tools — git, npm, python, ls, grep,
builds, tests — do, which is what the panel is for. The UI says so as well, so a TUI that
refuses to start is not a mystery.

Safety: the working directory goes through the same workspace resolution and the same
``is_path_safe`` gate the agent's own shells use, so the terminal cannot be pointed outside
the workspace. The command blocklist is *not* applied here, on purpose: this is the human
operator typing, the same as the "open in terminal" button that already spawns a real shell
today.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import uuid
from typing import Any

logger = logging.getLogger(__name__)

_TERMINALS: dict[str, TerminalSession] = {}
_LOCK = threading.Lock()

READ_CHUNK = 4096
# Keep the scrollback the panel receives per terminal bounded; the UI keeps its own copy
# and this process is long-lived.
MAX_BUFFER = 400_000


def _shell_command() -> list[str]:
    """The shell to run: honest defaults per platform, overridable by the environment."""
    if os.name == "nt":
        return [os.environ.get("COMSPEC") or "cmd.exe"]
    return [os.environ.get("SHELL") or "/bin/bash"]


def _shell_label() -> str:
    return "cmd" if os.name == "nt" else "bash"


class TerminalSession:
    """One shell child plus the thread that streams its output."""

    def __init__(self, terminal_id: str, *, cwd: str = "", sid: str = ""):
        self.id = terminal_id
        self.cwd = cwd
        self.sid = sid
        self.shell_type = _shell_label()
        self.process: subprocess.Popen | None = None
        self.exited = False
        self.return_code: int | None = None
        self.error = ""
        self._lock = threading.Lock()

    # ── lifecycle ───────────────────────────────────────────────────────────
    def start(self) -> dict[str, Any]:
        from opensquad.tools.system import _noninteractive_shell_env
        from opensquad.utils.path_utils import is_path_safe

        if not is_path_safe(self.cwd):
            return {"ok": False, "error": f"working directory is outside the workspace: {self.cwd}"}
        popen_kw: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "cwd": self.cwd,
            "env": _noninteractive_shell_env(),
            "bufsize": 0,
        }
        creationflags = 0
        startupinfo = None
        if os.name == "nt":  # pragma: no cover - platform specific
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
        else:
            popen_kw["start_new_session"] = True  # own process group: SIGINT reaches the shell
        try:
            self.process = subprocess.Popen(
                _shell_command(),
                creationflags=creationflags,
                startupinfo=startupinfo,
                **popen_kw,
            )
        except Exception as exc:  # noqa: BLE001 - reported to the panel
            self.error = str(exc)
            logger.warning("[Terminal] failed to start a shell: %s", exc)
            return {"ok": False, "error": str(exc)}
        threading.Thread(target=self._pump, name=f"terminal-{self.id}", daemon=True).start()
        if os.name == "nt":  # pragma: no cover - platform specific
            # cmd echoes what it reads from a pipe; the panel echoes the typed line itself,
            # so without this every command would appear twice on Windows.
            self.write("@echo off\r\n")
        self._emit_status("running")
        return {"ok": True, "terminal_id": self.id, "cwd": self.cwd, "shell": self.shell_type}

    def _pump(self) -> None:
        """Read the child's output and stream it, until it exits."""
        stream = getattr(self.process, "stdout", None)
        if stream is None:
            return
        try:
            while True:
                data = stream.read(READ_CHUNK)
                if not data:
                    break
                if isinstance(data, bytes):
                    text = data.decode("utf-8", errors="replace")
                else:
                    text = str(data)
                if text:
                    self._emit_stdout(text)
        except Exception as exc:  # noqa: BLE001 - a closed pipe on exit is normal
            logger.debug("[Terminal] %s read loop ended: %s", self.id, exc)
        finally:
            code = None
            try:
                code = self.process.wait(timeout=5) if self.process else None
            except Exception:
                code = None
            self.exited = True
            self.return_code = code
            self._emit_status("done", return_code=code)

    # ── input ───────────────────────────────────────────────────────────────
    def write(self, text: str) -> dict[str, Any]:
        if self.exited or self.process is None or self.process.stdin is None:
            return {"ok": False, "error": "terminal is not running"}
        try:
            self.process.stdin.write(text.encode("utf-8"))
            self.process.stdin.flush()
        except Exception as exc:  # noqa: BLE001 - reported, never raised at the user
            return {"ok": False, "error": f"write failed: {exc}"}
        return {"ok": True}

    def interrupt(self) -> dict[str, Any]:
        """Send the shell an interrupt (Ctrl+C)."""
        if self.exited or self.process is None:
            return {"ok": False, "error": "terminal is not running"}
        pid = self.process.pid
        try:
            if os.name == "nt":  # pragma: no cover - platform specific
                self.process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(os.getpgid(pid), signal.SIGINT)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"interrupt failed: {exc}"}
        return {"ok": True}

    def close(self) -> dict[str, Any]:
        """Stop the shell and its children."""
        proc = self.process
        if proc is None or proc.poll() is not None:
            self.exited = True
            return {"ok": True, "already_closed": True}
        pid = proc.pid
        try:
            if os.name == "nt":  # pragma: no cover - platform specific
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    shell=False,
                )
            else:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except Exception:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[Terminal] %s close failed: %s", self.id, exc)
        self.exited = True
        self._emit_status("aborted", reason="closed")
        return {"ok": True}

    # ── events ──────────────────────────────────────────────────────────────
    def _emit(self, etype: str, payload: dict[str, Any]) -> None:
        from opensquad.tools.system import emit_job_event

        emit_job_event(
            etype,
            {
                "call_id": self.call_id,
                "job_id": self.id,
                "sid": self.sid,
                "command": self.label,
                "session_id": self.sid,
                "shell_type": self.shell_type,
                **payload,
            },
        )

    def _emit_stdout(self, chunk: str) -> None:
        self._emit("job_stdout", {"chunk": chunk})

    def _emit_status(self, state: str, **extra: Any) -> None:
        self._emit("job_status", {"state": state, **extra})

    @property
    def call_id(self) -> str:
        return f"terminal:{self.id}"

    @property
    def label(self) -> str:
        return f"{self.shell_type} · {os.path.basename(self.cwd) or self.cwd}"

    def info(self) -> dict[str, Any]:
        return {
            "terminal_id": self.id,
            "cwd": self.cwd,
            "shell": self.shell_type,
            "running": not self.exited,
            "return_code": self.return_code,
        }


# ── registry ────────────────────────────────────────────────────────────────


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def open_terminal(
    *, terminal_id: str = "", cwd: str = "", sid: str = "", working_directory: str = ""
) -> dict[str, Any]:
    """Start a terminal. ``cwd``/``working_directory`` resolve inside the workspace."""
    from opensquad.tools.system import _resolve_working_directory

    tid = str(terminal_id or "").strip() or _new_id()
    resolved = _resolve_working_directory(cwd or working_directory or "")
    with _LOCK:
        existing = _TERMINALS.get(tid)
        if existing is not None and not existing.exited:
            return {"ok": True, "terminal_id": tid, "reused": True, **existing.info()}
        session = TerminalSession(tid, cwd=resolved, sid=str(sid or ""))
        _TERMINALS[tid] = session
    result = session.start()
    if not result.get("ok"):
        with _LOCK:
            _TERMINALS.pop(tid, None)
    return result


def _get(terminal_id: str) -> TerminalSession | None:
    with _LOCK:
        return _TERMINALS.get(str(terminal_id or "").strip())


def write_terminal(terminal_id: str, text: str) -> dict[str, Any]:
    session = _get(terminal_id)
    if session is None:
        return {"ok": False, "error": f"unknown terminal: {terminal_id}"}
    return session.write(text)


def interrupt_terminal(terminal_id: str) -> dict[str, Any]:
    session = _get(terminal_id)
    if session is None:
        return {"ok": False, "error": f"unknown terminal: {terminal_id}"}
    return session.interrupt()


def close_terminal(terminal_id: str) -> dict[str, Any]:
    with _LOCK:
        session = _TERMINALS.pop(str(terminal_id or "").strip(), None)
    if session is None:
        return {"ok": True, "already_closed": True}
    return session.close()


def list_terminals() -> list[dict[str, Any]]:
    with _LOCK:
        return [s.info() for s in _TERMINALS.values()]


def close_all() -> int:
    """Stop every terminal (shutdown / tests). Returns how many were closed."""
    with _LOCK:
        sessions = list(_TERMINALS.values())
        _TERMINALS.clear()
    for session in sessions:
        try:
            session.close()
        except Exception:
            logger.debug("[Terminal] close_all: %s failed", session.id, exc_info=True)
    return len(sessions)
