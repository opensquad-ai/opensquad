# OpenSquad Plugin Development Guide

## Overview

OpenSquad plugins extend the system with new tools, lifecycle hooks, platform adapters, and optional web dashboards. The plugin system uses a **decorator-based API** — no XML, no YAML, just Python decorators and conventions.

**Key principle:** Convention over configuration. Follow the directory and naming conventions, and the framework handles discovery, routing, and UI integration automatically.

---

## Quick Start

```
plugins/
  my_plugin/
    __init__.py          # empty
    plugin.py            # required: plugin class with @register
    plugin.json          # auto-generated, do not hand-edit
    query.py             # optional: data endpoint for web UI
    storage.py           # optional: your data persistence layer
```

Minimal plugin in 20 lines:

```python
# plugins/my_plugin/plugin.py
from opensquad.plugin_api import register, Plugin, Context

@register(
    name="my_plugin",
    author="Your Name",
    description="What this plugin does",
    version="1.0.0",
    plugin_type="hook",  # "hook" | "tool" | "platform"
    display_name="My Plugin",
)
class MyPlugin(Plugin):
    def __init__(self, context: Context):
        super().__init__(context)

    def on_load(self):
        pass  # initialize resources

    def on_unload(self):
        pass  # cleanup resources
```

That's it. The framework will:
1. Discover the plugin directory
2. Import `plugin.py`, find the `@register`-decorated class
3. Auto-generate `plugin.json`
4. Show the plugin in the admin UI with enable/disable toggle

---

## Plugin Types

| Type | Purpose | Example |
|------|---------|---------|
| `tool` | Register callable tools for agents | websearch, vision, mcp_query |
| `hook` | Intercept lifecycle events (before/after LLM, tool calls) | token_analytics, sequential_think |
| `platform` | Inbound message adapter + outbound send tools | feishu, telegram, qq |
| `service` | Spawns an embedded HTTP/background service managed by the plugin lifecycle | whisper, websearch |

> **Service plugins** should inherit the `ServicePlugin` base class, which automatically manages service process startup, shutdown, health checks, and auto-restart. Just configure service parameters — no service management code needed. See the "Service Plugin Development" section below.

---

## API Reference

All imports from one module:

```python
from opensquad.plugin_api import register, tool, hook, on_event, Plugin, Context
```

### @register decorator

```python
@register(
    name="my_plugin",            # unique identifier; directory name can differ (see note below)
    author="Your Name",          # displayed in plugin card
    description="...",           # human-readable description
    version="1.0.0",             # semantic version
    plugin_type="hook",          # "hook" | "tool" | "platform" | "service"
    display_name="My Plugin",    # UI display name (auto-generated if omitted)
    config_schema={...},         # optional: user-configurable settings
    contributes={...},           # optional: frontend UI contribution points
    dependencies={"pip": [...]}, # optional: pip dependencies
    node_scope="all",            # optional: "all" (default) | "single"
)
class MyPlugin(Plugin): ...
```

#### `node_scope` parameter

| Value | Meaning | Default `enabled` in generated `plugin.json` |
|-------|---------|----------------------------------------------|
| `"all"` | Plugin runs on every node (default). E.g. a tool plugin that any node can use. | `true` |
| `"single"` | Plugin should run on exactly **one** node. E.g. an IMAP background listener — running it on multiple nodes would cause duplicate processing. | `false` |

When `node_scope="single"`, `plugin.json` is generated with `enabled: false` so the plugin is disabled by default on every node. The administrator must explicitly enable it on exactly one node via the **Node Management** panel in the admin UI (the network icon on each plugin card).

> This is an advisory hint, not a runtime enforcement. The framework does not prevent you from enabling a `single`-scoped plugin on multiple nodes.

> **Plugin name vs. directory name:** `@register(name=...)` does **not** need to match the directory name. For example, `plugins/whisper/plugin.py` uses `@register(name="whisper_transcribe")`. The Launcher resolves the correct directory automatically by scanning `plugin.json` files.

> **`plugin.json` is auto-generated.** Every time an agent starts or hot-reloads, `plugin_manager.py` calls `generate_plugin_json()` which regenerates `plugin.json` from the `@register(...)` decorator. **Never hand-edit `plugin.json`** — your changes will be overwritten. Put all configuration (including `config_schema`) in `@register` in `plugin.py`.

### Plugin base class

```python
class MyPlugin(Plugin):
    def __init__(self, context: Context):
        super().__init__(context)

    def on_load(self):
        """Called after instantiation. Initialize resources here."""
        db_path = self.context.config.get("db_path", "default.db")

    def on_unload(self):
        """Called on disable/shutdown. Cleanup resources here."""
```

### Context object

Injected at instantiation. Available as `self.context`.

| Attribute | Type | Description |
|-----------|------|-------------|
| `agent_id` | str | ID of the agent this plugin is loaded for |
| `project_root` | str | Absolute path to the project root |
| `event_bus` | EventBus | EventBus singleton for pub/sub |
| `config` | dict | Merged config (schema defaults + user overrides) |
| `data_dir` | str | `data/plugins/{plugin_name}/` (persistent storage) |
| `plugin_dir` | str | `plugins/{plugin_name}/` (plugin source code) |

