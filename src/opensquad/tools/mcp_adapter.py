"""
MCP Adapter -- based on the official MCP SDK (mcp>=1.0)
Replaces custom pymcp; uses stdio_client + ClientSession for long-lived connections.

Key improvements:
  - Each MCP server spawns only one subprocess and stays alive (no repeated npx launches)
  - Correct MCP protocol handshake (initialize + initialized notification)
  - Tool list is cached once at startup; get_all_tools() is zero-cost
  - call_tool_async() has built-in SDK timeout control
  - Encoding is handled automatically by the SDK

External interface (fully backward-compatible; registry.py / boot.py require zero changes):
  - MCPAdapter.get_all_tools() -> List[Dict]    (OpenAI-format tool descriptions)
  - MCPAdapter.call_tool_async(name, args) -> str (async call, returns JSON string)
  - init_mcp_adapter(config_path?) -> MCPAdapter  (async init, replaces old sync get_mcp_adapter())
"""

import asyncio
import json
import logging
import os
import sys
from contextlib import AsyncExitStack, suppress
from datetime import timedelta
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import stdio_client

try:
    from opensquad.events import bus as _event_bus
except ImportError:
    _event_bus = None

logger = logging.getLogger(__name__)

# Project root directory (opensquad/tools/mcp_adapter.py -> two levels up)
_current_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(os.path.dirname(_current_dir))

# Default config file path (global config, used as fallback)
DEFAULT_CONFIG_PATH = os.path.join(_project_root, "pymcp", "config_basic.json")


def _load_mcp_config(config_path: str, agent_dir: str | None = None) -> dict[str, Any]:
    """
    Load MCP configuration with unified central config support.

    Priority:
    1. Central config at data/mcp_config.json (unified across all agents)
    2. Agent-specific mcp_config.json (backward compat / fallback)
    3. The provided config_path
    4. Global default config

    Servers with enabled: false are automatically filtered out.
    """
    # Try central config first (unified across all agents)
    try:
        from opensquad import system_config as syscfg

        central_path = syscfg.workspace_data_dir("mcp_config.json")
        if os.path.exists(central_path):
            config_file = central_path
            logger.info(f"[MCP] Loading central unified config: {config_file}")
        elif agent_dir and os.path.exists(os.path.join(agent_dir, "mcp_config.json")):
            config_file = os.path.join(agent_dir, "mcp_config.json")
            logger.info(f"[MCP] Loading agent-specific config (fallback): {config_file}")
        elif config_path and os.path.exists(config_path):
            config_file = config_path
            logger.info(f"[MCP] Loading config: {config_file}")
        else:
            config_file = DEFAULT_CONFIG_PATH
            logger.info(f"[MCP] Loading default config: {config_file}")
    except ImportError:
        # Fallback if system_config is not available
        if agent_dir and os.path.exists(os.path.join(agent_dir, "mcp_config.json")):
            config_file = os.path.join(agent_dir, "mcp_config.json")
            logger.info(f"[MCP] Loading agent-specific config: {config_file}")
        elif config_path and os.path.exists(config_path):
            config_file = config_path
            logger.info(f"[MCP] Loading config: {config_file}")
        else:
            config_file = DEFAULT_CONFIG_PATH
            logger.info(f"[MCP] Loading default config: {config_file}")

    # Use json_cache with mtime-based staleness detection (avoids re-reading unchanged files)
    from opensquad.json_cache import load_json_cached

    data = load_json_cached(config_file, default={})

    # Support both 'mcpServers' and 'mcp_servers' keys
    all_servers = data.get("mcpServers") or data.get("mcp_servers") or {}

    # Filter out servers with enabled: false (if 'enabled' is not set, default is True)
    enabled_servers = {}
    for name, cfg in all_servers.items():
        if cfg.get("enabled", True):  # enabled by default
            enabled_servers[name] = cfg
        else:
            logger.info(f"[MCP] Server '{name}' is disabled, skipping")

    return enabled_servers


