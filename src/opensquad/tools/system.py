"""
System Tools v2.1
Provides system information, control functions, and a powerful background job management system.
Allows agents to execute time-consuming commands in a "non-blocking" manner and poll for results.
"""

import asyncio
import os
import platform
import queue
import subprocess
import threading
import time
import uuid
from datetime import datetime
from typing import Any

import psutil

try:
    from ..sleep_controller import sleep_controller
    from ..tool import logger
except ImportError:
    import logging

    logger = logging.getLogger(__name__)
    sleep_controller = None

import contextlib

from opensquad.utils.path_utils import get_workspace_root as _get_workspace_root
from opensquad.utils.path_utils import is_path_safe as _is_path_safe

# Project root (workspace-aware)
_PROJECT_ROOT = _get_workspace_root()


def _resolve_working_directory(working_directory: str | None) -> str:
    """
    Resolve working directory relative to workspace root.
    - None/empty -> workspace root
    - relative path -> workspace_root / relative_path
    - absolute path -> as-is
    """
    root = _get_workspace_root()
    if not working_directory:
        return root
    if os.path.isabs(working_directory):
        return os.path.normcase(os.path.abspath(working_directory))
    return os.path.normcase(os.path.abspath(os.path.join(root, working_directory)))


# _is_path_safe is imported from opensquad.utils.path_utils above

# --- Background job management core ---


class Job:
    def __init__(self, job_id: str, command: str, shell: bool = True, working_directory: str | None = None):
        self.id = job_id
        self.command = command
        self.process: subprocess.Popen | None = None
        self.stdout_queue = queue.Queue()
        self.start_time = None
        self.end_time = None
        self.return_code = None
        self.shell = shell
        self.working_directory = _resolve_working_directory(working_directory)

    def start(self):
        self.start_time = datetime.now()
        try:
            # Subprocess environment: force Python subprocesses to output UTF-8
            env = os.environ.copy()
            if platform.system() == "Windows":
                env.setdefault("PYTHONUTF8", "1")
                env.setdefault("PYTHONIOENCODING", "utf-8")

            # Start process with redirected output
            # Windows: create a new process group so Ctrl+C / console shutdown
            # signals from the launcher are less likely to cascade into this job.
            creationflags = 0
            if platform.system() == "Windows":
                # CREATE_NEW_PROCESS_GROUP: isolate from parent's Ctrl+C group
                # CREATE_NO_WINDOW: fully detach from launcher's console so that
                # taskkill /F on this process cannot leak console signals back
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW

            self.process = subprocess.Popen(
                self.command,
                # Security: shell=True allows pipes/redirects/chaining ("&&", "|").
                # The agent is a trusted entity within the OpenSquad sandbox;
                # agent configuration and role cards constrain what commands
                # the model can request.  If stricter isolation is needed,
                # set shell=False and implement an allow-list of safe commands
                # in the agent's role card / tool definitions.
                shell=self.shell,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # Merge stderr into stdout
                stdin=subprocess.DEVNULL,
                bufsize=0,  # Unbuffered for real-time reading
                text=False,  # Binary read to prevent encoding crashes
                env=env,
                cwd=self.working_directory,
                creationflags=creationflags,
            )

            # Start listener thread
            t = threading.Thread(target=self._read_output, daemon=True)
            t.start()
            return True, "Started"
        except Exception as e:
            self.end_time = datetime.now()
            return False, str(e)

    def _read_output(self):
        """Background thread: read output stream in real-time."""
        if not self.process:
            return

        while True:
            # Blocking read one byte (or one line) until stream closes
            line = self.process.stdout.readline()
            if not line:
                break
            try:
                # Prefer UTF-8 (Python scripts, npm, git, and other UTF-8 programs)
                decoded = line.decode("utf-8").rstrip()
            except UnicodeDecodeError:
                # Fall back to GBK (Windows system commands like dir/type with native GBK output)
                decoded = line.decode("gbk", errors="replace").rstrip()
            self.stdout_queue.put(decoded)

        # Wait for process to fully exit
        self.return_code = self.process.wait()
        self.end_time = datetime.now()

    def get_new_output(self, max_lines=50) -> str:
        """Get new output since the last check."""
        lines = []
        try:
            while len(lines) < max_lines:
                lines.append(self.stdout_queue.get_nowait())
        except queue.Empty:
            pass
        return "\n".join(lines)

    def is_running(self) -> bool:
        if self.process is None:
            return False
        return self.process.poll() is None

    def stop(self):
        if self.process and self.is_running():
            pid = self.process.pid
            try:
                if platform.system() == "Windows":
                    # Use taskkill /F /T to force-kill the entire process tree.
                    # IMPORTANT: Do NOT call terminate() first — it kills the cmd.exe
                    # wrapper (shell=True), orphaning child processes and creating a
                    # PID-reuse race condition where taskkill may hit a recycled PID.
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(pid)],
                        capture_output=True,
                        timeout=10,
                    )
                else:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
            except Exception:
                with contextlib.suppress(Exception):
                    self.process.kill()


