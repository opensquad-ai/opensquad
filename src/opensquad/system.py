"""
System Tools v2.1
Provides system information, control functions, and a powerful background job management system.
Allows agents to execute time-consuming commands in a "non-blocking" manner and poll for results.
"""

import asyncio
import contextlib
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

# Project root (derived from __file__: opensquad/system.py -> project root)
_PROJECT_ROOT = os.path.normcase(os.path.abspath(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _is_path_safe(path: str) -> bool:
    """
    Check path safety.
    Ensures the path is within the project root directory; supports both absolute and relative paths.
    """
    try:
        if os.path.isabs(path):
            target_path = os.path.normcase(os.path.abspath(path))
        else:
            target_path = os.path.normcase(os.path.abspath(os.path.join(os.getcwd(), path)))
        return os.path.commonpath([_PROJECT_ROOT, target_path]) == _PROJECT_ROOT
    except Exception:
        return False


# --- Background job management core ---


class Job:
    def __init__(self, job_id: str, command: str, shell: bool = True):
        self.id = job_id
        self.command = command
        self.process: subprocess.Popen | None = None
        self.stdout_queue = queue.Queue()
        self.start_time = None
        self.end_time = None
        self.return_code = None
        self.shell = shell

    def start(self):
        self.start_time = datetime.now()
        try:
            # Subprocess environment: force Python subprocesses to output UTF-8
            env = os.environ.copy()
            if platform.system() == "Windows":
                env.setdefault("PYTHONUTF8", "1")
                env.setdefault("PYTHONIOENCODING", "utf-8")

            # Start process with redirected output
            self.process = subprocess.Popen(
                self.command,
                shell=self.shell,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # Merge stderr into stdout
                stdin=subprocess.PIPE,
                bufsize=0,  # Unbuffered for real-time reading
                text=False,  # Binary read to prevent encoding crashes
                env=env,
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
            try:
                # Attempt graceful termination
                self.process.terminate()
                # terminate() may not be sufficient on Windows; kill is more direct
                if platform.system() == "Windows":
                    # Use taskkill to force-kill including child processes
                    subprocess.run(
                        f"taskkill /F /T /PID {self.process.pid}",
                        shell=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    self.process.wait(timeout=1)
            except (subprocess.TimeoutExpired, OSError):
                with contextlib.suppress(OSError):
                    self.process.kill()


# Global job store
_JOBS: dict[str, Job] = {}

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


# --- Tool functions exposed to the agent ---


def start_job(command: str, wait_seconds: float = 3.0) -> dict[str, Any]:
    """
    Start a background command job.
    Suitable for time-consuming tasks (e.g. npm install, running a server, long scripts, large compilations).

    After starting, waits up to wait_seconds (default 3s):
    - If the task completes within this window, returns output directly (completed=True); no need to call check_job.
    - If still running after timeout, returns job_id (completed=False); use check_job to poll.

    Difference from api_process.run_command: run_command blocks until completion (suitable for <2min commands);
    start_job only waits a brief window so long tasks don't block the agent.

    Args:
        command:      Command-line string to execute.
        wait_seconds: Maximum wait seconds after start (default 3s). Set to 0 to skip waiting and return job_id directly.
    """
    _cleanup_old_jobs()

    job_id = str(uuid.uuid4())[:8]
    job = Job(job_id, command)
    success, msg = job.start()

    if not success:
        return {"status": "error", "message": f"Failed to start job: {msg}"}

    _JOBS[job_id] = job

    # -- Wait window: poll until done or timeout --
    if wait_seconds > 0:
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
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
                    "job_id": job_id,
                    "command": command,
                    "return_code": job.return_code,
                    "output": output or "(no output)",
                    "elapsed_seconds": elapsed,
                }

    # Exceeded wait window; task is still running
    return {
        "status": "success",
        "completed": False,
        "message": (f"Job is still running after {wait_seconds}s. Use check_job('{job_id}') to poll for results."),
        "job_id": job_id,
        "command": command,
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
                "current_path": os.getcwd(),
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
