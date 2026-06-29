# OpenSquad Architecture

## Overview

OpenSquad is a multi-agent framework where each agent runs as an independent process with its own LLM connection, tool set, and session state. Agents communicate via a shared group chat (ChatPro) and coordinate through Collab Card-defined workflows.

```
                   Telegram / Feishu / External API
                              |
                        Gateway (9555)
                       /      |      \
                  WebSocket  HTTP   Proxy
                  (real-time) (REST) (-> Launcher)
                      |       |        |
                 Frontend  Backend   Launcher (9600)
                 (9530)    (FastAPI)   |
                                    Agent processes
                                   /    |    \
                               PM    Coder   QA
                              (8003) (8002) (8006)
                                \     |     /
                             ChatPro Group Chat
```

---

## Core Modules (`src/opensquad/`)

### Agent Runtime

| Module | Responsibility |
|--------|---------------|
| `runner.py` | Main agent loop. Receives input, builds prompt (with placeholders), calls LLM, parses response, dispatches tool calls, handles streaming output. Key function: `_setup_prompt()` at line ~2731. |
| `parser.py` | Parses LLM output into structured events: `<tool_call>`, `<thought>`, `<plan>`, `<state>`, `<to_user>`, `<sleep>`, etc. |
| `registry.py` | Tool registry. `register()` to add tools, `dispatch()` to call them, `generate_tool_descriptions()` to render tool docs into prompts. |
| `input_hub.py` | Unified input queue. All sources (Web, CLI, group chat, timer) feed into a single async queue. Agent reads from this. |
| `state_manager.py` | Agent state machine: `idle` / `working` / `sleeping`. Controls when agent accepts new input. |
| `sleep_controller.py` | Interruptible sleep. Agent can `<sleep seconds="120">` and be woken by new messages. |

### Session & Memory

| Module | Responsibility |
|--------|---------------|
| `session_manager.py` | Manages conversation sessions. Saves/loads session history to JSON files in `agents/{name}/data/sessions/`. |
| `context_base.py` | Standard context injection via `inject_standard()`. Provides system-level vars (`AGENT_PROFILE`, `CONTEXT_SUMMARY`, `AGENT_WORKSPACE`, `TEAM_COLLAB_CARDS`) and dynamic vars (`RUNTIME_STATE`, `MEMORY_CONTEXT`). |
| `memory_manager.py` | Long-term memory abstraction layer. Wraps `agent_memory_tool` for semantic search/recall across sessions. |
| `task_logger.py` | Task lifecycle logger. Records task start/end/turns to JSON files in `agents/{name}/data/tasks/`. |

### Communication

| Module | Responsibility |
|--------|---------------|
| `bridge.py` | `ChatProBridge` class. Handles login, group join, WebSocket connection to ChatPro for real-time group messaging. |
| `message_router.py` | Routes incoming group chat messages. Filters self-messages, detects @mentions, routes to input queue. |
| `message_queue.py` | Internal message queue for cross-component communication. |
| `gateway_adapter.py` | Agent-side adapter for Gateway WebSocket connection (AI Web channel). |

### Skills & Plugins

| Module | Responsibility |
|--------|---------------|
| `skill_loader.py` | Loads SKILL.md files (YAML frontmatter + Markdown). Manages Task Board (`_task_board` dict), auto-syncs to `workspace/collab/`. |
| `plugin_api.py` | Plugin decorator API: `@register`, `@on_event`, `@tool`, etc. Defines `ToolPlugin`, `HookPlugin`, `AdapterPlugin` base concepts. |
| `sdk.py` | Public SDK surface for plugins to interact with the agent runtime. |

---

## Built-in Tools (`src/opensquad/tools/`)

