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

### 2.2 自动任务 ID（6 位大写十六进制）

每次启动协作任务时自动生成 6 位大写十六进制 ID（例如：`A8F2C1`）——即 UUID4 前 6 位转大写（`collab_board._gen_task_id`）。

### 2.3 最新工具快照字段（已不再自动写入）

条目带 `latest_tool_name` / `latest_tool_summary`，保存**最新工具调用摘要**而非完整历史。过去由 runner 自动填充，该自动同步已下线（见 §6）；如今只有调用方显式传入时才会写入。

### 2.4 公共讨论区（共享记忆）

独立存储 `discussion` 项，记录对全体可见的任务方案、决策、约束与关键上下文。

### 2.5 清单派生的进度

任务项的 `status` 与 `progress` **由条目自身内容里的清单标记派生**——`[x]` 完成、`[>]` 进行中、`[ ]` 待办，仅在行首匹配且跳过围栏代码块（`collab_board._derive_task_status_progress_from_content`）。Worker 用 `collaboration.update_task_progress(subtask_id, status)` 推进子任务，不必重写 Markdown；agent 被明确要求**不要**用 `board_update` 更新进度。

---

## 3. 数据模型

## 3.1 任务记录（Task）

存储文件：`<workspace>/data/collab_board/board_tasks.json`（`syscfg.workspace_data_dir("collab_board")`——每个 workspace 一份，同机两个安装不共享看板）

关键字段：

- `task_id`：6 位大写十六进制任务 ID
- `task_name`：任务名称
- `created_by`
- `status`：`active | done | failed | archived | stale`（`stale` 由 `cleanup_stale_tasks` 写入）
- `progress`：0~100（由清单标记派生——见 2.5）
- `created_at`
- `started_at`（默认等于 `created_at`）
- `ended_at`（结束时写入）
- `updated_at`
- `closed_at`
- `duration_seconds`（列表聚合时计算）

## 3.2 看板条目（Item）

存储文件：`<workspace>/data/collab_board/board_items.json`

关键字段：

- `id`
- `collab_id` / `task_id`
- `task_name`
- `agent_id`
- `item_type`：`requirement | requirement_doc | plan | task | discussion | change_request | approval | attachment`（外加 `task_meta`）——任务窗口的分组键就是 `collab_board._SUMMARY_ITEM_TYPES`
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

## 6. 自动同步行为（已下线）

runner 过去会在每次工具调用后写入一条 `item_type="status"` 条目——即每个 agent 的“最新工具调用”流，显示为任务窗口的「任务进度区」。该区域已移除：进度由任务条目自身的清单标记承载，不再有任何地方自动写 status 条目。`latest_tool_name` / `latest_tool_summary` 仍保留在条目 schema 上（见 2.3），但不再自动填充。

---

## 7. 前端使用说明（CollabBoardPage）

入口：侧边栏「协作看板」

支持能力：

1. 任务切换
   - 顶部选择任务（`task_name + task_id`）

2. 新建任务
   - 点击「新建任务」，自动生成 6 位任务 ID

3. 需求 / 方案文档就地编辑
   - 直接编辑并保存任务的「需求」「方案」Markdown

4. 方案历史
   - 查看方案文档的历史快照

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
6. 各 Worker 用 `update_task_progress(subtask_id, status)` 推进子任务；进度随清单标记派生
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
- 自动同步：`src/opensquad/_runner/_tool_executor.py`、`src/opensquad/_runner/_turn_loop.py`
- 后端 API：`src/opensquad/gateway/backend/app/ai_web/routes/`（包；协作看板路由在 `_main.py`）
- 前端 API：`src/opensquad/gateway/nexuschat-pro/services/api.ts`
- 看板页面：`src/opensquad/gateway/nexuschat-pro/components/CollabBoardPage.tsx`

---

如需进一步扩展，建议下一步补充：

1. 任务权限模型（仅 PM 可改任务总进度）
2. 任务标签与优先级
3. 任务 SLA/超时预警
4. 看板时间轴视图（状态变更审计）
