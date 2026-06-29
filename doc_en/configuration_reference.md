# Configuration Reference Manual

This document lists all OpenSquad configuration items and their descriptions.

---

## Configuration File Locations

| File | Purpose |
|------|------|
| `system_config.json` | System-level configuration (workspace root) |
| `src/agents/<name>/config.json` | Agent configuration |
| `src/model_cards/*.json` | Model card configuration |

> Configuration priority: **environment variables > system_config.json > hard-coded defaults**

---

## system_config.json

The configuration file is located in the workspace root directory. It is automatically created from `system_config.example.json` on first launch.

### ports — Service Ports

```json
{
  "ports": {
    "gateway": 9555,
    "launcher": 9600,
    "external_adapter": 9700,
    "whisper": 5001,
    "websearch": 9001,
    "agent_web_server": 8001,
    "legacy_server": 8000,
    "frontend": 9530
  }
}
```

| Field | Type | Default | Env Variable | Description |
|------|------|--------|----------|------|
| `gateway` | int | 9555 | `GATEWAY_PORT` | Gateway backend port |
| `launcher` | int | 9600 | `LAUNCHER_PORT` | Agent management port |
| `external_adapter` | int | 9700 | `EXTERNAL_ADAPTER_PORT` | External adapter port |
| `whisper` | int | 5001 | `WHISPER_PORT` | Speech-to-text service port |
| `websearch` | int | 9001 | `WEBSEARCH_PORT` | Web search service port |
| `agent_web_server` | int | 8001 | `AGENT_WEB_SERVER_PORT` | Agent web service port |
| `legacy_server` | int | 8000 | `LEGACY_SERVER_PORT` | Legacy server port |
| `frontend` | int | 9530 | `FRONTEND_PORT` | Frontend dev server port |

### hosts — Service Bind Addresses

```json
{
  "hosts": {
    "gateway": "127.0.0.1",
    "launcher": "127.0.0.1",
    "external_adapter": "0.0.0.0",
    "whisper": "0.0.0.0",
    "legacy_server": "0.0.0.0",
    "frontend": "0.0.0.0"
  }
}
```

| Field | Type | Default | Env Variable | Description |
|------|------|--------|----------|------|
| `gateway` | string | 127.0.0.1 | `GATEWAY_HOST` | Gateway bind address |
| `launcher` | string | 127.0.0.1 | `LAUNCHER_HOST` | Launcher bind address |
| `external_adapter` | string | 0.0.0.0 | `EXTERNAL_ADAPTER_HOST` | External adapter address |

> **Note**: When deploying with Docker, `gateway` is automatically set to `0.0.0.0` to allow external access.

### auth — Authentication

```json
{
  "auth": {
    "gateway_token": "",
    "external_api_key": "",
    "node_secret": ""
  }
}
```

| Field | Type | Default | Env Variable | Description |
|------|------|--------|----------|------|
| `gateway_token` | string | "" | `GATEWAY_TOKEN` | Gateway access token |
| `external_api_key` | string | auto-generated | `EXTERNAL_API_KEY` | External API access key |
| `node_secret` | string | "" | `NODE_SECRET` | Multi-node communication key |

### node — Node Identity

```json
{
  "node": {
    "id": "node-local",
    "label": "",
    "register_to_gateway": false,
    "launcher_url": ""
  }
}
```

| Field | Type | Default | Env Variable | Description |
|------|------|--------|----------|------|
| `id` | string | auto-generated | `NODE_ID` | Unique node ID |
| `label` | string | "" | `NODE_LABEL` | Node display name |
| `register_to_gateway` | bool | false | `NODE_REGISTER_TO_GATEWAY` | Auto-register to Gateway |
| `launcher_url` | string | "" | `NODE_LAUNCHER_URL` | Cross-machine Launcher URL |

### defaults — Default Parameters

```json
{
  "defaults": {
    "agent_id": "default-001",
    "request_timeout": 120,
    "async_result_ttl": 600
  }
}
```

| Field | Type | Default | Description |
|------|------|--------|------|
| `agent_id` | string | default-001 | Default Agent ID |
| `request_timeout` | int | 120 | HTTP request timeout (seconds) |
| `async_result_ttl` | int | 600 | Async result cache TTL (seconds) |

### logging — Logging Configuration

```json
{
  "logging": {
    "log_level": "INFO",
    "log_format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    "log_date_format": "%Y-%m-%d %H:%M:%S",
    "max_size_mb": 3,
    "backup_count": 5,
    "tool_call_debug": false,
    "tool_call_debug_max_size_mb": 5,
    "tool_call_debug_backup_count": 3
  }
}
```

| Field | Type | Default | Env Variable | Description |
|------|------|--------|----------|------|
| `log_level` | string | INFO | `LOG_LEVEL` | Log level (DEBUG/INFO/WARNING/ERROR) |
| `max_size_mb` | int | 3 | `LOG_MAX_SIZE_MB` | Max log file size (MB) |
| `backup_count` | int | 5 | `LOG_BACKUP_COUNT` | Log backup count |
| `tool_call_debug` | bool | false | `TOOL_CALL_DEBUG` | Enable tool call debug logging |

