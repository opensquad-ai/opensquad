"""A settings PUT that changes ``voice`` must reach the running agent.

The agent reads config.json only for ``tools`` / ``tool_levels`` / ``model`` on
its mtime poll, so voice cards edited from the UI (or written by the launcher's
``PUT /api/agents/{name}/config``) were ignored until the agent restarted — the
ASR/TTS tools kept talking to the previous endpoint. These tests pin the new
voice branch of ``StateMachine._poll_hot_reload``.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from opensquad._runner import _state_machine as sm_mod


class _FakeRunner:
    def __init__(self, cfg: dict, config_path: str):
        self._plugin_manager = None
        self._config_path = config_path
        self._config_mtime = 0.0
        self._agent_dir = ""
        self._agent_tool_names = cfg.get("tools", [])
        self._agent_tool_levels = cfg.get("tool_levels", {})
        self._model_config = cfg.get("model", {})
        self._voice_config = cfg.get("voice", {})


def _write_config(path: str, cfg: dict) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(cfg, handle, ensure_ascii=False)


@pytest.fixture
def voice_recorder(monkeypatch):
    """Record the two calls that make a voice change take effect at runtime."""
    seen = {"context": [], "tools": []}

    import opensquad.agent_runtime_context as arc
    import plugins.step_voice.step_voice_tools as sv

    monkeypatch.setattr(arc, "set_context", lambda **kw: seen["context"].append(kw.get("config")))
    monkeypatch.setattr(sv, "set_agent_config", lambda cfg: seen["tools"].append(cfg))
    return seen


def test_voice_change_is_applied_on_config_reload(tmp_path, voice_recorder):
    config_path = str(tmp_path / "config.json")
    old = {"voice": {"asr_card": "old-asr"}, "tools": ["a"], "model": {"x": 1}}
    new = {"voice": {"asr_card": "new-asr"}, "tools": ["a"], "model": {"x": 1}}
    _write_config(config_path, old)
    runner = _FakeRunner(old, config_path)

    _write_config(config_path, new)
    asyncio.run(sm_mod.StateMachine()._poll_hot_reload(runner))

    assert runner._voice_config == {"asr_card": "new-asr"}
    assert voice_recorder["context"] == [new]
    assert voice_recorder["tools"] == [new]


def test_unchanged_voice_is_not_reapplied(tmp_path, voice_recorder):
    config_path = str(tmp_path / "config.json")
    cfg = {"voice": {"asr_card": "same"}, "tools": [], "model": {}}
    _write_config(config_path, cfg)
    runner = _FakeRunner(cfg, config_path)

    # A reload with the same voice (e.g. an unrelated tools edit) must not
    # re-push the voice config.
    asyncio.run(sm_mod.StateMachine()._poll_hot_reload(runner))

    assert runner._voice_config == {"asr_card": "same"}
    assert voice_recorder["context"] == []
    assert voice_recorder["tools"] == []


def test_voice_reload_survives_a_context_failure(tmp_path, monkeypatch):
    config_path = str(tmp_path / "config.json")
    old = {"voice": {"asr_card": "old"}}
    new = {"voice": {"asr_card": "new"}}
    _write_config(config_path, old)
    runner = _FakeRunner(old, config_path)

    import opensquad.agent_runtime_context as arc
    import plugins.step_voice.step_voice_tools as sv

    def _boom(**kw):
        raise RuntimeError("context exploded")

    seen = []
    monkeypatch.setattr(arc, "set_context", _boom)
    monkeypatch.setattr(sv, "set_agent_config", lambda cfg: seen.append(cfg))

    _write_config(config_path, new)
    asyncio.run(sm_mod.StateMachine()._poll_hot_reload(runner))

    # A failure in one half must not stop the other, nor blow up the poll loop.
    assert runner._voice_config == {"asr_card": "new"}
    assert seen == [new]
