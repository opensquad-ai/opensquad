"""Generic window cards ([[WINDOW_CARD]]) — protocol + agent tool."""

from __future__ import annotations

import pytest

import opensquad.bridge as bridge_mod
from opensquad import window_cards as wc
from opensquad.tools import window_card as wc_tool


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
        return "msg_9"


@pytest.fixture()
def fake_bridge(monkeypatch):
    bridge = _FakeBridge()
    monkeypatch.setattr(bridge_mod, "bridge", bridge)
    return bridge


# --------------------------------------------------------------------------
# View normalization — the window renders whatever the sender described
# --------------------------------------------------------------------------
def test_view_kind_inferred_from_the_payload():
    assert wc.normalize_view({"rows": [["a"]], "columns": ["c"]})["kind"] == "table"
    assert wc.normalize_view({"steps": [{"title": "s"}]})["kind"] == "flow"
    assert wc.normalize_view({"items": [{"label": "l"}]})["kind"] == "metrics"
    assert wc.normalize_view({"blocks": [{"text": "t"}]})["kind"] == "sections"
    assert wc.normalize_view({})["kind"] == "raw"


def test_view_kind_aliases_and_string_form():
    assert wc.normalize_view("list")["kind"] == "sections"
    assert wc.normalize_view("steps")["kind"] == "flow"
    assert wc.normalize_view("weird-kind")["kind"] == "sections"
    assert wc.normalize_view({"kind": "markdown", "text": "hi"})["kind"] == "raw"


def test_view_normalization_shapes():
    view = wc.normalize_view(
        {
            "kind": "sections",
            "blocks": [
                {"title": "需求", "text": "要做审核", "items": ["a", "b"]},
                "plain string block",
            ],
        }
    )
    assert view["blocks"][0]["items"] == ["a", "b"]
    assert view["blocks"][1]["text"] == "plain string block"

    table = wc.normalize_view({"kind": "table", "columns": ["名称", "状态"], "rows": [{"名称": "A", "状态": "通过"}]})
    assert table["rows"] == [["A", "通过"]]

    flow = wc.normalize_view({"kind": "flow", "steps": ["确定需求", {"title": "讨论方案", "status": "doing"}]})
    assert flow["steps"][0] == {"title": "确定需求", "detail": "", "status": ""}
    assert flow["steps"][1]["status"] == "doing"


def test_action_normalization():
    actions = wc.normalize_actions(
        [
            "查看详情",
            {"label": "打开", "url": "https://example.com"},
            {"label": "跳到任务", "collab_id": "AB12CD"},
            {"label": "复制结论", "copy": "通过"},
            {"label": "无动作"},
            {"no_label": "x"},
        ]
    )
    assert [a["label"] for a in actions] == ["查看详情", "打开", "跳到任务", "复制结论", "无动作"]
    assert [a["intent"] for a in actions] == ["none", "open_url", "open_collab_task", "copy", "none"]
    assert actions[0]["id"] == "act_1"


# --------------------------------------------------------------------------
# Encode / parse
# --------------------------------------------------------------------------
def _payload(**overrides):
    kwargs = dict(
        title="自建应用发布申请 · 自动审核通过",
        summary="系统基于免审规则已自动审核通过。",
        view={"kind": "table", "columns": ["项", "结果"], "rows": [["申请人", "quanker"]]},
        actions=[{"label": "查看审核详情", "url": "https://example.com"}],
    )
    kwargs.update(overrides)
    return wc.build_window_card_payload(**kwargs)


def test_round_trip_and_readable_fallback():
    payload = _payload(recipient_name="aa")
    content = wc.encode_window_card_message(payload)
    assert content.startswith(wc.WINDOW_CARD_START)
    assert "自动审核通过" in content
    assert "查看审核详情" in content
    parsed = wc.parse_window_card_payload(content)
    assert parsed is not None
    assert parsed["id"] == payload["id"]
    assert parsed["view"]["kind"] == "table"
    assert parsed["actions"][0]["intent"] == "open_url"


