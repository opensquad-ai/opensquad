"""A custom agent avatar must reach every surface that renders it, at once.

The avatar is just a URL string, so the launcher's only job is where it lands:
``data/profile.json``, which ``GET /api/agents`` reads. Two things therefore
have to hold, and neither is visible from the gateway side that owns the upload:

* the file is written on the canonical *and* the legacy path, because
  ``read_agent_profile_file`` accepts either and the agent rewrites both on boot;
* the list cache is dropped, because ``_handle_list_agents`` memoises its result
  for a few seconds — without the invalidation the avatar the user just picked
  would take up to the TTL to show up in the very card they clicked.

An upload is an ``/uploads/...`` path owned by the gateway; the launcher never
sees the bytes. Reset from the UI is the same call with an empty string.
"""

from __future__ import annotations

import json
import threading
from urllib.error import HTTPError

import pytest

import opensquad.launcher.management_api._agents as agents_mod
import opensquad.launcher.management_api._sessions as sessions_mod
from opensquad.launcher.management_api import ExclusiveHTTPServer, ManagementHandler
from opensquad.utils.local_http import open_local

AGENT_NAME = "avatar_probe_agent"
CUSTOM_AVATAR = "/uploads/agent-avatars/avatar-probe-agent/deadbeef1234.png"


@pytest.fixture
def _no_launcher_token(monkeypatch):
    """Remove any ambient launcher token so the requests below are unauthenticated."""
    monkeypatch.setattr(ManagementHandler, "_get_launcher_token", staticmethod(lambda: ""))


