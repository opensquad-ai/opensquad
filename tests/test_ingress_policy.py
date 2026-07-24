"""Unit tests for unified IngressPolicy."""

from __future__ import annotations

from pathlib import Path

from opensquad.ingress_policy import (
    classify,
    push_ingress,
    resolve_session_id,
    trigger_process_queue,
)
from opensquad.input_hub import InputHub
from opensquad.session_manager import SessionManager


def test_classify_kinds():
    assert classify("gateway", "feishu_group") == "external"
    assert classify("group:demo", "chatpro_group") == "external"
    assert classify("wake", "") == "external"
    assert classify("reminder", "external") == "external"
    assert classify("gateway", "web") == "web"
    assert classify("system", "") == "system"


def test_resolve_session_id_external_uses_primary(tmp_path: Path):
    save = tmp_path / "sessions"
    hist = tmp_path / "history"
    save.mkdir()
    hist.mkdir()
    sm = SessionManager(save_dir=str(save), history_dir=str(hist))
    primary = sm.get_current_session_id()
    sm.start_new_session()
    focused = sm.get_current_session_id()
    assert primary != focused
    assert sm.get_primary_session_id() == primary

    sid = resolve_session_id(source="gateway", channel="telegram", session_id=focused, sm=sm)
    assert sid == primary

    web_sid = resolve_session_id(source="gateway", channel="web", session_id=focused, sm=sm)
    assert web_sid == focused

    web_empty = resolve_session_id(source="gateway", channel="web", session_id="", sm=sm)
    assert web_empty == focused


def test_trigger_process_queue_pushes_primary(tmp_path: Path, monkeypatch):
    save = tmp_path / "sessions"
    hist = tmp_path / "history"
    save.mkdir()
    hist.mkdir()
    sm = SessionManager(save_dir=str(save), history_dir=str(hist))
    primary = sm.get_current_session_id()
    sm.start_new_session()

    hub = InputHub()
    monkeypatch.setattr("opensquad.input_hub.get_input_hub", lambda: hub)
    monkeypatch.setattr(
        "opensquad.ingress_policy.resolve_primary_session_id",
        lambda _sm=None: primary,
    )

    sid = trigger_process_queue(source="group:demo", channel="chatpro_group")
    assert sid == primary
    popped = hub._try_pop_any()
    assert popped is not None
    got_sid, item = popped
    assert got_sid == primary
    assert item["content"] == "__PROCESS_QUEUE__"
    assert item["channel"] == "chatpro_group"


def test_push_ingress_reminder_is_external(tmp_path: Path, monkeypatch):
    save = tmp_path / "sessions"
    hist = tmp_path / "history"
    save.mkdir()
    hist.mkdir()
    sm = SessionManager(save_dir=str(save), history_dir=str(hist))
    primary = sm.get_current_session_id()
    sm.start_new_session()

    hub = InputHub()
    monkeypatch.setattr("opensquad.input_hub.get_input_hub", lambda: hub)
    monkeypatch.setattr(
        "opensquad.ingress_policy.resolve_primary_session_id",
        lambda _sm=None: primary,
    )

    sid = push_ingress("[Reminder] hi", source="reminder", channel="external")
    assert sid == primary
    popped = hub._try_pop_any()
    assert popped is not None
    assert popped[0] == primary
    assert popped[1]["source"] == "reminder"


def test_session_parallel_reexports():
    from opensquad import session_parallel as sp

    assert sp.is_external_channel("chatpro_group")
    assert sp.is_external_ingress("wake", "")
    assert callable(sp.resolve_primary_session_id)
    assert "chatpro_dm" in sp.EXTERNAL_CHANNELS