### context_compression — Context Compression

```json
{
  "context_compression": {
    "trigger_threshold": 0.75,
    "keep_recent_fraction": 0.1,
    "recent_hard_cap_fraction": 0.30,
    "keep_recent_rounds": 2,
    "summary_max_tokens": 4000,
    "conv_text_budget_chars": 24000
  }
}
```

| Field | Type | Default | Env Variable | Description |
|------|------|--------|----------|------|
| `trigger_threshold` | float | 0.75 | `CTX_TRIGGER_THRESHOLD` | Compression trigger threshold (fraction of token_max) |
| `keep_recent_fraction` | float | 0.10 | `CTX_KEEP_RECENT_FRAC` | Fraction of recent tokens to keep after compression |
| `recent_hard_cap_fraction` | float | 0.30 | `CTX_RECENT_HARD_CAP_FRAC` | Max fraction of current tokens the recent (unsummarized) section may occupy; exceeding it drops user-anchor/rounds protection and sends the excess to the summary, preventing compression from degrading to a no-op during long autonomous tool-calling runs |
| `summary_max_tokens` | int | 4000 | `CTX_SUMMARY_MAX_TOKENS` | Max tokens for summary generation |
| `conv_text_budget_chars` | int | 24000 | `CTX_CONV_TEXT_BUDGET_CHARS` | Conversation text budget for summarization |

### vcs — Version Control

```json
{
  "vcs": {
    "git_server": "",
    "default_remote": "origin",
    "default_branch": "main"
  }
}
```

| Field | Type | Default | Description |
|------|------|--------|------|
| `git_server` | string | "" | Git server base URL |
| `default_remote` | string | origin | Default remote name |
| `default_branch` | string | main | Default branch name |

### filesystem — Filesystem Whitelist

```json
{
  "filesystem": {
    "workspace_dirs": ["/data/projects", "../shared"]
  }
}
```

| Field | Type | Description |
|------|------|------|
| `workspace_dirs` | string[] | Global filesystem whitelist; all Agents can access these directories |

### services — Service Toggles

```json
{
  "services": {
    "feishu": { "enabled": false },
    "telegram": { "enabled": false },
    "qq": { "enabled": false }
  }
}
```

Controls enabling/disabling of IM platform integration plugins.

### github — GitHub Integration

```json
{
  "github": {
    "plugins_token": ""
  }
}
```

| Field | Type | Description |
|------|------|------|
| `plugins_token` | string | GitHub token for the plugin marketplace |

---

## Agent config.json

Each Agent is defined in `src/agents/<name>/config.json`.

### Full Example

```json
{
  "agent_id": "default-001",
  "agent_name": "Default",
  "agent_type": "general",
  "description": "General-purpose AI assistant",
  "model": {
    "provider": "openai_compat",
    "api_key": "",
    "base_url": "https://api.deepseek.com",
    "model_name": "deepseek-v4-flash",
    "token_max": 128000,
    "temperature": 0.7,
    "frequency_penalty": 0,
    "presence_penalty": 0,
    "top_k": 0,
    "is_think": true,
    "is_image": false,
    "is_audio": false,
    "is_video": false,
    "tool_call_mode": "auto",
    "tool_filter": "high",
    "_card": "deepseek-v4-flash"
  },
  "tools": [
    "system", "filesystem", "agent_setup", "im",
    "collaboration", "delegate_task", "workspace", "task_watch",
    "websearch", "reminder", "vision", "mcp_query", "plugin_admin"
  ],
  "group_chat": { "enabled": false },
  "web_server": { "enabled": true },
  "gateway": { "enabled": true, "url": "" },
  "prompt": { "role": "role.md" },
  "mcp": { "enabled": true },
  "skills": {
    "enabled": true,
    "active": []
  }
}
```

### Top-Level Fields

| Field | Type | Required | Description |
|------|------|------|------|
| `agent_id` | string | Yes | Unique Agent identifier |
| `agent_name` | string | Yes | Agent display name |
| `agent_type` | string | No | Agent type: `general`/`specialized` |
| `description` | string | No | Agent description |

### model — Model Configuration

| Field | Type | Required | Description |
|------|------|------|------|
| `provider` | string | Yes | Interface protocol: `openai`/`openai_compat`/`anthropic`/`google` |
| `model_name` | string | Yes | Model name |
| `base_url` | string | Yes | API base URL |
| `api_key` | string | Yes | API key |
| `token_max` | int | No | Max context tokens, default 128000 |
| `temperature` | float | No | Sampling temperature, default 0.7 |
| `frequency_penalty` | float | No | Frequency penalty |
| `presence_penalty` | float | No | Presence penalty |
| `top_k` | int | No | Top-K sampling |
| `is_think` | bool | No | Enable think mode |
| `is_image` | bool | No | Support image input |
| `is_audio` | bool | No | Support audio input |
| `is_video` | bool | No | Support video input |
| `tool_call_mode` | string | No | Tool call mode: `auto`/`native`/`xml` |
| `tool_filter` | string | No | Tool filter level: `high`/`medium`/`low` |
| `_card` | string | No | Referenced model card name |

