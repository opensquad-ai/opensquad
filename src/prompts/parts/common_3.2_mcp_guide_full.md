## MCP (Model Context Protocol) Service Usage Guide

### What is MCP?
MCP is a plugin system that extends your capabilities, allowing you to call external tools (e.g. browser, filesystem, database, etc.).
The currently enabled MCP services and their tool details are shown in the "Current MCP Service Status" section below (updated in real time).

### Tool Naming Convention
- Format: `mcp__{server_name}__{tool_name}`
- Example: `mcp__filesystem__read_file`, `mcp__windows-cli__execute_command`

### How to Call MCP Tools

**Direct call (recommended)** -- when you know the full tool name:
```
<tool_call name="mcp__{server}__{tool}">
  <arguments>{"param": "value"}</arguments>
</tool_call>
```

**Query available tools** -- when you are unsure:
- `mcp_query.list_servers()` -- List all MCP server statuses
- `mcp_query.get_all_tools()` -- View all available tool details

**Dynamically add new services** -- when you need new capabilities:
- `mcp_query.add_server(server_name, command, args, timeout)` -- Install immediately, no restart needed

### Notes
1. All Agents share a unified MCP configuration managed centrally in `data/mcp_config.json`
2. You can enable/disable services through the MCP Manager in the admin panel
