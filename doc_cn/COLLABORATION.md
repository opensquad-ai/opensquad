# OpenSquad 协作指南

## 概览

OpenSquad 使用**协作卡片驱动**的协作模型。Agent 之间通过以下方式进行协调：
1. **群聊** — 主要通信渠道（自然语言）
2. **协作卡片（Collab Card）** — 结构化工作流协议，定义角色、阶段和规则
3. **协作看板（Collaboration Board）** — 每张协作一张看板，存在**拥有该群的那台机器**上
   （`collab_board.py`），以 6 位 `collab_id` 为键，通过看板工具读写

看板是**每台机器上集中**的，不是一堆各写各的 Agent 备注：需求、方案、任务、进度、讨论都是看板
上分区的条目，所有 Agent 用同一套工具读写它；由对端机器拥有的协作会经中继访问。存储、归属与转
发规则见 `COLLAB_BOARD_DESIGN.md`。

---

## 协作卡片系统

协作卡片以平铺文件的形式存储在 `collab_cards/*.md` 中。每张卡片定义：
- **建议角色**（仅供参考——由 PM 决定实际邀请谁）
- **工作流阶段**（规划、执行、评审、用户验收）
- **通信规则**（@提及、睡眠/唤醒模式）
- **看板规则**（谁写哪个分区、什么时候写）

当前可用的卡片：
- `software_dev_team` — 完整软件开发生命周期（PM / Developer / QA）
- `general_software_dev_collab` — 通用软件开发协作（PM / Dev / QA / DevOps / Reviewer）
- `code_review` — 代码评审工作流（Reviewer / Author）
- `distributed_deep_research` — 分布式深度调研（PM / Researcher / Analyst）

### 协作卡片 Frontmatter 格式

```yaml
---
name: software_dev_team
description: ...
tags: software, team, pm, dev, qa, full-cycle
suggested_roles: pm, developer, qa
min_members: 2
---
```

`suggested_roles` 是**建议性**的——PM 根据任务需要决定邀请谁。卡片在协作期间加载到每位参与者的 Prompt 中。

### 生命周期工具

| 工具 | 调用方 | 作用 |
|------|--------|------|
| `start_collaboration(card, members?)` | PM | 将卡片加载到 PM 的 Prompt，可选邀请成员 |
| `join_collaboration(card)` | Worker | 将卡片加载到 Worker 的 Prompt |
| `end_collaboration(card)` | PM | 卸载卡片，通知成员 |
| `leave_collaboration(card)` | Worker | 卸载卡片 |
| `get_team_status()` | 任意人 | 查看所有 Agent 的实时状态（idle/working/sleeping） |
| `get_group_roster(group_id)` | PM / Worker | 列出特定群组中的 Agent 成员（交叉引用 config.json） |
| `list_collab_cards()` | 任意人 | 列出可用协作卡片及建议角色 |
| `list_active_collaborations()` | 任意人 | 列出当前活跃的协作会话 |

### Board & Task 工具

| 工具 | 调用方 | 作用 |
|------|--------|------|
| `assign_task(collab_id, worker_id, task_name, ...)` | PM | 给 Worker 派活，带检查项 |
| `add_subtask(collab_id, item_key, title, ...)` | PM / Worker | 给已有任务项加子任务 |
| `update_task_progress(collab_id, item_key, subtask_id, ...)` | Worker | 更新某个子任务的进度 |
| `batch_update_tasks(collab_id, item_key, updates)` | Worker | 批量更新多个任务项 |
| `board_update(collab_id, task_name?, title?, ...)` | PM / Worker | 更新协作看板条目 |
| `board_list(collab_id, agent_id?, scope?, item_type?)` | 任意人 | 读取协作看板条目 |
| `board_view(collab_id)` | 任意人 | 查看完整协作看板（全部分区） |
| `board_list_tasks(collab_id)` | 任意人 | 查看该协作的全部派活 |
| `board_list_my_tasks(collab_id, scope?, debug?)` | Worker | 列出当前 Agent 自己的检查项 |
| `board_post_public_discussion(collab_id, task_name, title, content)` | 任意人 | 发布对全队可见的讨论/决策 |

### PM 主导模型

PM Agent 自主推进协作：
1. PM 调用 `list_collab_cards()` 查看可用协议
2. PM 调用 `start_collaboration(card="software_dev_team")` — 卡片加载到 PM 的 Prompt
3. PM 查看卡片中的 `suggested_roles`，然后通过群聊决定邀请谁
4. PM 也可以在 `start_collaboration()` 中传入 `members=["coder", "qa"]` 进行自动邀请
5. Worker 收到邀请后调用 `join_collaboration(card="software_dev_team")`

---

## 协作看板（Collaboration Board）

每张协作拥有一块看板（一个 `collab_id`），看板就是这次协作的记忆：需求、方案、任务分工、进度与
讨论全都在上面，作为分区（`requirement` / `plan` / `task` / `status` / `discussion`）里的条目。

- **归属**：看板属于**拥有该群的那台机器**，看板工具会按 `collab_id` 解析出主人 —— 所以对端机器
  上的 Agent 读写的是**同一块**看板（经中继），而不是自己的一份副本。