### @tool decorator

Register methods as agent-callable tools:

```python
class MyPlugin(Plugin):
    @tool(name="websearch", description="Search the web", level="core")
    def search(self, queries: list, max_results: int = 30):
        """Search the web for given queries."""
        return [{"title": "...", "url": "...", "snippet": "..."}]
```

Parameters:

| Param | Default | Description |
|-------|---------|-------------|
| `name` | method name | Tool namespace (multiple methods can share one namespace) |
| `description` | "" | Tool-level description |
| `level` | "extended" | `"core"` (detailed docs) or `"extended"` (summary only) |
| `auto_register` | False | Auto-register to all agents |
| `requires_agent_id` | False | Whether agent_id is needed |

---

## Custom Tool Development

A "tool" is any function the agent can call. OpenSquad supports two
packaging paths for custom tools — **via Skills** (recommended for
project-scoped helpers) or **via Plugins** (the path for cross-project,
admin-distributed, or toggleable tools). The `@tool` decorator section
above covers the plugin path's contract; this section is the practical
how-to for both, plus the conventions every tool must follow.

### Tool Invocation Modes

OpenSquad supports two wire formats for tool calls:

| Mode | Description | Best for |
|------|-------------|----------|
| Native FC | LLM provider's native function-calling API (OpenAI / Anthropic / Google) | All models that support FC |
| XML | XML `<tool>` tags parsed by the agent from the prompt | Models without FC support; most robust |

The runtime picks the right one based on model capabilities and the
`tool_call_mode` config. When XML mode is in use, calls look like:

```xml
<tool name="filesystem_read_file">
  <parameter name="path">/workspace/main.py</parameter>
</tool>
```

The agent parses the XML, runs the tool, and injects the result.

### Built-in Tools

These ship with OpenSquad and are always available (subject to
`level` rules below):

| Module | Tools | Level |
|--------|-------|-------|
| `system` | shell commands, system info, time, environment | core |
| `filesystem` | read/write, list, search | core |
| `agent_setup` | skill / session / profile management | core |
| `im` | send / receive messages, history, group ops | core |
| `collaboration` | start / join team workflows | core |
| `delegate_task` | delegate tasks to child agents | core |
| `workspace` | workspace management | core |
| `task_watch` | task monitoring and progress tracking | core |
| `web` | HTTP requests | extended |
| `websearch` | real-time web search | extended |
| `vision` | image analysis | extended |
| `mcp_query` | query external MCP services | extended |
| `long_memory` | long-term memory and semantic recall | extended |

### Method 1: Via Skills (recommended for project helpers)

Add a `tools.py` to your skill directory; the framework auto-discovers
and registers every top-level function:

```
skills/my-skill/
├── SKILL.md
├── skill.json
└── tools.py         # <-- tool functions live here
```

Example `tools.py`:

```python
def calculate_sum(a: float, b: float) -> dict:
    """Add two numbers and return the result.
    
    a: The first number
    b: The second number
    """
    return {"sum": a + b}

def format_text(text: str, style: str = "upper") -> dict:
    """Format text in different styles.
    
    text: The input text to format
    style: Format style — 'upper', 'lower', or 'title'
    """
    if style == "upper":
        return {"result": text.upper()}
    elif style == "lower":
        return {"result": text.lower()}
    return {"result": text.title()}
```

**Rules:**

- Function name = tool name
- docstring is parsed as the tool's description and parameter docs
- Return value must be a `dict` (or anything JSON-serializable)

### Method 2: Via Plugin

For tools that need to be packaged, distributed, or admin-toggled,
use the plugin path. The `@tool` decorator section above has the full
parameter table; here is a minimal example:

```python
# plugins/my-plugin/plugin.py
from opensquad.plugin_api import register, tool, Context

@register(
    name="my-plugin",
    display_name="My Plugin",
    config_schema={}
)
class MyPlugin:
    def __init__(self, context: Context):
        self.context = context

    @tool(name="my_tool", description="Tool description", level="extended")
    def my_tool_function(self, param1: str) -> dict:
        """Tool description — param1: parameter description"""
        return {"result": param1}
```

### Tool Levels

| Level | Description |
|-------|-------------|
| `core` | Always available; cannot be disabled (e.g. `system`, `filesystem`) |
| `extended` | Available by default; can be disabled per agent |
| `premium` | Must be explicitly enabled in agent config |

### Writing Effective Tool Descriptions

The LLM picks a tool based on the description. Be specific.

✅ **Good:**

```python
def search_files(pattern: str, directory: str = ".") -> dict:
    """Search files matching a glob pattern in the specified directory.
    
    pattern: Glob pattern, e.g. '*.py' or 'test_*.ts'
    directory: Target directory path, defaults to current directory
    """
```

❌ **Bad:**

```python
def search_files(pattern: str, directory: str = ".") -> dict:
    """Search files."""
```

### Security: Filesystem Whitelist

Filesystem tools only access whitelisted paths. Configure in
`system_config.json`:

```json
{
  "filesystem": {
    "workspace_dirs": ["/data/projects", "../shared"]
  }
}
```

