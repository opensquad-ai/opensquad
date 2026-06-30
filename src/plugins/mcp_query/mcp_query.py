"""
MCP Service Tools — MCP server management and querying

Provides Agent with tools to manage MCP servers at runtime:
  - list_servers        — List all MCP servers and their status
  - get_all_tools       — List all available MCP tools
  - get_server_config   — Get config details for a specific server
  - add_server          — Dynamically add and connect a new MCP server
  - remove_server       — Dynamically remove an MCP server
  - reconnect_server    — Reconnect a disconnected MCP server
  - reload_servers      — Reload config file and connect new servers
"""

import json
import sys

from opensquad.tools.mcp_adapter import get_mcp_adapter


def list_servers() -> str:
    """
    List all MCP servers and their connection status.

    Returns:
        JSON with each server's connected state, tool list, tool count, and timeout
    """
    adapter = get_mcp_adapter()
    if adapter is None:
        return json.dumps({"status": "error", "message": "MCP adapter not initialized"}, ensure_ascii=False)

    servers = adapter.list_servers()
    return json.dumps({"status": "success", "servers": servers, "total": len(servers)}, ensure_ascii=False, indent=2)


def get_all_tools() -> str:
    """
    List all available MCP tools across all connected servers.

    Returns:
        JSON with tool details in OpenAI function-calling format
    """
    adapter = get_mcp_adapter()
    if adapter is None:
        return json.dumps({"status": "error", "message": "MCP adapter not initialized"}, ensure_ascii=False)

    tools = adapter.get_all_tools()
    return json.dumps({"status": "success", "tools": tools, "total": len(tools)}, ensure_ascii=False, indent=2)


def get_server_config(server_name: str) -> str:
    """
    Get configuration details for a specific MCP server (sensitive info hidden).

    Args:
        server_name: MCP server name (e.g. "playwright", "filesystem")

    Returns:
        JSON with server config (command, args, timeout, autoApprove)
    """
    adapter = get_mcp_adapter()
    if adapter is None:
        return json.dumps({"status": "error", "message": "MCP adapter not initialized"}, ensure_ascii=False)

    cfg = adapter._server_configs.get(server_name)
    if cfg is None:
        return json.dumps({"status": "error", "message": f"Server '{server_name}' not found"}, ensure_ascii=False)

    safe_cfg = {
        "command": cfg.get("command"),
        "args": cfg.get("args"),
        "timeout": cfg.get("timeout", 30),
        "autoApprove": cfg.get("autoApprove", []),
    }

    return json.dumps({"status": "success", "server": server_name, "config": safe_cfg}, ensure_ascii=False, indent=2)


async def add_server(
    server_name: str,
    command: str,
    args: list,
    timeout: int = 30,
    auto_approve: list | None = None,
    env: dict | None = None,
) -> str:
    """
    Dynamically add and connect a new MCP server. Takes effect immediately, no restart needed.

    Args:
        server_name: Server name (e.g. "my-mcp-server")
        command: Launch command (e.g. "npx", "python")
        args: Command arguments list (e.g. ["-y", "@modelcontextprotocol/server-filesystem"])
        timeout: Timeout in seconds, default 30
        auto_approve: List of tools to auto-approve (optional)
        env: Environment variables dict (optional)

    Returns:
        JSON with connection result and available tools
    """
    adapter = get_mcp_adapter()
    if adapter is None:
        return json.dumps(
            {"status": "error", "message": "MCP adapter not initialized. Please enable MCP in config.json first."},
            ensure_ascii=False,
        )

    # Windows npx compatibility
    resolved_command = command
    if sys.platform == "win32" and command == "npx":
        from shutil import which

        resolved_command = which("npx") or "npx.cmd"

    cfg = {"command": resolved_command, "args": args, "timeout": timeout}
    if auto_approve:
        cfg["autoApprove"] = auto_approve
    if env:
        cfg["env"] = env

    try:
        result = await adapter.add_server(server_name, cfg, persist=True)

        if result.get("success"):
            all_tools = adapter.get_all_tools()
            return json.dumps(
                {
                    "status": "success",
                    "message": f"MCP server '{server_name}' added and connected",
                    "server": result.get("server"),
                    "new_tools": result.get("tools", []),
                    "total_tools": len(all_tools),
                },
                ensure_ascii=False,
                indent=2,
            )
        else:
            return json.dumps(
                {
                    "status": "error",
                    "message": f"Failed to add server: {result.get('error', 'Unknown error')}",
                },
                ensure_ascii=False,
                indent=2,
            )

    except Exception as e:
        return json.dumps({"status": "error", "message": f"Error adding MCP server: {e!s}"}, ensure_ascii=False)


async def remove_server(server_name: str) -> str:
    """
    Dynamically remove an MCP server and disconnect. Takes effect immediately.

    Args:
        server_name: MCP server name to remove

    Returns:
        JSON with removal result
    """
    adapter = get_mcp_adapter()
    if adapter is None:
        return json.dumps({"status": "error", "message": "MCP adapter not initialized"}, ensure_ascii=False)

    try:
        result = await adapter.remove_server(server_name, persist=True)
        return json.dumps(
            {
                "status": "success" if result.get("success") else "error",
                "message": f"MCP server '{server_name}' removed" if result.get("success") else result.get("error"),
            },
            ensure_ascii=False,
            indent=2,
        )

    except Exception as e:
        return json.dumps({"status": "error", "message": f"Error removing MCP server: {e!s}"}, ensure_ascii=False)


async def reconnect_server(server_name: str) -> str:
    """
    Reconnect a specific MCP server (e.g. after a crash or timeout).

    Args:
        server_name: MCP server name (e.g. "chrome-devtools", "playwright")

    Returns:
        JSON with reconnection result
    """
    adapter = get_mcp_adapter()
    if adapter is None:
        return json.dumps({"status": "error", "message": "MCP adapter not initialized"}, ensure_ascii=False)

    try:
        success = await adapter.reconnect_server(server_name)
        return json.dumps(
            {
                "status": "success" if success else "error",
                "message": f"Reconnected to '{server_name}'" if success else f"Failed to reconnect to '{server_name}'",
                "connected": success,
            },
            ensure_ascii=False,
        )

    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)


async def reload_servers() -> str:
    """
    Reload MCP config file and connect any newly added servers.

    Call this after manually editing mcp_config.json to apply changes
    without restarting the Agent.

    Returns:
        JSON with reload results for each new server
    """
    adapter = get_mcp_adapter()
    if adapter is None:
        return json.dumps({"status": "error", "message": "MCP adapter not initialized"}, ensure_ascii=False)

    try:
        from opensquad.tools.mcp_adapter import DEFAULT_CONFIG_PATH, _load_mcp_config

        new_config = _load_mcp_config(adapter.config_path or DEFAULT_CONFIG_PATH, adapter.agent_dir)

        results = []
        for server_name, cfg in new_config.items():
            if server_name not in adapter._server_configs:
                try:
                    result = await adapter.add_server(server_name, cfg, persist=False)
                    results.append(
                        {
                            "server": server_name,
                            "status": "connected" if result.get("success") else "failed",
                            "tools": result.get("tools", []),
                        }
                    )
                except Exception as e:
                    results.append({"server": server_name, "status": "error", "error": str(e)})

        return json.dumps(
            {
                "status": "success",
                "message": f"Reloaded config, {len(results)} new server(s) processed",
                "servers": results,
            },
            ensure_ascii=False,
            indent=2,
        )

    except Exception as e:
        return json.dumps({"status": "error", "message": f"Error reloading servers: {e!s}"}, ensure_ascii=False)
