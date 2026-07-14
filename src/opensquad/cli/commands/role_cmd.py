"""opensquad role — list / show / edit / assign / unassign / rm"""

from __future__ import annotations

import sys
from pathlib import Path

from opensquad.cli.api_client import GatewayClient, handle_api_error, print_json, print_table


def run_role(args) -> None:
    action = getattr(args, "role_action", None)
    if not action:
        print("[role] Usage: opensquad role {list|show|edit|assign|unassign|rm}")
        sys.exit(1)

    client = GatewayClient(gateway_url=getattr(args, "gateway", None))
    try:
        if action == "list":
            _list(client)
        elif action == "show":
            _show(client, args.name)
        elif action == "edit":
            _edit(client, args)
        elif action == "assign":
            _assign(client, args.agent, args.name)
        elif action == "unassign":
            _unassign(client, args.agent)
        elif action == "rm":
            result = client.admin_delete(f"role-cards/{args.name}")
            print(f"[role] deleted: {args.name}")
            print_json(result)
        else:
            print(f"[role] Unknown action: {action}")
            sys.exit(1)
    except Exception as e:
        handle_api_error(e)
        print(f"[role] {e}")
        sys.exit(1)


def _list(client: GatewayClient) -> None:
    data = client.admin_get("role-cards")
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


def _show(client: GatewayClient, name: str) -> None:
    data = client.admin_get(f"role-cards/{name}")
    print(data.get("content") or "")


def _edit(client: GatewayClient, args) -> None:
    name = args.name
    if getattr(args, "file", None):
        content = Path(args.file).read_text(encoding="utf-8")
    elif getattr(args, "content", None):
        content = args.content
    else:
        print("[role] Provide --file or --content")
        sys.exit(1)
    result = client.admin_put(f"role-cards/{name}", {"content": content})
    print(f"[role] saved: {name}")
    print_json(result)


def _assign(client: GatewayClient, agent: str, name: str) -> None:
    card = client.admin_get(f"role-cards/{name}")
    content = card.get("content") or ""
    result = client.admin_put(
        f"agents/{agent}/role-prompt",
        {"content": content, "card_name": name},
    )
    print(f"[role] assigned '{name}' → agent '{agent}'")
    print_json(result)


def _unassign(client: GatewayClient, agent: str) -> None:
    result = client.admin_delete(f"agents/{agent}/role-prompt")
    print(f"[role] unassigned from agent '{agent}'")
    print_json(result)
