---
name: mcp_auto_deploy
description: Allows the agent to self-configure and deploy MCP servers. Covers central config, per-agent sync, web UI visibility, registration diagnostics, and validation.
allowed-tools: filesystem, system
---

## MCP Server Auto Deploy — Agent Self-Service Guide

### 1. MCP 架构概述

OpenSquad 的 MCP 分为两层：

| 层级 | 文件路径 | 说明 |
|------|---------|------|
| **中央配置** (Central) | `<workspace>/data/mcp_config.json` | 统一的 MCP 服务器列表，所有 agent 共享 |
| **Agent 配置** (Per-agent) | `<workspace>/agents/<agent_name>/mcp_config.json` | 从中央配置自动同步，也可单独覆盖 |

Agent 启动时，Launcher 会读取中央配置并同步到每个 agent。**所有 Agent 共享同一套 MCP 服务器**。

> 💡 **MCP 配置存储路径（中央）**：
> `{workspace}/data/mcp_config.json`
>
> **通过 filesystem 工具可读写此文件**。

---

### 2. MCP Server 配置格式 (`mcpServers`)

中央 `mcp_config.json` 的结构如下：

```json
{
  "mcpServers": {
    "<server-name>": {
      "enabled": true,
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed/dir"],
      "timeout": 60,
      "env": {
        "API_KEY": "YOUR_API_KEY_HERE"
      },
      "autoApprove": ["read_file", "write_file"]
    },
    "<server-name-2>": {
      "enabled": false,
      "command": "uvx",
      "args": ["mcp-server-google-maps"],
      "timeout": 30
    }
  }
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `enabled` | 是 | `true`=启用，`false`=禁用但在 UI 中可见 |
| `command` | 是 | 可执行命令，如 `npx`、`uvx`、`python`、`node` |
| `args` | 是 | 命令行参数数组 |
| `timeout` | 否 | 超时秒数 (默认 60) |
| `env` | 否 | 环境变量字典，传递给子进程 |
| `autoApprove` | 否 | 不需要用户确认即可自动调用的工具名列表 |

---

### 3. 部署一个新的 MCP 服务器

#### 步骤 1: 确定 MCP 服务器来源

MCP 服务器可来自：

- **npm 包**: `npx -y @modelcontextprotocol/server-xxx`
- **Python 包**: `uvx mcp-server-xxx`
- **本地脚本**: `python path/to/server.py`
- **自定义二进制**: 任何可执行文件

常用 MCP 服务器示例：

| 名称 | 安装命令 | 用途 |
|------|---------|------|
| `filesystem` | `npx -y @modelcontextprotocol/server-filesystem /path` | 文件系统操作 |
| `puppeteer` | `npx -y @modelcontextprotocol/server-puppeteer` | 浏览器自动化 |
| `brave-search` | `npx -y @modelcontextprotocol/server-brave-search` | Brave 搜索引擎 |
| `fetch` | `npx -y @modelcontextprotocol/server-fetch` | 网页抓取 |
| `git` | `npx -y @modelcontextprotocol/server-git` | Git 操作 |

#### 步骤 2: 确认目标机器上已安装所需运行时

```bash
# Node.js MCP 服务器需要 npx
npx --version

# Python MCP 服务器需要 uvx
uvx --version
```

如果缺少，需要先安装运行时。

#### 步骤 3: 通过 Filesystem 工具写入中央配置

```python
import json

workspace_path = "C:/path/to/workspace"  # 请替换为实际 workspace 路径
central_mcp_path = f"{workspace_path}/data/mcp_config.json"

# 格式：mcpServers 字典
new_server = {
    "mcpServers": {
        "my-filesystem-server": {
            "enabled": true,
            "command": "npx",
            "args": [
                "-y",
                "@modelcontextprotocol/server-filesystem",
                "C:/allowed/directory"
            ],
            "timeout": 60
        }
    }
}

# 读取现有配置（如果存在），合并新旧服务器
try:
    with open(central_mcp_path, "r", encoding="utf-8") as f:
        existing = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    existing = {"mcpServers": {}}

existing["mcpServers"].update(new_server["mcpServers"])

# 写回中央配置
with open(central_mcp_path, "w", encoding="utf-8") as f:
    json.dump(existing, f, ensure_ascii=False, indent=2)
```

> ⚠️ **同步说明**：写入后需要 **重启 agent** 或 **通知 launcher 重新加载**。Launcher 启动时会自动扫描中央配置并同步到所有 agent。

---

### 4. 验证 MCP 服务器是否正常工作

#### 检查中央配置是否已写入

通过 `filesystem` 工具读取中央配置文件：

```python
import json

