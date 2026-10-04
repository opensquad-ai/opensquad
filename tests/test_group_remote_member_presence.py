"""An agent on a paired machine is not offline — it is somewhere else.

Its local status on this gateway is always "offline" because it holds no socket here; its messages
arrive over the relay. The member row read exactly that status, so a working agent looked absent.
The registration-time origin record only catches one registration path, which is why a remote agent
could still fall through to "Offline". A relay subscription is this machine's own runtime record,
and it names the member's user id on this gateway, so the match is exact.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_BACKEND_DIR = Path(__file__).resolve().parents[1] / "src" / "opensquad" / "gateway" / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from opensquad import relay_link  # noqa: E402
from opensquad.gateway.backend.app import api as gw_api  # noqa: E402


def _user(user_id: str = "u-pm", name: str = "pm") -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        name=name,
        email="agent-pm@local",
        avatar="",
        status=SimpleNamespace(value="offline"),
    )


def test_a_subscription_names_the_member_and_where_it_lives(monkeypatch):
    monkeypatch.setattr(
        relay_link,
        "subscribers",
        lambda group_id: [
            {"user_id": "u-pm", "host": "192.168.5.2", "agent_id": "pm", "callback_url": "http://192.168.5.2:9555"},
            {"user_id": "", "host": "nowhere"},
        ],
    )

    assert gw_api._relay_member_origins("G") == {"u-pm": "192.168.5.2"}


def test_a_subscription_without_a_host_still_places_the_member_elsewhere(monkeypatch):
    monkeypatch.setattr(relay_link, "subscribers", lambda group_id: [{"user_id": "u-pm"}] * 1)

    assert gw_api._relay_member_origins("G") == {"u-pm": ""}


def test_an_unreadable_subscription_store_never_breaks_the_member_list(monkeypatch):
    def _boom(group_id):
        raise RuntimeError("relay store is unreadable")

    monkeypatch.setattr(relay_link, "subscribers", _boom)

    assert gw_api._relay_member_origins("G") == {}


def test_a_member_found_in_the_subscriptions_is_remote_even_while_offline(monkeypatch):
    monkeypatch.setattr(gw_api, "_ensure_agent_user_avatar", lambda user: "")

    info = gw_api._member_info(_user(), "offline", remote_label="192.168.5.2")

    assert info.is_remote is True
    assert info.remote_label == "192.168.5.2"
    assert info.status == "offline", "its local status is still what it is"


def test_the_registration_record_still_wins_when_both_are_present(monkeypatch):
    monkeypatch.setattr(gw_api, "_ensure_agent_user_avatar", lambda user: "")
    monkeypatch.setattr(gw_api, "_remote_label_for", lambda user: "peer-from-record")

    info = gw_api._member_info(_user(), "offline", remote_label="192.168.5.2")

    assert info.remote_label == "peer-from-record"


def test_a_local_member_is_untouched(monkeypatch):
    monkeypatch.setattr(gw_api, "_ensure_agent_user_avatar", lambda user: "")
    monkeypatch.setattr(gw_api, "_remote_label_for", lambda user: None)

    info = gw_api._member_info(_user(), "online")

    assert info.is_remote is False
    assert info.remote_label is None


def test_the_group_endpoint_consults_the_subscriptions():
    src = (Path(__file__).resolve().parents[1] / "src/opensquad/gateway/backend/app/api.py").read_text(encoding="utf-8")

    start = src.index("async def get_group(")
    body = src[start : src.index("async def update_group(", start)]

    assert "relay_origins = _relay_member_origins(group_id)" in body
    assert "remote_label=relay_origins.get(member.id) or None" in body
