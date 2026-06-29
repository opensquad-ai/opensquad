# 技能开发指南

技能（Skill）是 OpenSquad 中的可复用任务指令包，Agent 可以按需加载技能来获取特定任务的专业知识和操作流程。

技能系统兼容 Claude Code / AgentSkills.io 开放标准。

---

## 技能系统架构

### 公共技能 vs 私有技能

| 类型 | 存储位置 | 加载方式 | 注入方式 |
|------|----------|----------|----------|
| 公共技能 | `src/skills/` | 自动发现 | 摘要注入（名称+描述），按需激活 |
| 私有技能 | Agent 目录的 `skills/` | 配置文件指定 | 完整注入 Prompt |

### 加载流程

```
Agent 启动
    │
    ├─ 1. 加载私有技能（agent_dir/skills/）
    │     config.json 中 skills.active 列出的技能完整注入
    │
    └─ 2. 加载公共技能（src/skills/）
          自动发现所有 SKILL.md，仅注入摘要
          Agent 可通过 read_skill() 按需激活
```

---

## 技能目录结构

```
skills/
├── my-skill/           # 技能目录名（技能标识符）
│   ├── SKILL.md        # 技能指令文件（必需）
│   ├── skill.json      # 技能元数据（可选，用于市场展示）
│   ├── tools.py        # 附加工具模块（可选）
│   └── scripts/        # 脚本目录（可选）
│       └── helper.py
```

---

## SKILL.md 格式

SKILL.md 使用 YAML frontmatter + Markdown 主体格式：

```markdown
---
name: my-skill
description: 简短描述这个技能做什么
disable-model-invocation: false
allowed-tools: filesystem, web
---

# 技能标题

## 概述

这个技能用于...

## 工作流程

### 步骤 1：...

### 步骤 2：...

## 注意事项

- ...
```

### Frontmatter 字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 否 | 显示名称，默认使用目录名 |
| `description` | string | 否 | 技能描述，用于摘要展示 |
| `disable-model-invocation` | bool | 否 | 禁止模型自动调用，默认 false |
| `user-invocable` | bool | 否 | 允许用户手动调用，默认 true |
| `allowed-tools` | string | 否 | 逗号分隔的允许工具列表 |

> 如果 frontmatter 中没有 `description`，系统会自动取正文第一段作为描述。

---

## skill.json 格式（可选）

用于技能市场的元数据展示：

```json
{
  "name": "data-analysis",
  "display_name": "Data Analysis",
  "version": "1.0.0",
  "description": "Analyze Excel (.xlsx/.xls) or CSV files...",
  "author": "OpenSquad",
  "tags": ["data", "excel", "csv", "analysis"],
  "category": "analysis"
}
```

| 字段 | 说明 |
|------|------|
| `name` | 技能标识符，与目录名一致 |
| `display_name` | 显示名称 |
| `version` | 版本号 |
| `description` | 技能描述 |
| `author` | 作者 |
| `tags` | 标签列表 |
| `category` | 分类 |

---

## 完整示例：代码审查技能

### 目录结构

```
skills/code_reviewer_lite/
├── SKILL.md
└── tools.py
```

### SKILL.md

```markdown
---
name: code-reviewer
description: 代码审查助手，检测代码异味、安全问题和 TODO 注释
allowed-tools: filesystem
---

# 代码审查技能

## 概述

对 Python 和 TypeScript 代码进行自动化审查，检测常见问题。

## 工作流程

1. 确定要审查的文件或目录
2. 调用 review_file() 或 review_directory() 进行审查
3. 整理问题列表，按严重程度排序
4. 向用户报告审查结果，给出修复建议

## 可用工具

- `review_file(path)` — 审查单个文件
- `review_directory(path)` — 审查整个目录
- `find_todos(path)` — 查找 TODO 注释
- `estimate_complexity(path)` — 估算圈复杂度
```

### tools.py

```python
def review_file(path: str) -> dict:
    """
    审查单个文件并返回问题列表。

    Args:
        path: 文件路径（支持 .py / .ts / .tsx）

    Returns:
        {"file": str, "issues": [...], "summary": str}
    """
    # 实现代码审查逻辑
    ...

def review_directory(path: str) -> dict:
    """
    批量审查目录中的所有文件。

    Args:
        path: 目录路径

    Returns:
        {"files_reviewed": int, "total_issues": int, "results": [...]}
    """
    ...
```

> **关键**：`tools.py` 中的函数会被自动注册为 Agent 可调用的工具。函数名即工具名，docstring 即工具描述。

---

## 技能注入机制

### 完整注入（私有技能）

私有技能的全部内容直接注入到 Agent 的系统提示词中：

```markdown
## Skills

### Full-injected Skills (2)

#### my-skill - 描述
*Allowed tools: filesystem, web*

（完整的 SKILL.md 内容）
```

### 摘要注入（公共技能）

公共技能仅注入名称和描述，Agent 按需激活：

```markdown
### Summary Skills (15, activate/read on demand)

**Important: Before starting any complex task, first check if a relevant skill exists in the library.**

How to use skills:
- Use `agent_setup.list_skills()` to see all available skills
- Use `agent_setup.read_skill(skill_name)` for one-time lookup
- Use `agent_setup.publish_skill(skill_dir)` to contribute a skill

- **Data Analysis** (`data-analysis`): Analyze Excel/CSV files...
- **Code Reviewer** (`code_reviewer_lite`): 代码审查助手...
```

### 控制注入级别

在 Agent 配置中可以通过 `prompt_preload` 控制：

```json
{
  "prompt_preload": {
    "full_skills": ["code_reviewer_lite"],
    "hidden_skills": ["deprecated-skill"],
    "include_skills": true
  }
}
```

- `full_skills`：将这些公共技能完整注入
- `hidden_skills`：隐藏这些技能（不在摘要中列出）
- `include_skills`：是否包含摘要，默认 true

---

## 运行时 API

Agent 在运行时可以通过以下工具管理技能：

| 方法 | 说明 |
|------|------|
| `list_skills()` | 列出所有已加载的技能 |
| `read_skill(name)` | 读取并激活指定技能的完整内容 |
| `publish_skill(dir)` | 发布技能到公共库（立即生效，无需重启） |
| `add_skill(dir, name)` | 热加载新技能 |
| `remove_skill(name)` | 移除已加载的技能 |

---

## 最佳实践

### 1. 技能应该是自包含的

技能应该包含完成任务所需的全部信息，Agent 加载后即可独立执行。

### 2. 提供清晰的工作流程

用步骤化的方式描述工作流程，让 Agent 能够按步骤执行：

```markdown
### 步骤 1：理解需求
### 步骤 2：检查文件
### 步骤 3：执行分析
### 步骤 4：报告结果
```

### 3. 使用具体示例

在 SKILL.md 中提供具体的命令示例，帮助 Agent 理解如何调用工具。

### 4. 合理使用 tools.py

- 将复杂的逻辑封装在 `tools.py` 中
- 函数 docstring 要清晰描述参数和返回值
- 避免在 SKILL.md 中写大量代码，应该放在 tools.py 中

### 5. 技能粒度

- 一个技能专注一个任务领域
- 不要创建一个"万能"技能
- 技能之间可以通过 Agent 的决策来组合使用