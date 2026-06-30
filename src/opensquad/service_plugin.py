"""
[DEPRECATED] Generic plugin service auto-start base class.

⚠️  This module is deprecated as of OpenSquad v2.
    Plugin services are now managed centrally by the Launcher's
    PluginServiceProcess. Plugin developers should declare service
    configuration in plugin.json and implement service/main.py
    without inheriting any base class.

    See docs: Service Management Architecture

For backward compatibility, this module remains functional but new
plugins should NOT use it.

Usage (legacy):
1. Plugin directory structure:
   plugins/
   +-- my_plugin/
       +-- plugin.py
       +-- service/
           +-- main.py        (FastAPI service)
           +-- service.py     (Flask service)
           +-- ...

2. Inherit ServicePlugin in plugin.py:

   from opensquad.service_plugin import ServicePlugin
   from opensquad.plugin_api import register, Context

   @register(
       name="my_plugin",
       config_schema={
           "port": {"type": "integer", "default": 9000},
           "auto_start": {"type": "boolean", "default": True},
       }
   )
   class MyPlugin(ServicePlugin):
       def __init__(self, context: Context):
           super().__init__(
               context=context,
               service_script="main.py",      # or "service.py"
               health_endpoint="/health",
               service_name="MyPlugin"
           )
"""

import contextlib
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Any

import requests

from opensquad.plugin_api import Context, Plugin


class _RotatingLogWriter:
    """
    Simple rotating file writer for subprocess log output.

    Rotates when file exceeds max_bytes. Keeps backup_count old files.
    Naming: agent_run.log → agent_run.log.1 → agent_run.log.2 → ...
    """

    def __init__(self, path: str, max_bytes: int = 3 * 1024 * 1024, backup_count: int = 3):
        self.path = path
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self._fh = open(path, "a", encoding="utf-8")
        self._size = os.path.getsize(path) if os.path.exists(path) else 0

    def write(self, data: str) -> int:
        written = self._fh.write(data)
        self._size += written if written is not None else len(data)
        if self._size >= self.max_bytes:
            self._rotate()
        return written if written is not None else len(data)

    def flush(self):
        self._fh.flush()

    def fileno(self):
        return self._fh.fileno()

    def close(self):
        self._fh.close()

    def _rotate(self):
        self._fh.close()
        for i in range(self.backup_count - 1, 0, -1):
            src = f"{self.path}.{i}"
            dst = f"{self.path}.{i + 1}"
            if os.path.exists(src):
                os.replace(src, dst)
        if os.path.exists(self.path):
            os.replace(self.path, f"{self.path}.1")
        self._fh = open(self.path, "w", encoding="utf-8")
        self._size = 0


class _ServiceRegistry:
    """Global singleton registry: ensures one service process per port across all agents."""

    _lock = threading.Lock()
    _services: dict[int, dict] = {}  # port -> {"process": Popen, "refcount": int, "plugin_names": set}

    @classmethod
    def register(cls, port: int, process: subprocess.Popen, plugin_name: str) -> bool:
        """Register a service. Returns True if caller is the owner (should manage lifecycle)."""
        with cls._lock:
            if port in cls._services:
                cls._services[port]["refcount"] += 1
                cls._services[port]["plugin_names"].add(plugin_name)
                return False  # already owned by another agent
            cls._services[port] = {
                "process": process,
                "refcount": 1,
                "plugin_names": {plugin_name},
            }
            return True

    @classmethod
    def unregister(cls, port: int, plugin_name: str) -> bool:
        """Unregister a service. Returns True if caller should stop the process."""
        with cls._lock:
            svc = cls._services.get(port)
            if not svc:
                return False
            svc["refcount"] -= 1
            svc["plugin_names"].discard(plugin_name)
            if svc["refcount"] <= 0:
                del cls._services[port]
                return True  # last reference, should stop
            return False

    @classmethod
    def get_process(cls, port: int) -> subprocess.Popen | None:
        with cls._lock:
            svc = cls._services.get(port)
            return svc["process"] if svc else None

    @classmethod
    def is_running(cls, port: int) -> bool:
        with cls._lock:
            return port in cls._services


def _acquire_service_lock(port: int, timeout: float = 6.0) -> Any | None:
    """Acquire a file-based lock for a service port (cross-process safe).

    Returns the lock object if acquired, or None on timeout.
    Uses a simple lockfile in the temp directory so that only one agent
    process attempts to start the service subprocess at a time.
    """
    import tempfile

    lock_path = os.path.join(tempfile.gettempdir(), f"opensquad_svc_{port}.lock")
    lock_file = open(lock_path, "w")
    try:
        if sys.platform == "win32":
            import msvcrt

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    return lock_file
                except OSError:
                    time.sleep(0.1)
            lock_file.close()
            return None
        else:
            import fcntl

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return lock_file
                except OSError:
                    time.sleep(0.1)
            lock_file.close()
            return None
    except Exception:
        lock_file.close()
        return None