Out-of-whitelist requests are rejected.

### Tool Call Debug Logging

Enable in `system_config.json`:

```json
{
  "logging": {
    "tool_call_debug": true,
    "tool_call_debug_max_size_mb": 5,
    "tool_call_debug_backup_count": 3
  }
}
```

View via:

```bash
opensquad logs -s gateway --grep "tool_call"
```

---

### @hook decorators

Intercept the agent lifecycle. Must return the context dict (pass-through or modified):

```python
class MyPlugin(Plugin):
    @hook.on_after_tool
    async def track_tool(self, context: dict) -> dict:
        tool_name = context.get("tool_name", "")
        result = context.get("result", "")
        # ... do something ...
        return context  # must return context
```

Available hooks:

| Hook | Fires when |
|------|-----------|
| `@hook.on_message_received` | User message arrives |
| `@hook.on_before_llm` | Before LLM API call |
| `@hook.on_after_llm` | After LLM API call |
| `@hook.on_before_tool` | Before tool execution |
| `@hook.on_after_tool` | After tool execution |

### @on_event decorator

Subscribe to EventBus events (fire-and-forget, no return value needed):

```python
class MyPlugin(Plugin):
    @on_event("token_stats")
    def handle_token_stats(self, event_data: dict):
        sid = event_data.get("sid", "")
        data = event_data.get("data", {})
        # ... record, log, forward, etc.
```

---

## Configuration

### Declaring config schema

Add `config_schema` to `@register`:

```python
@register(
    name="my_plugin",
    # ...
    config_schema={
        "api_key": {
            "type": "string",
            "default": "",
            "description": "API key for the service",
        },
        "max_retries": {
            "type": "integer",
            "default": 3,
            "description": "Maximum retry attempts",
        },
        "debug_mode": {
            "type": "boolean",
            "default": False,
            "description": "Enable verbose logging",
        },
    },
)
```

Supported types: `string`, `integer`, `number`, `boolean`.

#### `bot_list` type (platform plugins only)

Use `bot_list` when a plugin manages a list of bots, each with multiple fields. The admin UI renders a full list editor with add/remove/edit per bot.

```python
config_schema={
    "service_enabled": {
        "type": "boolean",
        "default": False,
        "description": "Enable the platform service",
    },
    "bots": {
        "type": "bot_list",
        "default": [],
        "description": "Bot instances",
        "item_schema": {
            "name":      {"type": "string",  "default": "",    "description": "Display name"},
            "token":     {"type": "string",  "default": "",    "description": "Bot token", "secret": True},
            "agent_id":  {"type": "string",  "default": "",    "description": "Agent to route messages to"},
            "enabled":   {"type": "boolean", "default": True,  "description": "Enable this bot"},
        },
    },
},
```

`item_schema` defines the fields for each bot entry. Fields with `"secret": True` are rendered as password inputs in the UI.

#### Platform plugin config bridging

For `platform` plugins (feishu, telegram, qq), the config is bridged to `system_config.json` instead of `data/plugins/{name}/config.json`. The `config.section` in the generated `plugin.json` is set to the `@register(name=...)` value. The Launcher's GET/PUT config handlers detect this and read/write the corresponding section in `system_config.json`.

### Distributed config broadcast

When a user saves plugin config via the admin UI, the change is automatically **broadcast to all online nodes** in a multi-node deployment. Single-node deployments are unaffected (empty node list). No extra code is needed in the plugin itself.

### Reading config at runtime

```python
def on_load(self):
    api_key = self.context.config.get("api_key", "")
    max_retries = self.context.config.get("max_retries", 3)
```

Config values are automatically merged: **schema defaults < user-saved values** (stored at `data/plugins/{name}/config.json`).

### Config UI

The admin web UI automatically renders a settings panel for any plugin that has `config_schema`. Users can edit and save values through the UI. No frontend code needed.

### Hot-Reload vs. Restart

Not everything can be hot-reloaded. Use the table below to decide:

| Scenario | Action needed |
|----------|--------------|
| New plugin (first time creation) | `trigger_reload()` — no restart |
| Plugin `config_schema` value changed (user config) | `trigger_reload()` — no restart |
| `plugin.json` → `"enabled": false` (disable) | `trigger_reload()` — no restart |
| **Modify existing plugin `.py` code** | **Restart agent process** (module already imported) |
| **Add/remove plugin from `config.json` `tools` list** | **Restart agent process** |

#### Triggering hot-reload

```python
skill_plugin_dev.trigger_reload()
```

Or manually (touch the sentinel file):
```
plugins/.reload_ts
```

When triggered:
1. Launcher writes a timestamp to `plugins/.reload_ts`
2. AgentRunner detects the change within 5 seconds
3. PluginManager diffs disk state vs in-memory state
4. Changed plugins are unloaded (`on_unload()`) and/or reloaded (`on_load()` with new config)

#### Restarting the agent process

When you need a full restart, use the Launcher management API on port **9600**:

```
POST http://127.0.0.1:9600/api/agents/{dir_name}/restart
POST http://127.0.0.1:9600/api/agents/{dir_name}/stop
POST http://127.0.0.1:9600/api/agents/{dir_name}/start
```