# Global job store
_JOBS: dict[str, Job] = {}

# --- Persistent shell session (to unify api_process into system namespace) ---
_DEFAULT_SESSION_ID = "default"


class ShellSession:
    def __init__(self, session_id: str, shell_type: str | None = None, working_directory: str | None = None):
        self.session_id = session_id
        self.shell_type = shell_type or ("cmd" if os.name == "nt" else "bash")
        self.working_directory = _resolve_working_directory(working_directory)
        self.output_buffer: list[str] = []
        self._max_buffer_size = 10000
        self._lock = threading.Lock()
        self.process: subprocess.Popen | None = None
        self._stop_event = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self._start_process()

    def _start_process(self):
        env = os.environ.copy()
        if os.name == "nt":
            env.setdefault("PYTHONIOENCODING", "utf-8")
        self.process = subprocess.Popen(
            [self.shell_type],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            cwd=self.working_directory,
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) if os.name == "nt" else 0,
        )

        # Shell bootstrap
        if os.name == "nt":
            if "powershell" in self.shell_type.lower():
                self._send_raw(
                    "$OutputEncoding = [System.Text.Encoding]::UTF8; [Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
                )
            else:
                self._send_raw("@echo off\n")
                self._send_raw("chcp 65001 >nul 2>nul\n")
        else:
            self._send_raw("export PYTHONUNBUFFERED=1\n")

        self._stop_event.clear()
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def _send_raw(self, cmd: str):
        if not self.process or not self.process.stdin:
            return
        self.process.stdin.write(cmd)
        self.process.stdin.flush()

    def _read_loop(self):
        while not self._stop_event.is_set() and self.process and self.process.poll() is None:
            line = self.process.stdout.readline() if self.process.stdout else ""
            if not line:
                break
            with self._lock:
                self.output_buffer.append(line)
                if len(self.output_buffer) > self._max_buffer_size:
                    self.output_buffer.pop(0)

    def execute(self, command: str, timeout: float = 120.0) -> dict[str, Any]:
        if not self.process or self.process.poll() is not None:
            return {"status": "error", "message": "Session shell is not running."}

        marker = f"END_OF_COMMAND_{uuid.uuid4().hex[:8]}"
        full_command = f"{command}\n"
        echo_cmd = f"Write-Host '{marker}'\n" if "powershell" in self.shell_type.lower() else f"echo {marker}\n"

        with self._lock:
            start_index = len(self.output_buffer)

        self._send_raw(full_command)
        self._send_raw(echo_cmd)

        start_time = time.time()
        while time.time() - start_time < timeout:
            if self._stop_event.is_set() or (self.process and self.process.poll() is not None):
                with self._lock:
                    partial = "".join(self.output_buffer[start_index:])
                return {
                    "status": "error",
                    "session_id": self.session_id,
                    "message": "Command aborted (shell closed or process exited)",
                    "partial_data": partial,
                    "working_directory": self.working_directory,
                    "aborted": True,
                }
            if _user_stop_requested():
                with self._lock:
                    partial = "".join(self.output_buffer[start_index:])
                return {
                    "status": "error",
                    "session_id": self.session_id,
                    "message": "Command aborted by user stop",
                    "partial_data": partial,
                    "working_directory": self.working_directory,
                    "aborted": True,
                }
            with self._lock:
                combined = "".join(self.output_buffer[start_index:])
                if marker in combined:
                    captured = combined.split(marker)[0].strip()
                    return {
                        "status": "success",
                        "session_id": self.session_id,
                        "shell_type": self.shell_type,
                        "working_directory": self.working_directory,
                        "data": captured,
                    }
            time.sleep(0.1)

        with self._lock:
            partial = "".join(self.output_buffer[start_index:])
        return {
            "status": "error",
            "session_id": self.session_id,
            "message": f"Command timed out after {timeout}s",
            "partial_data": partial,
            "working_directory": self.working_directory,
        }

    def close(self):
        self._stop_event.set()
        if self.process:
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(self.process.pid)],
                        capture_output=True,
                        timeout=10,
                    )
                else:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
            except Exception:
                with contextlib.suppress(Exception):
                    self.process.kill()


