# Agent Dynamic MCP Server Management Guide

OpenSquad Agents support **restart-free** dynamic install, configuration, and startup of MCP servers.

## 1. Adding an MCP Server Dynamically

### Method: Use the `mcp_query.add_server` tool

In a conversation, an Agent can call this tool to add a new MCP server on the fly:

```xml
<tool_call>
  <name>mcp_query.add_server</name>
  <arguments>
    {
      "server_name": "filesystem",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:/Users/data"],
      "timeout": 60,
      "auto_approve": ["read_file", "list_directory"]
    }
  </arguments>
</tool_call>
```

### Parameter Reference

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `server_name` | string | ✅ | Server name (unique identifier) |
| `command` | string | ✅ | Startup command (e.g. `npx`, `python`, `node`) |
| `args` | list | ✅ | Argument array for the command |
| `timeout` | int | ❌ | Timeout in seconds; default 30 |
| `auto_approve` | list | ❌ | List of tool names to auto-approve |
| `env` | dict | ❌ | Environment variable dictionary |

### Example Scenarios

#### 1. Add the filesystem MCP
```json
{
  "server_name": "my-filesystem",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/docs"],
  "timeout": 60
}
```

#### 2. Add the SQLite MCP
```json
{
  "server_name": "my-database",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-sqlite", "/path/to/db.sqlite"],
  "timeout": 30
}
```

#### 3. Add a custom Python MCP
```json
{
  "server_name": "custom-mcp",
  "command": "python",
  "args": ["/path/to/my_mcp_server.py"],
  "env": {"API_KEY": "xxx"}
}
```

## 2. How Changes Take Effect

### No restart required!

After calling `add_server`:

1. **Immediate connection** — the MCP server process is started
2. **Tool registration** — new tools automatically enter the tool list
3. **Persistent storage** — the config is written to `pymcp/config_basic.json`
4. **Immediately available** — the Agent can use the new tools in the current conversation

### Two Ways to Use the New Tools

#### Method 1: Auto-recognition by the Agent

The Agent's tool list is generated dynamically; new MCP tools automatically appear in the format `mcp__{server_name}__{tool_name}`:

```xml
<tool_call>
  <name>mcp__my-filesystem__read_file</name>
  <arguments>{"path": "/home/user/docs/readme.md"}</arguments>
</tool_call>
```

#### Method 2: Query available tools

Call `mcp_query.get_all_tools()` to see every available MCP tool.

## 3. Managing MCP Servers

### List all servers
```xml
<tool_call>
  <name>mcp_query.list_servers</name>
  <arguments>{}</arguments>
</tool_call>
```

Returns:
```json
{
  "status": "success",
  "servers": {
    "chrome-devtools": {
      "connected": true,
      "tools": ["click", "navigate_page", ...],
      "tool_count": 26
    },
    "my-filesystem": {
      "connected": true,
      "tools": ["read_file", "write_file", ...],
      "tool_count": 8
    }
  },
  "total": 2
}
```

### Remove a server
```xml
<tool_call>
  <name>mcp_query.remove_server</name>
  <arguments>{"server_name": "my-filesystem"}</arguments>
</tool_call>
```

Disconnects immediately and removes the configuration.

### Reconnect
```xml
<tool_call>
  <name>mcp_query.reconnect_server</name>
  <arguments>{"server_name": "chrome-devtools"}</arguments>
</tool_call>
```

### Reload configuration

If you hand-edited `pymcp/config_basic.json`:

```xml
<tool_call>
  <name>mcp_query.reload_servers</name>
  <arguments>{}</arguments>
</tool_call>
```

## 4. Configuration File Location

Even though the Agent manages MCP dynamically, the configuration is actually stored at:

**`pymcp/config_basic.json`**

```json
{
  "mcpServers": {
    "my-filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/docs"],
      "timeout": 60,
      "autoApprove": ["read_file", "list_directory"]
    }
  }
}
```

## 5. Recommended MCP Servers

| MCP server | Install command | Function |
|------------|-----------------|----------|
| **filesystem** | `@modelcontextprotocol/server-filesystem` | Read/write files |
| **sqlite** | `@modelcontextprotocol/server-sqlite` | SQLite database |
| **github** | `@modelcontextprotocol/server-github` | GitHub API |
| **postgres** | `@modelcontextprotocol/server-postgres` | PostgreSQL database |
| **puppeteer** | `@modelcontextprotocol/server-puppeteer` | Browser automation |
| **sequential-thinking** | `@langgpt/sequential-thinking-mcp` | Chain-of-thought |

## 6. Troubleshooting

### Server failed to connect
1. Verify the command is installed: `npx -v` or `python --version`
2. Check detailed errors: `mcp_query.list_servers()`
3. Try reconnecting: `mcp_query.reconnect_server()`

### Tools not taking effect
Make sure the system has finished updating the tool list before invoking the new tools (usually 1–2 seconds).

### Port conflict
If an MCP server needs a port, the system will automatically find an available one.

---

**Summary:** OpenSquad's MCP management is fully dynamic — install → configure → start → use, no Agent restart needed at any step!
