"""Launcher single-instance lock must not block on a dead pid / dead port."""

from __future__ import annotations

import os

from opensquad.launcher_main import _launcher_api_healthy, _pid_alive


def test_pid_alive_self():
    assert _pid_alive(os.getpid()) is True


def test_pid_alive_missing():
    assert _pid_alive(0) is False
    assert _pid_alive(-1) is False
    assert _pid_alive(999_999_999) is False


def test_launcher_api_unhealthy_when_nothing_listens():
    assert _launcher_api_healthy(1) is False
