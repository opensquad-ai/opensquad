"""opensquad mcp — list / show / enable / disable / set / add / remove"""

from __future__ import annotations

import json
import sys

from opensquad.cli.api_client import GatewayClient, handle_api_error, print_json, print_table


def run_mcp(args) -> None:
    action = getattr(args, "mcp_action", None)
    if not action:
        print("[mcp] Usage: opensquad mcp {list|show|enable|disable|set|add|remove}")
        sys.exit(1)

    client = GatewayClient(gateway_url=getattr(args, "gateway", None))
    try:
        if action == "list":
            _list(client)
        elif action == "show":
            _show(client, args.name)
        elif action == "enable":
            _toggle(client, args.name, True)
        elif action == "disable":
            _toggle(client, args.name, False)
        elif action == "set":
            _set(client, args)
        elif action == "add":
            _add(client, args)
        elif action == "remove":
            _remove(client, args.name)
        else:
            print(f"[mcp] Unknown action: {action}")
            sys.exit(1)
    except Exception as e:
        handle_api_error(e)
        print(f"[mcp] {e}")
        sys.exit(1)


def _get_servers(client: GatewayClient) -> dict:
    data = client.admin_get("mcp/config")
    return data.get("mcpServers") or {}


def _save_servers(client: GatewayClient, servers: dict) -> dict:
    return client.admin_put("mcp/config", {"mcpServers": servers})


def _list(client: GatewayClient) -> None:
    servers = _get_servers(client)
    global_cfg = {}
    try:
        global_cfg = (client.admin_get("mcp/global") or {}).get("servers") or {}
    except Exception:
        pass
    rows = []
    for name, cfg in servers.items():
        g = global_cfg.get(name) or {}
        rows.append(
            {
                "name": name,
                "enabled": "yes" if cfg.get("enabled", True) else "no",
                "global": "yes" if g.get("enabled", True) else "no",
                "command": cfg.get("command") or "",
            }
        )
    print_table(
        rows,
        [
            ("name", "NAME"),
            ("enabled", "ENABLED"),
            ("global", "GLOBAL"),
            ("command", "COMMAND"),
        ],
    )


def _show(client: GatewayClient, name: str) -> None:
    servers = _get_servers(client)
    if name not in servers:
        print(f"[mcp] Server not found: {name}")
        sys.exit(1)
    print_json(servers[name])


def _toggle(client: GatewayClient, name: str, enabled: bool) -> None:
    action = "enable" if enabled else "disable"
    # Prefer global switch (matches Web McpManager)
    try:
        result = client.admin_put(f"mcp/global/servers/{name}/{action}", {})
        print(f"[mcp] global {action}: {name}")
        print_json(result)
        return
    except Exception:
        pass
    servers = _get_servers(client)
    if name not in servers:
        print(f"[mcp] Server not found: {name}")
        sys.exit(1)
    servers[name]["enabled"] = enabled
    result = _save_servers(client, servers)
    print(f"[mcp] {action}: {name}")
    print_json(result)


def _set(client: GatewayClient, args) -> None:
    """Replace entire mcpServers from a JSON file."""
    with open(args.file, encoding="utf-8") as f:
        payload = json.load(f)
    if "mcpServers" in payload:
        servers = payload["mcpServers"]
    else:
        servers = payload
    result = _save_servers(client, servers)
    print("[mcp] config saved")
    print_json(result)


def _add(client: GatewayClient, args) -> None:
    servers = _get_servers(client)
    name = args.name
    if getattr(args, "from_json", None):
        with open(args.from_json, encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        command = args.command
        if not command:
            print("[mcp] --command required (or --from-json)")
            sys.exit(1)
        cfg = {
            "enabled": True,
            "command": command,
            "args": list(getattr(args, "arg", None) or []),
        }
        if getattr(args, "env", None):
            env = {}
            for item in args.env:
                if "=" in item:
                    k, v = item.split("=", 1)
                    env[k] = v
            cfg["env"] = env
    servers[name] = cfg
    result = _save_servers(client, servers)
    print(f"[mcp] added: {name}")
    print_json(result)


def _remove(client: GatewayClient, name: str) -> None:
    servers = _get_servers(client)
    if name not in servers:
        print(f"[mcp] Server not found: {name}")
        sys.exit(1)
    del servers[name]
    result = _save_servers(client, servers)
    print(f"[mcp] removed: {name}")
    print_json(result)
