# 任务协作看板设计与使用文档

## 1. 目标与定位

任务协作看板用于多 Agent 协作场景下的**任务级可视化管理**，核心目标：

1. 监控各 Agent 当前状态与进度
2. 避免任务漂移（错误理解导致无效作业）
3. 沉淀公开协作上下文（抗遗忘）
4. 支持多任务并行与历史任务回看

看板强调“状态与进度”，而非完整执行日志。

---

## 2. 核心设计原则

### 2.1 任务隔离（Task-Scoped）

每个协作任务都有独立：

- `task_id`（即 `collab_id`）
- `task_name`

所有看板读写都必须带 `collab_id`，确保不同任务数据不混淆。

### 2.2 自动任务 ID（6 位字母数字）

每次启动协作任务时自动生成 6 位混合 ID（例如：`a8K2pQ`）。

### 2.3 最新工具快照策略

工具调用量大，默认只保存**最新工具调用摘要**，不保存完整工具历史：

- `latest_tool_name`
- `latest_tool_summary`

### 2.4 公共讨论区（共享记忆）

独立存储 `discussion` 项，记录对全体可见的任务方案、决策、约束与关键上下文。

### 2.5 PM 可控进度

任务总进度由 PM Agent 或 Web 管理端更新（`task.progress`），用于统一对齐。

---

## 3. 数据模型

## 3.1 任务记录（Task）

存储文件：`data/collab_board/board_tasks.json`

关键字段：

- `task_id`：6 位任务 ID
- `task_name`：任务名称
- `created_by`
- `status`：`active | done | archived`
- `progress`：0~100（PM 维护）
- `created_at`
- `started_at`（默认等于 `created_at`）
- `ended_at`（结束时写入）
- `updated_at`
- `closed_at`
- `duration_seconds`（列表聚合时计算）

## 3.2 看板条目（Item）

存储文件：`data/collab_board/board_items.json`

关键字段：

- `id`
- `collab_id` / `task_id`
- `task_name`
- `agent_id`
- `item_type`：`task | status | plan | progress | discussion | ...`
- `title`
- `content`
- `status`
- `progress`
- `visibility`：`public | private`
- `latest_tool_name`
- `latest_tool_summary`
- `created_at`
- `updated_at`

说明：

- 对于同一 `(collab_id, agent_id, item_type)`，采用 **upsert 覆盖**（保留最新状态）
- `discussion` 为追加记录（不会覆盖）

---

## 4. 后端 API

前缀：`/api/ai-web/collab-board`

### 4.1 任务相关

1. `GET /tasks`
   - 获取任务列表（含统计）

2. `POST /tasks`
   - 创建任务
   - 请求体：`{ task_name, created_by? }`
   - 返回包含自动生成的 `task_id`

3. `PUT /tasks/{task_id}`
   - 更新任务元信息
   - 请求体可含：`task_name`, `progress`, `status`

### 4.2 看板项相关

4. `GET /items?collab_id=...&agent_id=...&scope=public|all`
   - 获取指定任务下看板项
   - `collab_id` 必填

5. `POST /items`
   - upsert 看板项
   - 请求体必须包含 `collab_id`

6. `POST /discussions`
   - 追加公开讨论项
   - 请求体必须包含 `collab_id`

---

## 5. Agent 工具使用方式

文件：`src/opensquad/tools/collaboration.py`

### 5.1 启动协作

`start_collaboration(...)`

行为：

- 加载协作卡
- 自动创建协作任务（生成 6 位 `task_id`）
- 返回 `task` 信息

### 5.2 写入看板状态

`board_update(collab_id, title, content, status, progress, visibility, item_type)`

必须传 `collab_id`（任务 ID）。

### 5.3 查询看板

`board_list(collab_id, agent_id?, scope?)`

必须传 `collab_id`。

### 5.4 发布公共讨论

`board_post_public_discussion(collab_id, task_name, title, content)`

用于沉淀可共享、可回看的公共协作决策。

---

## 6. 自动同步行为

在 `runner.py` 中，工具调用后会自动同步“最新工具快照”到当前活动任务：

- 自动更新 `latest_tool_name`
- 自动更新 `latest_tool_summary`
- 不写完整历史

这样看板可持续反映 Agent 当前动作，不会因日志量过大变得不可读。

---

## 7. 前端使用说明（CollabBoardPage）

入口：侧边栏「协作看板」

支持能力：

1. 任务切换
   - 顶部选择任务（`task_name + task_id`）

2. 新建任务
   - 点击「新建任务」，自动生成 6 位任务 ID

3. 按成员筛选
   - 查看全部成员或单成员状态

4. PM 进度编辑
   - 直接编辑并保存任务总进度

5. 时间信息查看
   - 开始时间 `started_at`
   - 结束时间 `ended_at`
   - 耗时 `duration_seconds`（格式化显示）

6. 公开讨论区
   - 查看讨论历史
   - 发布新的讨论结论（任务级）

---

## 8. 推荐协作流程（实践）

1. PM 调用 `start_collaboration`，获得 `task_id`
2. PM 在群聊与看板同步发布：本任务统一使用该 `task_id`
3. 每个 Agent 开工即 `board_update(collab_id=task_id, ...)`
4. 关键里程碑更新进度与状态
5. 有分歧/方案确认时发布 `discussion`
6. PM 持续维护任务总进度
7. 任务完成后将任务状态更新为 `done`，记录结束时间

---

## 9. 常见问题

### Q1：为什么必须传 `collab_id`？
A：避免多任务并行时数据串线，保证任务看板隔离。

### Q2：为什么不保存所有工具调用？
A：完整工具日志噪声过大，影响管理效率。看板只需“当前状态快照”。

### Q3：如何抗遗忘？
A：通过任务级公开讨论区沉淀关键结论，供全员随时回看。

---

## 10. 文件位置总览

- 存储层：`src/opensquad/collab_board.py`
- Agent 工具：`src/opensquad/tools/collaboration.py`
- 自动同步：`src/opensquad/runner.py`
- 后端 API：`src/opensquad/gateway/backend/app/ai_web/routes.py`
- 前端 API：`src/opensquad/gateway/nexuschat-pro/services/api.ts`
- 看板页面：`src/opensquad/gateway/nexuschat-pro/components/CollabBoardPage.tsx`

---

如需进一步扩展，建议下一步补充：

1. 任务权限模型（仅 PM 可改任务总进度）
2. 任务标签与优先级
3. 任务 SLA/超时预警
4. 看板时间轴视图（状态变更审计）
