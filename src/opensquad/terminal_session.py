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


# ── shell profiles ──────────────────────────────────────────────────────────
# What the panel offers is whatever this machine actually has, not one hard-coded shell: cmd /
# pwsh / powershell on Windows, plus bash (Git Bash, or WSL's bash.exe) when they are present;
# bash / sh / zsh / fish on POSIX. "Found" means the interpreter exists — for WSL we also check
# that a distribution is installed, because wsl.exe itself always exists and would otherwise be
# offered on every Windows box.
SHELL_PROFILES: tuple[dict[str, Any], ...] = (
    {"id": "cmd", "label": "Command Prompt", "kind": "cmd", "candidates": (r"C:\Windows\System32\cmd.exe", "cmd.exe")},
    {"id": "powershell", "label": "Windows PowerShell", "kind": "powershell", "candidates": ("powershell.exe",)},
    {"id": "pwsh", "label": "PowerShell", "kind": "pwsh", "candidates": ("pwsh.exe", "pwsh")},
    {
        "id": "bash",
        "label": "Git Bash",
        "kind": "bash",
        "candidates": (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe", "bash.exe"),
    },
    {"id": "wsl", "label": "WSL", "kind": "wsl", "candidates": ("wsl.exe",)},
    {"id": "zsh", "label": "zsh", "kind": "zsh", "candidates": ("zsh",)},
    {"id": "fish", "label": "fish", "kind": "fish", "candidates": ("fish",)},
    {"id": "sh", "label": "sh", "kind": "sh", "candidates": ("/bin/sh", "sh")},
)


def _which(candidates: tuple[str, ...]) -> str:
    """First candidate that exists on PATH (or as an absolute path)."""
    import shutil

    for candidate in candidates:
        found = (
            shutil.which(candidate)
            if not os.path.isabs(candidate)
            else (candidate if os.path.isfile(candidate) else None)
        )
        if found:
            return found
    return ""


def _wsl_has_distro() -> bool:
    """``wsl.exe`` exists on every Windows box; a distribution may not."""
    try:
        out = subprocess.run(["wsl.exe", "-l", "-q"], capture_output=True, timeout=8, check=False)
        return bool((out.stdout or b"").decode("utf-16-le", errors="ignore").strip() or (out.stdout or b"").strip())
    except Exception:
        return False


def available_shells() -> list[dict[str, Any]]:
    """The shells this machine can actually run, best default first."""
    found: list[dict[str, Any]] = []
    for profile in SHELL_PROFILES:
        path = _which(profile["candidates"])
        if not path:
            continue
        if profile["kind"] == "wsl" and not _wsl_has_distro():
            continue
        found.append({"id": profile["id"], "label": profile["label"], "path": path, "kind": profile["kind"]})
    if not found:  # never leave the panel with nothing to offer
        fallback = os.environ.get("COMSPEC") or "/bin/sh"
        found.append({"id": os.path.basename(fallback).split(".")[0], "label": fallback, "path": fallback, "kind": ""})
    return found


def default_shell_id() -> str:
    shells = available_shells()
    return shells[0]["id"] if shells else ""


def resolve_shell(shell_id: str = "") -> dict[str, Any]:
    """The command line for ``shell_id`` (or this platform's default)."""
    wanted = str(shell_id or "").strip() or default_shell_id()
    for profile in available_shells():
        if profile["id"] == wanted:
            command = [profile["path"]]
            # A login-ish, non-interactive-friendly start for the ones that need it.
            if profile["kind"] == "bash":
                command += ["--noprofile", "--norc", "-i"]
            return {**profile, "command": command}
    return {
        "id": wanted,
        "label": wanted,
        "path": "",
        "kind": "",
        "command": [],
        "error": f"shell not available: {wanted}",
    }


def _console_encoding(kind: str) -> str:
    """The encoding this shell speaks on Windows; UTF-8 everywhere else."""
    if os.name != "nt":
        return "utf-8"
    if kind in ("cmd", "powershell", "pwsh"):
        import locale

        try:
            return locale.getpreferredencoding(False) or "cp936"
        except Exception:
            return "cp936"
    return "utf-8"


class TerminalSession:
    """One shell child plus the thread that streams its output."""

    def __init__(self, terminal_id: str, *, cwd: str = "", sid: str = "", trusted: bool = False, shell: str = ""):
        self.id = terminal_id
        self.cwd = cwd
        self.sid = sid
        self.trusted = trusted
        self.shell = resolve_shell(shell)
        self.shell_type = str(self.shell.get("label") or self.shell.get("id") or "shell")
        # How to talk to this shell. A Windows console shell reads and writes its **console code
        # page** (cp936 on a Chinese Windows); decoding its output as UTF-8 turns every 中文 into
        # `\ufffd`, and encoding input as UTF-8 mangles a pasted Chinese path into `������` — the
        # field report. So both directions use the shell's own encoding.
        self.encoding = _console_encoding(str(self.shell.get("kind") or ""))
        self.process: subprocess.Popen | None = None
        self.exited = False
        self.return_code: int | None = None
        self.error = ""
        # Output is kept so a *polling* caller (the launcher serves the panel over HTTP, which
        # has no push channel) can read what it has not seen yet: `offset` is the running total
        # of characters emitted, and the buffer keeps the tail of it.
        self.buffer = ""
        self.offset = 0
        self._lock = threading.Lock()

    # ── lifecycle ───────────────────────────────────────────────────────────
    def start(self) -> dict[str, Any]:
        from opensquad.tools.system import _noninteractive_shell_env
        from opensquad.utils.path_utils import is_path_safe

        # Two trust boundaries, and they are not the same one. A shell an *agent* asks for is
        # confined to its workspace. A terminal the **user** opens from the panel is not: the
        # launcher already resolved that directory as the agent's own workspace (which can
        # legitimately live outside `get_workspace_root()` — e.g. on the Desktop), and applying
        # the agent's fence here refuses the very directory the panel is showing.
        if not self.trusted and not is_path_safe(self.cwd):
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
        if self.shell.get("error"):
            return {"ok": False, "error": str(self.shell["error"]), "shells": available_shells()}
        try:
            self.process = subprocess.Popen(
                self.shell.get("command") or [os.environ.get("COMSPEC") or "/bin/sh"],
                creationflags=creationflags,
                startupinfo=startupinfo,
                **popen_kw,
            )
        except Exception as exc:  # noqa: BLE001 - reported to the panel
            self.error = str(exc)
            logger.warning("[Terminal] failed to start a shell: %s", exc)
            return {"ok": False, "error": str(exc)}
        threading.Thread(target=self._pump, name=f"terminal-{self.id}", daemon=True).start()
        self._bootstrap()
        self._emit_status("running")
        return {
            "ok": True,
            "terminal_id": self.id,
            "cwd": self.cwd,
            "shell": self.shell_type,
            "shell_id": self.shell.get("id"),
        }

    def _bootstrap(self) -> None:
        """Put the shell into a state that can be driven over a pipe.

        Two Windows-specific things bit us here:

        * ``cmd`` reads piped stdin in the **console code page** — GBK on a Chinese Windows — so
          a pasted path containing 中文 arrived as `������` and the shell could not enter the
          folder. Switching the code page to UTF-8 first is what the agent's own persistent shell
          does, and it is the difference between a usable terminal and one that cannot cd into a
          Chinese directory.
        * ``cmd`` also echoes what it reads from a pipe while the panel echoes the typed line
          itself, so without ``@echo off`` every command appears twice.
        """
        kind = str(self.shell.get("kind") or "")
        if os.name != "nt":  # pragma: no cover - POSIX shells are UTF-8 already
            return
        if kind == "cmd":  # pragma: no cover - platform specific
            # cmd echoes what it reads from a pipe, and the panel echoes the typed line itself,
            # so without this every command appears twice. (No `chcp` here: switching the code
            # page mid-stream left cmd waiting on a continuation — `More?` — and split the very
            # next command. The session talks in the console's own code page instead.)
            self.write("@echo off\r\n")

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
                    # The shell's own encoding (cp936 for a Windows console shell); a byte that
                    # does not fit is replaced rather than killing the stream.
                    text = data.decode(self.encoding, errors="replace")
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
            self.process.stdin.write(text.encode(self.encoding, errors="replace"))
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
        with self._lock:
            self.buffer = (self.buffer + chunk)[-MAX_BUFFER:]
            self.offset += len(chunk)
        self._emit("job_stdout", {"chunk": chunk})

    def read(self, since: int = 0) -> dict[str, Any]:
        """Everything written since ``since``, plus this terminal's state.

        ``since`` is a character offset, not a timestamp: the caller polls with the ``offset``
        it last received, and a client that fell behind gets the tail it missed rather than a
        gap. Safe to call while the shell is running (the reader thread holds the same lock).
        """
        with self._lock:
            start = max(0, int(since or 0))
            missed = start - (self.offset - len(self.buffer))
            chunk = self.buffer[max(0, missed) :] if start < self.offset else ""
            return {
                "ok": True,
                "terminal_id": self.id,
                "chunk": chunk,
                "offset": self.offset,
                "running": not self.exited,
                "return_code": self.return_code,
                "error": self.error,
            }

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
            "shell_id": self.shell.get("id"),
            "running": not self.exited,
            "return_code": self.return_code,
        }