`dir_name` is the **directory name** under `agents/` (not the `agent_id` in config.json).

Example (PowerShell via `api_process.run_command`):

```
api_process.run_command('Invoke-WebRequest -Uri "http://127.0.0.1:9600/api/agents/opensquad001/restart" -Method POST | Select-Object -ExpandProperty Content')
```

Successful response:
```json
{"message": "opensquad001 restarted", "pid": 12345}
```

You can also restart from the Admin UI: **Agents → {agent} → Restart**.

---

## Web UI Dashboard (Optional)

### Option A: Zero frontend code (GenericPluginView)

If your plugin has a `query.py` with a standard `query_data()` function, the admin UI automatically provides a generic data viewer with:
- Summary cards (extracted from the `summary` field of your returned data)
- Collapsible JSON tree (full data inspection)
- Time range selector and refresh button

**This means: just write `query.py`, get a web dashboard for free.**

### Option B: Custom dashboard (any frontend framework)

For polished visualizations (charts, tables, custom layouts), create a custom view using the mount/unmount adapter interface. The core app is framework-agnostic — you can use React, Vue, Svelte, vanilla JS, or anything else.

**3 steps total:**

#### Step 1: Declare contribution points in @register

```python
@register(
    name="my_plugin",
    # ...
    contributes={
        "views": [
            {
                "name": "my_dashboard",       # view identifier
                "title": "My Dashboard",       # button label in UI
                "icon": "BarChart3",           # lucide-react icon name
                "data_endpoint": "/api/plugins/my_plugin/data",
            }
        ]
    },
)
```

#### Step 2: Create the view file with mount/unmount exports

The view file must export two functions:

```ts
mount(container: HTMLElement, props: { onBack: () => void }): void
unmount(container: HTMLElement): void
```

**React example** (`gateway/nexuschat-pro/components/plugin-views/my-plugin/MyDashboard.tsx`):

```tsx
import React, { useState, useEffect, useCallback } from 'react';
import { createRoot, Root } from 'react-dom/client';
import { ArrowLeft, RefreshCw, Loader2 } from 'lucide-react';
import { pluginAPI } from '../../../services/api';

// ---- PluginViewAdapter exports ----

const _roots = new WeakMap<HTMLElement, Root>();

export function mount(container: HTMLElement, props: { onBack: () => void }): void {
  const root = createRoot(container);
  _roots.set(container, root);
  root.render(<MyDashboard {...props} />);
}

export function unmount(container: HTMLElement): void {
  const root = _roots.get(container);
  if (root) {
    root.unmount();
    _roots.delete(container);
  }
}

// ---- Component ----

interface Props {
  onBack: () => void;
}

export const MyDashboard: React.FC<Props> = ({ onBack }) => {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [range, setRange] = useState('24h');

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const result = await pluginAPI.getPluginData('my_plugin', { range });
      setData(result);
    } finally {
      setLoading(false);
    }
  }, [range]);

  useEffect(() => { fetchData(); }, [fetchData]);

  return (
    <div className="flex-1 h-full bg-bgLight flex flex-col overflow-hidden">
      {/* Header with back button */}
      <div className="px-6 py-4 border-b border-border bg-panel flex items-center gap-4">
        <button onClick={onBack} className="p-2 rounded-lg text-textMuted hover:bg-primary/10">
          <ArrowLeft size={20} />
        </button>
        <h1 className="text-lg font-bold text-textMain">My Dashboard</h1>
        <button onClick={fetchData} className="ml-auto p-2 rounded-lg text-textMuted hover:bg-primary/10">
          <RefreshCw size={18} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {loading ? (
          <div className="flex justify-center py-20">
            <Loader2 className="animate-spin text-primary" size={32} />
          </div>
        ) : data ? (
          <div>{/* Your custom visualization here */}</div>
        ) : null}
      </div>
    </div>
  );
};
```

**Vue 3 example** (`gateway/nexuschat-pro/components/plugin-views/my-plugin/MyDashboard.ts`):

```ts
import { createApp, App } from 'vue';
import MyDashboardVue from './MyDashboard.vue';

const _apps = new WeakMap<HTMLElement, App>();

export function mount(container: HTMLElement, props: { onBack: () => void }): void {
  const app = createApp(MyDashboardVue, { onBack: props.onBack });
  _apps.set(container, app);
  app.mount(container);
}

export function unmount(container: HTMLElement): void {
  const app = _apps.get(container);
  if (app) {
    app.unmount();
    _apps.delete(container);
  }
}
```

**Vanilla JS example:**

```ts
export function mount(container: HTMLElement, props: { onBack: () => void }): void {
  container.innerHTML = `<div class="p-6 text-textMain">Hello from vanilla JS!</div>`;
  // attach event listeners, render charts, etc.
}

export function unmount(container: HTMLElement): void {
  container.innerHTML = '';
}
```

#### Step 3: Register in the view registry

File: `gateway/nexuschat-pro/components/plugin-views/registry.ts`

Add one entry to `PLUGIN_VIEW_LOADERS`:

