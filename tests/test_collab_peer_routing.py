"""Collaboration notices and task chat go to the group's owning gateway.

Pairing a second machine no longer repoints the agent's chat bridge, so a group
joined there is not "remote" by the old test. These cover the follow-through: the
invitation, the assignment notice and the approval card are posted where the group
lives, and a task's attachments are uploaded to the machine that serves its window.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "src"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import opensquad.bridge as bridge_mod  # noqa: E402
import opensquad.collab_board as cb  # noqa: E402
import opensquad.peer_bridge as peer_bridge_mod  # noqa: E402
from opensquad.tools import collaboration as collab  # noqa: E402

_CARD = """---
name: software_dev_team
description: a small team
suggested_roles: [pm, coder]
---

# Software Dev Team

Work together.
"""


class _Recorder:
    """A stand-in bridge that records what was sent and uploaded, and where."""

    def __init__(self, label: str):
        self.label = label
        self.token = "tok"
        self.base_url = f"http://{label}"
        self.sent: list[dict] = []
        self.uploaded: list[str] = []

    def list_groups_api(self):
        return [{"id": "g-7f3a", "name": "远端群"}]

    def send_message(self, content, target_id, target_type="group", file_paths=None, **kwargs):
        self.sent.append({"content": content, "target_id": target_id, "target_type": target_type})
        return True

    def last_sent_message_id(self):
        return "m1"

    def upload_file(self, path):
        self.uploaded.append(str(path))
        name = Path(path).name
        return {"url": f"/uploads/{name}", "name": name, "size": "1KB", "type": "file"}

    def get_group_detail_api(self, group_id):
        return {"members": []}


@pytest.fixture()
def agent(tmp_path, monkeypatch):
    """An agent whose board writes go to a temp dir, with a loadable collab card."""
    import opensquad.input_hub as input_hub_mod
    import opensquad.skill_loader as skill_loader
    from opensquad.input_hub import input_hub

    agent_dir = tmp_path / "agents" / "pm"
    agent_dir.mkdir(parents=True)
    cards = tmp_path / "collab_cards"
    cards.mkdir()
    (cards / "software_dev_team.md").write_text(_CARD, encoding="utf-8")
    monkeypatch.setattr(input_hub, "agent_dir", str(agent_dir))
    monkeypatch.setattr(input_hub_mod.input_hub, "agent_dir", str(agent_dir))
    monkeypatch.setattr(cb, "_board_dir", lambda: str(tmp_path / "board"))
    monkeypatch.setattr(cb, "_board_owners_file", lambda: str(tmp_path / "board" / "board_owners.json"))
    monkeypatch.setattr(collab, "_collab_cards_dir", lambda: str(cards))
    # The card is a skill; loading it needs the skill runtime, which these routing
    # tests do not exercise. Stub it so start_collaboration reaches the send.
    monkeypatch.setattr(skill_loader, "add_skill_from_file", lambda *a, **k: {"success": True})
    monkeypatch.setattr(skill_loader, "get_loaded_skills", lambda: [])
    peer_bridge_mod._BRIDGES.clear()
    return agent_dir


def _peer_and_home(monkeypatch):
    home = _Recorder("home")
    peer = _Recorder("192.168.5.4")
    monkeypatch.setattr(bridge_mod, "bridge", home)
    monkeypatch.setattr(peer_bridge_mod, "peer_bridge", lambda host: (peer, ""))
    monkeypatch.setattr(cb, "board_owner", lambda **kwargs: "192.168.5.4")
    return home, peer


def test_the_invitation_is_posted_to_the_group_on_the_peer(agent, monkeypatch):
    """PM on this machine, group on a paired one: the invite must land there."""
    home, peer = _peer_and_home(monkeypatch)

    res = collab.start_collaboration(
        card="software_dev_team", members=["coder"], group_id="g-7f3a", project_name="跨机项目"
    )

    assert peer.sent, "the invitation should have gone through the peer bridge"
    assert not home.sent, "nothing should be posted to this machine's gateway"
    assert "g-7f3a" in str(res.get("invitation"))


def test_the_approval_card_is_posted_to_the_group_on_the_peer(agent, monkeypatch):
    home, peer = _peer_and_home(monkeypatch)
    rec = cb.create_task(task_name="跨机项目", created_by="pm", group_id="g-7f3a")

    res = collab.request_step_approval(collab_id=rec["task_id"], step="plan", title="需要确认", summary="继续吗")

    assert res.get("status") == "pending"  # the card is posted; the user has yet to decide
    assert peer.sent and not home.sent
    assert any(s["target_id"] == "g-7f3a" for s in peer.sent)


def test_a_task_attachment_uploads_to_the_machine_that_serves_the_window(agent, monkeypatch):
    home, peer = _peer_and_home(monkeypatch)
    # Keep the board write local so we can inspect it; the upload is what routes.
    monkeypatch.setattr(cb, "board_base_url", lambda *a, **k: "")
    rec = cb.create_task(task_name="跨机项目", created_by="pm", group_id="g-7f3a")
    f = Path(agent) / "spec.txt"
    f.write_text("hello", encoding="utf-8")

    res = collab.attach_file(collab_id=rec["task_id"], file_paths=[str(f)], note="验收")

    assert res.get("status") == "success"
    assert peer.uploaded == [str(f)], "the file must be uploaded to the owning machine"
    items = cb.local_call("list_items", collab_id=rec["task_id"])
    urls = [str((i.get("extra") or {}).get("url") or "") for i in items]
    assert any("spec.txt" in u for u in urls)


def test_a_local_group_still_posts_to_the_home_bridge(agent, monkeypatch):
    """No recorded owner -> home. This is the single-machine path, unchanged."""
    home = _Recorder("home")
    monkeypatch.setattr(bridge_mod, "bridge", home)
    monkeypatch.setattr(cb, "board_owner", lambda **kwargs: "")

    collab.start_collaboration(card="software_dev_team", members=["coder"], group_id="g-home")

    assert home.sent, "a local group's invitation stays on this machine"