# ── registry ────────────────────────────────────────────────────────────────


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def open_terminal(
    *,
    terminal_id: str = "",
    cwd: str = "",
    sid: str = "",
    working_directory: str = "",
    trusted: bool = False,
    shell: str = "",
) -> dict[str, Any]:
    """Start a terminal. ``cwd``/``working_directory`` resolve inside the workspace.

    ``trusted=True`` skips the agent-side workspace fence for a shell the *user* asked for
    (the launcher-hosted terminal); the caller is then responsible for the directory.
    ``shell`` picks one of :func:`available_shells` — the panel offers what this machine has,
    and an unavailable one is refused (with the list) rather than silently substituted.
    """
    from opensquad.tools.system import _resolve_working_directory

    tid = str(terminal_id or "").strip() or _new_id()
    resolved = _resolve_working_directory(cwd or working_directory or "")
    with _LOCK:
        existing = _TERMINALS.get(tid)
        if existing is not None and not existing.exited:
            return {"ok": True, "terminal_id": tid, "reused": True, **existing.info()}
        session = TerminalSession(tid, cwd=resolved, sid=str(sid or ""), trusted=trusted, shell=shell)
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


def read_terminal(terminal_id: str, since: int = 0) -> dict[str, Any]:
    """Polling read: the launcher serves the panel over HTTP, so output is pulled, not pushed."""
    session = _get(terminal_id)
    if session is None:
        return {"ok": False, "error": f"unknown terminal: {terminal_id}", "chunk": "", "offset": 0, "running": False}
    return session.read(since)


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
