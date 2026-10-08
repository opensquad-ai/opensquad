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

### 2.2 Auto Task ID (6-character uppercase hex)

A 6-character uppercase-hex ID (e.g. `A8F2C1`) is auto-generated each time a collaboration task is started — the uppercased first 6 characters of a UUID4 (`collab_board._gen_task_id`).

### 2.3 Latest Tool Snapshot Fields (no longer auto-written)

Items carry `latest_tool_name` / `latest_tool_summary` to hold the **latest tool call summary** rather than the full history. The runner-side auto-sync that used to fill them was retired (see 6); they are set today only when a caller passes them explicitly.

### 2.4 Public Discussion Area (Shared Memory)

A `discussion` record type stores task plans, decisions, constraints, and key context visible to all.

### 2.5 Checklist-Derived Progress

A task item's `status` and `progress` are **derived from the checklist markers in its own content** — `[x]` done, `[>]` doing, `[ ]` pending — matched at line start and outside fenced code blocks (`collab_board._derive_task_status_progress_from_content`). A worker advances a subtask with `collaboration.update_task_progress(subtask_id, status)` instead of rewriting Markdown, and agents are told **not** to use `board_update` for progress.

---

## 3. Data Model

### 3.1 Task Record

Storage file: `<workspace>/data/collab_board/board_tasks.json` (`syscfg.workspace_data_dir("collab_board")` — one directory per workspace, so two installations on one machine never share a board)

Key fields:

- `task_id`: 6-character uppercase-hex task ID
- `task_name`: Task name
- `created_by`
- `status`: `active | done | failed | archived | stale` (`stale` is written by `cleanup_stale_tasks`)
- `progress`: 0–100 (derived from checklist markers — see 2.5)
- `created_at`
- `started_at` (defaults to `created_at`)
- `ended_at` (written on completion)
- `updated_at`
- `closed_at`
- `duration_seconds` (computed on list aggregation)

### 3.2 Board Item

Storage file: `<workspace>/data/collab_board/board_items.json`

Key fields:

- `id`
- `collab_id` / `task_id`
- `task_name`
- `agent_id`
- `item_type`: `requirement | requirement_doc | plan | task | discussion | change_request | approval | attachment` (plus `task_meta`) — the window's grouping keys are exactly `collab_board._SUMMARY_ITEM_TYPES`
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

## 6. Auto-Sync Behavior (retired)

The runner used to write an `item_type="status"` item after every tool call — a per-agent "latest tool call" feed, shown as the task window's 任务进度区 (Progress). That area is gone: progress is carried by the task items' own checklist markers, and nothing auto-writes status items any more. `latest_tool_name` / `latest_tool_summary` stay on the item schema (see 2.3) but are no longer filled automatically.

---

## 7. Frontend Usage (CollabBoardPage)

Entry: Sidebar → "Collaboration Board"

Supported capabilities:

1. Task switching
   - Top bar to select task (`task_name + task_id`)

2. New task creation
   - Click "New Task", auto-generates 6-character task ID

3. Requirement / plan document editing
   - Edit and save the task's Requirements and Plan Markdown in place

4. Plan history
   - Browse the plan document's snapshot history

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
6. Each worker advances its subtasks with `update_task_progress(subtask_id, status)`; progress follows the checklist markers
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
- Auto-sync: `src/opensquad/_runner/_tool_executor.py`, `src/opensquad/_runner/_turn_loop.py`
- Backend API: `src/opensquad/gateway/backend/app/ai_web/routes/` (package; the collab-board routes live in `_main.py`)
- Frontend API: `src/opensquad/gateway/nexuschat-pro/services/api.ts`
- Board page: `src/opensquad/gateway/nexuschat-pro/components/CollabBoardPage.tsx`

---

For future extensions, suggested next steps:

1. Task permission model (only PM can modify overall task progress)
2. Task labels and priorities
3. Task SLA / timeout alerts
4. Board timeline view (status change audit)
