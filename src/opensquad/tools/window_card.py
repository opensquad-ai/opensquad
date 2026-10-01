"""Window-card tool — let an agent send a rich, clickable card to chat.

The card shows up in group chat or in a private chat (DM) as a compact card;
clicking it opens a window whose content is exactly the ``view`` the agent
passed. Use it whenever a message is better shown than told: a review table, a
step-by-step flow, a status report, a list of files, an approval summary.
"""

from __future__ import annotations

import os
from typing import Any


def _sender_identity() -> tuple[str, str]:
    """(agent_id, agent_name) of the running agent, best effort."""
    try:
        from opensquad.json_cache import load_json_cached

        from ..input_hub import input_hub

        agent_dir = input_hub.agent_dir or ""
        folder = os.path.basename(agent_dir) if agent_dir else ""
        agent_id = folder
        agent_name = folder
        cfg_path = os.path.join(agent_dir, "config.json") if agent_dir else ""
        if cfg_path and os.path.exists(cfg_path):
            cfg = load_json_cached(cfg_path) or {}
            agent_id = str(cfg.get("agent_id") or folder)
            agent_name = str(cfg.get("agent_name") or folder)
        return agent_id, agent_name
    except Exception:
        return "", ""


def send_window_card(
    title: str,
    view: dict[str, Any] | str,
    summary: str = "",
    group_id: str = "",
    recipient_name: str = "",
    actions: list[dict[str, Any]] | None = None,
    form: dict[str, Any] | None = None,
    icon: str = "",
    source: str = "",
) -> dict[str, Any]:
    """
    [All agents] Send a rich card that the user can click to open a full window.

    Use it when a message is better shown than told — a review table, a step
    flow, a status report, a file list, an approval summary. The card appears in
    group chat (pass group_id) or in a private chat (pass recipient_name) and the
    window renders whatever you put in `view`.

    Args:
        title: Card title, also the window header.
        view: What the window shows. Either a kind string, or a dict:
              - {"kind": "sections", "blocks": [{"title": "...", "text": "...", "items": ["..."]}]}
              - {"kind": "table", "columns": ["名称", "状态"], "rows": [["A", "通过"]]}
              - {"kind": "flow", "steps": [{"title": "确定需求", "detail": "...", "status": "done"}]}
              - {"kind": "metrics", "items": [{"label": "通过率", "value": "98%"}]}
              - {"kind": "raw", "text": "任何纯文本/ Markdown"}
        summary: One-line summary shown on the card itself.
        group_id: Group id or name to post to.
        recipient_name: IM username to DM instead (e.g. the human user's name).
        actions: Buttons under the card, each
                 {"label": "...", "intent": "open_url|copy|open_collab_task|respond|confirm|decline|none",
                  "url": "...", "copy": "...", "collab_id": "..."}.
                 `respond` / `confirm` / `decline` send the answer back to you.
        form: Make the window interactive — the user fills it in and submitting
              returns the values to you as a system message:
              {"submit_label": "确认",
               "fields": [{"id": "decision", "label": "是否通过", "type": "radio",
                           "options": [{"id": "yes", "label": "通过"}, {"id": "no", "label": "驳回"}],
                           "required": true},
                          {"id": "note", "label": "备注", "type": "textarea"}]}
              Field types: text | textarea | select | radio | checkbox.
        icon: Optional icon hint for the card header (e.g. "table", "shield").
        source: Optional refetch hint for the window (free-form string).

    Returns:
        dict with status, card_id, target_type, target and message_id.

    Examples:
        send_window_card(
            title="自建应用发布申请 · 自动审核通过",
            summary="系统基于免审规则已自动审核通过。",
            group_id="g-default",
            view={"kind": "table", "columns": ["项", "结果"],
                  "rows": [["申请人", "quanker"], ["结论", "自动通过"]]},
            actions=[{"label": "查看审核详情", "intent": "open_url", "url": "https://..."}],
        )

        send_window_card(
            title="需求确认进度", recipient_name="aa",
            view={"kind": "flow", "steps": [
                {"title": "确定需求", "status": "done"},
                {"title": "讨论方案", "status": "doing"},
            ]},
        )
    """
    try:
        from ..window_cards import build_window_card_payload, post_window_card

        group_id = (group_id or "").strip()
        recipient_name = (recipient_name or "").strip()
        if not group_id and not recipient_name:
            return {
                "status": "error",
                "message": "pass group_id (group chat) or recipient_name (private chat)",
            }
        if not (title or "").strip():
            return {"status": "error", "message": "title is required"}

        sender_id, sender_name = _sender_identity()
        payload = build_window_card_payload(
            title=title,
            view=view,
            summary=summary,
            icon=icon,
            group_id=group_id,
            recipient_name=recipient_name,
            actions=actions,
            form=form,
            sender_id=sender_id,
            sender_name=sender_name,
            source=source,
        )
        result = post_window_card(payload, group_id=group_id, recipient_name=recipient_name)
        if not result.get("ok"):
            return {"status": "error", "message": result.get("error", "send failed"), "detail": result}
        return {
            "status": "success",
            "card_id": payload["id"],
            "title": payload["title"],
            "target_type": result.get("target_type"),
            "target": result.get("target"),
            "message_id": result.get("message_id"),
            "hint": "The user sees a card; clicking it opens the full window.",
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