```ts
const PLUGIN_VIEW_LOADERS = {
  'token_analytics:token_dashboard': () =>
    import('./token-analytics/TokenDashboard') as Promise<PluginViewAdapter>,
  // Add your entry:
  'my_plugin:my_dashboard': () =>
    import('./my-plugin/MyDashboard') as Promise<PluginViewAdapter>,
};
```

> **Why dynamic import?** Adapters are loaded lazily at runtime. A syntax error in your view will *not* crash the main app — the view just fails to mount and shows a safe error fallback. The error is also automatically reported to the backend log.

**Done.** The plugin card will show a dashboard button. Clicking it calls `mount()`. Navigating away calls `unmount()`. If you skip Steps 2-3, the GenericPluginView fallback is used automatically.

### Web UI 自由度说明

自定义视图通过 `mount/unmount` 接口接入，**与前端框架无关**。以下是准确的约束边界：

#### 实际约束（只有这 4 条）

| 约束 | 原因 |
|------|------|
| Props 必须包含 `onBack: () => void` | 框架注入，用于返回插件列表页 |
| 样式使用 Tailwind + 主题 CSS 变量（`bg-bgLight`、`text-textMain` 等） | 与系统深色/浅色主题兼容，硬编码颜色会在主题切换时出问题 |
| 图标使用 `lucide-react` | 已内置，用其他图标库需自行 `npm install` |
| 数据读取只能走 `query.py` | Launcher 统一路由，不能在插件内自行监听端口 |

#### 完全自由的部分

- **组件内部所有逻辑**：`useState`、`useEffect`、`useCallback`、`useRef` 等任意使用
- **复杂交互**：搜索、多条件筛选、表单提交、拖拽、分页、实时轮询均支持
- **CRUD 操作**：读取走 `query.py`，写操作可以通过 Agent 工具执行（插件工具写数据，`query.py` 读数据展示）
- **引入额外 npm 包**：在 `gateway/nexuschat-pro/package.json` 中添加后即可 import
- **自定义动画、过渡、布局**：无任何限制

> 参考实现：`plugins/quick_note` 的 `QuickNoteDashboard.tsx` 包含搜索输入、标签筛选、完成状态切换、笔记删除——这些都是复杂交互，完全正常工作。

#### 常见误解

**误解 1：Web UI 插件不适合复杂交互**

不准确。复杂交互完全可以在组件内实现，限制的是数据**入口**（必须走 `query.py`），不是交互**行为**。

**误解 2：`contributes.links` 可以跳转到独立前端页面**

该字段**不存在**。`contributes` 目前只支持 `views`：

```python
# 正确
contributes={
    "views": [{"name": "...", "title": "...", "icon": "...", "data_endpoint": "..."}]
}

# 错误 — links 字段无效，会被忽略
contributes={
    "links": [...]  # 不存在
}
```

如果确实需要一个完全独立的前端应用（独立路由、独立认证），正确做法是在 `gateway/nexuschat-pro/` 下直接开发独立页面，与插件系统无关。

### Data query module (query.py)

Both Option A and Option B need this to serve data to the frontend.

File: `plugins/my_plugin/query.py`

```python
def query_data(project_root: str, params: dict) -> dict:
    """
    Standard entry point. Called by Launcher automatically.

    Args:
        project_root: absolute path to project root
        params: flat dict of query-string params (all values are strings)

    Returns:
        JSON-serializable dict
    """
    time_range = params.get("range", "24h")
    agent_id = params.get("agent_id")

    # Your data retrieval logic here
    # e.g. read from SQLite, parse log files, call APIs, etc.

    return {
        "summary": {
            "total_count": 42,
            "active_items": 10,
        },
        "details": [
            {"name": "item1", "value": 100},
            {"name": "item2", "value": 200},
        ],
        "meta": {
            "time_range": time_range,
        },
    }
```

**Convention:** The function signature must be exactly `query_data(project_root: str, params: dict) -> dict`. The Launcher discovers and calls it dynamically via `importlib` — no routing code or registration needed.

---

## Sidebar Navigation Icon (Optional)

A plugin can register a shortcut entry in the left sidebar. Clicking it opens the plugin view directly.

Add a `navigation` field to `contributes` in `@register`:

```python
contributes={
    "views": [...],  # standard view declaration (unchanged)
    "navigation": {
        "icon":     "Mail",                                          # lucide-react icon name (used when iconType=lucide)
        "label":    "Email Inbox",                                   # tooltip text on hover
        "view":     "email_assistant:inbox",                         # MUST be "pluginName:viewName" format
        "iconType": "image",                                         # "lucide" (default) or "image"
        "iconUrl":  "/api/plugins/static/email_assistant/email.jpg", # image URL when iconType=image
    },
},
```

| Field | Required | Description |
|-------|----------|-------------|
| `icon` | Yes | lucide-react icon name, used when `iconType=lucide` |
| `label` | Yes | Tooltip text shown on hover |
| `view` | Yes | **Must** be `"pluginName:viewName"` format, matching the name in `views[]` |
| `iconType` | No | `"lucide"` (default) or `"image"` |
| `iconUrl` | No | Image URL when `iconType="image"`; plugin assets are served at `/api/plugins/static/{pluginName}/filename` |

