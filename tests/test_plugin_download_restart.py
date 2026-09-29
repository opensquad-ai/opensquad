"""After a model download, the service that loads it must be restarted.

websearch and sensevoice both resolve their model at boot: a model fetched from
the admin UI *after* the service started stays invisible to the running process
(websearch silently keeps Bing order, sensevoice cannot transcribe). The plugins
now report the status file their download writes, and the launcher watches it
and restarts the owning service once the weights are usable.
"""

from __future__ import annotations

import json
import sys
import types

import pytest

from opensquad.launcher.management_api import _plugin_services as ps

# ── the decision, in isolation ─────────────────────────────────────────────


def test_watch_action_restarts_only_when_the_model_is_ready():
    assert ps.download_watch_action("ready", seen_downloading=True) == "restart"


def test_watch_action_stops_on_error():
    assert ps.download_watch_action("error", seen_downloading=True) == "stop"


def test_watch_action_stops_on_a_cancel_but_not_before_a_download_started():
    # Cancelled after we saw it running → give up.
    assert ps.download_watch_action("idle", seen_downloading=True) == "stop"
    # Pristine idle / no file yet → the status may simply not be written; wait.
    assert ps.download_watch_action("idle", seen_downloading=False) == "wait"
    assert ps.download_watch_action(None, seen_downloading=False) == "wait"


def test_watch_action_waits_while_downloading():
    assert ps.download_watch_action("downloading", seen_downloading=True) == "wait"


# ── the plugins declare what to watch ──────────────────────────────────────


@pytest.fixture
def plugin_queries():
    """Import both query modules, then restore sys.path.

    ``websearch/query.py`` puts the plugins tree first on ``sys.path``; leaving
    it there would let a later ``import telegram`` resolve to the plugin dir
    instead of the installed package, so the fixture undoes it.
    """
    before = list(sys.path)
    try:
        import plugins.sensevoice.query as sensevoice_query
        import plugins.websearch.query as websearch_query
    finally:
        sys.path[:] = before
    return websearch_query, sensevoice_query


def test_websearch_download_action_reports_its_status_path(plugin_queries, monkeypatch):
    websearch_query, _ = plugin_queries
    monkeypatch.setattr(
        websearch_query, "reranker_start_download", lambda force=False: {"started": True, "state": "downloading"}
    )
    result = websearch_query.handle_action(".", "download_reranker", {})
    assert result["started"] is True
    assert result["download_status_path"] == websearch_query.reranker_status_path()


def test_sensevoice_download_action_reports_its_status_path(plugin_queries, monkeypatch):
    _, sensevoice_query = plugin_queries
    monkeypatch.setattr(
        sensevoice_query, "start_download", lambda force=False: {"started": True, "state": "downloading"}
    )
    result = sensevoice_query.handle_action(".", "download_model", {})
    assert result["started"] is True
    assert result["download_status_path"] == sensevoice_query.status_path()


def test_other_actions_do_not_arm_a_restart(plugin_queries):
    websearch_query, _ = plugin_queries
    result = websearch_query.handle_action(".", "browser_config", {})
    assert "download_status_path" not in result


# ── the launcher watcher ───────────────────────────────────────────────────


class _Stub(ps.PluginServicesMixin):
    def __init__(self):
        self.restarts: list[str] = []

    def _restart_service_if_running(self, plugin_id: str) -> bool:
        self.restarts.append(plugin_id)
        return True


def _fake_threading(spawned: list[dict]) -> types.SimpleNamespace:
    class _Thread:
        def __init__(self, **kwargs):
            spawned.append(kwargs)

        def start(self) -> None:
            pass

    return types.SimpleNamespace(Thread=_Thread)


def test_arming_spawns_a_watcher_only_for_a_started_download(monkeypatch):
    spawned: list[dict] = []
    monkeypatch.setattr(ps, "threading", _fake_threading(spawned))
    handler = _Stub()

    handler._maybe_restart_service_after_download("websearch", {"started": True, "download_status_path": "p"})
    assert len(spawned) == 1
    assert spawned[0]["args"] == ("websearch", "p")

    # Nothing to watch: no download started, or no path declared.
    handler._maybe_restart_service_after_download("websearch", {"started": False, "download_status_path": "p"})
    handler._maybe_restart_service_after_download("websearch", {"started": True})
    handler._maybe_restart_service_after_download("websearch", "not-a-dict")
    assert len(spawned) == 1


def _run_watcher(tmp_path, monkeypatch, payload):
    status_path = tmp_path / "model_status.json"
    status_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(ps, "DOWNLOAD_WATCH_POLL_S", 0.0)
    monkeypatch.setattr(ps, "DOWNLOAD_WATCH_MAX_S", 0.2)
    handler = _Stub()
    handler._watch_download_then_restart("websearch", str(status_path))
    return handler


def test_watcher_restarts_the_service_when_ready(tmp_path, monkeypatch):
    handler = _run_watcher(tmp_path, monkeypatch, {"state": "ready"})
    assert handler.restarts == ["websearch"]


def test_watcher_gives_up_on_error(tmp_path, monkeypatch):
    handler = _run_watcher(tmp_path, monkeypatch, {"state": "error"})
    assert handler.restarts == []


def test_watcher_keeps_waiting_while_the_file_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "DOWNLOAD_WATCH_POLL_S", 0.0)
    monkeypatch.setattr(ps, "DOWNLOAD_WATCH_MAX_S", 0.2)
    handler = _Stub()
    handler._watch_download_then_restart("websearch", str(tmp_path / "absent.json"))
    assert handler.restarts == []


# ── restart only a service that is meant to be running ─────────────────────


class _FakePsp:
    def __init__(self, *, alive: bool, should_run: bool):
        self._alive = alive
        self.should_run = should_run
        self.port = 9001
        self.process = None
        self.stopped = 0
        self.started = 0

    def is_alive(self) -> bool:
        return self._alive

    def stop(self) -> bool:
        self.stopped += 1
        self._alive = False
        self.should_run = False
        return True

    def start(self) -> bool:
        self.started += 1
        self._alive = True
        return True

    def _resolve_port(self) -> int:
        return self.port + 1


@pytest.fixture
def fake_psp(monkeypatch):
    holder: dict[str, _FakePsp | None] = {"psp": None}
    monkeypatch.setattr(ps, "ensure_plugin_service_registered", lambda _pid: holder["psp"])
    return holder


def test_restart_skips_a_service_the_user_stopped(fake_psp):
    fake_psp["psp"] = _FakePsp(alive=False, should_run=False)
    # The real mixin method (not the recording stub): this pins the actual
    # stop-then-start path.
    assert ps.PluginServicesMixin()._restart_service_if_running("sensevoice") is False
    assert fake_psp["psp"].started == 0


def test_restart_restarts_an_alive_service(fake_psp):
    fake_psp["psp"] = _FakePsp(alive=True, should_run=True)
    assert ps.PluginServicesMixin()._restart_service_if_running("websearch") is True
    assert fake_psp["psp"].stopped == 1
    assert fake_psp["psp"].started == 1


def test_restart_revives_a_service_that_is_supposed_to_run(fake_psp):
    # Crash-looping toward a missing model: not alive, but should_run is set.
    fake_psp["psp"] = _FakePsp(alive=False, should_run=True)
    assert ps.PluginServicesMixin()._restart_service_if_running("websearch") is True
    assert fake_psp["psp"].started == 1
