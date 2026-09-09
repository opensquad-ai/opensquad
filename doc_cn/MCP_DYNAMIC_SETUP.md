# Agent 动态 MCP 服务管理指南

OpenSquad Agent 支持**无需重启**即可动态安装、配置和启动 MCP 服务器。

## 一、动态添加 MCP 服务器

### 方法：使用 `mcp_query.add_server` 工具

在对话中，Agent 可以调用此工具即时添加新的 MCP 服务器：

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

### 参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `server_name` | string | ✅ | 服务器名称（唯一标识） |
| `command` | string | ✅ | 启动命令（如 `npx`, `python`, `node`） |
| `args` | list | ✅ | 命令参数数组 |
| `timeout` | int | ❌ | 超时时间（秒），默认 30 |
| `auto_approve` | list | ❌ | 自动批准的工具列表 |
| `env` | dict | ❌ | 环境变量字典 |

### 示例场景

#### 1. 添加文件系统 MCP
```json
{
  "server_name": "my-filesystem",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/docs"],
  "timeout": 60
}
```

#### 2. 添加 SQLite 数据库 MCP
```json
{
  "server_name": "my-database",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-sqlite", "/path/to/db.sqlite"],
  "timeout": 30
}
```

#### 3. 添加自定义 Python MCP
```json
{
  "server_name": "custom-mcp",
  "command": "python",
  "args": ["/path/to/my_mcp_server.py"],
  "env": {"API_KEY": "xxx"}
}
```

## 二、即时生效机制

### 无需重启！

调用 `add_server` 后：

1. **立即连接** - MCP 服务器进程被启动
2. **工具注册** - 新工具自动进入工具列表
3. **持久化保存** - 配置写入 `pymcp/config_basic.json`
4. **立即可用** - Agent 可以在当前对话中使用新工具

### 使用新工具的两种方式

#### 方式 1：Agent 自动识别
Agent 的工具列表是动态生成的，新 MCP 工具会自动以 `mcp__{server_name}__{tool_name}` 格式出现：

```xml
<tool_call>
  <name>mcp__my-filesystem__read_file</name>
  <arguments>{"path": "/home/user/docs/readme.md"}</arguments>
</tool_call>
```

#### 方式 2：查询可用工具
调用 `mcp_query.get_all_tools()` 查看所有可用 MCP 工具。

## 三、管理 MCP 服务器

### 查看所有服务器
```xml
<tool_call>
  <name>mcp_query.list_servers</name>
  <arguments>{}</arguments>
</tool_call>
```

返回：
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

### 移除服务器
```xml
<tool_call>
  <name>mcp_query.remove_server</name>
  <arguments>{"server_name": "my-filesystem"}</arguments>
</tool_call>
```

立即断开连接并删除配置。

### 重新连接
```xml
<tool_call>
  <name>mcp_query.reconnect_server</name>
  <arguments>{"server_name": "chrome-devtools"}</arguments>
</tool_call>
```

### 重新加载配置
如果你手动编辑了 `pymcp/config_basic.json`：

```xml
<tool_call>
  <name>mcp_query.reload_servers</name>
  <arguments>{}</arguments>
</tool_call>
```

## 四、配置文件位置

虽然 Agent 可以动态管理 MCP，但配置实际存储在：

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

## 五、Playwright 浏览器登录态持久化

OpenSquad 在连接 Playwright MCP 时，会自动为工作区注入持久化浏览器配置目录（除非你在 `mcp_config.json` 里已显式配置 `--user-data-dir` 或 `--isolated`）：

**`data/mcp_browser_profiles/playwright/`**

该目录保存 Cookie、localStorage 等登录态。首次用 Playwright 打开站点并登录后，**同一工作区内所有 Agent 共享**这份登录态；重启 Agent 或重连 MCP 后一般无需再次登录。

### 使用建议

1. 用 Playwright MCP 打开目标站点（如 DeepSeek），完成一次登录。
2. 之后在同一工作区继续用 `mcp__playwright__browser_navigate` 等工具即可复用会话。
3. 若要清除登录态，删除 `data/mcp_browser_profiles/playwright/` 目录后重连 MCP。

### 注意事项

- **同一时刻只能有一个浏览器实例**占用该 profile；多个 Agent 并行调用 Playwright 可能冲突。需要并行时请为额外实例配置不同的 `--user-data-dir`，或使用 `--isolated`（不持久化）。
- 若你手动在 `args` 中写了 `--user-data-dir=...`，系统不会覆盖你的配置。

## 六、常用 MCP 服务器推荐

| MCP 服务器 | 安装命令 | 功能 |
|-----------|---------|------|
| **filesystem** | `@modelcontextprotocol/server-filesystem` | 文件读写 |
| **sqlite** | `@modelcontextprotocol/server-sqlite` | SQLite 数据库 |
| **github** | `@modelcontextprotocol/server-github` | GitHub API |
| **postgres** | `@modelcontextprotocol/server-postgres` | PostgreSQL 数据库 |
| **puppeteer** | `@modelcontextprotocol/server-puppeteer` | 浏览器自动化 |
| **sequential-thinking** | `@langgpt/sequential-thinking-mcp` | 思维链 |

## 七、故障排查

### 服务器连接失败
1. 检查命令是否正确安装：`npx -v` 或 `python --version`
2. 查看详细错误：`mcp_query.list_servers()`
3. 尝试重新连接：`mcp_query.reconnect_server()`

### 工具未生效
确保在调用新工具前，系统已完成工具列表更新（通常 1-2 秒）。

### 端口冲突
如果 MCP 服务器需要端口，系统会自动寻找可用端口。

---

**总结**：OpenSquad Agent 的 MCP 管理完全动态化，安装 → 配置 → 启动 → 使用，全程无需重启 Agent！