- **读**：`board_view(collab_id)` 看整块看板；`board_list(...)` 过滤；`board_list_tasks(collab_id)`
  看派活；`board_list_my_tasks(collab_id)` 看自己的活。
- **写**：`board_update(...)` 写条目；`assign_task(...)` 建任务项与检查项；`update_task_progress(...)`
  推进某个子任务；`board_post_public_discussion(...)` 发布全队可见的决策。
- **审批**：四道门（确定需求 / 讨论方案 / 任务分配 / 任务验收）是看板上 `approval` 类型的条目。
  用户在任务窗里批准它们，而前两道未通过前 `assign_task` 会拒绝派活。

阶段切换前**读看板**、而不是读某个人的私有备注；决策**写进看板**、而不是只在群聊里说一遍 ——
消息会被刷走，看板条目不会。

### 结构化任务项

`assign_task` 写入的任务项在 `extra` 里带结构化子任务（id / 标题 / 状态 / 进度 / 备注），
`update_task_progress` 一次推进一个子任务。条目渲染出来的正文由这些子任务生成 —— 所以检查项标记
和子任务表是**同一份事实**，不是两份副本。

---

## VCS 审计与透明度

Agent 在本地协作时，经常需要与远程仓库（GitHub）交互。OpenSquad 实现了 **VCS 审计系统**，确保所有 Agent 行为的透明性和可追责性。

### 工作原理

1. **自动记录足迹**：`vcs_remote` 和 `git_core` 插件自动捕获每条命令（如 `commit`、`push`、`pr_create`）。
2. **身份保留**：即使所有 Agent 使用同一个 GitHub 账号（通过 `gh auth login` 配置），审计系统也会记录触发操作的**内部 Agent ID**。
3. **审计日志**：每次操作的参数、状态和原始输出均保存到 `data/audit/vcs_footprints.jsonl`。

### 查看审计时间线

可以在 Web UI 中查看协作历史：
1. 从侧边栏进入 **VCS Audit** 视图。
2. 从项目列表中选择一个仓库。
3. 浏览所有 Agent 活动的时间线。
4. 展开任意条目可查看使用的精确参数和命令输出。

该系统对以下场景至关重要：
- **调试**：定位是哪个 Agent 导致了某个 Git 状态。
- **评审**：查看 PR 创建或合并的原始输出。
- **问责**：在共享 GitHub 账号环境中追踪"谁做了什么"。

---

## 软件开发工作流

在 `collab_cards/software_dev_team.md` 中定义。

### 阶段 1：规划
1. PM 接收用户需求（通过 Web UI 或群聊）
2. PM 与用户确认范围
3. PM 设计架构，定义接口约定
4. PM 通过群聊中的 @提及分配任务

### 阶段 2：执行
1. Worker 在群聊中读取 PM 的分配内容，并在看板上看自己的任务项
2. Worker 用 `update_task_progress(collab_id, item_key, subtask_id, ...)` 推进自己的子任务
3. Worker 实现功能，进度随做随写看板
4. Worker 在群聊中汇报完成情况或阻塞问题
5. PM 通过读看板（`board_list_tasks`）跟进进度，而不是轮询文件

### 阶段 3：评审与迭代
1. PM 审查完成的工作（读取代码、检查输出、读看板）
2. 通过：PM 在群聊中确认，并把子任务推进到 done
3. 拒绝：PM 提供反馈，Worker 修改后重新提交

### 阶段 4：用户验收
1. PM 审查所有完成的工作与看板
2. PM 通过 `<to_user>` 向用户报告摘要
3. 用户同意：PM 调用 `end_collaboration(card="software_dev_team")` 关闭协作
4. 用户要求变更：返回阶段 2

---

## 通信模式

### @提及
在群聊中使用 `@agent_name` 通知特定 Agent。例如：`@coder 请实现登录模块`。

### 睡眠/唤醒
在等待其他 Agent 时阻塞：
```xml
<sleep seconds="120">等待 @coder 完成</sleep>
```
唤醒后，在继续执行前先检查群聊中的更新。

### 状态检查
任意 Agent 可以调用 `get_team_status()` 查看整个团队所有人的 idle/working/sleeping 状态。

对于 PM Agent，推荐的模式是：
1. 调用 `im.list_groups()` 查看已加入的群组
2. 调用 `get_group_roster(group_id)` 查看特定群组中的 Agent 成员——这提供了群组范围的视图，在多群组部署场景下比全局 `get_team_status()` 更相关

---

## 与历史架构的对比

| 方面 | v3.0（Blueprint 系统） | v4.0（Collab Card 系统） |
|------|----------------------|------------------------|
| 协议存储 | `blueprints/{name}/BLUEPRINT.md`（子目录） | `collab_cards/{name}.md`（平铺文件） |
| 角色分配 | `roles` 字段（隐含强制） | `suggested_roles`（建议，PM 决定） |
| start 中的 members | 必填列表 | 可选——PM 通过群聊决定 |
| 状态共享 | `workspace/` 中的黑板（ProjectBoard） | 协作看板，每台机器一块（`collab_board.py`） |
| 团队注册 | `workspace/TEAM.md` 手动文件 | `get_team_status()` 实时 API |
| PM 可见性 | 需要解析黑板 | `board_view` / `board_list_tasks` |
| 任务持久化 | 仅内存 | 看板条目，持久化在拥有它的机器上 |
