"""post_task_message — the task-scoped chat channel (board + group card)."""

from __future__ import annotations

import pytest

import opensquad.bridge as bridge_mod
from opensquad import collab_approval as ca
from opensquad import collab_board as cb
from opensquad.tools import collaboration as collab


class _FakeBridge:
    token = "test-token"

    def __init__(self):
        self.sent: list[dict] = []

    def list_groups_api(self):
        return [{"id": "g-default", "name": "default"}]

    def send_message(self, content, target_id=None, target_type="group", **kwargs):
        self.sent.append({"content": content, "target_id": target_id, "target_type": target_type})
        return True

    def last_sent_message_id(self):
        return "msg_1"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "_board_dir", lambda: str(tmp_path))
    fake = _FakeBridge()
    monkeypatch.setattr(bridge_mod, "bridge", fake)
    collab._TASK_CARD_LAST.clear()
    cid = cb.create_task(task_name="发布流程", created_by="pm")["task_id"]
    cb.update_task(task_id=cid, extra={"group_id": "g-default"})
    return {"cid": cid, "bridge": fake}


def test_records_on_board_and_announces_card(env):
    res = collab.post_task_message(collab_id=env["cid"], content="我开始做审核流程了")
    assert res["status"] == "success"
    assert res["announced"] is True

    items = [i for i in cb.list_items(collab_id=env["cid"]) if i["item_type"] == "discussion"]
    assert len(items) == 1
    assert items[0]["content"] == "我开始做审核流程了"

    assert len(env["bridge"].sent) == 1
    sent = env["bridge"].sent[0]
    assert sent["target_id"] == "g-default"
    assert sent["target_type"] == "group"
    payload = ca.parse_collab_task_payload(sent["content"])
    assert payload is not None
    assert payload["collab_id"] == env["cid"]
    assert payload["kind"] == "discussion"
    # the readable fallback stays in the message
    assert "Task ID: " in sent["content"]


def test_second_announcement_is_throttled_but_board_keeps_all(env):
    first = collab.post_task_message(collab_id=env["cid"], content="第一句")
    second = collab.post_task_message(collab_id=env["cid"], content="第二句")
    assert first["announced"] is True
    assert second["announced"] is False
    assert second["card"]["throttled"] is True
    assert len(env["bridge"].sent) == 1
    items = [i for i in cb.list_items(collab_id=env["cid"]) if i["item_type"] == "discussion"]
    assert {i["content"] for i in items} == {"第一句", "第二句"}


def test_progress_kind_announces_separately(env):
    collab.post_task_message(collab_id=env["cid"], content="讨论")
    res = collab.post_task_message(collab_id=env["cid"], content="进度 50%", kind="progress")
    assert res["announced"] is True
    assert ca.parse_collab_task_payload(env["bridge"].sent[1]["content"])["kind"] == "progress"


def test_task_without_group_records_only(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "_board_dir", lambda: str(tmp_path))
    fake = _FakeBridge()
    monkeypatch.setattr(bridge_mod, "bridge", fake)
    collab._TASK_CARD_LAST.clear()
    cid = cb.create_task(task_name="无群任务", created_by="pm")["task_id"]

    res = collab.post_task_message(collab_id=cid, content="只有板上记录")
    assert res["status"] == "success"
    assert res["announced"] is False
    assert fake.sent == []
    assert len(cb.list_items(collab_id=cid)) == 1


def test_errors(env):
    assert collab.post_task_message(collab_id=env["cid"], content="   ")["status"] == "error"
    assert collab.post_task_message(collab_id="", content="hi")["status"] == "error"
    unknown = collab.post_task_message(collab_id="NOPE", content="hi")
    assert unknown["status"] == "error"
    assert env["bridge"].sent == []


def test_card_carries_participants(env):
    cb.mark_participant(collab_id=env["cid"], agent_id="qa", state="invited", name="QA")
    collab.post_task_message(collab_id=env["cid"], content="大家看下")
    payload = ca.parse_collab_task_payload(env["bridge"].sent[0]["content"])
    assert payload["participants"] == [{"agent_id": "qa", "name": "QA", "state": "invited"}]
