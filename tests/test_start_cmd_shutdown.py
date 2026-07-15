"""Tests for opensquad start shutdown helpers."""

import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from opensquad.cli.commands import start_cmd as start_mod


@pytest.fixture(autouse=True)
def _reset_shutdown_state():
    start_mod._SHUTDOWN_DONE = False
    start_mod._ACTIVE_PROCESSES = []
    start_mod._ACTIVE_PORTS = (9555, 9600)
    start_mod._ACTIVE_JOB = None
    start_mod._LAUNCHER_PORT = None
    yield
    start_mod._SHUTDOWN_DONE = False
    start_mod._ACTIVE_PROCESSES = []
    start_mod._ACTIVE_PORTS = ()
    start_mod._ACTIVE_JOB = None
    start_mod._LAUNCHER_PORT = None


def test_shutdown_supervised_services_is_idempotent():
    proc = MagicMock()
    proc.pid = 4242
    start_mod._ACTIVE_PROCESSES = [("gateway", proc)]
    start_mod._ACTIVE_PORTS = (9555,)

    with (
        patch.object(start_mod, "_kill_tree") as kill_tree,
        patch.object(start_mod, "_kill_port_owners") as kill_ports,
        patch.object(start_mod, "_try_graceful_launcher_shutdown"),
    ):
        start_mod._shutdown_supervised_services(reason="first")
        start_mod._shutdown_supervised_services(reason="second")

    kill_tree.assert_called_once_with(4242)
    kill_ports.assert_called_once_with(9555)


def test_shutdown_closes_windows_job():
    job = MagicMock()
    start_mod._ACTIVE_JOB = job
    start_mod._ACTIVE_PROCESSES = []
    start_mod._ACTIVE_PORTS = ()

    with (
        patch.object(start_mod, "_kill_tree"),
        patch.object(start_mod, "_kill_port_owners"),
        patch.object(start_mod, "_try_graceful_launcher_shutdown"),
    ):
        start_mod._shutdown_supervised_services(reason="job-close", graceful=False)

    job.close.assert_called_once()
    assert start_mod._ACTIVE_JOB is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job object only")
def test_windows_kill_on_close_job_create_and_close():
    job = start_mod._WindowsKillOnCloseJob()
    # Spawn a short-lived process and assign it; closing the job should not raise.
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert job.add(proc) is True
    finally:
        job.close()
        # Job close should have terminated the child; wait briefly.
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise AssertionError("child process survived job close")