### View Loading Order

When a navigation icon is clicked, the frontend loads the view in this order:

1. Hardcoded adapter in `registry.ts` → `PLUGIN_VIEW_LOADERS`
2. Remote ESM from `/api/plugins/static/{pluginName}/ui/index.js`
3. **Automatic fallback to `GenericPluginView`**: if both above fail, the built-in JSON dashboard is used (requires `query.py`)

> For plugins without custom UI, **just provide `query.py`** and you get a full data dashboard for free — no frontend code needed.

### User Opt-In

Navigation icons are **hidden by default** (users enable them individually on the plugin management page), keeping the sidebar clean. Once enabled, the icon appears in the left bar.

> See `doc_cn/plugin_navigation_guide.md` for full configuration examples.

---

## Data Flow Architecture

```
[Agent Runtime]
  AgentRunner
    -> EventBus.publish("token_stats", data)
    -> PluginManager calls @hook handlers
    -> Plugin writes data to SQLite / files / etc.

[User clicks Dashboard button in admin UI]
  Frontend
    -> pluginAPI.getPluginData("my_plugin", {range: "24h"})
    -> GET /api/ai-web/admin/plugins/my_plugin/data?range=24h     (Gateway :9555)
    -> GET /api/plugins/my_plugin/data?range=24h                   (Launcher :9600)
    -> importlib.import_module("plugins.my_plugin.query")
    -> query_data(project_root, {"range": "24h"})
    -> JSON response -> Frontend renders dashboard
```

Port reference:

| Port | Service |
|------|---------|
| 9555 | Gateway Backend (FastAPI, proxies to Launcher) |
| 9530 | Frontend Dev Server |
| 9600 | Launcher (HTTP management API) |

---

## Complete Example: token_analytics

The `token_analytics` plugin is the canonical reference implementation, demonstrating all features:

| File | Lines | Purpose |
|------|-------|---------|
| `plugins/token_analytics/plugin.py` | 179 | Plugin class with `@register`, `@hook.on_after_tool`, `@on_event("token_stats")`, `config_schema`, `contributes.views` |
| `plugins/token_analytics/storage.py` | 246 | SQLite persistence with WAL mode, buffered writes, two tables (`token_snapshots`, `tool_usage`) |
| `plugins/token_analytics/query.py` | 307 | `query_data()` standard entry + 6-dimension dashboard aggregation (summary, timeline, by_model, by_agent, top_tools, recent_snapshots) |
| `plugin-views/token-analytics/TokenDashboard.tsx` | 381 | PluginViewAdapter (`mount`/`unmount`) + React dashboard: summary cards, timeline bar chart, model/agent breakdown, top tools list |

Key patterns to study:

1. **Config declaration + runtime access** (`plugin.py:38-54` and `plugin.py:80-98`)
2. **View contribution point** (`plugin.py:55-64`)
3. **EventBus data collection** (`plugin.py:103-138`)
4. **Hook-based data collection** (`plugin.py:140-173`)
5. **Standard query entry point** (`query.py:26-52`)
6. **Frontend data fetching** (`TokenDashboard.tsx:92-110`)
7. **mount/unmount adapter** (`TokenDashboard.tsx:14-25`)

---

## Service Plugin Development: Using ServicePlugin Base Class

### Why Use ServicePlugin?

`ServicePlugin` is a **generic service plugin base class** that provides complete service management functionality for plugins that need background services. Using ServicePlugin offers:

- **Drastically reduced code**: From ~120 lines of service management code down to ~10 lines of configuration code (90% reduction)
- **Unified service management**: Automatic start/stop, health checks, and auto-restart
- **Simplified configuration**: Configurable service parameters (service_script, health_endpoint, port, etc.)
- **Unified logging**: Automatic logging of service startup/shutdown/health check status

### Quick Start

**Create a service plugin in 3 steps:**

#### Step 1: Create Service Script

Create a `service/` folder in your plugin directory and add your service script (Flask, FastAPI, etc.):

```
plugins/
  my_plugin/
    __init__.py
    plugin.py
    service/
      main.py          # Your service script
```

#### Step 2: Inherit ServicePlugin Base Class

In `plugin.py`, inherit `ServicePlugin` and configure service parameters:

```python
from opensquad.plugin_api import register, Plugin
from opensquad.service_plugin import ServicePlugin

@register(
    name="my_plugin",
    author="Your Name",
    description="My service plugin",
    version="1.0.0",
    plugin_type="service",
    config_schema={
        "port": {
            "type": "integer",
            "default": 9001,
            "description": "Service port",
        },
        "auto_start": {
            "type": "boolean",
            "default": True,
            "description": "Auto-start service",
        },
    },
)
class MyPlugin(ServicePlugin):
    def __init__(self, context):
        super().__init__(
            context=context,
            service_script="service/main.py",           # Service script path (relative to plugin dir)
            health_endpoint="http://127.0.0.1:{port}/health",  # Health check endpoint ({port} will be replaced)
            service_name="MyService",                   # Service name (for logging)
            health_check_interval=30,                   # Health check interval (seconds)
            startup_timeout=30,                         # Startup timeout (seconds)
        )
```

