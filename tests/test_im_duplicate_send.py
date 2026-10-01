"""im.send_message 的重复发送保护。

背景：模型曾把同一个 `im.send_message` 在同一个回合里发了两遍，同一条私信相隔
5 秒落库两次（工具折叠显示「发送消息 ×2」），用户看到两条一模一样的消息。
这里锁住「短窗口内同目标同正文只发一次」以及「带附件不做去重」两条边界。
"""

from __future__ import annotations

import pytest

from opensquad.tools import im


class _FakeBridge:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send_message(self, content, target_id, target_type="group", file_paths=None):
        self.calls.append(
            {"content": content, "target_id": target_id, "target_type": target_type, "file_paths": file_paths}
        )
        return True


@pytest.fixture()
def bridge(monkeypatch):
    fake = _FakeBridge()
    monkeypatch.setattr(im, "_bridge", lambda: fake)
    im._last_send_at.clear()
    return fake


def test_identical_dm_within_window_is_sent_once(bridge):
    first = im.send_message("你好！有什么我可以帮忙的吗？", "ss", "dm")
    second = im.send_message("你好！有什么我可以帮忙的吗？", "ss", "dm")

    assert first.get("duplicate") is not True
    assert second.get("duplicate") is True
    assert len(bridge.calls) == 1, bridge.calls


def test_different_content_or_target_still_sends(bridge):
    im.send_message("hi", "ss", "dm")
    im.send_message("hi", "Agent305", "dm")  # 另一个目标
    im.send_message("hello", "ss", "dm")  # 另一段正文

    assert len(bridge.calls) == 3, bridge.calls


def test_attachments_are_never_deduped():
    # 带附件的重复投递是有意行为（分片 / 多次投递），守卫必须放行。
    # 直接测守卫本身：`has_files=True` 永远不判重（真实路径里 `final_files`
    # 来自 prepare_file_for_sending，空列表等同于没有附件）。
    assert im._is_duplicate_send("dm", "ss", "file", True) is False
    assert im._is_duplicate_send("dm", "ss", "file", True) is False


def test_window_expiry_allows_a_later_identical_send(bridge, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(im.time, "monotonic", lambda: clock["t"])

    im.send_message("ping", "ss", "dm")
    clock["t"] += im._DUPLICATE_WINDOW_S + 1
    im.send_message("ping", "ss", "dm")

    assert len(bridge.calls) == 2, bridge.calls