| Module | Tools | Registration |
|--------|-------|-------------|
| `filesystem.py` | `read_file`, `write_file`, `list_directory`, `search_files`, etc. | Listed in agent `config.json > tools` |
| `im.py` | `send_message`, `get_history`, `list_groups`, `join_group` | Listed in config |
| `collaboration.py` | `start_collaboration`, `join_collaboration`, `end_collaboration`, `leave_collaboration`, `get_team_status` | Listed in config |
| `agent_setup.py` | `load_task_context`, `unload_task_context`, `list_installed`, `install_skill`, `get_skill_info` | Listed in config |
| `system.py` | `get_time`, `run_command`, `get_env_info` | Listed in config |
| `long_memory.py` | `query_memory`, `store_memory` (requires agent_memory_tool) | Listed in config |
| `delegate_task` (`delegate.py`) | `delegate_task` — delegate tasks to sub-agents | Listed in config |
| `workspace.py` | Workspace management tools | Listed in config |
| `task_watch.py` | Task watch/monitoring tools | Listed in config |
| `web.py` | Web-related tools | Listed in config |
| `mcp_adapter.py` | Dynamic MCP tool proxy | Auto-discovered from MCP config |

### Tool Registration Rules
- Built-in tools: agent must list tool module name in `config.json > tools[]`. If not listed, not registered.
- `MANDATORY_TOOLS` (defined in `agents_boot.py`): `system`, `filesystem`, `agent_setup`, `im`, `collaboration`, `delegate_task`, `workspace`, `task_watch` — always registered for every agent regardless of config.
- `CORE_TOOLS` (defined in `agents_boot.py`): `system`, `filesystem`, `im`, `long_memory`, `collaboration` — controls prompt detail level (full signature vs one-line summary), NOT whether tools are available.
- Plugin tools with `auto_register: true` in `plugin.json`: registered for ALL agents automatically.
- Plugin tools with `auto_register: false`: agent must list tool name in `config.json > tools[]`.

---

## Prompt Architecture

The system prompt templates are `src/opensquad/base_fc.md` (Native Function Calling mode) and `src/opensquad/base_xml.md` (XML mode). `runner._setup_prompt()` (line ~2731) selects the appropriate template via `tool_call_strategy.prepare_llm_call()`.

### Template Placeholders

| Placeholder | Source | Content |
|-------------|--------|---------|
| `{{EXPERT_ROLE_CARD}}` | `agents/{name}/role.md` | Agent's role definition |
| `{{MCP_GUIDE}}` | MCP server config | MCP tool usage instructions |
| `{{SKILLS_INSTRUCTIONS}}` | `skill_loader` | Loaded skill instructions |

### Context Variables (injected by `context_base.py`)

**System-level (static, cached):**

| Variable | Source | Content |
|----------|--------|---------|
| `AGENT_PROFILE` | `agents/{name}/agent.md` | Agent's permanent memory |
| `CONTEXT_SUMMARY` | `chat_api._latest_summary` | Context summary (changes on compression) |
| `AGENT_WORKSPACE` | Agent config | Agent working directory path |
| `TEAM_COLLAB_CARDS` | Collab card directory | Collab card table + usage instructions |

**Dynamic (per-turn, injected into user message prefix):**

| Variable | Source | Content |
|----------|--------|---------|
| `RUNTIME_STATE` | System state | Time + source + state + wakeup level |
| `MEMORY_CONTEXT` | `MemoryManager.auto_recall()` | Recalled memories per query |

---

## Gateway Layer (`gateway/`)

### Backend (`gateway/backend/`)
- FastAPI application on port 9555
- Routes: `/api/ai-web/admin/...` proxied to Launcher (9600) at `/api/...`
- WebSocket: `/ai-web/ws/{agentId}?token={token}` for real-time streaming
- Auth: token-based session management
- File uploads: stored in `gateway/backend/uploads/`

### Frontend (`gateway/nexuschat-pro/`)
- React + TypeScript + Vite on port 9530
- Key components: `AIChatPage.tsx` (main chat UI), `AgentManagerPage.tsx` (agent management)
- Vite proxy: `/api` and `/uploads` -> Gateway backend (HTTP only)
- WebSocket: direct connection to Gateway :9555 (not proxied)

---

## Plugin System (`src/plugins/`)

### Tool Plugins

| Plugin | Description |
|--------|-------------|
| `agent_factory` | Dynamically create, configure, and launch new Agents via the Launcher API |
| `chat_account` | ChatPro account and group management |
| `git_core` | Core Git tools for local repository management (disabled by default) |
| `mcp_query` | MCP server management and querying tools |
| `media` | Audio format conversion (ffmpeg-based) |
| `plugin_admin` | Plugin administration: list, enable/disable, configure, hot-reload |
| `quick_note` | Quick notes with tags and search |
| `sequential_think` | Sequential thinking and structured reasoning |
| `vcs_remote` | Remote VCS tools via GitHub CLI (gh) — Issues and PRs (disabled by default) |
| `vision` | Image reading for vision model processing |
| `websearch` | Web search and page fetching via WebSearch API |
| `whisper` | Speech-to-text transcription via Whisper service |