### tools — Enabled Tools

Tool list, available values:

| Tool | Description |
|--------|------|
| `system` | System command execution |
| `filesystem` | File read/write |
| `agent_setup` | Agent configuration management |
| `im` | Instant messaging |
| `collaboration` | Multi-Agent collaboration |
| `delegate_task` | Task delegation |
| `workspace` | Workspace management |
| `task_watch` | Task monitoring |
| `websearch` | Web search |
| `reminder` | Reminders |
| `vision` | Image recognition |
| `mcp_query` | MCP protocol queries |
| `plugin_admin` | Plugin administration |
| `web` | HTTP requests |
| `long_memory` | Long-term memory |

### group_chat — Group Chat

| Field | Type | Description |
|------|------|------|
| `enabled` | bool | Enable group chat |
| `email` | string | Group chat account email |
| `password` | string | Group chat account password |
| `groups` | string[] | Group chat ID list |

### skills — Skills

| Field | Type | Description |
|------|------|------|
| `enabled` | bool | Enable the skills system |
| `active` | string[] | List of activated private skills |

### prompt — Prompt Configuration

| Field | Type | Description |
|------|------|------|
| `role` | string | Path to role card file (relative to Agent directory) |

### gateway — Gateway

| Field | Type | Description |
|------|------|------|
| `enabled` | bool | Connect to Gateway |
| `url` | string | Gateway registration URL, auto-filled by default |

### web_server — Web Server

| Field | Type | Description |
|------|------|------|
| `enabled` | bool | Enable Agent web service |

### mcp — MCP Protocol

| Field | Type | Description |
|------|------|------|
| `enabled` | bool | Enable MCP protocol support |

---

## Agent Directory Structure

```
<workspace>/agents/{agent_id}/
├── config.json         # Main configuration (model, tools, permissions)
├── role.md             # System prompt / role definition
├── agent.md            # Long-term memory document (persistent)
├── data/               # Agent data storage
│   ├── sessions/       # Session history (JSON logs)
│   └── profile.json    # UI display info (name, avatar, description)
└── mcp_config.json     # (Optional) Static MCP service config
```

> **Note**: `MANDATORY_TOOLS` (system, filesystem, agent_setup, im, collaboration, delegate_task, workspace, task_watch) are always registered regardless of the `tools` list.

### Display Info (profile.json)

`data/profile.json` defines how the Agent appears in Web UI:

```json
{
  "name": "PM Agent",
  "avatar": "https://api.dicebear.com/7.x/bottts-neutral/svg?seed=pm",
  "description": "Project coordination and team management"
}
```

### Memory Management

- **Role prompt (`role.md`)**: Core identity definition, unchanged during sessions
- **Long-term memory (`agent.md`)**: Markdown file the Agent can read/write, injected every turn
- **Session history**: Stored in `agents/{agent_id}/data/sessions/`, JSON log format

### Creating a New Agent

1. **Copy**: Copy an existing Agent folder (e.g. `<workspace>/agents/coder`)
2. **Rename**: Change the folder name and `agent_id` in `config.json`
3. **Configure**: Update `model` and `tools` for the new Agent's purpose
4. **Define Role**: Edit `role.md` to give the Agent a new persona or specialized instructions
5. **Restart**: Launch the Agent via Launcher

---

## API Reference

### System Configuration API

Access system configuration via the `opensquad.system_config` module:

```python
from opensquad.system_config import syscfg

# Ports / Addresses / URLs
gateway_port = syscfg.port("gateway")
launcher_port = syscfg.port("launcher")
gateway_http  = syscfg.gateway_http()        # "http://127.0.0.1:9555"
gateway_ws    = syscfg.gateway_ws()           # "ws://127.0.0.1:9555"
api_key       = syscfg.auth("external_api_key")

# Generic config reading
value = syscfg.get("feishu", "app_id")

# Workspace paths
workspace = syscfg.get_workspace()
data_dir  = syscfg.workspace_data_dir("uploads")
logs_dir  = syscfg.workspace_logs_dir()
```

### WebSocket API

**Connection**: `ws://127.0.0.1:9555/ai-ws/chat`

**Sending a chat message**:
```json
{
  "type": "chat",
  "agent_id": "default-001",
  "session_id": "session-xxx",
  "content": "Hello",
  "attachments": []
}
```

**System commands**:

| Command | Description |
|------|------|
| `new_session` | Create a new session |
| `stop_task` | Stop the current task |
| `compress_context` | Manually compress context |
| `switch_and_reply` | Switch session and reply |
| `request_token_stats` | Request token statistics |

### Event Types

| Event | Description |
|------|------|
| `chat_message` | New chat message |
| `tool_call` | Tool invocation |
| `tool_result` | Tool invocation result |
| `agent_status` | Agent status change |
| `task_progress` | Task progress update |
| `session_created` | Session created |
| `session_deleted` | Session deleted |

### External API (Port 9700)

```http
POST /api/chat
Content-Type: application/json
X-API-Key: your-api-key

{
  "agent_id": "default-001",
  "message": "Hello",
  "user_id": "feishu_user_xxx",
  "channel": "feishu_private"
}
```
