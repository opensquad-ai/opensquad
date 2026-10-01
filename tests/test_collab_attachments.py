"""Task-level attachments: board items, the summary, and the agent tool."""

from __future__ import annotations

import pytest

import opensquad.bridge as bridge_mod
from opensquad import collab_board as cb
from opensquad.tools import collaboration as collab


@pytest.fixture()
def board(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "_board_dir", lambda: str(tmp_path))
    return cb


def _cid(board) -> str:
    return board.create_task(task_name="发布流程", created_by="pm")["task_id"]


class _UploadBridge:
    """Stands in for the bridge: upload_file returns gateway-shaped entries."""

    token = "t"

    def __init__(self):
        self.uploaded: list[str] = []
        self.sent: list[dict] = []

    def upload_file(self, path):
        self.uploaded.append(path)
        name = path.replace("\\", "/").split("/")[-1]
        if name == "broken.bin":
            return None
        return {"url": f"/uploads/{name}", "name": name, "size": "1KB", "type": "file"}

    def list_groups_api(self):
        return [{"id": "g-default", "name": "default"}]

    def send_message(self, content, target_id=None, target_type="group", **kwargs):
        self.sent.append({"content": content, "target_id": target_id, "target_type": target_type})
        return True

    def last_sent_message_id(self):
        return "m_1"


@pytest.fixture()
def bridge(monkeypatch):
    fake = _UploadBridge()
    monkeypatch.setattr(bridge_mod, "bridge", fake)
    collab._TASK_CARD_LAST.clear()
    return fake


# --------------------------------------------------------------------------
# The board
# --------------------------------------------------------------------------
def test_attach_files_records_items_and_exposes_them(board):
    cid = _cid(board)
    res = cb.attach_files(
        collab_id=cid,
        agent_id="coder",
        files=[
            {"url": "/uploads/spec.pdf", "name": "spec.pdf", "size": "12KB", "type": "file"},
            {"url": "/uploads/shot.png", "name": "shot.png", "size": "30KB", "type": "image"},
            {"url": "/uploads/guess.gif", "name": "guess.gif", "size": "1KB"},  # kind inferred
            {"name": "no-url.txt"},  # dropped
        ],
        note="验收材料",
    )
    assert res["count"] == 3

    summary = cb.board_summary(collab_id=cid)
    assert len(summary["attachments"]) == 3
    assert len(summary["items"]["attachment"]) == 3
    spec = next(a for a in summary["attachments"] if a["name"] == "spec.pdf")
    assert spec["url"] == "/uploads/spec.pdf"
    assert spec["kind"] == "file"
    assert spec["uploader"] == "coder"
    assert spec["note"] == "验收材料"
    assert next(a for a in summary["attachments"] if a["name"] == "guess.gif")["kind"] == "image"


def test_attaching_the_same_url_twice_updates_one_item(board):
    cid = _cid(board)
    files = [{"url": "/uploads/spec.pdf", "name": "spec.pdf", "size": "12KB", "type": "file"}]
    cb.attach_files(collab_id=cid, agent_id="coder", files=files)
    cb.attach_files(collab_id=cid, agent_id="coder", files=files, note="第二版")
    summary = cb.board_summary(collab_id=cid)
    assert len(summary["attachments"]) == 1
    assert summary["attachments"][0]["note"] == "第二版"


def test_attach_files_requires_a_collab_id(board):
    with pytest.raises(ValueError):
        cb.attach_files(collab_id="", agent_id="coder", files=[])


# --------------------------------------------------------------------------
# The agent tool
# --------------------------------------------------------------------------
def test_attach_file_uploads_local_paths_and_records_urls(board, bridge):
    cid = _cid(board)

    res = collab.attach_file(
        collab_id=cid,
        file_paths=["/tmp/spec.pdf", "/tmp/broken.bin"],
        urls=[{"url": "/uploads/from-chat.png", "name": "from-chat.png", "type": "image"}],
        note="材料",
    )

    assert res["status"] == "success"
    assert res["attached"] == 2
    assert res["failed"] == ["/tmp/broken.bin"]
    assert bridge.uploaded == ["/tmp/spec.pdf", "/tmp/broken.bin"]
    names = {a["name"] for a in cb.board_summary(collab_id=cid)["attachments"]}
    assert names == {"spec.pdf", "from-chat.png"}


def test_attach_file_reports_errors(board, bridge):
    assert collab.attach_file(collab_id="")["status"] == "error"
    assert collab.attach_file(collab_id="NOPE", file_paths=["/tmp/a"])["status"] == "error"
    # nothing uploaded and no url handed over => a clear error, not a silent no-op
    res = collab.attach_file(collab_id=_cid(board), file_paths=["/tmp/broken.bin"])
    assert res["status"] == "error"
    assert res["failed"] == ["/tmp/broken.bin"]


# --------------------------------------------------------------------------
# Task chat with attachments
# --------------------------------------------------------------------------
def test_post_task_message_records_and_announces_attachments(board, bridge):
    cid = _cid(board)
    board.update_task(task_id=cid, extra={"group_id": "g-default"})

    res = collab.post_task_message(
        collab_id=cid,
        content="交付材料见附件",
        attachments=[{"url": "/uploads/spec.pdf", "name": "spec.pdf", "size": "12KB", "type": "file"}],
    )

    assert res["status"] == "success"
    assert res["attachments"] == ["spec.pdf"]
    attachments = cb.board_summary(collab_id=cid)["attachments"]
    assert [a["name"] for a in attachments] == ["spec.pdf"]
    # the announcement says files came with it
    assert "附件 1 个" in bridge.sent[0]["content"]


def test_post_task_message_without_attachments_is_unchanged(board, bridge):
    cid = _cid(board)
    board.update_task(task_id=cid, extra={"group_id": "g-default"})

    res = collab.post_task_message(collab_id=cid, content="只有文字")

    assert res["attachments"] == []
    assert cb.board_summary(collab_id=cid)["attachments"] == []
    assert "附件" not in bridge.sent[0]["content"]
