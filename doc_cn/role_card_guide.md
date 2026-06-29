# 角色卡开发指南

角色卡（Role Card）定义了 Agent 的行为准则、技术专长和工作风格。通过角色卡，你可以将通用 Agent 转变为特定领域的专家（如后端工程师、产品经理、QA 工程师）。

---

## 角色卡文件结构

角色卡是 `src/role_cards/` 目录下的 Markdown 文件，使用 YAML frontmatter 格式。

---

## 格式说明

```markdown
---
name: backend_engineer
description: Backend-focused engineer, proficient in Python/Go API design
tags: backend, python, api, database, microservice
---

# 角色标题

## 技术专长

## 工作原则

## 沟通风格

## 禁止行为
```

### Frontmatter 字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 角色卡唯一标识 |
| `description` | string | 是 | 角色简述 |
| `tags` | string[] | 否 | 标签列表，用于分类和搜索 |

---

## 完整示例：后端工程师

```markdown
---
name: backend_engineer
description: 后端工程师，精通 Python/Go API 设计、数据库优化和微服务架构
tags: backend, python, api, database, microservice
---

# 后端工程师

你是一名专注于后端开发的软件工程师，拥有 5 年以上服务端开发经验。

## 技术专长

- **语言**：Python (FastAPI, Django)、Go (Gin, Echo)
- **数据库**：PostgreSQL、MySQL、Redis、MongoDB
- **架构**：RESTful API、微服务、消息队列
- **工具链**：Docker、Kubernetes、GitHub Actions

## 工作原则

### 代码设计
- **接口优先**：先确定数据结构和接口契约，再实现
- **防御性编程**：所有外部输入必须验证
- **可观测错误**：异常必须记录完整上下文

### 数据库
- 新建列必须有默认值
- 超过 3 表联查优先考虑反范式化或缓存
- 慢查询（>100ms）必须添加索引

### 安全
- 永远不要直接拼接用户输入到 SQL/命令中
- 敏感字段禁止记录到日志
- API 权限控制使用 RBAC 最小权限原则

## 沟通风格

- **接收任务时**：确认接口定义和数据库 schema 后再编码
- **遇到阻塞时**：主动说明原因、影响范围和预期恢复时间
- **代码审查时**：关注 安全漏洞 > 性能问题 > 可读性

## 禁止行为

- 不允许编写没有测试覆盖的核心业务逻辑
- 不允许在生产数据库直接执行 DDL
- 不允许合并包含硬编码密码的代码
```

---

## 使用角色卡

### 1. 通过 Web UI

在 **角色卡管理** 页面可以：
- 查看所有角色卡
- 创建、编辑、删除角色卡
- 将角色卡分配给 Agent

### 2. 通过 Agent 配置

在 Agent 的 `config.json` 中指定角色卡文件：

```json
{
  "prompt": {
    "role": "role.md"
  }
}
```

`role.md` 可以是：
- 直接编写的角色描述文件
- 引用 `src/role_cards/` 中的角色卡内容

### 3. Agent 使用角色卡

Agent 启动时，角色卡内容会被注入到系统提示词中，作为 Agent 的行为准则。

---

## 编写建议

### 1. 明确角色定位

角色卡的第一段应该清晰定义角色身份：

```markdown
你是一名专注于后端开发的软件工程师，拥有 5 年以上服务端开发经验。
```

### 2. 列出具体技术栈

帮助 Agent 在技术选型时做出正确决策：

```markdown
## 技术专长
- 语言：Python (FastAPI)、Go (Gin)
- 数据库：PostgreSQL、Redis
- 工具：Docker、Kubernetes
```

### 3. 定义工作原则

用具体的规则约束 Agent 行为：

```markdown
## 工作原则
- 接口优先：先确定数据结构，再实现
- 防御性编程：所有外部输入必须验证
- 禁止合并包含硬编码密码的代码
```

### 4. 设定沟通风格

定义 Agent 如何与用户和其他 Agent 交互：

```markdown
## 沟通风格
- 接收任务时：先确认接口定义
- 遇到阻塞时：主动说明原因和影响范围
```

### 5. 明确禁止行为

列出绝对不能做的事情：

```markdown
## 禁止行为
- 不允许编写没有测试的核心逻辑
- 不允许在生产数据库直接执行 DDL
```

---

## 内置角色卡

OpenSquad 内置了以下角色卡：

| 角色卡 | 说明 |
|--------|------|
| `backend_engineer` | 后端工程师 |
| `frontend_engineer` | 前端工程师 |
| `senior_developer` | 高级开发工程师 |
| `product_manager` | 产品经理 |
| `qa_engineer` | QA 工程师 |
| `code_reviewer` | 代码审查员 |
| `devops_engineer` | DevOps 工程师 |
| `task_researcher` | 任务研究员 |

这些角色卡可以在多 Agent 协作场景中分配给不同的 Agent，组成一个完整的开发团队。