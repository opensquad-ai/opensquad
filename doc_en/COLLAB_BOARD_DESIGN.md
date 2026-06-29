# Task Collaboration Board Design & Usage

## 1. Goals & Positioning

The task collaboration board is used for **task-level visual management** in multi-Agent collaboration scenarios. Core goals:

1. Monitor each Agent's current status and progress
2. Prevent task drift (misunderstanding leading to invalid work)
3. Accumulate public collaboration context (anti-forgetting)
4. Support multi-task parallelism and historical task review

The board emphasizes "status and progress", not full execution logs.

---

## 2. Core Design Principles

### 2.1 Task Scoping (Task-Scoped)

Each collaboration task has its own:

- `task_id` (i.e. `collab_id`)
- `task_name`

All board reads and writes must include `collab_id` to ensure data isolation across tasks.

### 2.2 Auto Task ID (6-character alphanumeric)

A 6-character mixed ID (e.g. `a8K2pQ`) is auto-generated each time a collaboration task is started.

### 2.3 Latest Tool Snapshot Strategy

Given high tool call volume, the board saves only the **latest tool call summary** by default, not the full tool history:

- `latest_tool_name`
- `latest_tool_summary`

### 2.4 Public Discussion Area (Shared Memory)

A `discussion` record type stores task plans, decisions, constraints, and key context visible to all.

### 2.5 PM-Controlled Progress

Overall task progress is updated by the PM Agent or the Web admin panel (`task.progress`) for unified alignment.

---

## 3. Data Model

### 3.1 Task Record

Storage file: `data/collab_board/board_tasks.json`

Key fields:

- `task_id`: 6-character task ID
- `task_name`: Task name
- `created_by`
- `status`: `active | done | archived`
- `progress`: 0–100 (maintained by PM)
- `created_at`
- `started_at` (defaults to `created_at`)
- `ended_at` (written on completion)
- `updated_at`
- `closed_at`
- `duration_seconds` (computed on list aggregation)

### 3.2 Board Item

Storage file: `data/collab_board/board_items.json`

Key fields:

- `id`
- `collab_id` / `task_id`
- `task_name`
- `agent_id`
- `item_type`: `task | status | plan | progress | discussion | ...`
- `title`
- `content`
- `status`
- `progress`
- `visibility`: `public | private`
- `latest_tool_name`
- `latest_tool_summary`
- `created_at`
- `updated_at`

Notes:

- For the same `(collab_id, agent_id, item_type)`, an **upsert** strategy is used (keeping the latest state)
- `discussion` items are appended (not overwritten)

---

## 4. Backend API

Prefix: `/collab-board`

### 4.1 Task Endpoints

1. `GET /tasks`
   - Get task list (with statistics)

2. `POST /tasks`
   - Create task
   - Request body: `{ task_name, created_by? }`
   - Returns auto-generated `task_id`

3. `PUT /tasks/{task_id}`
   - Update task metadata
   - Request body may include: `task_name`, `progress`, `status`

### 4.2 Board Item Endpoints

4. `GET /items?collab_id=...&agent_id=...&scope=public|all`
   - Get board items for the specified task
   - `collab_id` required

5. `POST /items`
   - Upsert board item
   - Request body must include `collab_id`

6. `POST /discussions`
   - Append public discussion item
   - Request body must include `collab_id`

---

## 5. Agent Tool Usage

File: `src/opensquad/tools/collaboration.py`

### 5.1 Start Collaboration

`start_collaboration(...)`

Behavior:

- Loads collaboration card
- Auto-creates collaboration task (generates 6-character `task_id`)
- Returns `task` info

### 5.2 Write Board Status

`board_update(collab_id, title, content, status, progress, visibility, item_type)`

Must pass `collab_id` (task ID).

### 5.3 Query Board

`board_list(collab_id, agent_id?, scope?)`

Must pass `collab_id`.

### 5.4 Post Public Discussion

`board_post_public_discussion(collab_id, task_name, title, content)`

Used to accumulate shareable, reviewable public collaboration decisions.

---

## 6. Auto-Sync Behavior

In `runner.py`, after each tool call, a "latest tool snapshot" is auto-synced to the current active task:

- Auto-updates `latest_tool_name`
- Auto-updates `latest_tool_summary`
- Does not write full history

This keeps the board continuously reflecting Agent current actions without becoming unreadable due to log volume.

---

## 7. Frontend Usage (CollabBoardPage)

Entry: Sidebar → "Collaboration Board"

Supported capabilities:

1. Task switching
   - Top bar to select task (`task_name + task_id`)

2. New task creation
   - Click "New Task", auto-generates 6-character task ID

3. Member filtering
   - View all members or single member status

4. PM progress editing
   - Directly edit and save overall task progress

5. Time info viewing
   - Start time `started_at`
   - End time `ended_at`
   - Duration `duration_seconds` (formatted display)

6. Public discussion area
   - View discussion history
   - Post new discussion conclusions (task-level)

---

## 8. Recommended Collaboration Flow (Practice)

1. PM calls `start_collaboration`, receives `task_id`
2. PM broadcasts in group chat and board: this task uses this `task_id`
3. Each Agent calls `board_update(collab_id=task_id, ...)` on start
4. Update progress and status at key milestones
5. Post `discussion` when there are disagreements or decisions to confirm
6. PM continuously maintains overall task progress
7. After task completion, update status to `done`, record end time

---

## 9. FAQ

### Q1: Why is `collab_id` required everywhere?
A: To prevent data interleaving during multi-task parallelism and ensure board isolation per task.

### Q2: Why not save all tool calls?
A: Full tool logs are too noisy and hurt management efficiency. The board only needs "current state snapshots".

### Q3: How to prevent forgetting?
A: Key conclusions are accumulated in the task-level public discussion area for all to review at any time.

---

## 10. File Location Overview

- Storage layer: `src/opensquad/collab_board.py`
- Agent tools: `src/opensquad/tools/collaboration.py`
- Auto-sync: `src/opensquad/runner.py`
- Backend API: `src/opensquad/gateway/backend/app/ai_web/routes.py`
- Frontend API: `src/opensquad/gateway/nexuschat-pro/services/api.ts`
- Board page: `src/opensquad/gateway/nexuschat-pro/components/CollabBoardPage.tsx`

---

For future extensions, suggested next steps:

1. Task permission model (only PM can modify overall task progress)
2. Task labels and priorities
3. Task SLA / timeout alerts
4. Board timeline view (status change audit)
