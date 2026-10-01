"""post_task_message — the task's own thread (board only, never the group)."""

from __future__ import annotations

import pytest

import opensquad.bridge as bridge_mod
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
    cid = cb.create_task(task_name="发布流程", created_by="pm")["task_id"]
    cb.update_task(task_id=cid, extra={"group_id": "g-default"})
    return {"cid": cid, "bridge": fake}


def test_records_on_the_board_and_never_touches_the_group(env):
    """A task behaves like a temporary group: the group chat stays readable."""
    res = collab.post_task_message(collab_id=env["cid"], content="我开始做审核流程了")
    assert res["status"] == "success"
    assert res["thread"] == "task-window"

    items = [i for i in cb.list_items(collab_id=env["cid"]) if i["item_type"] == "discussion"]
    assert len(items) == 1
    assert items[0]["content"] == "我开始做审核流程了"
    # nothing was posted into the group, even though the task has one bound
    assert env["bridge"].sent == []


def test_every_message_is_kept_even_when_rapid(env):
    """No throttling any more: the board is the thread, so nothing is dropped."""
    collab.post_task_message(collab_id=env["cid"], content="第一句")
    collab.post_task_message(collab_id=env["cid"], content="第二句")
    collab.post_task_message(collab_id=env["cid"], content="进度 50%", kind="progress")
    items = [i for i in cb.list_items(collab_id=env["cid"]) if i["item_type"] == "discussion"]
    assert {i["content"] for i in items} == {"第一句", "第二句", "进度 50%"}
    assert env["bridge"].sent == []


def test_message_carries_attachments_onto_the_task(env):
    res = collab.post_task_message(
        collab_id=env["cid"],
        content="交付材料见附件",
        attachments=[{"url": "/uploads/spec.pdf", "name": "spec.pdf", "size": "12KB", "type": "file"}],
    )
    assert res["status"] == "success"
    assert res["attachments"] == ["spec.pdf"]
    summary = cb.board_summary(collab_id=env["cid"])
    assert [a["name"] for a in summary["attachments"]] == ["spec.pdf"]
    assert env["bridge"].sent == []


def test_task_without_group_takes_the_same_path(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "_board_dir", lambda: str(tmp_path))
    fake = _FakeBridge()
    monkeypatch.setattr(bridge_mod, "bridge", fake)
    cid = cb.create_task(task_name="无群任务", created_by="pm")["task_id"]

    res = collab.post_task_message(collab_id=cid, content="只有板上记录")
    assert res["status"] == "success"
    assert fake.sent == []
    assert len(cb.list_items(collab_id=cid)) == 1


def test_errors(env):
    assert collab.post_task_message(collab_id=env["cid"], content="   ")["status"] == "error"
    assert collab.post_task_message(collab_id="", content="hi")["status"] == "error"
    assert collab.post_task_message(collab_id="NOPE", content="hi")["status"] == "error"
    assert env["bridge"].sent == []
