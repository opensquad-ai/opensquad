"""im.send_message with a task id routes into the task thread, not the group.

`collab_id` is the single switch that separates "talk to the group" from "talk
inside this task": the group chat is never touched for task talk, and the task
board keeps every message (no duplicate-send guard on that path).
"""

from __future__ import annotations

import pytest

import opensquad.bridge as bridge_mod
from opensquad import collab_board as cb
from opensquad.tools import im


class _FakeBridge:
    token = "test-token"

    def __init__(self):
        self.sent: list[dict] = []
        self.uploaded: list[str] = []
        self._group_cache = {"g-default": {"id": "g-default", "name": "default"}}

    def list_groups_api(self):
        return [{"id": "g-default", "name": "default"}]

    def send_message(self, content, target_id=None, target_type="group", **kwargs):
        self.sent.append({"content": content, "target_id": target_id, "target_type": target_type})
        return True

    def last_sent_message_id(self):
        return "msg_1"

    def upload_file(self, path):
        self.uploaded.append(path)
        name = path.replace("\\", "/").split("/")[-1]
        return {"url": f"/uploads/{name}", "name": name, "size": "1KB", "type": "file"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "_board_dir", lambda: str(tmp_path))
    fake = _FakeBridge()
    monkeypatch.setattr(bridge_mod, "bridge", fake)
    cid = cb.create_task(task_name="发布流程", created_by="pm")["task_id"]
    cb.update_task(task_id=cid, extra={"group_id": "g-default"})
    return {"cid": cid, "bridge": fake}


def _discussions(cid):
    return [i for i in cb.list_items(collab_id=cid) if i["item_type"] == "discussion"]


def test_task_id_sends_into_the_task_thread_only(env):
    res = im.send_message("开始做审核流程了", collab_id=env["cid"])
    assert res["status"] == "success"
    assert res["thread"] == "task-window"
    assert res["collab_id"] == env["cid"]
    assert [i["content"] for i in _discussions(env["cid"])] == ["开始做审核流程了"]
    # the group was not touched, even though the task has a group bound
    assert env["bridge"].sent == []


def test_task_messages_are_not_deduplicated(env):
    im.send_message("收到", collab_id=env["cid"])
    im.send_message("收到", collab_id=env["cid"])
    assert [i["content"] for i in _discussions(env["cid"])] == ["收到", "收到"]
    assert env["bridge"].sent == []


def test_files_go_to_the_task_as_attachments(env):
    res = im.send_message("交付材料", collab_id=env["cid"], file_paths=["/tmp/spec.pdf"])
    # an upload that fails must say so rather than vanish
    assert res.get("attachment_error", "") == ""
    assert res["status"] == "success"
    assert res["attachments"] == ["spec.pdf"]
    assert env["bridge"].uploaded == ["/tmp/spec.pdf"]
    assert env["bridge"].sent == []
    assert [a["name"] for a in cb.board_summary(collab_id=env["cid"])["attachments"]] == ["spec.pdf"]


def test_unknown_task_reports_the_error(env):
    res = im.send_message("hi", collab_id="NOPE")
    assert res["status"] == "error"
    assert env["bridge"].sent == []


def test_no_task_id_still_goes_to_the_group(env):
    res = im.send_message("大家早", target_id="g-default", target_type="group")
    assert res.get("status") == "success", res
    assert len(env["bridge"].sent) == 1
    assert env["bridge"].sent[0]["target_id"] == "g-default"
    # and nothing was written to a task board
    assert _discussions(env["cid"]) == []
