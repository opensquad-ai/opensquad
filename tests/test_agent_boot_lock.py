"""Single-instance agent lock must not block auto-start on a recycled PID."""

from pathlib import Path

from opensquad.agents_boot import _acquire_agent_lock
from opensquad.launcher.process_manager import reap_stale_agent_boot_lock


def test_acquire_lock_steals_when_owner_is_unrelated(tmp_path, monkeypatch):
    agent_dir = str(tmp_path)
    lock = Path(agent_dir) / "data" / "agents_boot.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("1", encoding="utf-8")

    monkeypatch.setattr("opensquad.agents_boot._agent_pid_alive", lambda pid: True)
    monkeypatch.setattr(
        "opensquad.agents_boot._lock_owner_is_this_agent",
        lambda pid, d: False,
    )
    assert _acquire_agent_lock(agent_dir) is True
    assert lock.read_text(encoding="utf-8").strip().isdigit()


def test_reap_stale_lock_removes_file_without_killing_unrelated_pid(tmp_path, monkeypatch):
    agent_dir = str(tmp_path)
    lock = Path(agent_dir) / "data" / "agents_boot.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("99999", encoding="utf-8")

    killed = []
    monkeypatch.setattr(
        "opensquad.launcher.process_manager._pid_exists",
        lambda pid: True,
    )
    monkeypatch.setattr(
        "opensquad.launcher.process_manager._pid_is_agent_boot",
        lambda pid, d: False,
    )
    monkeypatch.setattr(
        "opensquad.launcher.process_manager._terminate_pid_tree",
        lambda pid: killed.append(pid) or True,
    )
    reap_stale_agent_boot_lock(agent_dir)
    assert killed == []
    assert not lock.exists()