@pytest.fixture
def agents_dir(tmp_path, monkeypatch):
    """An isolated agents root holding exactly one agent.

    Both mixins bind ``AGENTS_DIR`` by value at import time, so both have to be
    repointed — otherwise the list endpoint keeps scanning the real workspace
    and the assertions below would pass against the wrong file.
    """
    agent_dir = tmp_path / AGENT_NAME
    (agent_dir / "data").mkdir(parents=True)
    (agent_dir / "config.json").write_text(
        json.dumps({"agent_id": "avatar-probe-001", "agent_name": "Probe Agent"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(agents_mod, "AGENTS_DIR", str(tmp_path))
    monkeypatch.setattr(sessions_mod, "AGENTS_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _fresh_list_cache():
    """``/api/agents`` memoises its payload in module globals; clear it so each
    test starts from a cold cache and cannot inherit another test's answer."""
    agents_mod._agents_list_cache_result = None
    agents_mod._agents_list_cache_at = 0.0
    yield


@pytest.fixture
def live_server():
    """A real ``ExclusiveHTTPServer`` on an ephemeral port, serving in a thread."""
    server = ExclusiveHTTPServer(("127.0.0.1", 0), ManagementHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _put_profile(port: int, avatar: str) -> dict:
    with open_local(
        f"http://127.0.0.1:{port}/api/agents/{AGENT_NAME}/profile",
        timeout=5,
        method="PUT",
        data=json.dumps({"avatar": avatar}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    ) as resp:
        assert resp.status == 200
        return json.loads(resp.read().decode("utf-8"))


def _list_agents(port: int) -> dict:
    with open_local(f"http://127.0.0.1:{port}/api/agents", timeout=5) as resp:
        assert resp.status == 200
        return json.loads(resp.read().decode("utf-8"))


def _data_dir(agents_dir):
    return agents_dir / AGENT_NAME / "data"


def _profile(agents_dir, name: str = "profile.json") -> dict:
    return json.loads((_data_dir(agents_dir) / name).read_text("utf-8"))


def _chat_profile(port: int) -> dict | None:
    for agent in _list_agents(port)["agents"]:
        if agent.get("dir_name") == AGENT_NAME or agent.get("agent_name") == AGENT_NAME:
            return agent.get("chat_profile")
    raise AssertionError("the agent under test did not appear in /api/agents")


def test_uploaded_avatar_is_visible_in_the_agent_list_without_waiting(live_server, agents_dir, _no_launcher_token):
    """The card the user just clicked must show the new picture on the spot.

    ``/api/agents`` is the single feed for the Agent Workstation, the sidebar and
    the nav shortcuts, and it is TTL-cached; priming it first is what makes this
    test able to fail if the invalidation is dropped.
    """
    assert _chat_profile(live_server) is None, "precondition: no profile yet"

    _put_profile(live_server, CUSTOM_AVATAR)

    assert _chat_profile(live_server)["chat_user_avatar"] == CUSTOM_AVATAR


def test_both_profile_paths_are_written_with_the_same_avatar(live_server, agents_dir, _no_launcher_token):
    """Canonical ``data/profile.json`` and the legacy copy must agree.

    ``read_agent_profile_file`` reads them in that order, so a stale legacy file
    would be picked up whenever the canonical one is missing (a fresh checkout,
    a hand-copied agent dir) and the avatar would silently revert.
    """
    _put_profile(live_server, CUSTOM_AVATAR)

    canonical = json.loads((agents_dir / AGENT_NAME / "data" / "profile.json").read_text("utf-8"))
    legacy = json.loads((agents_dir / AGENT_NAME / "data" / "group_chat" / "profile.json").read_text("utf-8"))
    assert canonical["avatar"] == CUSTOM_AVATAR
    assert legacy == canonical


def test_display_name_is_preserved_and_only_invented_when_missing(live_server, agents_dir, _no_launcher_token):
    """A rename happens in the chat UI — this endpoint must not undo it.

    With no profile on disk the name falls back to ``config.agent_name``, the
    same value the agent writes on boot, so the group chat does not end up with
    an unnamed member because someone picked an avatar first.
    """
    (_data_dir(agents_dir)).mkdir(parents=True, exist_ok=True)
    (_data_dir(agents_dir) / "profile.json").write_text(
        json.dumps({"name": "Renamed In Chat", "avatar": "data:image/png;base64,AAAA"}), encoding="utf-8"
    )

    _put_profile(live_server, CUSTOM_AVATAR)
    assert _profile(agents_dir)["name"] == "Renamed In Chat"

    # Both copies go: the handler reads the legacy one when the canonical file
    # is absent, so leaving it behind would test a different branch.
    (_data_dir(agents_dir) / "profile.json").unlink()
    (_data_dir(agents_dir) / "group_chat" / "profile.json").unlink()
    _put_profile(live_server, CUSTOM_AVATAR)
    assert _profile(agents_dir)["name"] == "Probe Agent"


def test_reset_clears_the_avatar_back_to_the_default(live_server, agents_dir, _no_launcher_token):
    """Reset is the same write with an empty value — never a deleted key.

    A dropped key would leave the legacy file describing the *previous* avatar,
    and the next read would bring the old picture back.
    """
    _put_profile(live_server, CUSTOM_AVATAR)
    _put_profile(live_server, "")

    assert _profile(agents_dir)["avatar"] == ""
    assert _chat_profile(live_server)["chat_user_avatar"] is None


def test_missing_avatar_field_is_rejected(live_server, agents_dir, _no_launcher_token):
    """An absent key is not the same as an empty one: reject instead of guessing."""
    with pytest.raises(HTTPError) as exc:
        open_local(
            f"http://127.0.0.1:{live_server}/api/agents/{AGENT_NAME}/profile",
            timeout=5,
            method="PUT",
            data=json.dumps({"name": "whatever"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
    assert exc.value.code == 400
    assert not (_data_dir(agents_dir) / "profile.json").exists()


@pytest.mark.parametrize("bad_name", ["..", ".", "a/b", "a\\b", ""])
def test_traversal_names_are_refused_before_touching_the_filesystem(monkeypatch, tmp_path, bad_name):
    """``..`` is a directory that exists, and ``_resolve_agent_dir_name`` would
    happily return it — so the guard is a negative test, not a nicety.

    Called on the handler directly: urllib normalises ``..`` out of a URL before
    it is ever sent, so an HTTP-level test could not reach the branch.
    """
    sent: list[tuple[dict, int]] = []

    class _Stub:
        _send_json = staticmethod(lambda payload, code=200: sent.append((payload, code)))
        _resolve_agent_dir_name = staticmethod(lambda name: name)

    monkeypatch.setattr(agents_mod, "AGENTS_DIR", str(tmp_path))
    ManagementHandler._handle_put_agent_profile(_Stub(), bad_name, {"avatar": CUSTOM_AVATAR})

    assert sent, "the handler answered at all"
    assert sent[0][1] == 400, f"{bad_name!r} was not refused: {sent[0]}"
    assert not list(tmp_path.rglob("profile.json")), "the guard ran after a write"
