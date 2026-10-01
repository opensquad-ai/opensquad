"""Generic window cards ([[WINDOW_CARD]]) — protocol + agent tool."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

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


# --------------------------------------------------------------------------
# Interactive forms + answers
# --------------------------------------------------------------------------
def test_form_normalization():
    form = wc.normalize_form(
        {
            "fields": [
                {
                    "id": "decision",
                    "label": "是否通过",
                    "type": "radio",
                    "required": True,
                    "options": [{"id": "yes", "label": "通过"}, "no"],
                },
                {"id": "note", "label": "备注", "type": "textarea"},
                {"id": "weird", "type": "nonsense"},
                {"label": "no id — dropped"},
            ]
        }
    )
    assert form is not None
    assert form["submit_label"] == "提交"
    assert [f["id"] for f in form["fields"]] == ["decision", "note", "weird"]
    assert form["fields"][0]["options"] == [{"id": "yes", "label": "通过"}, {"id": "no", "label": "no"}]
    assert form["fields"][0]["required"] is True
    assert form["fields"][2]["type"] == "text"
    assert wc.normalize_form({"fields": []}) is None
    assert wc.normalize_form("nope") is None


def test_payload_carries_the_form():
    payload = wc.build_window_card_payload(
        title="审批",
        view={"kind": "raw", "text": "看下面"},
        form={"fields": [{"id": "ok", "type": "checkbox"}]},
    )
    assert payload["view"]["form"]["fields"][0]["id"] == "ok"
    parsed = wc.parse_window_card_payload(wc.encode_window_card_message(payload))
    assert parsed["view"]["form"]["fields"][0]["id"] == "ok"


def test_patch_response_records_the_answer():
    content = wc.encode_window_card_message(_payload())
    updated = wc.patch_window_card_response_in_content(
        content, action_id="submit", values={"decision": "yes", "note": "lgtm"}, by="aa"
    )
    parsed = wc.parse_window_card_payload(updated)
    assert parsed["state"] == "answered"
    assert parsed["last_action_id"] == "submit"
    assert parsed["response"]["values"] == {"decision": "yes", "note": "lgtm"}
    assert parsed["response"]["by"] == "aa"
    assert parsed["response"]["at"]
    # the readable text survives the rewrite
    assert "自动审核通过" in updated


def test_action_intents_include_respond_confirm_decline():
    actions = wc.normalize_actions(
        [
            {"label": "确定", "intent": "confirm"},
            {"label": "驳回", "intent": "decline"},
            {"label": "回复", "intent": "respond"},
        ]
    )
    assert [a["intent"] for a in actions] == ["confirm", "decline", "respond"]


# --------------------------------------------------------------------------
# Respond endpoint (group card and DM card)
# --------------------------------------------------------------------------
class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value or []


class _DB:
    def __init__(self, results):
        self._results = list(results)
        self.commits = 0

    async def execute(self, *args, **kwargs):
        return _Result(self._results.pop(0))

    async def commit(self):
        self.commits += 1


class _NudgeSpy:
    """Stands in for registry.send_to_agent (the module attribute is the instance)."""

    def __init__(self):
        self.sent: list = []

    async def __call__(self, agent_id, payload):
        self.sent.append((agent_id, payload))
        return True


def _patch_registry(monkeypatch) -> _NudgeSpy:
    from app.ai_web.registry import registry as agent_registry

    spy = _NudgeSpy()
    monkeypatch.setattr(agent_registry, "send_to_agent", spy)
    return spy


def _card_message_for(content: str) -> SimpleNamespace:
    return SimpleNamespace(id="m_card", group_id="g-default", content=content, is_edited=False)


def _card_content() -> str:
    payload = wc.build_window_card_payload(
        title="审批卡",
        view={"kind": "raw", "text": "内容"},
        sender_id="Agent305",
        sender_name="Agent305",
        form={"fields": [{"id": "decision", "type": "radio", "options": ["yes", "no"]}]},
    )
    return wc.encode_window_card_message(payload)


@pytest.fixture()
def gateway_route():
    import sys
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1] / "src" / "opensquad" / "gateway" / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from opensquad.gateway.backend.app import api as gw

    return gw


def test_respond_route_is_registered(gateway_route):
    paths = {getattr(r, "path", "") for r in gateway_route.router.routes}
    assert "/window-cards/{card_id}/respond" in paths


def test_group_card_answer_updates_the_card_and_the_agent(gateway_route, monkeypatch):
    content = _card_content()
    card_id = wc.parse_window_card_payload(content)["id"]
    message = _card_message_for(content)
    db = _DB([message, object(), message])  # lookup, membership, refetch

    spy = _patch_registry(monkeypatch)
    monkeypatch.setattr(gateway_route, "notify_message_update", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(
        gateway_route,
        "format_message_response",
        lambda m: SimpleNamespace(model_dump=lambda mode="json": {"id": m.id}),
    )

    res = asyncio.run(
        gateway_route.respond_window_card(
            card_id=card_id,
            body={"message_id": "m_card", "action_id": "submit", "values": {"decision": "yes"}},
            current_user=SimpleNamespace(id="u1", name="aa"),
            db=db,
        )
    )
    assert res["ok"] is True
    assert res["scope"] == "group"
    assert res["agent_notified"] is True
    assert message.is_edited is True

    parsed = wc.parse_window_card_payload(message.content)
    assert parsed["state"] == "answered"
    assert parsed["response"]["values"] == {"decision": "yes"}
    assert parsed["response"]["by"] == "aa"

    agent_id, payload = spy.sent[0]
    assert agent_id == "Agent305"
    assert "decision=yes" in payload["content"]
    assert payload["channel"] == "gateway"


def test_dm_card_answer(gateway_route, monkeypatch):
    content = _card_content()
    card_id = wc.parse_window_card_payload(content)["id"]
    dm = SimpleNamespace(id="m_card", sender_id="u1", recipient_id="bot", content=content, is_edited=False)
    db = _DB([None, dm])  # not a group message → DM lookup

    _patch_registry(monkeypatch)

    res = asyncio.run(
        gateway_route.respond_window_card(
            card_id=card_id,
            body={"message_id": "m_card", "action_id": "confirm", "values": {"decision": "no"}},
            current_user=SimpleNamespace(id="u1", name="aa"),
            db=db,
        )
    )
    assert res["ok"] is True
    assert res["scope"] == "dm"
    assert wc.parse_window_card_payload(dm.content)["response"]["action_id"] == "confirm"


def test_answer_errors(gateway_route):
    content = _card_content()
    card_id = wc.parse_window_card_payload(content)["id"]
    user = SimpleNamespace(id="u1", name="aa")

    # message_id is required
    with pytest.raises(Exception) as missing:
        asyncio.run(gateway_route.respond_window_card(card_id=card_id, body={}, current_user=user, db=_DB([])))
    assert missing.value.status_code == 400

    # unknown message → 404
    with pytest.raises(Exception) as not_found:
        asyncio.run(
            gateway_route.respond_window_card(
                card_id=card_id, body={"message_id": "nope"}, current_user=user, db=_DB([None, None])
            )
        )
    assert not_found.value.status_code == 404

    # someone else's DM → 403
    foreign = SimpleNamespace(id="m_card", sender_id="x", recipient_id="y", content=content, is_edited=False)
    with pytest.raises(Exception) as forbidden:
        asyncio.run(
            gateway_route.respond_window_card(
                card_id=card_id, body={"message_id": "m_card"}, current_user=user, db=_DB([None, foreign])
            )
        )
    assert forbidden.value.status_code == 403

    # already answered → 409
    answered = wc.patch_window_card_response_in_content(content, action_id="submit", values={}, by="aa")
    with pytest.raises(Exception) as conflict:
        asyncio.run(
            gateway_route.respond_window_card(
                card_id=card_id,
                body={"message_id": "m_card"},
                current_user=user,
                db=_DB([_card_message_for(answered)]),
            )
        )
    assert conflict.value.status_code == 409
