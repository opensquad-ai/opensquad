"""Vision injection must happen at every event-pipeline drain site.

Reported 2026-09-27: an agent called 图像理解 (vision.read_image) and then slept
with 等待 forever. The plugin had verified the files and pushed its
`vision_tool` / `inject_images` event, but only the parallel loop's per-tool
drain turned that event into images. The wait loop drained it first and passed
it to the model as the literal text

    [vision_tool @ 11:03:07] [Image injection requested: ['C:\\shot.png']]

so `_current_images` stayed empty, no `[VISION]` line ever appeared in
agent.log, and the model kept waiting for pixels that had been dropped.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from opensquad.event_pipeline import event_pipeline
from opensquad.runner_wait_loop import RunnerWaitLoop
from opensquad.vision_inject import (
    apply_vision_injection,
    collect_paths,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "opensquad"

VISION_PATHS = ["C:\\shots\\a.png", "C:\\shots\\b.png"]


def _evt(source, content, metadata=None):
    return SimpleNamespace(source=source, content=content, metadata=metadata or {})


def _vision_evt(paths=VISION_PATHS):
    return _evt(
        "vision_tool",
        f"[Image injection requested: {paths}]",
        {"image_paths": paths, "action": "inject_images"},
    )


class DummyInputHub:
    def __init__(self):
        self.stop_requested = False

    def is_stop_requested(self):
        return False

    def clear_stop_request(self):
        pass

    def check_urgent_commands(self):
        return []

    def get_all_pending(self):
        return []


class DummyChatApi:
    def __init__(self):
        self.req = []
        self.pipeline_events = []

    def add_pipeline_events(self, content):
        self.pipeline_events.append(content)


class DummySessionManager:
    def __init__(self):
        self.messages = []

    def add_event(self, event_type, payload, turn_id=None, round_id=None):
        pass

    def add_message(self, role, content, sid=None, **kwargs):
        self.messages.append((role, content))


def _stub_runner(agent_dir: str, sid: str = "s-vision-test"):
    async def emit(event_type, payload):
        pass

    async def setup_prompt():
        return None

    return SimpleNamespace(
        _agent_dir=agent_dir,
        _turn_sid=sid,
        _current_images=[],
        _session_manager=DummySessionManager(),
        _chat_api=None,
        chat_api=DummyChatApi(),
        _emit=emit,
        _setup_prompt=setup_prompt,
        _input_hub=DummyInputHub(),
        _command_dispatcher=SimpleNamespace(),
        _message_queue=SimpleNamespace(get_all=lambda: []),
        _current_turn=1,
        _current_round=1,
        _inner_loop_count=0,
        _turn_start_time=0.0,
    )


def test_collect_paths_only_reads_vision_events():
    events = [
        _evt("web", "hello"),
        _vision_evt(["C:\\a.png"]),
        _evt("vision_tool", "wrong action", {"image_paths": ["C:\\x.png"], "action": "noop"}),
        _vision_evt(["C:\\a.png", "C:\\b.png"]),  # overlaps the first request
    ]
    assert collect_paths(events) == ["C:\\a.png", "C:\\b.png"]


def test_apply_vision_injection_queues_paths_and_clears_file(tmp_path):
    runner = _stub_runner(str(tmp_path))
    path_file = tmp_path / "img_path.txt"
    path_file.write_text(str(VISION_PATHS), encoding="utf-8")

    added = apply_vision_injection(runner, [_vision_evt()])

    assert added == VISION_PATHS
    assert runner._current_images == VISION_PATHS
    # A consumed request must not linger: the serial loop reads this file at turn
    # start and would otherwise attach the same images a second time.
    assert path_file.read_text(encoding="utf-8") == ""


def test_apply_vision_injection_is_idempotent_per_turn(tmp_path):
    runner = _stub_runner(str(tmp_path))
    apply_vision_injection(runner, [_vision_evt()])
    again = apply_vision_injection(runner, [_vision_evt()])

    assert again == []
    assert runner._current_images == VISION_PATHS


def test_apply_vision_injection_falls_back_to_file_when_queueing_fails(tmp_path):
    """A runner without an image list must not lose the request outright."""
    runner = SimpleNamespace(_agent_dir=str(tmp_path))

    assert apply_vision_injection(runner, [_vision_evt()]) == []
    assert (tmp_path / "img_path.txt").read_text(encoding="utf-8") == str(VISION_PATHS)


def test_wait_loop_drain_injects_vision_images(tmp_path):
    """The reported bug: 等待 wakes, drains, and used to swallow the vision event."""
    sid = "s-vision-wait-test"
    event_pipeline.push_nowait(
        source="vision_tool",
        content=f"[Image injection requested: {VISION_PATHS}]",
        metadata={"image_paths": VISION_PATHS, "action": "inject_images"},
        session_id=sid,
    )

    runner = _stub_runner(str(tmp_path), sid=sid)
    result = asyncio.run(RunnerWaitLoop(runner).wait_for_events(None, "current"))

    assert result.should_continue_turn_loop is True
    assert runner._current_images == VISION_PATHS
    assert (tmp_path / "img_path.txt").read_text(encoding="utf-8") == ""


def test_every_event_drain_site_injects_vision():
    """A new drain site must not silently drop vision events again.

    Each place that drains the pipeline either injects vision paths or re-queues
    the leftover events for a later drain (the turn-end sweep). Anything else is
    the bug this file was written for.
    """
    files = ["runner.py", "runner_wait_loop.py", "_runner/_turn_loop.py"]
    checked = 0
    for rel in files:
        lines = (SRC / rel).read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if "drain_sync(" not in line and "drain_formatted_sync(" not in line:
                continue
            checked += 1
            window = "\n".join(lines[max(0, i - 2) : i + 8])
            ok = (
                "apply_vision_injection" in window or "_leftover" in line  # turn-end sweep re-pushes non-user events
            )
            assert ok, f"{rel}:{i + 1} drains events without injecting vision paths"
    assert checked >= 5, f"expected the known drain sites to still exist, found {checked}"
