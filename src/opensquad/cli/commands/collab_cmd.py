"""opensquad collab — collaboration cards + board"""

from __future__ import annotations

import sys
from pathlib import Path

from opensquad.cli.api_client import GatewayClient, handle_api_error, print_json, print_table


def run_collab(args) -> None:
    action = getattr(args, "collab_action", None)
    if not action:
        print("[collab] Usage: opensquad collab {list|show|edit|rm|board}")
        sys.exit(1)

    client = GatewayClient(gateway_url=getattr(args, "gateway", None))
    try:
        if action == "list":
            _list_cards(client)
        elif action == "show":
            data = client.admin_get(f"collab-cards/{args.name}")
            print(data.get("content") or "")
        elif action == "edit":
            _edit(client, args)
        elif action == "rm":
            result = client.admin_delete(f"collab-cards/{args.name}")
            print(f"[collab] deleted card: {args.name}")
            print_json(result)
        elif action == "board":
            _board(client, args)
        else:
            print(f"[collab] Unknown action: {action}")
            sys.exit(1)
    except Exception as e:
        handle_api_error(e)
        print(f"[collab] {e}")
        sys.exit(1)


def _list_cards(client: GatewayClient) -> None:
    data = client.admin_get("collab-cards")
    cards = data.get("cards") or []
    rows = [
        {
            "name": c.get("name") or "",
            "title": c.get("title") or "",
            "tags": ",".join(c.get("tags") or []),
            "chars": c.get("char_count") or "",
        }
        for c in cards
    ]
    print_table(rows, [("name", "NAME"), ("title", "TITLE"), ("tags", "TAGS"), ("chars", "CHARS")])


def _edit(client: GatewayClient, args) -> None:
    if getattr(args, "file", None):
        content = Path(args.file).read_text(encoding="utf-8")
    elif getattr(args, "content", None):
        content = args.content
    else:
        print("[collab] Provide --file or --content")
        sys.exit(1)
    result = client.admin_put(f"collab-cards/{args.name}", {"content": content})
    print(f"[collab] saved: {args.name}")
    print_json(result)


def _board(client: GatewayClient, args) -> None:
    board_action = getattr(args, "board_action", None) or "tasks"
    client.require_auth()
    if board_action == "tasks":
        data = client.ai_web_get("collab-board/tasks")
        tasks = data.get("tasks") or []
        rows = [
            {
                "id": t.get("task_id") or "",
                "name": t.get("task_name") or "",
                "status": t.get("status") or "",
                "progress": t.get("progress") if t.get("progress") is not None else "",
                "items": t.get("item_count") if t.get("item_count") is not None else "",
            }
            for t in tasks
        ]
        print_table(
            rows,
            [
                ("id", "TASK_ID"),
                ("name", "NAME"),
                ("status", "STATUS"),
                ("progress", "PROG"),
                ("items", "ITEMS"),
            ],
        )
    elif board_action == "items":
        collab_id = args.collab_id
        params = {"collab_id": collab_id, "scope": getattr(args, "scope", "public") or "public"}
        if getattr(args, "agent_id", None):
            params["agent_id"] = args.agent_id
        data = client.ai_web_get("collab-board/items", params=params)
        items = data.get("items") or []
        rows = [
            {
                "id": i.get("id") or "",
                "type": i.get("item_type") or "",
                "title": i.get("title") or "",
                "status": i.get("status") or "",
                "agent": i.get("agent_id") or "",
            }
            for i in items
        ]
        print_table(
            rows,
            [
                ("id", "ID"),
                ("type", "TYPE"),
                ("title", "TITLE"),
                ("status", "STATUS"),
                ("agent", "AGENT"),
            ],
        )
    else:
        print("[collab] board actions: tasks | items")
        sys.exit(1)