_SESSIONS: dict[str, ShellSession] = {}


_CLEANUP_INTERVAL_MINUTES = 30


def _cleanup_old_jobs():
    """Clean up jobs that have been finished for more than 30 minutes to prevent memory leaks."""
    now = datetime.now()
    to_remove = []
    for jid, job in _JOBS.items():
        if not job.is_running() and job.end_time:
            elapsed = (now - job.end_time).total_seconds()
            if elapsed > _CLEANUP_INTERVAL_MINUTES * 60:
                to_remove.append(jid)
    for jid in to_remove:
        del _JOBS[jid]
    if to_remove:
        logger.info(f"Cleaned up {len(to_remove)} old jobs: {to_remove}")


def _get_or_create_session(
    session_id: str = _DEFAULT_SESSION_ID, working_directory: str | None = None, shell_type: str | None = None
) -> ShellSession:
    sess = _SESSIONS.get(session_id)
    if sess is None:
        resolved_cwd = _resolve_working_directory(working_directory)
        if not _is_path_safe(resolved_cwd):
            raise ValueError(f"working_directory outside workspace: {resolved_cwd}")
        sess = ShellSession(session_id=session_id, shell_type=shell_type, working_directory=resolved_cwd)
        _SESSIONS[session_id] = sess
    return sess


