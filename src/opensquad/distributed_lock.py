# -*- coding: utf-8 -*-
"""
Distributed lock based on OS file locking.

Guarantees mutual exclusion across processes (not just threads) on the same
machine.  This is sufficient for OpenSquad because:
  - Each agent runs as a separate process
  - SQLite DB and session files are local to the machine
  - The main risk is concurrent access from multiple agent processes

For a true multi-machine distributed lock, swap FileLock for a Redis / etcd
implementation using the same SessionLock protocol.

Usage:
    with SessionLock("session_abc123", timeout=5.0):
        # exclusive access to session abc123
        ...
"""
from __future__ import annotations

import os
import sys
import time
import logging
import tempfile
from contextlib import contextmanager
from typing import Generator, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal: OS-level file lock primitives (same approach as service_plugin.py)
# ---------------------------------------------------------------------------

def _acquire_file_lock(lock_path: str, timeout: float = 10.0) -> Optional[object]:
    """Try to acquire an exclusive file lock. Returns file handle or None."""
    try:
        # Use os.open for atomic creation on both Windows and Unix
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
        lock_file = os.fdopen(fd, "r+")
    except Exception:
        return None

    try:
        if sys.platform == "win32":
            import msvcrt
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    return lock_file
                except OSError:
                    time.sleep(0.05)
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
                    time.sleep(0.05)
            lock_file.close()
            return None
    except Exception:
        lock_file.close()
        return None


def _release_file_lock(lock_file: object) -> None:
    """Release a file lock and close the handle."""
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
        try:
            lock_file.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

DEFAULT_LOCK_DIR: str = os.path.join(tempfile.gettempdir(), "opensquad_locks")


def _lock_path(resource_id: str, lock_dir: str = DEFAULT_LOCK_DIR) -> str:
    """Sanitize resource_id and build the lock file path."""
    # Replace path separators to avoid directory traversal
    safe_id = resource_id.replace("/", "_").replace("\\", "_").replace("..", "_")
    os.makedirs(lock_dir, exist_ok=True)
    return os.path.join(lock_dir, f"{safe_id}.lock")


class LockTimeoutError(Exception):
    """Raised when a distributed lock cannot be acquired within the timeout."""
    pass


class SessionLock:
    """
    Distributed lock scoped to a single session (or any resource).

    Parameters
    ----------
    resource_id : str
        Unique identifier for the resource to lock (e.g. session UUID).
    timeout : float
        Maximum seconds to wait for the lock. 0 = non-blocking.
    lock_dir : str
        Directory where lock files are created. Defaults to temp dir.
    """

    def __init__(
        self,
        resource_id: str,
        *,
        timeout: float = 10.0,
        lock_dir: str = DEFAULT_LOCK_DIR,
    ):
        self.resource_id = resource_id
        self.timeout = timeout
        self.lock_dir = lock_dir
        self._lock_file: Optional[object] = None
        self._lock_path = _lock_path(resource_id, lock_dir)

    def acquire(self) -> bool:
        """Try to acquire the lock. Returns True on success, False on timeout."""
        logger.debug(f"[Lock] acquiring {self.resource_id} (timeout={self.timeout}s)")
        self._lock_file = _acquire_file_lock(self._lock_path, self.timeout)
        if self._lock_file is None:
            logger.warning(f"[Lock] FAILED to acquire {self.resource_id}")
            return False
        logger.debug(f"[Lock] acquired {self.resource_id}")
        return True

    def release(self) -> None:
        """Release the lock if held."""
        if self._lock_file is not None:
            _release_file_lock(self._lock_file)
            self._lock_file = None
            logger.debug(f"[Lock] released {self.resource_id}")

    def __enter__(self) -> "SessionLock":
        if not self.acquire():
            raise LockTimeoutError(
                f"Could not acquire lock for '{self.resource_id}' within {self.timeout}s"
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


@contextmanager
def session_lock(
    resource_id: str,
    *,
    timeout: float = 10.0,
    lock_dir: str = DEFAULT_LOCK_DIR,
) -> Generator[SessionLock, None, None]:
    """Context-manager shortcut for SessionLock (acquires on enter)."""
    lock = SessionLock(resource_id, timeout=timeout, lock_dir=lock_dir)
    if not lock.acquire():
        raise LockTimeoutError(
            f"Could not acquire lock for '{resource_id}' within {timeout}s"
        )
    try:
        yield lock
    finally:
        lock.release()