### Hook Plugins

| Plugin | Description |
|--------|-------------|
| `long_memory` | Long-term memory with semantic recall, keyword extraction, and co-occurrence knowledge graph |
| `task_watch` | Task supervision dashboard — monitors agent task lifecycle, check-ins, stalls |
| `token_analytics` | Token usage data collection with model/tool breakdown |

### Platform Plugins

| Plugin | Description |
|--------|-------------|
| `external_api` | HTTP/WebSocket gateway for third-party system integrations |
| `feishu` | Feishu/Lark platform integration (disabled by default) |
| `qq` | QQ platform integration via NapCat/NTQQ (disabled by default) |
| `telegram` | Telegram platform integration (disabled by default) |

Plugin loading: `plugin_manager.py` scans `src/plugins/*/plugin.json`, instantiates plugin classes, registers tools based on `auto_register` flag and agent config. `plugin.json` is **auto-generated** from the `@register(...)` decorator in `plugin.py` on every agent start/hot-reload — never hand-edit it.

### Plugin config routing

- **Standard plugins** (`tool`, `hook`, `service`): config saved to `data/plugins/{name}/config.json`, merged with schema defaults at load time.
- **Platform plugins** (`platform` type — feishu, telegram, qq): config is bridged to `system_config.json`. The Launcher's GET/PUT config handlers detect `config.section` in `plugin.json` and read/write the corresponding section in `system_config.json` instead. Example: `feishu.bots` → `system_config.json["feishu"]["bots"]`.
- **Distributed broadcast**: when a config save is received by any node, it is broadcast to all other online nodes automatically. No extra plugin code needed.

---

## Agent Bootstrap Flow (`src/opensquad/agents_boot.py`)

1. Load `config.json` for target agent
2. Initialize logging (`log_setup.py`)
3. Initialize session manager (sessions dir: `agents/{name}/data/sessions/`)
4. Initialize `InputHub` with agent directory
5. Register built-in tools based on `config.json > tools[]`
6. Register plugin tools (auto_register + explicitly listed)
7. Load MCP servers from `agents/{name}/mcp_config.json`
8. Setup ChatPro bridge (login, join group, WebSocket)
9. Setup Gateway adapter (connect to Gateway WebSocket)
10. Initialize context (agent.md, role.md, env info)
11. Load skills (private from `agents/{name}/skills/`, public from `skills/`)
12. Start runner main loop

---

## Data Flow

### User message -> Agent response (Web UI)
```
Browser -> WebSocket -> Gateway(9555) -> Agent process -> LLM API
                                           |
                                        Tool calls -> filesystem / im / mcp / ...
                                           |
                                        Response stream -> WebSocket -> Browser
```

### Group chat message -> Agent
```
ChatPro server -> WebSocket -> bridge.py -> message_router -> input_hub -> runner
```

### Agent -> Group chat
```
runner -> tool_call(im.send_message) -> bridge.py -> ChatPro API
```

---

## Configuration

### `system_config.json`
Central configuration for all services. Known fields (see `src/system_config.example.json`):
- `ports`: Port assignments for all services
- `hosts`: Bind addresses
- `auth`: Contains `node_secret` (node-to-node authentication secret)
- `node`: Node identity and registration settings

> **Note**: The fields `agent_registry`, `services`, and `auto_start` do not exist in the current `src/system_config.example.json`. Do not rely on them.

### `system_config.py`
Reader class (`syscfg`) that provides typed access to `system_config.json` with fallback to environment variables and hardcoded defaults. Priority: **env var > system_config.json > hardcoded default**.

### Agent `config.json`
Per-agent configuration:
- `agent_id`: Unique identifier
- `model_name`, `base_url`, `api_key`: LLM connection
- `tools`: List of tool module names to register
- `max_tokens`, `temperature`: LLM parameters
- `chatpro`: Group chat connection settings
