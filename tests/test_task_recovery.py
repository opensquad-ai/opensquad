"""Resuming an unfinished task when the agent (or the machine) comes back up.

The chain had two broken links and no test could have caught either, because nothing called
the code: `task_checkpoint.write_checkpoint()` had no caller, and the only intended clear
site imported `opensquad.checkpoint` — a module that does not exist — inside
`try/except: pass`. So these pin both ends: that the record is written/cleared from the
runner, and that boot turns it into one injected recovery message.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from opensquad import task_recovery  # noqa: E402
from opensquad.task_checkpoint import read_checkpoint, write_checkpoint  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("OPENSQUAD_NO_TASK_RECOVERY", raising=False)
    yield


def _agent_dir(tmp_path: Path) -> str:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    return str(tmp_path)


def _in_flight(agent_dir: str, **overrides) -> None:
    payload = {
        "active": True,
        "task_id": "t-1",
        "original_request": "把 site 的图片都下载下来并转成 webp",
        "progress_summary": "fetch_images completed",
        "last_tool_call": {"name": "fetch_images", "args": "{}", "status": "completed", "result_preview": "42 files"},
        "session_id": "20261011_090000_abcd",
    }
    payload.update(overrides)
    write_checkpoint(agent_dir, **payload)


# ── the boot end ────────────────────────────────────────────────────────────


def test_the_agent_is_asked_to_continue(tmp_path):
    agent_dir = _agent_dir(tmp_path)
    _in_flight(agent_dir)
    pushed: list[tuple[str, dict]] = []

    assert task_recovery.recover_pending_task(agent_dir, push=lambda text, **kw: pushed.append((text, kw))) is True

    assert len(pushed) == 1
    text, kwargs = pushed[0]
    assert "[RECOVERY_MODE]" in text
    assert "把 site 的图片都下载下来并转成 webp" in text
    assert "fetch_images" in text
    assert kwargs["source"] == task_recovery.RECOVERY_SOURCE
    assert kwargs["session_id"] == "20261011_090000_abcd"


def test_recovery_is_injected_only_once(tmp_path):
    agent_dir = _agent_dir(tmp_path)
    _in_flight(agent_dir)
    calls: list[str] = []
    push = lambda text, **kw: calls.append(text)  # noqa: E731

    assert task_recovery.recover_pending_task(agent_dir, push=push) is True
    # A restart loop (or a second boot path) must not re-inject the same recovery.
    assert task_recovery.recover_pending_task(agent_dir, push=push) is False
    assert len(calls) == 1
    assert read_checkpoint(agent_dir)["active"] is False


def test_a_finished_task_is_not_resumed(tmp_path):
    agent_dir = _agent_dir(tmp_path)
    _in_flight(agent_dir)
    from opensquad.task_checkpoint import clear_checkpoint

    clear_checkpoint(agent_dir)

    assert task_recovery.recover_pending_task(agent_dir, push=lambda *a, **k: None) is False


def _age_checkpoint(agent_dir: str, age_s: float) -> None:
    """Backdate the record: `write_checkpoint` always stamps `ts` itself."""
    import json
    import os

    path = os.path.join(agent_dir, "data", "task_checkpoint.json")
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    data["ts"] = time.time() - age_s
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)


def test_a_stale_checkpoint_is_left_alone(tmp_path):
    agent_dir = _agent_dir(tmp_path)
    _in_flight(agent_dir)
    _age_checkpoint(agent_dir, task_recovery.MAX_RECOVERY_AGE_S + 60)

    assert task_recovery.pending_checkpoint(agent_dir) is None
    assert task_recovery.recover_pending_task(agent_dir, push=lambda *a, **k: None) is False


def test_the_env_var_opts_out(tmp_path, monkeypatch):
    agent_dir = _agent_dir(tmp_path)
    _in_flight(agent_dir)
    monkeypatch.setenv("OPENSQUAD_NO_TASK_RECOVERY", "1")

    assert task_recovery.recover_pending_task(agent_dir, push=lambda *a, **k: None) is False
    # …and the checkpoint is untouched, so a later boot without the flag still resumes.
    assert read_checkpoint(agent_dir)["active"] is True


def test_a_push_that_raises_does_not_break_the_boot(tmp_path):
    agent_dir = _agent_dir(tmp_path)
    _in_flight(agent_dir)

    def _boom(*_a, **_k):
        raise RuntimeError("hub is closed")

    assert task_recovery.recover_pending_task(agent_dir, push=_boom) is False


# ── the writing end (source contract, like the repo's other wiring locks) ───


def test_the_runner_writes_and_clears_the_record():
    runner_src = (_SRC / "opensquad" / "runner.py").read_text(encoding="utf-8")
    assert "write_checkpoint(" in runner_src, "the runner never records what it is doing"
    assert "_record_task_checkpoint(original_request=content" in runner_src, "turn start is not recorded"
    assert runner_src.count("_clear_task_checkpoint()") >= 2, "finish/stop paths must retire the record"

    loop_src = (_SRC / "opensquad" / "_runner" / "_turn_loop.py").read_text(encoding="utf-8")
    assert "_record_tool_checkpoint(" in loop_src, "tool progress is not recorded"

    boot_src = (_SRC / "opensquad" / "agents_boot.py").read_text(encoding="utf-8")
    assert "recover_pending_task(agent_dir" in boot_src, "boot never asks the agent to resume"


def test_the_dead_import_is_gone():
    """`from opensquad import checkpoint` never existed — it silently no-oped for months."""
    loop_src = (_SRC / "opensquad" / "_runner" / "_turn_loop.py").read_text(encoding="utf-8")
    assert "opensquad import checkpoint" not in loop_src