def create_shell_session(
    session_id: str = _DEFAULT_SESSION_ID, working_directory: str | None = None, shell_type: str | None = None
) -> dict[str, Any]:
    """Create a persistent shell session under system namespace."""
    try:
        sess = _get_or_create_session(session_id=session_id, working_directory=working_directory, shell_type=shell_type)
        return {
            "status": "success",
            "session_id": sess.session_id,
            "shell_type": sess.shell_type,
            "working_directory": sess.working_directory,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def run_session_job(command: str, timeout: float = 120.0, session_id: str = _DEFAULT_SESSION_ID) -> dict[str, Any]:
    """Run command in persistent shell session. Uses the same shell per session_id.

    WARNING: If a previous command in this session started a foreground process
    (e.g. npm run dev, python server.py), this call will BLOCK until that process
    exits, then time out. For long-running services, use start_job instead.
    """
    try:
        sess = _get_or_create_session(session_id=session_id)
        return sess.execute(command=command, timeout=timeout)
    except Exception as e:
        return {"status": "error", "message": str(e)}


def get_shell_session_status(session_id: str = _DEFAULT_SESSION_ID) -> dict[str, Any]:
    """Get status of a shell session."""
    try:
        sess = _get_or_create_session(session_id=session_id)
        alive = bool(sess.process and sess.process.poll() is None)
        return {
            "status": "success",
            "session_id": sess.session_id,
            "shell_type": sess.shell_type,
            "working_directory": sess.working_directory,
            "pid": sess.process.pid if sess.process else None,
            "alive": alive,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def restart_shell_session(session_id: str = _DEFAULT_SESSION_ID) -> dict[str, Any]:
    """Restart a shell session while preserving its configured shell/cwd."""
    try:
        old = _SESSIONS.get(session_id)
        old_cwd = old.working_directory if old else _resolve_working_directory(None)
        old_shell = old.shell_type if old else ("cmd" if os.name == "nt" else "bash")
        if old:
            old.close()
            _SESSIONS.pop(session_id, None)
        sess = ShellSession(session_id=session_id, shell_type=old_shell, working_directory=old_cwd)
        _SESSIONS[session_id] = sess
        return {
            "status": "success",
            "session_id": sess.session_id,
            "shell_type": sess.shell_type,
            "working_directory": sess.working_directory,
            "pid": sess.process.pid if sess.process else None,
            "alive": True,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def close_shell_session(session_id: str = _DEFAULT_SESSION_ID) -> dict[str, Any]:
    """Close and remove a shell session."""
    sess = _SESSIONS.get(session_id)
    if not sess:
        return {"status": "error", "message": f"Session {session_id} not found."}
    sess.close()
    _SESSIONS.pop(session_id, None)
    return {"status": "success", "message": f"Session {session_id} closed."}


def list_shell_sessions() -> dict[str, Any]:
    """List active shell sessions."""
    items = []
    for sid, sess in _SESSIONS.items():
        items.append(
            {
                "session_id": sid,
                "shell_type": sess.shell_type,
                "working_directory": sess.working_directory,
                "pid": sess.process.pid if sess.process else None,
                "alive": bool(sess.process and sess.process.poll() is None),
            }
        )
    return {"status": "success", "count": len(items), "sessions": items}


def _user_stop_requested() -> bool:
    try:
        from opensquad.input_hub import input_hub

        return bool(input_hub.is_stop_requested())
    except Exception:
        return False


def abort_all_tool_processes(reason: str = "user stop") -> dict[str, Any]:
    """Force-stop Jobs, ShellSessions, and any child OS processes.

    Called from ``input_hub.request_stop()`` so UI Stop actually unblocks hung
    tools (git/cmd/shell) instead of waiting for their natural timeout.
    """
    stopped_jobs = 0
    closed_sessions = 0
    killed_children = 0

    for job in list(_JOBS.values()):
        try:
            if job.is_running():
                job.stop()
                stopped_jobs += 1
        except Exception:
            logger.debug("[system] abort job failed", exc_info=True)

    for sid in list(_SESSIONS.keys()):
        try:
            sess = _SESSIONS.pop(sid, None)
            if sess is not None:
                sess.close()
                closed_sessions += 1
        except Exception:
            logger.debug("[system] abort shell session failed sid=%s", sid, exc_info=True)

    try:
        me = psutil.Process()
        children = me.children(recursive=True)
        for child in children:
            try:
                child.kill()
                killed_children += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            except Exception:
                logger.debug("[system] kill child pid failed", exc_info=True)
        _, alive = psutil.wait_procs(children, timeout=1.0)
        for child in alive:
            with contextlib.suppress(Exception):
                child.kill()
    except Exception:
        logger.debug("[system] abort child processes failed", exc_info=True)

    logger.info(
        "[system] abort_all_tool_processes(%s): jobs=%d sessions=%d children=%d",
        reason,
        stopped_jobs,
        closed_sessions,
        killed_children,
    )
    return {
        "status": "success",
        "reason": reason,
        "stopped_jobs": stopped_jobs,
        "closed_sessions": closed_sessions,
        "killed_children": killed_children,
    }


# --- Tool functions exposed to the agent ---


def start_job(
    command: str,
    wait_seconds: float = 3.0,
    working_directory: str | None = None,
    blocking: bool = False,
    max_wait_seconds: float | None = None,
) -> dict[str, Any]:
    """
    Start a background command job.
    Suitable for time-consuming tasks (e.g. npm install, running a server, long scripts, large compilations).

    Behavior:
    - blocking=False (default): waits up to wait_seconds, then returns job_id for polling if still running.
    - blocking=True: waits until the command fully exits, then returns final output.
      You can set max_wait_seconds as a safety guard to avoid waiting forever.

    Difference from api_process.run_command:
    - start_job(blocking=True) can mimic synchronous waiting.
    - api_process still provides persistent shell context (cd/env state carry-over), while jobs are process-isolated.

    Working directory behavior:
    - If working_directory is omitted, defaults to current workspace root (same as api_process default)
    - Relative path is resolved against workspace root
    - Absolute path is supported but must remain inside workspace

    Args:
        command:           Command-line string to execute.
        wait_seconds:      Maximum wait seconds after start (default 3s). Ignored when blocking=True.
        working_directory: Optional working directory for this job.
        blocking:          Whether to block until completion.
        max_wait_seconds:  Optional safety timeout when blocking=True. If exceeded, returns completed=False and keep job running.
    """
    _cleanup_old_jobs()

    resolved_cwd = _resolve_working_directory(working_directory)
    if not _is_path_safe(resolved_cwd):
        return {
            "status": "error",
            "message": f"Security Denied: working_directory outside workspace: {resolved_cwd}",
        }

    job_id = str(uuid.uuid4())[:8]
    job = Job(job_id, command, working_directory=resolved_cwd)
    success, msg = job.start()

    if not success:
        return {"status": "error", "message": f"Failed to start job: {msg}"}

    _JOBS[job_id] = job

    # -- Blocking mode: wait until job exits (with optional safety timeout) --
    if blocking:
        started_at = time.time()
        while job.is_running():
            # NOTE: start_job() is called via ToolRegistry.call() which wraps
            # sync tools in run_in_executor, so we're always in a thread and
            # time.sleep() does not block the event loop.
            if _user_stop_requested():
                with contextlib.suppress(Exception):
                    job.stop()
                return {
                    "status": "error",
                    "completed": False,
                    "blocking": True,
                    "aborted": True,
                    "message": "Job aborted by user stop",
                    "job_id": job_id,
                    "command": command,
                    "working_directory": resolved_cwd,
                    "output": job.get_new_output(max_lines=200),
                }
            time.sleep(0.1)
            if max_wait_seconds is not None and max_wait_seconds >= 0:
                if (time.time() - started_at) >= max_wait_seconds:
                    return {
                        "status": "success",
                        "completed": False,
                        "blocking": True,
                        "message": (
                            f"Blocking wait exceeded max_wait_seconds={max_wait_seconds}s. "
                            f"Job is still running; use check_job('{job_id}') to continue polling or stop_job('{job_id}') to terminate."
                        ),
                        "job_id": job_id,
                        "command": command,
                        "working_directory": resolved_cwd,
                    }
        output = job.get_new_output(max_lines=2000)
        elapsed = round((job.end_time - job.start_time).total_seconds(), 2) if job.end_time and job.start_time else None
        return {
            "status": "success",
            "completed": True,
            "blocking": True,
            "job_id": job_id,
            "command": command,
            "working_directory": resolved_cwd,
            "return_code": job.return_code,
            "output": output or "(no output)",
            "elapsed_seconds": elapsed,
        }

    # -- Non-blocking mode: wait window then return job_id if still running --
    if wait_seconds > 0:
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if _user_stop_requested():
                with contextlib.suppress(Exception):
                    job.stop()
                return {
                    "status": "error",
                    "completed": False,
                    "blocking": False,
                    "aborted": True,
                    "message": "Job aborted by user stop",
                    "job_id": job_id,
                    "command": command,
                    "working_directory": resolved_cwd,
                    "output": job.get_new_output(max_lines=200),
                }
            time.sleep(0.1)
            if not job.is_running():
                # Task completed within the wait window; collect all output and return directly
                output = job.get_new_output(max_lines=500)
                elapsed = (
                    round((job.end_time - job.start_time).total_seconds(), 2)
                    if job.end_time and job.start_time
                    else None
                )
                return {
                    "status": "success",
                    "completed": True,
                    "blocking": False,
                    "job_id": job_id,
                    "command": command,
                    "working_directory": resolved_cwd,
                    "return_code": job.return_code,
                    "output": output or "(no output)",
                    "elapsed_seconds": elapsed,
                }

    # Exceeded wait window; task is still running
    return {
        "status": "success",
        "completed": False,
        "blocking": False,
        "message": (f"Job is still running after {wait_seconds}s. Use check_job('{job_id}') to poll for results."),
        "job_id": job_id,
        "command": command,
        "working_directory": resolved_cwd,
    }


def check_job(job_id: str) -> dict[str, Any]:
    """
    Check the status of a background job and get the **latest** output.
    Note: Only returns new logs since the last check; does not return full history.

    Args:
        job_id: ID returned by start_job.
    """
    job = _JOBS.get(job_id)
    if not job:
        return {"status": "error", "message": f"Job ID {job_id} not found."}

    is_running = job.is_running()
    new_output = job.get_new_output()

    status_str = (
        "RUNNING"
        if is_running
        else (f"FINISHED (Code: {job.return_code})" if job.return_code is not None else "UNKNOWN")
    )

    return {
        "status": "success",
        "data": {
            "job_id": job_id,
            "state": status_str,
            "new_output": new_output or "(No new output)",
            "return_code": job.return_code,
            "working_directory": job.working_directory,
        },
    }


def stop_job(job_id: str) -> dict[str, Any]:
    """
    Force-terminate a background job.
    """
    job = _JOBS.get(job_id)
    if not job:
        return {"status": "error", "message": f"Job ID {job_id} not found."}

    job.stop()
    return {"status": "success", "message": f"Job {job_id} terminated."}


def list_jobs() -> dict[str, Any]:
    """
    List all active or recently finished background jobs.
    """
    data = []

    for jid, job in _JOBS.items():
        state = "RUNNING" if job.is_running() else "FINISHED"
        data.append(
            {
                "id": jid,
                "command": job.command[:50] + "..." if len(job.command) > 50 else job.command,
                "state": state,
                "working_directory": job.working_directory,
                "start_time": job.start_time.strftime("%H:%M:%S") if job.start_time else "-",
            }
        )

    return {"status": "success", "count": len(data), "jobs": data}


def write_binary_file(path: str, data_base64: str) -> dict[str, Any]:
    """
    Write a base64 string to the specified file.

    Args:
        path: Target file path (safe path within the project).
        data_base64: Base64-encoded file content (without data: prefix).
    """
    if not _is_path_safe(path):
        return {"status": "error", "message": "Security Denied: Path outside project."}
    try:
        import base64

        if not data_base64:
            return {"status": "error", "message": "No data provided."}
        data = base64.b64decode(data_base64)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return {"status": "success", "message": f"File written: {path}", "size": len(data)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# --- Original system functions ---


def get_system_info() -> dict[str, Any]:
    """Get core information about the current system, such as OS, CPU, memory, etc."""
    try:
        mem = psutil.virtual_memory()
        return {
            "status": "success",
            "data": {
                "os": platform.system(),
                "os_release": platform.release(),
                "cpu_count": psutil.cpu_count(),
                "memory_total_gb": round(mem.total / (1024**3), 2),
                "memory_available_gb": round(mem.available / (1024**3), 2),
                "current_path": _get_workspace_root(),
                "python_version": platform.python_version(),
            },
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def get_time() -> dict[str, Any]:
    """Get the current system time."""
    now = datetime.now()
    return {
        "status": "success",
        "data": {
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "timestamp": int(time.time()),
            "timezone": time.tzname[0],
        },
    }


async def wait(seconds: float, interruptible: bool = True) -> dict[str, Any]:
    """
    Pause agent execution for a specified number of seconds.

    Args:
        seconds: Seconds to wait (must be positive).
        interruptible: Whether wakeup by external events is allowed (default True).
            - True: use interruptible sleep; can be woken by group messages or other external events
                    (suitable for waiting on user replies)
            - False: use fixed wait; cannot be interrupted (suitable for API rate limiting, retry intervals, etc.)

    Returns:
        Non-interruptible mode:
            Success: {"status": "success", "message": "Wait completed for {seconds}s."}
            Failure: {"status": "error", "message": "error message"}

        Interruptible mode:
            Returns {
                "status": "success",
                "wake_type": "natural" | "interrupted",
                "planned_seconds": int,
                "actual_seconds": float,
                "wake_reason": str,
                "wake_time": str
            }

    Usage tips:
        - Fixed delay, no interruption needed: wait(5, interruptible=False)
        - Waiting for group chat reply, can be woken: wait(300, interruptible=True)
    """
    try:
        sec = float(seconds)
        if sec < 0:
            raise ValueError("Seconds must be positive")

        # Interruptible mode: use sleep_controller
        if interruptible:
            if sleep_controller is None:
                return {
                    "status": "error",
                    "message": "Interruptible sleep not available (sleep_controller not imported)",
                }
            try:
                from ..state_manager import state_manager

                await state_manager.set_state("sleeping")
            except Exception:
                pass
            wake_info = await sleep_controller.sleep(int(sec))
            try:
                from ..state_manager import state_manager

                await state_manager.set_state("idle")
            except Exception:
                pass
            return {"status": "success", **wake_info}

        # Non-interruptible mode: use asyncio.sleep
        else:
            await asyncio.sleep(sec)
            return {"status": "success", "message": f"Wait completed for {sec}s."}

    except Exception as e:
        return {"status": "error", "message": str(e)}


# Import web tools for convenient access
from .web import send_file as _web_send_file
from .web import send_message as _web_send_message


def send_file_to_web(file_paths, message: str = "", agent_id: str = "") -> dict:
    """
    Send one or more files to the AI Web chat panel.
    Supports images (displayed inline), videos, audio, and any other file type.

    Args:
        file_paths: List of absolute file paths or single path string. E.g., ["C:/data/chart.png"] or "C:/data/chart.png"
        message: Optional accompanying text message
        agent_id: Optional explicit target agent_id. Recommended in multi-agent runtime.

    Returns:
        Dict with status and message

    Example:
        send_file_to_web(file_paths=["C:/workspace/chart.png"], message="分析结果")
        send_file_to_web(file_paths="C:/workspace/chart.png", message="分析结果")
    """
    import json

    # Handle different input types
    if isinstance(file_paths, str):
        # Try to parse as JSON array
        try:
            parsed = json.loads(file_paths)
            file_paths = parsed if isinstance(parsed, list) else [file_paths]
        except json.JSONDecodeError:
            # It's a single string path
            file_paths = [file_paths]
    elif not isinstance(file_paths, list):
        file_paths = [str(file_paths)]

    return _web_send_file(file_paths=file_paths, message=message, agent_id=agent_id)


def send_message_to_web(content: str, agent_id: str = "") -> dict:
    """
    Send a text message to the AI Web chat panel.

    Args:
        content: Message text to send
        agent_id: Optional explicit target agent_id. Recommended in multi-agent runtime.

    Returns:
        Dict with status and message

    Example:
        send_message_to_web(content="分析完成！")
    """
    return _web_send_message(content=content, agent_id=agent_id)


async def set_state(state: str) -> dict[str, Any]:
    """
    Update this agent's internal state. Controls message filtering and behavior mode.

    States:
    - `idle`: Idle — receive all messages from group chat
    - `working`: Working — filter group messages, focus on current task
    - `sleeping`: (Set automatically by `wait()` in interruptible mode; do not call manually)

    Args:
        state: State name, one of: "idle", "working"

    Returns:
        Dict with status and result

    Example:
        set_state(state="working")  # Focus on current task
        set_state(state="idle")     # Resume listening to all messages
    """
    valid_states = ["idle", "working", "sleeping"]
    state_lower = state.strip().lower()
    if state_lower not in valid_states:
        return {"status": "error", "message": f"Invalid state: {state!r}. Must be one of {valid_states}"}
    try:
        from ..state_manager import state_manager

        await state_manager.set_state(state_lower)
    except Exception as e:
        return {"status": "error", "message": f"Failed to set state: {e}"}
    return {"status": "success", "message": f"State changed to '{state_lower}'."}


async def get_wake_mode() -> dict[str, Any]:
    """
    Query this agent's current wake mode, which controls which group-chat messages wake you from idle.

    Wake modes:
    - `strict`: only messages that @mention you (and replies received while you are sleeping/awaiting a reply) wake you. The default for multi-agent groups — prevents every agent from waking on every line.
    - `normal`: every group message wakes you. Use when you should follow the whole conversation.

    Useful when joining a collaboration mid-stream to decide whether you need to switch modes.

    Returns:
        Dict with status and the current wake_mode, e.g. {"status": "success", "wake_mode": "strict"}

    Example:
        get_wake_mode()  # check current mode before deciding whether to switch
    """
    try:
        from ..state_manager import state_manager

        mode = await state_manager.get_wake_mode()
    except Exception as e:
        return {"status": "error", "message": f"Failed to get wake mode: {e}"}
    return {"status": "success", "wake_mode": mode}


async def set_wake_mode(mode: str) -> dict[str, Any]:
    """
    Switch this agent's wake mode at runtime — controls which group-chat messages wake you from idle.

    Modes:
    - `strict`: only @mentions (and replies received while you are sleeping/awaiting a reply) wake you. Recommended for multi-agent groups to avoid noise and duplicate responses.
    - `normal`: every group-chat message wakes you. Use when you are the sole agent or should actively follow the entire conversation.

    The change takes effect immediately and is persisted across restarts.

    Args:
        mode: Wake mode name, one of: "strict", "normal"

    Returns:
        Dict with status and result

    Example:
        set_wake_mode(mode="normal")   # Wake on every group message
        set_wake_mode(mode="strict")   # Only wake on @mentions (multi-agent groups)
    """
    valid_modes = ["strict", "normal"]
    if not isinstance(mode, str):
        return {"status": "error", "message": f"Invalid mode type: {type(mode).__name__}. Must be a string."}
    mode_lower = mode.strip().lower()
    if mode_lower not in valid_modes:
        return {"status": "error", "message": f"Invalid wake mode: {mode!r}. Must be one of {valid_modes}"}
    try:
        from ..state_manager import state_manager

        await state_manager.set_wake_mode(mode_lower)
    except Exception as e:
        return {"status": "error", "message": f"Failed to set wake mode: {e}"}
    return {"status": "success", "message": f"Wake mode changed to '{mode_lower}'.", "wake_mode": mode_lower}