#### Step 3: Ensure Service Has Health Check Endpoint

Your service script must provide a `/health` endpoint:

```python
# service/main.py
from flask import Flask

app = Flask(__name__)

@app.route('/health')
def health():
    return {"status": "healthy"}, 200

@app.route('/api/my_feature')
def my_feature():
    return {"result": "..."}

if __name__ == '__main__':
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9001
    app.run(host='0.0.0.0', port=port)
```

**Done!** ServicePlugin will automatically:
- Start the service process on `on_load()`
- Check service health every 30 seconds
- Auto-restart the service if it becomes unhealthy
- Gracefully stop the service on `on_unload()`

### Configuration Requirements

| Config Item | Type | Description |
|-------------|------|-------------|
| `port` | integer | Service port (recommended to declare in config_schema for user configuration) |
| `auto_start` | boolean | Whether to auto-start service (recommended default: `true`) |

**Health Check Endpoint Requirements:**
- Must return HTTP 200 status code
- Recommended to return JSON format: `{"status": "healthy"}`
- Endpoint path is typically `/health`

### Complete Example: Whisper Plugin

**Before using ServicePlugin:** 192 lines

```python
# Had to manually implement:
# - Start service process (subprocess.Popen)
# - Health check logic (HTTP request + retry)
# - Background monitoring thread
# - Auto-restart logic
# - Graceful service shutdown
# ... about 120 lines of service management code
```

**After using ServicePlugin:** 80 lines (58% reduction)

```python
from opensquad.plugin_api import register, tool, Context
from opensquad.service_plugin import ServicePlugin

@register(
    name="whisper_transcribe",
    author="OpenSquad",
    description="Whisper speech-to-text transcription",
    version="1.0.0",
    plugin_type="service",
    config_schema={
        "port": {"type": "integer", "default": 5001, "description": "Whisper service port"},
        "auto_start": {"type": "boolean", "default": True, "description": "Auto-start service"},
        "model_size": {"type": "string", "default": "base", "description": "Model size (tiny/base/small/medium/large)"},
    },
)
class WhisperPlugin(ServicePlugin):
    def __init__(self, context: Context):
        super().__init__(
            context=context,
            service_script="service/service.py",
            health_endpoint="http://127.0.0.1:{port}/health",
            service_name="Whisper",
            health_check_interval=30,
            startup_timeout=30,
        )

    @tool(name="whisper.transcribe", description="Speech to text transcription", level="extended")
    def transcribe(self, audio_path: str, language: str = "auto"):
        """Speech to text transcription tool"""
        from .whisper_transcribe import whisper_transcribe
        port = self.context.config.get("port", 5001)
        return whisper_transcribe(audio_path=audio_path, language=language, port=port)
```

### Code Comparison

| Plugin | Before | After | Reduction |
|--------|--------|-------|-----------|
| Whisper | 192 lines | 80 lines | 112 lines (58%) |
| WebSearch | 198 lines | 79 lines | 119 lines (60%) |

**Key Benefits:**
- ✅ No need to write service management code
- ✅ No need to implement health check logic
- ✅ No need to handle process lifecycle
- ✅ Automatic exception recovery and restart
- ✅ Unified logging format

### Reference Implementations

Complete examples available at:
- `opensquad/service_plugin.py` - ServicePlugin base class implementation (opensquad/service_plugin.py:54-202)
- `plugins/whisper/plugin.py` - Whisper plugin usage example (plugins/whisper/plugin.py:50-58)
- `plugins/websearch/plugin.py` - WebSearch plugin usage example (plugins/websearch/plugin.py:49-57)
- `docs/ServicePlugin_Guide.md` - ServicePlugin detailed usage guide

---

## Service Plugin Demo: hello_service

`plugins/hello_service/` is a reference implementation of a **service plugin** — a plugin that spawns an embedded HTTP service in a background thread on load and stops it on unload.

```
plugins/hello_service/
    __init__.py
    plugin.py        # @register with port/host config_schema; starts Flask server
    README.md
```

Key patterns demonstrated:

1. **Declare `port` and `host` in `config_schema`** so they appear in the admin UI settings panel.
2. **Read config in `on_load()`** and start the HTTP server thread.
3. **Stop the server cleanly in `on_unload()`**.

See `plugins/hello_service/plugin.py` for the full runnable example.

> **Note:** For new service plugins, it is recommended to use the `ServicePlugin` base class instead of manually implementing service management as shown in `hello_service`. The `hello_service` example is kept for reference and backward compatibility.

---

## Checklist

### Backend (required)

- [ ] Create directory `plugins/{name}/`
- [ ] Create empty `__init__.py`
- [ ] Create `plugin.py` with `@register` + `Plugin` subclass
- [ ] Implement `on_load()` and `on_unload()`
- [ ] Add decorators as needed: `@tool`, `@hook.on_xxx`, `@on_event`
- [ ] Add `config_schema` if the plugin needs user configuration
- [ ] Add plugin name to target agent's `config.json` → `tools` list
- [ ] **Restart agent process** (new plugin + tools list change both require restart)
- [ ] Verify plugin appears in `skill_plugin_dev.list_plugins()`

