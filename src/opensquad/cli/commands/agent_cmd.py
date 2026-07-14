"""opensquad agent — list / start / stop / restart / config / logs"""

from __future__ import annotations

import json
import sys

from opensquad.cli.api_client import GatewayClient, handle_api_error, print_json, print_table


def run_agent(args) -> None:
    action = getattr(args, "agent_action", None)
    if not action:
        print("[agent] Usage: opensquad agent {list|show|start|stop|restart|config|logs}")
        sys.exit(1)

    client = GatewayClient(gateway_url=getattr(args, "gateway", None))
    try:
        if action == "list":
            _list(client)
        elif action == "show":
            _show(client, args.name)
        elif action == "start":
            _lifecycle(client, args.name, "start")
        elif action == "stop":
            _lifecycle(client, args.name, "stop")
        elif action == "restart":
            _lifecycle(client, args.name, "restart")
        elif action == "config":
            _config(client, args)
        elif action == "logs":
            _logs(client, args.name, getattr(args, "tail", 50))
        else:
            print(f"[agent] Unknown action: {action}")
            sys.exit(1)
    except Exception as e:
        handle_api_error(e)
        print(f"[agent] {e}")
        sys.exit(1)


def _list(client: GatewayClient) -> None:
    data = client.admin_get("agents")
    agents = data.get("agents") or []
    rows = []
    for a in agents:
        rows.append(
            {
                "dir": a.get("dir_name") or a.get("agent_id") or "",
                "name": a.get("agent_name") or "",
                "status": a.get("process_status") or "",
                "ready": "yes" if a.get("ready") else "no",
                "model": a.get("model_card") or "",
                "role": a.get("role_card") or "",
            }
        )
    print_table(
        rows,
        [
            ("dir", "DIR"),
            ("name", "NAME"),
            ("status", "STATUS"),
            ("ready", "READY"),
            ("model", "MODEL"),
            ("role", "ROLE"),
        ],
    )


def _show(client: GatewayClient, name: str) -> None:
    data = client.admin_get("agents")
    agents = data.get("agents") or []
    match = next(
        (a for a in agents if a.get("dir_name") == name or a.get("agent_id") == name),
        None,
    )
    if not match:
        print(f"[agent] Not found: {name}")
        sys.exit(1)
    print_json(match)


def _lifecycle(client: GatewayClient, name: str, action: str) -> None:
    result = client.admin_post(f"agents/{name}/{action}")
    print(f"[agent] {action} {name}: OK")
    if isinstance(result, dict) and result:
        print_json(result)


def _config(client: GatewayClient, args) -> None:
    name = args.name
    if getattr(args, "set_json", None):
        with open(args.set_json, encoding="utf-8") as f:
            body = json.load(f)
        if "config" not in body:
            body = {"config": body}
        result = client.admin_put(f"agents/{name}/config", body)
        print(f"[agent] config updated: {name}")
        print_json(result)
        return
    data = client.admin_get(f"agents/{name}/config")
    print_json(data)


def _logs(client: GatewayClient, name: str, tail: int) -> None:
    data = client.admin_get(f"agents/{name}/logs", params={"tail": tail} if tail else None)
    if isinstance(data, dict):
        text = data.get("logs") or data.get("content") or data.get("text")
        if text is not None:
            print(text)
            return
        print_json(data)
    else:
        print(data)