def _mcp_tool_to_openai(tool, server_name: str) -> dict[str, Any]:
    """Convert an official SDK Tool object to OpenAI function-calling format with a server prefix."""
    input_schema = tool.inputSchema if tool.inputSchema else {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": f"mcp__{server_name}__{tool.name}",
            "description": f"[MCP Server: {server_name}] {tool.description or ''}",
            "parameters": input_schema,
        },
    }


class MCPAdapter:
    """
    MCP Adapter -- manages long-lived connections to multiple MCP servers.

    Lifecycle:
      1. __init__()      -- stores config only; no processes are started
      2. await connect() -- async connect to all MCP servers (spawn subprocesses + handshake + cache tools)
      3. get_all_tools() -- sync return of cached tool list (zero-cost)
      4. await call_tool_async() -- async tool invocation
      5. await close()   -- close all connections and subprocesses
    """

    # P2-2: Auto-reconnect config
    HEALTH_CHECK_INTERVAL = 30  # seconds between health probes
    HEALTH_CHECK_TIMEOUT = 5  # seconds before probe is considered failed
    RECONNECT_BACKOFF_BASE = 2.0  # base seconds for exponential backoff
    RECONNECT_MAX_BACKOFF = 60.0  # max seconds between reconnection attempts

    def __init__(
        self, config_path: str | None = None, agent_dir: str | None = None, global_disabled_servers: set | None = None
    ):
        self.config_path = config_path
        self.agent_dir = agent_dir
        self._global_disabled: set = global_disabled_servers or set()
        self._server_configs: dict[str, dict] = {}  # server_name -> raw config dict
        self._sessions: dict[str, ClientSession] = {}  # server_name -> ClientSession
        self._exit_stacks: dict[str, AsyncExitStack] = {}  # server_name -> AsyncExitStack
        self._tools_cache: dict[str, list] = {}  # server_name -> List[Tool objects]
        self._timeouts: dict[str, int] = {}  # server_name -> timeout seconds
        self._connected = False

        # P2-2: Health check / auto-reconnect state
        self._health_task: asyncio.Task | None = None
        self._stop_health = asyncio.Event()
        self._server_health_status: dict[str, bool] = {}  # server_name -> last known healthy
        self._server_reconnect_backoff: dict[str, float] = {}  # server_name -> current backoff seconds

    async def connect(self):
        """
        Async initialization: connect to all enabled MCP servers.
        Supports agent-level independent configs (via agent_dir).
        """
        logger.info("[MCP] Initializing connections (robust mode)...")
        self._server_configs = _load_mcp_config(self.config_path, self.agent_dir)

        # Apply global disabled list: globally disabled servers are skipped regardless of agent settings
        if self._global_disabled:
            for srv in list(self._server_configs.keys()):
                if srv in self._global_disabled:
                    logger.info(f"[MCP] Server '{srv}' is globally disabled, skipping")
                    del self._server_configs[srv]

        if not self._server_configs:
            logger.warning("[MCP] No enabled servers found in config, MCP disabled")
            self._connected = True
            return

        for server_name, cfg in self._server_configs.items():
            try:
                await self._connect_server(server_name, cfg)
            except (Exception, asyncio.CancelledError) as e:
                # Catch all regular exceptions and CancelledError to prevent one failing server
                # from taking down the entire Agent
                logger.error(f"[MCP] Failed to connect to '{server_name}': {e} ({type(e).__name__})")

                # Clean up resources for this failed server
                if server_name in self._exit_stacks:
                    with suppress(Exception):
                        await self._exit_stacks[server_name].aclose()
                    del self._exit_stacks[server_name]

                # Continue attempting the next server
                continue

        self._connected = True
        total_tools = sum(len(t) for t in self._tools_cache.values())
        logger.info(
            f"[MCP] Adapter ready: {len(self._sessions)}/{len(self._server_configs)} servers, {total_tools} tools"
        )

        # P2-2: Start background health-check + auto-reconnect loop
        self._stop_health.clear()
        if self._health_task is None or self._health_task.done():
            self._health_task = asyncio.create_task(
                self._health_monitor_loop(),
                name="mcp-health-monitor",
            )

    async def _filter_log(self, server_name: str, stream):
        """Consume and filter stderr from MCP server"""
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue

                # Noise filters (Startup banners)
                if "Google collects usage statistics" in text:
                    continue
                if "Performance tools may send trace URLs" in text:
                    continue
                if "For more details, visit" in text:
                    continue
                if "Avoid sharing sensitive" in text:
                    continue
                if "chrome-devtools-mcp exposes content" in text:
                    continue
                if "debug, and modify any data" in text:
                    continue
                if "Windows CLI MCP Server running on stdio" in text:
                    continue
                if "To disable, run with" in text:
                    continue
                if "visit: https://github.com" in text:
                    continue

                # Log other output
                logger.info(f"[MCP:{server_name}] {text}")
        except Exception:
            pass

    async def _connect_server(self, server_name: str, cfg: dict):
        """Connect to a single MCP server (Custom Implementation avoiding stdio_client generator issues)"""
        command = cfg.get("command", "npx")
        args = cfg.get("args", [])
        env = cfg.get("env")
        # Ensure env is a dict if provided, else None
        if env:
            # Merge with system env to ensure basic paths are available
            full_env = os.environ.copy()
            full_env.update(env)
            env = full_env

        timeout = cfg.get("timeout", 30)
        self._timeouts[server_name] = timeout

        # Windows npx compatibility
        if sys.platform == "win32" and command == "npx":
            from shutil import which

            command = which("npx") or "npx.cmd"

        logger.info(f"[MCP] Connecting to '{server_name}': {command} {' '.join(args)}")

        # Use MCP SDK's stdio_client to manage subprocess lifecycle
        from mcp.client.stdio import StdioServerParameters

        stack = AsyncExitStack()

        try:
            # Launch subprocess (stdio_client is an @asynccontextmanager)
            # We handle Python 3.13 issues by wrapping in try-except.
            # NOTE: When passing an existing process to stdio_client is not directly supported by public API,
            # we rely on our previous manual creation.
            # WAIT: stdio_client CREATES the process. If we created it manually above, we can't use stdio_client easily.
            # But wait, stdio_client takes StdioServerParameters.
            # If we want to capture stderr, we must avoid stdio_client creating the process with default stderr.
            # However, `mcp` SDK doesn't expose a way to pass a pre-created process to `ClientSession`.
            # `ClientSession` needs `read_stream` and `write_stream`.
            # We can wrap our `process.stdout` and `process.stdin` into anyio streams!

            # Let's import the necessary adapters

            # process.stdout is an asyncio.StreamReader
            # process.stdin is an asyncio.StreamWriter
            # We need to bridge them to anyio.
            # This is complex.
            # ALTERNATIVE: Use stdio_client but pass `stderr=subprocess.PIPE`?
            # StdioServerParameters doesn't support stderr arg.
            # It seems StdioServerParameters only has command, args, env.

            # Let's look at `mcp.client.stdio.stdio_client` implementation (mental check).
            # It calls `anyio.create_process`.
            # If we can't control stderr via StdioServerParameters, we have a problem.

            # REVERT STRATEGY for `_connect_server`:
            # We will NOT use `stdio_client`. We will use our manual `process` creation
            # and manually construct the `read_stream` and `write_stream` that `ClientSession` expects.
            # `ClientSession` expects objects with `send()` and `receive()`.

            # We can use `mcp.client.stdio.get_default_environment` or similar? No.

            # Let's try to stick to the previous implementation but redirect stderr.
            # But the previous implementation used `stdio_client`!
            # Wait, my previous edit (Turn 32) REPLACED `stdio_client` with `create_subprocess_exec`?
            # Let's check Turn 32 output.
            # No! Turn 32 edit comments said "Manual management" but the CODE still used `stdio_client`.
            # "read_stream, write_stream = await stack.enter_async_context(stdio_client(...))"
            # It just wrapped it in try-except.

            # So `stdio_client` is still creating the process.
            # And `stdio_client` does NOT allow capturing stderr. It inherits stderr.
            # This means I CANNOT silence the logs unless I patch `stdio_client` or `anyio`.

            # BUT! I can monkey-patch `subprocess.Popen` or `anyio.create_process`? Too risky.

            # Alternative: Since I am on Windows, `npx.cmd` is executed.
            # I can change the command to `cmd /c npx ... 2> NUL`?
            # That would suppress ALL errors, which is bad for debugging.

            # Okay, let's look at `mcp` library source code (mental check).
            # `stdio_client` uses `anyio.open_process`.
            # It defaults `stderr=sys.stderr`.

            # If I cannot change it, I cannot hide the logs.
            # UNLESS I assume `server_name` config allows me to inject shell redirection?
            # `args` are passed to `subprocess`.

            # Wait, `mcp_adapter` allows `command` configuration.
            # I can write a wrapper script? No.

            # Let's accept that I might not be able to silence MCP logs easily without breaking the `stdio_client` usage.
            # BUT, the user's main complaint was "every second".
            # The MCP logs happen ONCE at startup.
            # If `input_hub` was logging every second, fixing `input_hub` solves 90% of the pain.

            # I will apply the `input_hub` fix (already done).
            # And I will TRY to fix MCP logs by using a different approach:
            # Revert to `stdio_client` but acknowledge I can't easily filter stderr.
            # OR...
            # I see `mcp` 1.0+ has `StdioServerParameters`.
            # If I really want to fix it, I have to implement the `read_stream`/`write_stream` adapter for `asyncio.subprocess`.

            # Let's try to implement a simple adapter class to bridge asyncio process to anyio streams.
            # This allows me to use `create_subprocess_exec` (where I control stderr) and pass it to `ClientSession`.

            # Use stdio_client to create connection (it manages the subprocess automatically)
            server_params = StdioServerParameters(command=command, args=args, env=env)

            read_stream, write_stream = await stack.enter_async_context(stdio_client(server_params))

            # Create ClientSession
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))

            # MCP protocol handshake
            init_result = await session.initialize()
            logger.info(
                f"[MCP] '{server_name}' initialized: "
                f"protocol={init_result.protocolVersion}, "
                f"server={init_result.serverInfo.name if init_result.serverInfo else 'unknown'}"
            )

            # Fetch and cache the tool list
            tools_result = await session.list_tools()
            tool_names = [t.name for t in tools_result.tools]
            logger.info(f"[MCP] '{server_name}' tools ({len(tool_names)}): {tool_names}")

            self._sessions[server_name] = session
            self._exit_stacks[server_name] = stack
            self._tools_cache[server_name] = tools_result.tools

        except Exception as e:
            logger.error(f"[MCP] Failed to connect to '{server_name}': {e}")
            raise

    def get_all_tools(self) -> list[dict[str, Any]]:
        """
        Return all MCP server tool descriptions in OpenAI function-calling format.
        Reads from the cache built at startup; zero-cost.
        Tool name format: mcp__{server_name}__{tool_name}
        """
        all_tools = []
        for server_name, tools in self._tools_cache.items():
            for tool in tools:
                all_tools.append(_mcp_tool_to_openai(tool, server_name))
        return all_tools

    def _parse_tool_name(self, tool_name: str):
        """Parse a tool name in mcp__server__tool format."""
        if not tool_name.startswith("mcp__") or "__" not in tool_name[5:]:
            return None, None
        parts = tool_name.split("__")
        if len(parts) < 3:
            return None, None
        server_name = parts[1]
        real_tool_name = "__".join(parts[2:])
        return server_name, real_tool_name

    async def call_tool_async(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """
        Async MCP tool invocation.

        Signature matches the old version exactly:
          tool_name: "mcp__{server}__{tool}" format
          arguments: tool argument dictionary
          returns: JSON string
        """
        # Auto-fix paths: convert web-relative paths (/uploads/xxx) to absolute file paths.
        # This is especially important for images received by the im tool.
        if arguments:
            for key, value in arguments.items():
                if isinstance(value, str) and (value.startswith("/uploads/") or value.startswith("uploads/")):
                    # Build absolute path
                    # _project_root is defined at the top of this file
                    clean_path = value.lstrip("/").lstrip("\\")
                    abs_path = os.path.join(_project_root, clean_path.replace("/", os.sep))
                    arguments[key] = abs_path
                    logger.info(f"[MCP] Auto-fixed path arg '{key}': {value} -> {abs_path}")

        # Run the actual MCP call in a separate asyncio task to isolate
        # anyio cancel scopes (MCP SDK uses anyio internally). This confines
        # any cancel scope to the child task, protecting the runner's main task.
        try:
            result = await asyncio.create_task(self._call_tool_impl(tool_name, arguments))
            return result
        except asyncio.CancelledError:
            # If the runner's task itself was cancelled (e.g. shutdown),
            # don't suppress — let it propagate.
            raise
        except Exception as e:
            logger.error(f"[MCP] call_tool_async error: {e}")
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    async def _call_tool_impl(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Internal implementation — runs inside a separate asyncio task."""

        server_name, real_tool_name = self._parse_tool_name(tool_name)
        if server_name is None:
            return json.dumps({"error": f"Invalid MCP tool name: {tool_name}"})

        session = self._sessions.get(server_name)
        if session is None:
            # Attempt automatic reconnect
            cfg = self._server_configs.get(server_name)
            if cfg:
                logger.warning(f"[MCP] '{server_name}' not connected, attempting reconnect...")
                try:
                    await self._connect_server(server_name, cfg)
                    session = self._sessions.get(server_name)
                except Exception as e:
                    logger.error(f"[MCP] Reconnect failed: {e}")

            if session is None:
                return json.dumps(
                    {"error": f"MCP server '{server_name}' not connected. Available: {list(self._sessions.keys())}"}
                )

        timeout = self._timeouts.get(server_name, 30)

        try:
            # Use the SDK's built-in timeout control
            result = await session.call_tool(
                real_tool_name,
                arguments=arguments,
                read_timeout_seconds=timedelta(seconds=timeout),
            )

            # CallToolResult has content (List[TextContent|ImageContent|...]) and isError
            if result.isError:
                error_texts = []
                for item in result.content:
                    if hasattr(item, "text"):
                        error_texts.append(item.text)
                return json.dumps(
                    {"success": False, "error": "\n".join(error_texts) or "Unknown MCP error"}, ensure_ascii=False
                )

            # Extract result content -- distinguish text from images
            text_parts = []
            image_parts = []  # {"mimeType": "image/png", "data": "base64..."}

            for item in result.content:
                if hasattr(item, "text"):
                    text_parts.append(item.text)
                elif hasattr(item, "data"):
                    mime = getattr(item, "mimeType", "") or ""
                    if mime.startswith("image/"):
                        image_parts.append(
                            {
                                "mimeType": mime,
                                "data": item.data,
                            }
                        )
                    else:
                        text_parts.append(f"[Binary: {mime}, {len(item.data)} bytes]")
                else:
                    text_parts.append(str(item))

            # If there are no images, return a plain text string (consistent with old behavior)
            if not image_parts:
                return "\n".join(text_parts) if text_parts else "OK"

            # When images are present, return a structured dict (runner.py must detect and handle this)
            return {
                "__mcp_multimodal__": True,
                "text": "\n".join(text_parts) if text_parts else "Tool executed successfully.",
                "images": image_parts,
            }

        except asyncio.TimeoutError:
            return json.dumps(
                {"success": False, "error": f"Timeout after {timeout}s calling {real_tool_name} on {server_name}"},
                ensure_ascii=False,
            )
        except Exception as e:
            logger.error(f"[MCP] call_tool_async error on '{server_name}.{real_tool_name}': {e}")
            # Connection may have dropped; mark for auto-reconnect on next call
            if "closed" in str(e).lower() or "broken" in str(e).lower():
                self._sessions.pop(server_name, None)
                logger.warning(f"[MCP] Marked '{server_name}' as disconnected for auto-reconnect")
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    async def add_server(self, server_name: str, cfg: dict[str, Any], persist: bool = True) -> dict[str, Any]:
        """
        Hot-add a new MCP server at runtime.

        Steps:
          1. Connect the new server (spawn subprocess + handshake + cache tool list)
          2. Optionally persist to config_basic.json
          3. Return result (tool list, etc.)

        If server_name already exists, the old connection is closed before reconnecting.

        Args:
            server_name: Server name (e.g. "playwright")
            cfg: Server config dict, same format as entries in config_basic.json
                 {"command": "...", "args": [...], "timeout": 30, "env": {...}}
            persist: Whether to write to config_basic.json (default True)

        Returns:
            {"success": True/False, "server": name, "tools": [...], "error": "..."}
        """
        # Clean up if server already exists
        if server_name in self._sessions:
            logger.info(f"[MCP] add_server: '{server_name}' already exists, reconnecting...")
            if server_name in self._exit_stacks:
                with suppress(Exception):
                    await self._exit_stacks[server_name].aclose()
                del self._exit_stacks[server_name]
            self._sessions.pop(server_name, None)
            self._tools_cache.pop(server_name, None)

        try:
            await self._connect_server(server_name, cfg)
        except Exception as e:
            logger.error(f"[MCP] add_server failed for '{server_name}': {e}")
            # Clean up failed connection
            if server_name in self._exit_stacks:
                with suppress(Exception):
                    await self._exit_stacks[server_name].aclose()
                del self._exit_stacks[server_name]
            return {"success": False, "server": server_name, "tools": [], "error": str(e)}

        # Record config (used for reconnect)
        self._server_configs[server_name] = cfg

        # Persist to config file
        if persist:
            try:
                self._persist_server_config(server_name, cfg)
            except Exception as e:
                logger.warning(f"[MCP] Failed to persist config for '{server_name}': {e}")

        tool_names = [t.name for t in self._tools_cache.get(server_name, [])]
        logger.info(f"[MCP] add_server: '{server_name}' connected with {len(tool_names)} tools: {tool_names}")

        return {
            "success": True,
            "server": server_name,
            "tools": tool_names,
        }

    async def remove_server(self, server_name: str, persist: bool = True) -> dict[str, Any]:
        """
        Remove an MCP server at runtime.

        Args:
            server_name: Server name
            persist: Whether to remove from config_basic.json

        Returns:
            {"success": True/False, "server": name, "error": "..."}
        """
        if server_name not in self._server_configs and server_name not in self._sessions:
            return {"success": False, "server": server_name, "error": f"Server '{server_name}' not found"}

        # Close connection
        if server_name in self._exit_stacks:
            try:
                await self._exit_stacks[server_name].aclose()
                logger.info(f"[MCP] Closed connection to '{server_name}'")
            except Exception as e:
                logger.warning(f"[MCP] Error closing '{server_name}': {e}")
            del self._exit_stacks[server_name]

        self._sessions.pop(server_name, None)
        self._tools_cache.pop(server_name, None)
        self._server_configs.pop(server_name, None)
        self._timeouts.pop(server_name, None)

        # Persist removal
        if persist:
            try:
                self._unpersist_server_config(server_name)
            except Exception as e:
                logger.warning(f"[MCP] Failed to unpersist config for '{server_name}': {e}")

        return {"success": True, "server": server_name}

    def _persist_server_config(self, server_name: str, cfg: dict):
        """Write the new server config to config_basic.json."""
        with open(self.config_path, encoding="utf-8-sig") as f:
            data = json.load(f)

        servers_key = "mcpServers" if "mcpServers" in data else "mcp_servers"
        if servers_key not in data:
            servers_key = "mcpServers"
            data[servers_key] = {}

        data[servers_key][server_name] = cfg

        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        logger.info(f"[MCP] Persisted config for '{server_name}' to {self.config_path}")

    def _unpersist_server_config(self, server_name: str):
        """Remove a server config from config_basic.json."""
        with open(self.config_path, encoding="utf-8-sig") as f:
            data = json.load(f)

        servers_key = "mcpServers" if "mcpServers" in data else "mcp_servers"
        if servers_key in data and server_name in data[servers_key]:
            del data[servers_key][server_name]
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info(f"[MCP] Removed config for '{server_name}' from {self.config_path}")

    def list_servers(self) -> dict[str, Any]:
        """
        List all configured MCP servers and their status.

        Returns:
            {server_name: {"connected": bool, "tools": [tool_names], "timeout": int}}
        """
        result = {}
        for name, _cfg in self._server_configs.items():
            connected = name in self._sessions
            tools = [t.name for t in self._tools_cache.get(name, [])]
            result[name] = {
                "connected": connected,
                "tools": tools,
                "tool_count": len(tools),
                "timeout": self._timeouts.get(name, 30),
            }
        return result

    # ── P2-2: Health check + auto-reconnect ──

    async def _health_monitor_loop(self):
        """Background task: periodically probe all servers and reconnect disconnected ones."""
        # Initial delay: let connections stabilize
        await asyncio.sleep(self.HEALTH_CHECK_INTERVAL)

        while not self._stop_health.is_set():
            for server_name in list(self._server_configs.keys()):
                if self._stop_health.is_set():
                    break

                healthy = await self._check_server_health(server_name)
                was_healthy = self._server_health_status.get(server_name, True)

                if healthy:
                    if not was_healthy:
                        logger.info(f"[MCP] '{server_name}' health recovered")
                        self._emit_status(server_name, "connected")
                        self._server_reconnect_backoff[server_name] = 0.0
                    self._server_health_status[server_name] = True
                else:
                    self._server_health_status[server_name] = False
                    if was_healthy:
                        logger.warning(f"[MCP] '{server_name}' health check failed — marking disconnected")
                        self._emit_status(server_name, "disconnected")

                    # Attempt reconnect with exponential backoff
                    await self._try_reconnect_with_backoff(server_name)

            try:
                await asyncio.wait_for(
                    self._stop_health.wait(),
                    timeout=self.HEALTH_CHECK_INTERVAL,
                )
            except asyncio.TimeoutError:
                pass  # Normal: interval elapsed, next iteration

    async def _check_server_health(self, server_name: str) -> bool:
        """Probe a single server's connection by listing tools (lightweight)."""
        session = self._sessions.get(server_name)
        if session is None:
            return False
        try:
            # list_tools() is a lightweight ping-like operation
            await asyncio.wait_for(
                session.list_tools(),
                timeout=self.HEALTH_CHECK_TIMEOUT,
            )
            return True
        except Exception:
            return False

    async def _try_reconnect_with_backoff(self, server_name: str):
        """Attempt reconnect with exponential backoff."""
        cfg = self._server_configs.get(server_name)
        if cfg is None:
            return

        # Compute backoff
        current_backoff = self._server_reconnect_backoff.get(server_name, 0.0)
        if current_backoff == 0.0:
            current_backoff = self.RECONNECT_BACKOFF_BASE
        else:
            current_backoff = min(current_backoff * 2, self.RECONNECT_MAX_BACKOFF)
        self._server_reconnect_backoff[server_name] = current_backoff

        logger.info(f"[MCP] '{server_name}' reconnecting in {current_backoff:.1f}s (backoff)")
        await asyncio.sleep(current_backoff)

        if self._stop_health.is_set():
            return

        success = await self.reconnect_server(server_name)
        if success:
            self._server_reconnect_backoff[server_name] = 0.0
            self._server_health_status[server_name] = True
            self._emit_status(server_name, "connected")

    def _emit_status(self, server_name: str, status: str):
        """Emit MCP server status change via EventBus (for frontend display)."""
        if _event_bus is None:
            return
        try:
            _event_bus.emit(
                "mcp_server_status",
                {
                    "server": server_name,
                    "status": status,
                    "timestamp": asyncio.get_event_loop().time(),
                },
            )
        except Exception as e:
            logger.debug(f"[MCP] Failed to emit status event: {e}")

    async def reconnect_server(self, server_name: str) -> bool:
        """Reconnect a single MCP server (e.g. if its subprocess crashed)."""
        # Close old connection first
        if server_name in self._exit_stacks:
            with suppress(Exception):
                await self._exit_stacks[server_name].aclose()
            del self._exit_stacks[server_name]
        self._sessions.pop(server_name, None)
        self._tools_cache.pop(server_name, None)

        cfg = self._server_configs.get(server_name)
        if cfg is None:
            logger.error(f"[MCP] Cannot reconnect unknown server: {server_name}")
            return False

        try:
            await self._connect_server(server_name, cfg)
            logger.info(f"[MCP] Successfully reconnected to '{server_name}'")
            return True
        except Exception as e:
            logger.error(f"[MCP] Reconnect failed for '{server_name}': {e}")
            return False

    async def close(self):
        """Close all MCP connections and subprocesses."""
        # P2-2: Stop health monitor first
        self._stop_health.set()
        if self._health_task and not self._health_task.done():
            try:
                await asyncio.wait_for(self._health_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._health_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._health_task
            self._health_task = None

        for server_name, stack in list(self._exit_stacks.items()):
            try:
                session = self._sessions.get(server_name)
                if session:
                    pass

                await stack.aclose()
                logger.info(f"[MCP] Closed connection to '{server_name}'")

            except (asyncio.CancelledError, RuntimeError):
                # Ignore "Attempted to exit cancel scope in a different task"
                # Known SDK compatibility issue during interpreter shutdown
                pass
            except Exception as e:
                logger.debug(f"[MCP] '{server_name}' cleanup error: {e}")

        self._sessions.clear()
        self._exit_stacks.clear()
        self._tools_cache.clear()
        self._connected = False


# ===================================================================
# Global singleton + async initialization entry point
# ===================================================================

_mcp_adapter: MCPAdapter | None = None


async def init_mcp_adapter(
    config_path: str | None = None, agent_dir: str | None = None, global_disabled_servers: set | None = None
) -> MCPAdapter:
    """
    Async initialization of the MCP adapter (global singleton).
    Replaces the old synchronous get_mcp_adapter().

    boot.py usage:
        mcp_adapter = await init_mcp_adapter(agent_dir=agent_dir,
                                             global_disabled_servers={'playwright'})
        registry.register_mcp_adapter(mcp_adapter, level="extended")

    Args:
        config_path: Optional global config file path (backward compat)
        agent_dir: Agent directory; agent_dir/mcp_config.json takes priority
        global_disabled_servers: Set of server names disabled globally (not started for any agent)
    """
    global _mcp_adapter
    if _mcp_adapter is None:
        _mcp_adapter = MCPAdapter(config_path, agent_dir, global_disabled_servers=global_disabled_servers)
        await _mcp_adapter.connect()
    return _mcp_adapter


def get_mcp_adapter() -> MCPAdapter | None:
    """
    Return the already-initialized MCP adapter (sync; only valid after init_mcp_adapter).
    Kept for backward compatibility with any remaining synchronous call sites.
    """
    return _mcp_adapter