### Web UI (optional)

- [ ] Add `contributes.views` to `@register`
- [ ] Create `query.py` with `query_data(project_root, params)` function
- [ ] Test with GenericPluginView (works automatically, no frontend code)
- [ ] (Optional) Create view adapter in `plugin-views/{name}/` with `mount` and `unmount` exports
- [ ] (Optional) Register in `plugin-views/registry.ts` with `import('./...') as Promise<PluginViewAdapter>`
- [ ] Open the view in admin UI and check `plugins/{name}/view_errors.log` for runtime errors

---

## Testing & Debugging

### Backend Plugin

**Step 1 — Syntax check before reload**

```bash
python -m py_compile plugins/my_plugin/plugin.py
```

Or via skill:

```
skill_plugin_dev.validate_syntax("my_plugin")
```

A syntax error here means the plugin will silently fail to load. Always check before triggering reload.

**Step 2 — Load the plugin**

For a **new plugin** (first load), trigger hot-reload:

```
skill_plugin_dev.trigger_reload()
skill_plugin_dev.list_plugins()
```

For **modified plugin code** or **tools list change in config.json**, hot-reload won't work — restart the agent process via Launcher API:

```
POST http://127.0.0.1:9600/api/agents/{dir_name}/restart
```

Or via PowerShell (`api_process.run_command`):

```
Invoke-WebRequest -Uri "http://127.0.0.1:9600/api/agents/opensquad001/restart" -Method POST | Select-Object -ExpandProperty Content
```

`dir_name` is the folder name under `agents/` — run `filesystem.list_directory("agents/")` if unsure.

Confirm the plugin appears in the list with `enabled: true`. If it doesn't appear, check the agent process logs.

**Step 3 — Check agent logs**

Log files are written per-agent. Typical paths:

```
agents/{agent_id}/data/logs/
```

Or via launcher API in the admin UI: **Agents → {agent} → View Logs**.

Look for lines like:
```
[PluginManager] Loaded plugin: my_plugin
[PluginManager] ERROR loading plugin my_plugin: ...
```

**Step 4 — Test the tool**

After reload, call the tool from the agent chat to verify it works end-to-end:

```
quick_note.add(content="test note", tags=["test"])
quick_note.list()
```

If the tool is not found, verify the plugin name is in the agent's `config.json` `tools` list.

---

### Frontend Views (Dashboard Components)

<a name="debugging-frontend-views"></a>

**Error isolation**

Plugin view components are loaded with `React.lazy` (dynamic import) and wrapped in an Error Boundary. This means:

- A **syntax error** in your `.tsx` file will not crash the main app. The view fails to load silently, and the browser console will show a chunk load error.
- A **runtime error** (exception during render) is caught by the Error Boundary. The admin UI shows a safe fallback with the error message, and the error is automatically written to the log file.

**Reading the error log**

Every runtime error from a plugin view is appended to:

```
plugins/{plugin_name}/view_errors.log
```

Read it with:

```
filesystem.read_file("plugins/quick_note/view_errors.log")
```

Log entry format:

```
[2026-02-19 14:32:01] view=quick_note:dashboard
  error: TypeError: Cannot read properties of undefined (reading 'map')
  stack:     at QuickNoteDashboard (QuickNoteDashboard.tsx:144)
              at ...
────────────────────────────────────────────────
```

**Common frontend errors and fixes**

| Error | Likely cause |
|-------|-------------|
| `Cannot read properties of undefined` | API returned unexpected shape; add null checks |
| `Objects are not valid as a React child` | Rendered `{object}` directly; stringify or extract a field |
| `Each child in a list should have a unique "key"` | Missing `key` prop in `.map()` |
| Chunk load error in console (view doesn't open) | Syntax error in `.tsx`; fix the file |

**Testing cycle**

1. Write/fix the component
2. The Vite dev server hot-reloads automatically
3. Open the plugin view in admin UI
4. If it crashes → read `view_errors.log` → fix → repeat
5. When the view renders correctly → done

---

## Tips

- **Plugin name does not need to match directory name.** `@register(name="whisper_transcribe")` can live in `plugins/whisper/plugin.py`. The Launcher resolves the directory by scanning `plugin.json` files.
- **`plugin.json` is auto-generated.** Never hand-edit it; change `@register` parameters instead. It is regenerated on every agent start/hot-reload.
- **Config persistence is automatic.** User edits via UI are saved to `data/plugins/{name}/config.json` and merged with schema defaults at load time.
- **Platform plugin configs write to `system_config.json`.** For feishu/telegram/qq, the Launcher bridges config GET/PUT to `system_config.json[section]` automatically.
- **Config saves are broadcast cluster-wide.** In multi-node deployments, saving plugin config from any node propagates to all online nodes automatically.
- **Hot-reload works.** Enable/disable/config changes take effect within 5 seconds without restarting agents.
- **GenericPluginView is your friend.** Start with `query.py` only; build a custom dashboard later when you need it.
- **`query.py` is hot-reloaded too.** The Launcher uses `importlib.reload()` if the module was previously imported, so you can update query logic without restarting.