with open(f"{workspace_path}/data/mcp_config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

# 检查计划中的服务器是否在其中
server_name = "my-filesystem-server"
assert server_name in config.get("mcpServers", {}), f"MCP server '{server_name}' not found in central config"
assert config["mcpServers"][server_name]["enabled"], f"MCP server '{server_name}' is disabled"
```

#### 检查 Agent 级配置是否已同步

每个 agent 的 `mcp_config.json` 如下：

```python
agent_mcp_path = f"{workspace_path}/agents/{agent_name}/mcp_config.json"
with open(agent_mcp_path, "r", encoding="utf-8") as f:
    agent_config = json.load(f)

server = agent_config.get("mcpServers", {}).get(server_name)
if server:
    print(f"✅ '{server_name}' synced to agent '{agent_name}'")
else:
    print(f"❌ '{server_name}' NOT synced to agent '{agent_name}'")
```

#### 检查 Web UI 可见性

MCP 管理页面在 Web UI 的导航路径为：

**Web UI → MCP 管理页面**

该页面读取 `GET /api/mcp/config`（中央配置）和 `GET /api/mcp/global`（全局开关）来展示 MCP 服务器列表。

要使 MCP 服务器在 Web UI 中可见：
1. 中央 `data/mcp_config.json` 中必须包含此服务器 ✅（已写入）
2. 重启 Launcher 或 Agent 后生效
3. 在 UI 中可以看到所有中央配置中的服务器，每个带 `enabled` 开关

---

### 5. 禁用 / 删除 MCP 服务器

```python
with open(central_mcp_path, "r", encoding="utf-8") as f:
    config = json.load(f)

# 方式 A: 禁用（保留在配置中，UI 仍可见但标记为关闭）
config["mcpServers"]["my-filesystem-server"]["enabled"] = false

# 方式 B: 完全删除
# config["mcpServers"].pop("my-filesystem-server", None)

with open(central_mcp_path, "w", encoding="utf-8") as f:
    json.dump(config, f, ensure_ascii=False, indent=2)
```

---

### 6. 常见 MCP 服务器部署模板

#### 文件系统服务器 (Filesystem)

```json
{
  "filesystem": {
    "enabled": true,
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:/workspace"],
    "autoApprove": ["read_file", "write_file", "edit_file", "search_files", "list_directory", "create_directory", "move_file", "get_file_info"]
  }
}
```

#### Puppeteer 浏览器

```json
{
  "puppeteer": {
    "enabled": true,
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-puppeteer"]
  }
}
```

#### Brave 搜索

```json
{
  "brave-search": {
    "enabled": true,
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-brave-search"],
    "env": { "BRAVE_API_KEY": "your-api-key" }
  }
}
```

#### Fetch (网页抓取)

```json
{
  "fetch": {
    "enabled": true,
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-fetch"]
  }
}
```

#### Git 操作

```json
{
  "git": {
    "enabled": true,
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-git", "--repository", "C:/path/to/repo"]
  }
}
```

---

---

### 7. 故障排除

| 现象 | 可能原因 | 解决 |
|------|---------|------|
| Web UI 中看不到新服务器 | 未写入中央配置 | 检查 `data/mcp_config.json` |
| Agent 不识别该工具 | 配置未同步到 agent | 重启 Agent 或 Launcher |
| 调用时报错 "command not found" | 运行时未安装 | 确认 `npx`/`uvx` 已在 PATH |
| 服务器显示为 "disconnected" | 命令或参数错误 | 验证 `command` 和 `args` 正确 |
| **Agent 报 "MCP 未开启服务"** | `config.json` 中 `mcp.enabled` 缺失或为 `false` | 读取自己的 `config.json`，确保 `"mcp": {"enabled": true}` 存在。如果缺失，用 `filesystem` 工具写入。修改配置后需要重启才能生效，可以通过 `system.run_session_job` 调用 `POST http://127.0.0.1:{LAUNCHER_PORT}/api/agents/{agent_id}/restart` 来重启自己 |

---

### 8. MCP 工具注册诊断（关键问题排查）

#### 问题现象

MCP 服务已连接（system prompt 显示 "Active MCP Services"），但 LLM 不调用 MCP 工具，工具列表中缺少 `mcp__xxx` 前缀的工具。

#### 注册链（6 步）

```
MCP服务器连接 → session.list_tools() 发现工具 → _tools_cache 缓存
    → registry.register_mcp_adapter() 注册到Registry
    → registry.generate_openai_tools() 追加到最终工具列表
    → runner._current_tools → chat_api.chat(tools=...) → LLM
```

#### 诊断步骤

**第 1 步：检查 MCP 服务是否已连接**

通过 `filesystem` 读取 Agent 的 `mcp_config.json` 确认服务配置正确：

```python
import json

# 读 Agent 的 mcp_config.json
with open(f"{workspace_path}/agents/{agent_name}/mcp_config.json") as f:
    agent_mcp = json.load(f)

print(json.dumps(agent_mcp, indent=2))
```

**第 2 步：检查注册链是否中断**

通过 `system.run_session_job` 执行诊断脚本来验证 MCP 工具是否进入了工具列表：

```python
import json

# 方案 A: 直接读取 Agent 当前运行时状态
# 检查 agent 进程日志中是否存在以下关键行：
# "[MCP] '{server_name}' tools (N): [...]"  ← 工具已发现
# "[Registry] MCP get_all_tools failed"       ← 注册失败！

# 方案 B: 直接测试 MCPAdapter 能否返回工具
# 通过 logging 或打印来确认 _tools_cache 有数据
logger.info(f"MCP tools cache: {adapter._tools_cache.keys() if adapter else 'None'}")
logger.info(f"Total cached tools: {sum(len(v) for v in adapter._tools_cache.values()) if adapter else 0}")
```

**第 3 步：确认工具命名空间是否正确暴露**

MCP 工具在 LLM 调用中应呈现为 `mcp__playwright__navigate` 格式。如果缺失，说明 `registry.generate_openai_tools()` 中 MCP 合并段（`tools.extend(mcp_tools)`）可能被静默跳过。

查看 Agent 日志中是否有以下关键内容：

```python
# 搜索日志关键字
keywords = [
    "[Registry] MCP get_all_tools failed",  # 静默失败
    "MCP get_all_tools",                     # 注册过程
    "mcp__",                                 # MCP 工具进入列表
]
```

#### 已知的注册问题

| 问题 | 表现 | 位置 |
|------|------|------|
| `get_all_tools()` 异常被静默捕获 | MCP 服务显示已连接，但工具列表中无 `mcp__` 工具 | `registry.py` L161-L168 的 `except Exception: pass` |
| `_tools_cache` 为空 | connect 阶段 `session.list_tools()` 失败但未报错 | `mcp_adapter.py` L338-L345 |
| 缓存未及时刷新 | 热添加 MCP 服务后 `_tools_cache` 未更新 | `mcp_adapter.py` `_tools_cache` 只在 connect 时填充 |

#### 诊断脚本

通过 `system.run_session_job` 直接运行 Python 脚本来确诊：

```python
import sys
import os

workspace = "C:/path/to/workspace"
sys.path.insert(0, workspace)

# 1. 检查 tools 目录是否存在
plugins_dir = os.path.join(workspace, "plugins")
mcp_tools_path = os.path.join(plugins_dir, "mcp_query", "tools.py")
if os.path.isfile(mcp_tools_path):
    print(f"✅ mcp_query plugin exists: {mcp_tools_path}")
else:
    print(f"❌ mcp_query plugin NOT found at {mcp_tools_path}")

# 2. 检查 mcp_config.json 是否存在且有效
data_dir = os.path.join(workspace, "data")
mcp_config = os.path.join(data_dir, "mcp_config.json")
if os.path.isfile(mcp_config):
    import json
    with open(mcp_config) as f:
        cfg = json.load(f)
    servers = cfg.get("mcpServers", {})
    for name, svr in servers.items():
        status = "✅" if svr.get("enabled") else "⏸️"
        print(f"{status} {name}: {svr.get('command')} {' '.join(svr.get('args', []))}")
else:
    print(f"❌ No central mcp_config.json at {mcp_config}")

# 3. 检查 agent 日志中 MCP 注册状态
log_dir = os.path.join(workspace, "data", "logs")
agent_log = os.path.join(log_dir, f"{agent_name}.log")
if os.path.isfile(agent_log):
    with open(agent_log, "r", encoding="utf-8") as f:
        log_text = f.read()
    if "MCP get_all_tools" in log_text:
        print("✅ MCP tools registered in agent log")
    if "mcp__" in log_text:
        print("✅ MCP tools (mcp__ prefix) found in log")
    if "MCP get_all_tools failed" in log_text:
        print("⚠️  MCP get_all_tools FAILED — registration bug, tools not exposed to LLM")
else:
    print(f"❌ Agent log not found at {agent_log}")
```

#### 临时工法（当注册链断裂时）

如果确认是框架注册问题（MCP 服务已连接但工具不注册给 LLM），可直接通过 `system.run_session_job` 用 Python 脚本绕过工具调度层，直接调用 MCP 服务：

```python
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def call_mcp_tool(server_name, tool_name, arguments):
    server_config = mcp_servers[server_name]
    params = StdioServerParameters(
        command=server_config["command"],
        args=server_config["args"],
        env=server_config.get("env")
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            return result.content

result = asyncio.run(call_mcp_tool("playwright", "navigate", {"url": "https://example.com"}))
```
