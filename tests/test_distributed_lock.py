# -*- coding: utf-8 -*-
"""Tests for distributed_lock — cross-process mutual exclusion."""
import multiprocessing
import os
import tempfile
import time

import pytest

from opensquad.distributed_lock import (
    SessionLock,
    LockTimeoutError,
    session_lock,
    DEFAULT_LOCK_DIR,
)


class TestSessionLockBasics:
    def test_acquire_and_release(self):
        lock = SessionLock("test_res_1", timeout=1.0, lock_dir=DEFAULT_LOCK_DIR)
        assert lock.acquire() is True
        lock.release()

    def test_context_manager(self):
        with SessionLock("test_res_2", timeout=1.0, lock_dir=DEFAULT_LOCK_DIR) as lock:
            assert lock._lock_file is not None

    def test_timeout_when_already_locked(self):
        lock1 = SessionLock("test_res_3", timeout=0.5, lock_dir=DEFAULT_LOCK_DIR)
        assert lock1.acquire() is True
        try:
            lock2 = SessionLock("test_res_3", timeout=0.2, lock_dir=DEFAULT_LOCK_DIR)
            assert lock2.acquire() is False
        finally:
            lock1.release()

    def test_context_manager_raises_on_timeout(self):
        lock1 = SessionLock("test_res_4", timeout=0.5, lock_dir=DEFAULT_LOCK_DIR)
        assert lock1.acquire() is True
        try:
            with pytest.raises(LockTimeoutError):
                with SessionLock("test_res_4", timeout=0.1, lock_dir=DEFAULT_LOCK_DIR):
                    pass
        finally:
            lock1.release()

    def test_session_lock_contextmanager(self):
        with session_lock("test_res_5", timeout=1.0, lock_dir=DEFAULT_LOCK_DIR) as lock:
            assert lock._lock_file is not None

    def test_different_resources_no_conflict(self):
        lock_a = SessionLock("res_a", timeout=1.0, lock_dir=DEFAULT_LOCK_DIR)
        lock_b = SessionLock("res_b", timeout=1.0, lock_dir=DEFAULT_LOCK_DIR)
        assert lock_a.acquire() is True
        assert lock_b.acquire() is True
        lock_a.release()
        lock_b.release()


# ---------------------------------------------------------------------------
# Cross-process test: verify the lock actually blocks another process
# ---------------------------------------------------------------------------

def _worker_acquire_and_hold(lock_dir: str, resource_id: str, hold_time: float, result_queue):
    """Worker process: acquire lock, signal success, hold for hold_time, then release."""
    lock = SessionLock(resource_id, timeout=2.0, lock_dir=lock_dir)
    acquired = lock.acquire()
    result_queue.put(("acquired", acquired))
    if acquired:
        time.sleep(hold_time)
        lock.release()
        result_queue.put(("released", True))
    else:
        result_queue.put(("released", False))


class TestCrossProcessLock:
    def _cleanup(self, lock_dir: str, resource_id: str):
        lock_file = os.path.join(lock_dir, f"{resource_id}.lock")
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
            except Exception:
                pass

    def test_second_process_waits_for_lock(self):
        lock_dir = os.path.join(tempfile.gettempdir(), "opensquad_test_locks")
        resource_id = "cross_proc_test"
        self._cleanup(lock_dir, resource_id)

        result_queue = multiprocessing.Queue()

        # Start a worker that holds the lock for 0.3s
        p1 = multiprocessing.Process(
            target=_worker_acquire_and_hold,
            args=(lock_dir, resource_id, 0.3, result_queue),
        )
        p1.start()

        # Wait until p1 has definitely acquired the lock
        msg1 = result_queue.get(timeout=5.0)
        assert msg1 == ("acquired", True)

        # Now try to acquire the same lock from this process with a 1s timeout
        # (longer than the 0.3s hold time)
        lock2 = SessionLock(resource_id, timeout=1.0, lock_dir=lock_dir)
        acquired2 = lock2.acquire()
        assert acquired2 is True, "Second process should wait and eventually acquire"
        lock2.release()

        p1.join(timeout=5.0)
        assert p1.exitcode == 0
        self._cleanup(lock_dir, resource_id)

    def test_second_process_times_out(self):
        lock_dir = os.path.join(tempfile.gettempdir(), "opensquad_test_locks")
        resource_id = "cross_proc_timeout_test"
        self._cleanup(lock_dir, resource_id)

        result_queue = multiprocessing.Queue()

        # Start a worker that holds the lock for 1.0s
        p1 = multiprocessing.Process(
            target=_worker_acquire_and_hold,
            args=(lock_dir, resource_id, 1.0, result_queue),
        )
        p1.start()

        msg1 = result_queue.get(timeout=10.0)
        assert msg1 == ("acquired", True)

        # Try with a very short timeout — should fail
        lock2 = SessionLock(resource_id, timeout=0.1, lock_dir=lock_dir)
        acquired2 = lock2.acquire()
        assert acquired2 is False, "Second process should time out"

        p1.join(timeout=10.0)
        assert p1.exitcode == 0
        self._cleanup(lock_dir, resource_id)