def _release_service_lock(lock_file: Any) -> None:
    """Release the file-based service lock."""
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    finally:
        with contextlib.suppress(Exception):
            lock_file.close()


class ServicePlugin(Plugin):
    """
    Generic plugin service auto-start base class.
    Uses a global singleton registry to ensure only one service process runs
    per port across all agents.

    Subclasses only need to call super().__init__() in __init__ and pass the service configuration.
    """

    def __init__(
        self,
        context: Context,
        service_script: str = "main.py",
        health_endpoint: str = "/health",
        service_name: str = "Service",
        max_startup_wait: int = 8,
        health_check_interval: int = 60,
    ):
        """
        Initialize service plugin.

        Args:
            context: Plugin context
            service_script: Startup script name under the service folder (e.g. "main.py" or "service.py")
            health_endpoint: Health-check endpoint (e.g. "/health")
            service_name: Service name (used in log messages)
            max_startup_wait: Maximum startup wait time (seconds)
            health_check_interval: Health-check interval (seconds)
        """
        super().__init__(context)

        self.service_script = service_script
        self.health_endpoint = health_endpoint
        self.service_name = service_name
        self.max_startup_wait = max_startup_wait
        self.health_check_interval = health_check_interval

        self._service_process: subprocess.Popen | None = None
        self._health_check_thread: threading.Thread | None = None
        self._startup_thread: threading.Thread | None = None
        self._stop_health_check = threading.Event()
        self._log_writer: _RotatingLogWriter | None = None
        self._port: int = 0
        self._is_owner: bool = False  # True if this instance owns the service process

        # Extract plugin name from plugin_dir for log prefix
        plugin_name = os.path.basename(context.plugin_dir) if context.plugin_dir else "unknown"
        self.logger = logging.getLogger(f"plugins.{plugin_name}")

    def on_load(self) -> None:
        """Automatically start the service when the plugin is loaded."""
        self.logger.info(f"[{self.service_name}] loaded.")

        # Check whether auto-start is enabled
        if not self.context.config.get("auto_start", True):
            self.logger.info(f"[{self.service_name}] auto_start=False, service not started.")
            return

        # Check if service already running (shared across agents)
        port = self.context.config.get("port")
        if port and _ServiceRegistry.is_running(port):
            self.logger.info(f"[{self.service_name}] Service already running on port {port} (shared across agents)")
            return

        # Start service in background to avoid blocking agent startup path
        self._start_service_async()

    def on_unload(self) -> None:
        """Stop the service when the plugin is unloaded (only if last reference)."""
        if self._port > 0:
            should_stop = _ServiceRegistry.unregister(self._port, self.service_name)
            if should_stop:
                self._stop_service()
            else:
                self.logger.info(
                    f"[{self.service_name}] Service on port {self._port} still in use by other agents, keeping alive."
                )
        else:
            self._stop_service()
        self.logger.info(f"[{self.service_name}] unloaded.")

    def _start_service_async(self) -> None:
        """Start service in a daemon thread to avoid blocking plugin load."""
        if self._startup_thread and self._startup_thread.is_alive():
            return
        self._startup_thread = threading.Thread(
            target=self._start_service, daemon=True, name=f"{self.service_name.lower()}_startup"
        )
        self._startup_thread.start()

    def _start_service(self) -> None:
        """Start the service process (if not already running elsewhere).

        Uses a file-based lock keyed on the port number so that when
        multiple agent processes start simultaneously, only ONE of them
        actually launches the service subprocess.  The others wait for
        the lock, then discover the service is already healthy.
        """
        port = self.context.config.get("port")
        if not port:
            self.logger.error(f"[{self.service_name}] No port configured, cannot start service.")
            return

        self._port = port

        # Check whether the service is already running (via shared registry or health check)
        existing = _ServiceRegistry.get_process(port)
        if existing and existing.poll() is None:
            self.logger.info(f"[{self.service_name}] Service already running on port {port} (shared)")
            _ServiceRegistry.register(port, existing, self.service_name)
            self._start_health_monitor(port)
            return

        if self._check_service_health(port):
            self.logger.info(f"[{self.service_name}] Service already healthy on port {port}")
            _ServiceRegistry.register(port, None, self.service_name)
            self._start_health_monitor(port)
            return

        # Acquire cross-process lock for this port — only one agent process
        # will proceed to start the subprocess; others will wait and then
        # discover the service is already running.
        svc_lock = _acquire_service_lock(port)
        if svc_lock is None:
            self.logger.warning(f"[{self.service_name}] Could not acquire service lock for port {port}, skipping")
            return

        try:
            # Double-check after acquiring lock (another process may have started it)
            if self._check_service_health(port):
                self.logger.info(f"[{self.service_name}] Service started by another process on port {port}")
                _ServiceRegistry.register(port, None, self.service_name)
                self._start_health_monitor(port)
                return

            # Get the service script path
            service_path = os.path.join(self.context.plugin_dir, "service", self.service_script)
            if not os.path.isfile(service_path):
                self.logger.error(f"[{self.service_name}] Service script not found: {service_path}")
                return

            try:
                # Prepare log file path
                log_dir = os.path.join(os.path.dirname(self.context.plugin_dir), "..", "data", "logs")
                os.makedirs(log_dir, exist_ok=True)
                plugin_name = os.path.basename(self.context.plugin_dir)
                log_file = os.path.join(log_dir, f"{plugin_name}_service.log")

                # Start subprocess (background, no window)
                self.logger.info(f"[{self.service_name}] Starting service on port {port}...")
                self.logger.info(f"[{self.service_name}] Service script: {service_path}")
                self.logger.info(f"[{self.service_name}] Service log: {log_file}")

                self._log_writer = _RotatingLogWriter(log_file, max_bytes=3 * 1024 * 1024, backup_count=3)
                self._service_process = subprocess.Popen(
                    [sys.executable, service_path],
                    stdout=self._log_writer,
                    stderr=self._log_writer,
                    stdin=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )

                # Register in global singleton registry
                self._is_owner = _ServiceRegistry.register(port, self._service_process, self.service_name)

                # Wait for service to start
                # Use 0.5s intervals so we can detect dead subprocesses quickly.
                elapsed = 0.0
                while elapsed < self.max_startup_wait:
                    time.sleep(0.5)
                    elapsed += 0.5
                    # Fail-fast: if subprocess already exited, stop waiting immediately
                    if self._service_process and self._service_process.poll() is not None:
                        exit_code = self._service_process.returncode
                        self.logger.error(
                            f"[{self.service_name}] Service process exited prematurely "
                            f"(exit_code={exit_code}) after {elapsed:.1f}s"
                        )
                        break
                    if self._check_service_health(port):
                        self.logger.info(
                            f"[{self.service_name}] Service started successfully on port {port} ({elapsed:.1f}s)"
                        )
                        self._start_health_monitor(port)
                        return

                self.logger.error(f"[{self.service_name}] Service failed to start after {self.max_startup_wait}s")
                self._stop_service()

            except Exception as e:
                self.logger.error(f"[{self.service_name}] Failed to start service: {e}")
                self._stop_service()
        finally:
            _release_service_lock(svc_lock)

    def _stop_service(self) -> None:
        """Stop the service process."""
        # Stop startup / health-check threads
        # Avoid deadlock: cannot join current thread from within itself
        if self._startup_thread and self._startup_thread.is_alive():
            if self._startup_thread is not threading.current_thread():
                self._startup_thread.join(timeout=1)
            self._startup_thread = None
        if self._health_check_thread:
            self._stop_health_check.set()
            self._health_check_thread.join(timeout=2)
            self._health_check_thread = None

        # Terminate the service process
        if self._service_process:
            try:
                self._service_process.terminate()
                self._service_process.wait(timeout=5)
                self.logger.info(f"[{self.service_name}] Service stopped")
            except Exception as e:
                self.logger.warning(f"[{self.service_name}] Failed to stop service gracefully: {e}")
                with contextlib.suppress(Exception):
                    self._service_process.kill()
            finally:
                self._service_process = None

        # Close log writer
        if self._log_writer:
            with contextlib.suppress(Exception):
                self._log_writer.close()
            self._log_writer = None

    def _check_service_health(self, port: int) -> bool:
        """Check whether the service is healthy."""
        try:
            resp = requests.get(f"http://localhost:{port}{self.health_endpoint}", timeout=2)
            return resp.status_code == 200
        except Exception:
            return False

    def _start_health_monitor(self, port: int) -> None:
        """Start the health-monitoring thread (checks service status periodically)."""

        def monitor():
            while not self._stop_health_check.wait(self.health_check_interval):
                if not self._check_service_health(port):
                    self.logger.warning(f"[{self.service_name}] Service health check failed, attempting restart...")
                    # Stop service process (without joining the current thread)
                    if self._service_process:
                        try:
                            self._service_process.terminate()
                            self._service_process.wait(timeout=5)
                        except Exception:
                            with contextlib.suppress(Exception):
                                self._service_process.kill()
                        finally:
                            self._service_process = None

                    # Restart service
                    time.sleep(2)
                    self._start_service()
                    break  # Exit this thread after restart; a new thread is created inside _start_service

        self._stop_health_check.clear()
        self._health_check_thread = threading.Thread(
            target=monitor, daemon=True, name=f"{self.service_name.lower()}_health_monitor"
        )
        self._health_check_thread.start()
        self.logger.info(f"[{self.service_name}] Health monitor started")
