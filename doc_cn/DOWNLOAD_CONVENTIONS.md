# 外部资源下载与安装规范

Agent 在从网络下载文件、安装工具时，**必须遵守本规范**。违反规范（如将文件写入项目根目录）可能破坏框架运行、影响其他 Agent，甚至导致数据丢失。

---

## 一、禁止区域（绝对禁止写入）

以下路径属于框架基础设施，任何情况下不得向其中写入或覆盖文件（未获得用户明确授权）：

| 禁止路径 | 原因 |
|---------|------|
| 项目根目录（`/` 直接下的文件） | 根目录污染，难以清理，可能覆盖配置文件 |
| `opensquad/` | 框架核心包，修改影响全部 Agent |
| `gateway/` | Gateway 前后端代码，改动破坏 Web UI |
| `agents/boot.py` | 所有 Agent 通用启动器 |
| `launcher.py` | 多进程管理器 |
| `system_config.py` / `system_config.json` | 全局配置中心 |
| `plugins/` 已有目录内部 | 可能覆盖已有插件 |
| `agents/{name}/` 下非数据目录 | 可能覆盖 Agent 配置 |

---

## 二、按类型的正确落地路径

### 2.1 Skill 技能包

Skill 是含有 `SKILL.md` 的目录，可选含 `tools.py`。

| 用途 | 落地路径 | 说明 |
|------|---------|------|
| **全局公有**（所有 Agent 可用） | `skills/{skill_name}/` | 自动被所有 Agent 发现 |
| **Agent 私有**（仅当前 Agent 可用） | `agents/{name}/skills/{skill_name}/` | 还需在 `config.json` 的 `skills.private` 列表中添加名称 |

下载并安装 Skill 的完整流程：

```
1. 将 SKILL.md（必须）和 tools.py（可选）写入目标目录
   例：skills/my_new_skill/SKILL.md

2. 调用 install_skill 热加载，无需重启：
   install_skill(skill_dir="<项目根>/skills/my_new_skill")

3. 验证：list_installed() 确认 skill 出现在列表中
```

SKILL.md 最小格式：

```markdown
---
name: my_new_skill
description: 简短描述（一句话）
allowed-tools: filesystem
---

## 技能说明

...正文指令...
```

---

### 2.2 MCP Server

MCP server 本身通常不需要"下载文件"，通过 `npx` 按需拉取 npm 包。需要写入的只有**配置**。

| 用途 | 落地路径 | 说明 |
|------|---------|------|
| **全局默认**（所有 Agent 共用） | `pymcp/config_basic.json` 的 `mcpServers` 字段 | 优先级最低 |
| **Agent 专属**（仅当前 Agent） | `agents/{name}/mcp_config.json` 的 `mcpServers` 字段 | 优先级高于全局 |

**不要手动写配置文件**，而是调用工具让框架处理持久化：

```xml
<tool_call>
  <name>mcp_query.add_server</name>
  <arguments>
    {
      "server_name": "my-mcp",
      "command": "npx",
      "args": ["-y", "@scope/mcp-package", "/path/to/target"],
      "timeout": 60
    }
  </arguments>
</tool_call>
```

`add_server` 会自动：写入配置 → 启动进程 → 注册工具，**立即可用，无需重启**。

如需 Agent 专属而非全局，在调用前先检查 `agents/{name}/mcp_config.json` 是否存在，不存在则创建：

```json
{
  "mcpServers": {}
}
```

然后调用 `add_server`，再手动将条目移动到 `agents/{name}/mcp_config.json`（或通知用户由用户决定范围）。

---

### 2.3 任务/项目相关文件

Agent 在执行任务时产生或下载的代码、文档、数据等：

| 内容类型 | 落地路径 | 说明 |
|---------|---------|------|
| 项目代码、工程文件 | `workspace/projects/{project_name}/` | 协作任务的标准工作区 |
| 数据文件（CSV、JSON、图片等） | `workspace/data/{context}/` | 按上下文分目录 |
| 临时/中间文件 | `workspace/tmp/` | 可随时清理，不保证持久 |
| Agent 私有数据（不需协作共享） | `agents/{name}/data/` | 仅当前 Agent 使用 |

---

### 2.4 通用脚本/工具文件

下载的 Python 工具脚本、Shell 脚本等，**绝对禁止放在项目根目录**。

| 用途 | 落地路径 |
|------|---------|
| 属于某个 Skill 的辅助脚本 | 放入对应 `skills/{name}/` 目录 |
| 任务专属脚本 | `workspace/projects/{name}/scripts/` |
| 全局工具脚本 | `scripts/` 目录（已存在） |

---

### 2.5 pip 包 / npm 包

通过 `run_command` 安装包时，**优先使用虚拟环境或用户级安装**，不要修改系统 Python 环境。

```python
# 推荐（用户级，不影响系统）
run_command("pip install --user some-package")

# MCP server 用 npx，按需拉取，不需要手动安装
run_command("npx -y @scope/mcp-package --version")  # 验证可用
```

---

## 三、下载前的决策树

```
需要下载外部资源
       |
       ├─ 是 Skill 包（SKILL.md）？
       │     ├─ 所有 Agent 通用 → skills/{skill_name}/
       │     └─ 仅当前 Agent   → agents/{name}/skills/{skill_name}/
       │
       ├─ 是 MCP server 配置？
       │     └─ 调用 mcp_query.add_server（不要手动写文件）
       │
       ├─ 是任务/项目文件？
       │     ├─ 多 Agent 协作   → workspace/projects/{name}/
       │     ├─ 数据文件        → workspace/data/{context}/
       │     └─ 临时文件        → workspace/tmp/
       │
       └─ 是工具脚本？
             ├─ 属于某 Skill   → skills/{skill_name}/
             └─ 独立工具       → scripts/
```

---

## 四、写文件前必须确认的检查项

在调用 `filesystem.write_file` 写入任何下载内容前，逐项确认：

1. **路径不在禁止区域**（参见第一节）
2. **路径不是项目根目录**（文件路径中有至少一层子目录）
3. **如果是新目录，已先用 `list_directory` 确认父目录存在且符合预期**
4. **如果覆盖已有文件，已用 `read_file` 确认内容，并明确告知用户**
5. **如果是 Skill，写完后调用 `install_skill` 激活**

---

## 五、快速参考

| 下载内容 | 命令/工具 | 落地路径 |
|---------|---------|---------|
| 公有 Skill | `write_file` + `install_skill` | `skills/{name}/SKILL.md` |
| 私有 Skill | `write_file` + `install_skill` + 更新 config | `agents/{name}/skills/{name}/SKILL.md` |
| MCP server | `mcp_query.add_server` | 自动写入 `pymcp/config_basic.json` |
| 项目代码 | `write_file` | `workspace/projects/{name}/` |
| 数据文件 | `write_file` | `workspace/data/{context}/` |
| 临时文件 | `write_file` | `workspace/tmp/` |
| pip 包 | `run_command pip install --user` | Python 用户目录（系统管理） |
| npm 包 | `run_command npx -y ...` | node_modules（系统管理） |
