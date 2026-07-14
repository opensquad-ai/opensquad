"""opensquad group — list / history / send / watch / approve / choose"""

from __future__ import annotations

import json
import sys
import threading

from opensquad.cli.api_client import GatewayClient, handle_api_error, print_json, print_table
from opensquad.cli.group_render import print_message


def run_group(args) -> None:
    action = getattr(args, "group_action", None)
    if not action:
        print("[group] Usage: opensquad group {list|history|send|watch|approve|choose}")
        sys.exit(1)

    client = GatewayClient(gateway_url=getattr(args, "gateway", None))
    client.require_auth()
    try:
        if action == "list":
            _list(client)
        elif action == "history":
            _history(client, args.group_id, getattr(args, "limit", 30))
        elif action == "send":
            _send(client, args.group_id, args.message)
        elif action == "watch":
            _watch(client, args.group_id)
        elif action == "approve":
            _approve(client, args)
        elif action == "choose":
            _choose(client, args)
        else:
            print(f"[group] Unknown action: {action}")
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n[group] stopped")
    except Exception as e:
        handle_api_error(e)
        print(f"[group] {e}")
        sys.exit(1)


def _list(client: GatewayClient) -> None:
    groups = client.get("/api/groups")
    if not isinstance(groups, list):
        print_json(groups)
        return
    rows = []
    for i, g in enumerate(groups, 1):
        rows.append(
            {
                "n": i,
                "id": g.get("id") or "",
                "name": g.get("name") or "",
                "members": g.get("member_count") or len(g.get("members") or []),
                "unread": g.get("unread_count") or 0,
            }
        )
    print_table(
        rows,
        [("n", "#"), ("id", "ID"), ("name", "NAME"), ("members", "MEMBERS"), ("unread", "UNREAD")],
    )


def _history(client: GatewayClient, group_id: str, limit: int) -> None:
    msgs = client.get(f"/api/groups/{group_id}/messages", params={"limit": limit})
    if not isinstance(msgs, list):
        print_json(msgs)
        return
    for m in msgs:
        print_message(m, shell_style=False)


def _send(client: GatewayClient, group_id: str, message: str) -> None:
    result = client.post(
        f"/api/groups/{group_id}/messages",
        {"content": message, "group_id": group_id, "type": "TEXT"},
    )
    mid = result.get("id") if isinstance(result, dict) else ""
    print(f"[group] sent{(' ' + mid) if mid else ''}")


def _approve(client: GatewayClient, args) -> None:
    action = "reject" if getattr(args, "reject", False) else "approve"
    body = {"action": action, "note": getattr(args, "note", "") or ""}
    if getattr(args, "message_id", None):
        body["message_id"] = args.message_id
    result = client.post(
        f"/api/groups/{args.group_id}/collab-approvals/{args.approval_id}/resolve",
        body,
    )
    print(f"[group] approval {action}: {args.approval_id}")
    print_json(result)


def _choose(client: GatewayClient, args) -> None:
    body = {
        "action": getattr(args, "action", "choose") or "choose",
        "value": args.value,
        "note": getattr(args, "note", "") or "",
    }
    if getattr(args, "message_id", None):
        body["message_id"] = args.message_id
    result = client.post(
        f"/api/groups/{args.group_id}/propose-options/{args.proposal_id}/resolve",
        body,
    )
    print(f"[group] propose-options resolved: {args.proposal_id}")
    print_json(result)


def _watch(client: GatewayClient, group_id: str) -> None:
    """Standalone watch (prefer `opensquad chat` → /group join)."""
    try:
        import websockets.sync.client as ws_sync
    except ImportError:
        print("[group] websockets package required")
        sys.exit(1)

    print(f"[group] Watching {group_id} — prefer: opensquad chat → /group join {group_id}\n")
    try:
        _history(client, group_id, 10)
        print("─" * 40)
    except Exception:
        pass

    url = client.group_ws_url()
    stop = threading.Event()

    def _input_loop(ws):
        while not stop.is_set():
            try:
                line = input()
            except EOFError:
                stop.set()
                break
            line = line.strip()
            if not line:
                continue
            if line in ("/quit", "/exit", "/q"):
                stop.set()
                try:
                    ws.close()
                except Exception:
                    pass
                break
            if line.startswith("/approve "):
                parts = line.split(maxsplit=2)
                if len(parts) < 2:
                    print("usage: /approve <approval_id>")
                    continue
                try:
                    client.post(
                        f"/api/groups/{group_id}/collab-approvals/{parts[1]}/resolve",
                        {"action": "approve", "note": parts[2] if len(parts) > 2 else ""},
                    )
                    print(f"[group] approved {parts[1]}")
                except Exception as e:
                    print(f"[group] {e}")
                continue
            if line.startswith("/reject "):
                parts = line.split(maxsplit=2)
                if len(parts) < 2:
                    continue
                try:
                    client.post(
                        f"/api/groups/{group_id}/collab-approvals/{parts[1]}/resolve",
                        {"action": "reject", "note": parts[2] if len(parts) > 2 else ""},
                    )
                    print(f"[group] rejected {parts[1]}")
                except Exception as e:
                    print(f"[group] {e}")
                continue
            if line.isdigit():
                print("(numeric picks work best inside `opensquad chat` group mode)")
            try:
                client.post(
                    f"/api/groups/{group_id}/messages",
                    {"content": line, "group_id": group_id, "type": "TEXT"},
                )
            except Exception as e:
                print(f"[group] send failed: {e}")

    with ws_sync.connect(url, open_timeout=15) as ws:
        ws.send(json.dumps({"type": "subscribe", "group_id": group_id}))
        ws.send(json.dumps({"type": "subscribe", "data": {"group_id": group_id}}))
        t = threading.Thread(target=_input_loop, args=(ws,), daemon=True)
        t.start()
        print("[group] Type to send. /approve /reject /quit\n")
        try:
            for raw in ws:
                if stop.is_set():
                    break
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                mtype = msg.get("type")
                data = msg.get("data") or {}
                if mtype == "new_message":
                    print_message(data if isinstance(data, dict) else msg, shell_style=True)
                elif mtype == "message_updated" and isinstance(data, dict):
                    print_message(data, shell_style=True)
        finally:
            stop.set()