def test_parse_rejects_unusable_content():
    assert wc.parse_window_card_payload("") is None
    assert wc.parse_window_card_payload("plain") is None
    assert wc.parse_window_card_payload(f"{wc.WINDOW_CARD_START}not json{wc.WINDOW_CARD_END}") is None
    no_title = f'{wc.WINDOW_CARD_START}{{"id":"w1","view":{{"kind":"raw"}}}}{wc.WINDOW_CARD_END}'
    assert wc.parse_window_card_payload(no_title) is None
    no_view = f'{wc.WINDOW_CARD_START}{{"id":"w1","title":"t"}}{wc.WINDOW_CARD_END}'
    assert wc.parse_window_card_payload(no_view) is None


def test_markers_do_not_collide_with_collab_cards():
    from opensquad import collab_approval as ca

    window = wc.encode_window_card_message(_payload())
    collab = ca.encode_collab_task_message(ca.build_collab_task_payload(collab_id="AB12CD", title="t"))
    assert ca.parse_collab_task_payload(window) is None
    assert wc.parse_window_card_payload(collab) is None
    assert wc.WINDOW_CARD_START not in wc.strip_window_card_marker(window)
    assert wc.strip_window_card_marker(collab) == collab


def test_patch_state_is_idempotent():
    content = wc.encode_window_card_message(_payload())
    once = wc.patch_window_card_state_in_content(content, "actioned", action_id="act_1")
    twice = wc.patch_window_card_state_in_content(once, "actioned", action_id="act_1")
    assert once == twice
    parsed = wc.parse_window_card_payload(twice)
    assert parsed["state"] == "actioned"
    assert parsed["last_action_id"] == "act_1"


# --------------------------------------------------------------------------
# Delivery — group and DM
# --------------------------------------------------------------------------
def test_post_to_group(fake_bridge):
    result = wc.post_window_card(_payload(), group_id="g-default")
    assert result["ok"] is True
    assert result["target_type"] == "group"
    assert result["target"] == "g-default"
    assert fake_bridge.sent[0]["target_type"] == "group"
    assert wc.parse_window_card_payload(fake_bridge.sent[0]["content"]) is not None


def test_post_to_dm(fake_bridge):
    result = wc.post_window_card(_payload(), recipient_name="aa")
    assert result["ok"] is True
    assert result["target_type"] == "dm"
    assert fake_bridge.sent[0] == {
        "content": fake_bridge.sent[0]["content"],
        "target_id": "aa",
        "target_type": "dm",
    }


def test_post_requires_a_target(fake_bridge):
    assert wc.post_window_card(_payload())["ok"] is False
    assert fake_bridge.sent == []


def test_post_without_bridge(monkeypatch):
    monkeypatch.setattr(bridge_mod, "bridge", None)
    assert wc.post_window_card(_payload(), group_id="g")["ok"] is False


# --------------------------------------------------------------------------
# Agent tool
# --------------------------------------------------------------------------
def test_tool_sends_to_group_and_dm(fake_bridge):
    group = wc_tool.send_window_card(title="审核通过", view={"kind": "raw", "text": "ok"}, group_id="g-default")
    assert group["status"] == "success"
    assert group["target_type"] == "group"

    dm = wc_tool.send_window_card(title="进度", view={"kind": "flow", "steps": [{"title": "x"}]}, recipient_name="aa")
    assert dm["status"] == "success"
    assert dm["target_type"] == "dm"

    sent_payload = wc.parse_window_card_payload(fake_bridge.sent[1]["content"])
    assert sent_payload["title"] == "进度"
    assert sent_payload["view"]["kind"] == "flow"
    assert sent_payload["target"]["recipient_name"] == "aa"


def test_tool_validates_arguments(fake_bridge):
    assert wc_tool.send_window_card(title="t", view={"kind": "raw"})["status"] == "error"
    assert wc_tool.send_window_card(title="  ", view={"kind": "raw"}, group_id="g")["status"] == "error"
    assert fake_bridge.sent == []


def test_tool_is_registered_for_agents():
    from opensquad import agents_boot

    assert agents_boot.TOOL_MODULES["window_card"] == "opensquad.tools.window_card"
    # the docstring is the schema the model reads — it must describe both targets
    doc = wc_tool.send_window_card.__doc__ or ""
    assert "group_id" in doc and "recipient_name" in doc
