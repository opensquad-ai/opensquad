"""opensquad model — list / show / edit / assign / unassign / rm"""

from __future__ import annotations

import json
import sys

from opensquad.cli.api_client import GatewayClient, handle_api_error, print_json, print_table


def run_model(args) -> None:
    action = getattr(args, "model_action", None)
    if not action:
        print("[model] Usage: opensquad model {list|show|edit|assign|unassign|rm}")
        sys.exit(1)

    client = GatewayClient(gateway_url=getattr(args, "gateway", None))
    try:
        if action == "list":
            _list(client)
        elif action == "show":
            _show(client, args.name, getattr(args, "reveal", False))
        elif action == "edit":
            _edit(client, args)
        elif action == "assign":
            _assign(client, args.agent, args.name)
        elif action == "unassign":
            result = client.admin_delete(f"agents/{args.agent}/model-card")
            print(f"[model] unassigned from agent '{args.agent}'")
            print_json(result)
        elif action == "rm":
            result = client.admin_delete(f"model-cards/{args.name}")
            print(f"[model] deleted: {args.name}")
            print_json(result)
        else:
            print(f"[model] Unknown action: {action}")
            sys.exit(1)
    except Exception as e:
        handle_api_error(e)
        print(f"[model] {e}")
        sys.exit(1)


def _list(client: GatewayClient) -> None:
    data = client.admin_get("model-cards")
    cards = data.get("cards") or []
    rows = [
        {
            "name": c.get("name") or "",
            "title": c.get("title") or "",
            "model": c.get("model_name") or "",
            "protocol": c.get("api_protocol") or "",
            "provider": c.get("provider") or "",
        }
        for c in cards
    ]
    print_table(
        rows,
        [
            ("name", "NAME"),
            ("title", "TITLE"),
            ("model", "MODEL"),
            ("protocol", "PROTOCOL"),
            ("provider", "PROVIDER"),
        ],
    )


def _mask_key(key: str) -> str:
    if not key or len(key) < 8:
        return "***"
    return key[:4] + "…" + key[-4:]


def _show(client: GatewayClient, name: str, reveal: bool) -> None:
    data = client.admin_get(f"model-cards/{name}")
    card = data.get("card") or data
    if isinstance(card, dict) and "api_key" in card and not reveal:
        card = dict(card)
        card["api_key"] = _mask_key(str(card.get("api_key") or ""))
    print_json(card)


def _edit(client: GatewayClient, args) -> None:
    name = args.name
    if getattr(args, "file", None):
        with open(args.file, encoding="utf-8") as f:
            card = json.load(f)
    else:
        # Build from flags / merge with existing
        try:
            existing = (client.admin_get(f"model-cards/{name}") or {}).get("card") or {}
        except Exception:
            existing = {}
        card = dict(existing)
        for field in (
            "title",
            "api_protocol",
            "provider",
            "model_name",
            "base_url",
            "api_key",
            "tool_call_mode",
            "render_mode",
        ):
            val = getattr(args, field, None)
            if val is not None:
                card[field] = val
        if getattr(args, "token_max", None) is not None:
            card["token_max"] = args.token_max
        if getattr(args, "temperature", None) is not None:
            card["temperature"] = args.temperature
        card["name"] = name
        if not card.get("model_name") and not existing:
            print("[model] Need --file or at least --model-name (and usually --base-url / --api-key)")
            sys.exit(1)
    result = client.admin_put(f"model-cards/{name}", card)
    print(f"[model] saved: {name}")
    print_json(result)


def _assign(client: GatewayClient, agent: str, name: str) -> None:
    data = client.admin_get(f"model-cards/{name}")
    card = data.get("card") or data
    body = dict(card)
    body["card_name"] = name
    result = client.admin_put(f"agents/{agent}/model-card", body)
    print(f"[model] assigned '{name}' → agent '{agent}'")
    print_json(result)
