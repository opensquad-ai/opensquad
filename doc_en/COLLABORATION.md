# OpenSquad Collaboration Guide

## Overview

OpenSquad uses a **Collab Card-driven** collaboration model. Agents coordinate through:
1. **Group chat** — primary communication channel (natural language)
2. **Collab Card** — structured workflow protocol defining roles, phases, and rules
3. **Collaboration Board** — one board per collaboration, held by the machine that owns the group
   (`collab_board.py`), keyed by a 6-character `collab_id` and reached through the board tools

The board is centralized per machine, not a pile of agent notes: requirements, plan, tasks, status
and discussion are items on it, every agent reads and writes it through the same tools, and a
collaboration owned by a paired machine is reached over the relay. See `COLLAB_BOARD_DESIGN.md`
for the storage, ownership and forwarding rules.

---

## Collab Card System

Collab cards are stored as flat files in `collab_cards/*.md`. Each card defines:
- **Suggested Roles** (advisory only — PM decides who to actually invite)
- **Workflow phases** (Planning, Execution, Review, User Acceptance)
- **Communication rules** (@mention, sleep/wake patterns)
- **Board rules** (who writes which zone, and when)

Currently available cards (in `src/collab_cards/`):
- `software_dev_team` — full software development lifecycle (PM / Developer / QA)
- `general_software_dev_collab` — general software development collaboration (PM / Dev / QA / DevOps / Reviewer)
- `code_review` — code review workflow (Reviewer / Author)
- `distributed_deep_research` — distributed deep research (PM / Researcher / Analyst)

### Collab Card Frontmatter Format

```yaml
---
name: software_dev_team
description: ...
tags: software, team, pm, dev, qa, full-cycle
suggested_roles: pm, developer, qa
min_members: 2
---
```

`suggested_roles` is **advisory** — PM decides who to invite based on the task. The card is loaded into each participant's prompt for the duration of the collaboration.

### Lifecycle Tools

| Tool | Who calls it | What it does |
|------|-------------|--------------|
| `start_collaboration(card, members?)` | PM | Loads card into PM's prompt, optionally invites members |
| `join_collaboration(card, collab_id?)` | Workers | Loads card into worker's prompt |
| `end_collaboration(card, collab_id?, group_id?)` | PM | Unloads card, notifies members |
| `leave_collaboration(card)` | Workers | Unloads card |
| `list_active_collaborations()` | Anyone | List all active collaboration sessions the current agent has joined |
| `get_team_status()` | Anyone | Check all agents' real-time status (idle/working/sleeping) |
| `get_group_roster(group_id)` | PM / Workers | List agent members in a specific group (cross-refs config.json) |
| `list_collab_cards()` | Anyone | List available collab cards with suggested roles |

### Task & Board Tools

| Tool | Who calls it | What it does |
|------|-------------|--------------|
| `assign_task(collab_id, worker_id, task_name, ...)` | PM | Assign a task to a worker with checklist items |
| `add_subtask(collab_id, item_key, title, ...)` | PM / Worker | Add a subtask to an existing task item |
| `update_task_progress(collab_id, item_key, subtask_id, ...)` | Worker | Update progress on a specific subtask |
| `batch_update_tasks(collab_id, item_key, updates)` | Worker | Batch update multiple task items |
| `board_update(collab_id, task_name?, title?, ...)` | PM / Worker | Update collaboration board entries |
| `board_list(collab_id, agent_id?, scope?, item_type?)` | Anyone | Read collaboration board entries |
| `board_view(collab_id)` | Anyone | View the complete collaboration board (all zones) |
| `board_list_tasks(collab_id)` | Anyone | View all task assignments for a collaboration session |
| `board_list_my_tasks(collab_id, scope?, debug?)` | Worker | List task checklist items assigned to the current agent |
| `board_post_public_discussion(collab_id, task_name, title, content)` | Anyone | Post a public discussion/decision memo visible to all agents |

### PM-Led Model

The PM agent drives collaboration autonomously:
1. PM calls `list_collab_cards()` to review available protocols
2. PM calls `start_collaboration(card="software_dev_team")` — card is loaded into PM's prompt
3. PM reviews `suggested_roles` in the card, then decides who to invite via group chat
4. PM can also pass `members=["coder", "qa"]` to `start_collaboration()` for automatic invitations
5. Workers call `join_collaboration(card="software_dev_team")` after receiving the invitation

---

## Collaboration Board

Each collaboration owns a board (a `collab_id`), and the board is the collaboration's memory: the
requirements, the plan, the task assignments, the progress and the discussion all live on it, as
items in zones (`requirement` / `plan` / `task` / `status` / `discussion`).

- **Ownership**: the board belongs to the machine that owns the group, and the board tools resolve
  that owner for the `collab_id` — so an agent on a paired machine reads and writes the same board,
  over the relay, rather than a copy of its own.
- **Reading**: `board_view(collab_id)` for the whole board, `board_list(...)` to filter,
  `board_list_tasks(collab_id)` for the assignments, `board_list_my_tasks(collab_id)` for the
  current agent's own work.
- **Writing**: `board_update(...)` for an entry, `assign_task(...)` to create a task item with its
  checklist, `update_task_progress(...)` for a subtask, `board_post_public_discussion(...)` for a
  decision the whole team should see.
- **Approvals**: the four gates (需要 / 方案 / 分配 / 验收) are items of type `approval` on the
  board. The user approves them in the task window, and `assign_task` refuses until the first two
  are approved.

Read the board rather than a private note before a phase transition, and post decisions to it
instead of only saying them in chat — a message scrolls away, a board item does not.

### Structured task items

`assign_task` writes a task item whose `extra` carries the structured subtasks (id, title, status,
progress, note), and `update_task_progress` moves one subtask at a time. The item's rendered content
is generated from those subtasks, so the checklist markers are the same facts, not a second copy.

---

## VCS Audit & Transparency

While agents collaborate locally, they often need to interact with remote repositories (GitHub). OpenSquad implements a **VCS Audit System** to ensure transparency and accountability across all agents.

### How it works

1. **Automatic Footprinting**: The `vcs_remote` and `git_core` plugins automatically capture every command (e.g., `commit`, `push`, `pr_create`).
2. **Identity Preservation**: Even if all agents use the same GitHub account (configured via `gh auth login`), the audit system records the **internal Agent ID** who triggered the action.
3. **Audit Log**: Every operation's arguments, status, and raw output are saved to `data/audit/vcs_footprints.jsonl`.

### Viewing the Audit Timeline

You can view the collaborative history in the Web UI:
1. Navigate to the **VCS Audit** view from the sidebar.
2. Select a repository from the project list.
3. Browse the chronological timeline of all agent activities.
4. Expand any entry to see the exact parameters used and the command output.

This system is crucial for:
- **Debugging**: Identifying which agent caused a specific Git state.
- **Reviewing**: Seeing the raw output of PR creations or merges.
- **Accountability**: Tracking "who did what" in a shared GitHub account environment.

---

## Software Development Workflow

Defined in `collab_cards/software_dev_team.md`.

### Phase 1: Planning
1. PM receives user requirements (via Web UI or group chat)
2. PM clarifies scope with user
3. PM designs architecture, defines interface contracts
4. PM assigns tasks via @mention in group chat

### Phase 2: Execution
1. Workers read PM's assignments in group chat, and the task items on the board
2. Workers move their own subtasks with `update_task_progress(collab_id, item_key, subtask_id, ...)`
3. Workers implement, reporting progress on the board as it happens
4. Workers report completion/blockers in group chat
5. PM follows progress by reading the board (`board_list_tasks`), not by polling files

### Phase 3: Review & Iteration
1. PM reviews completed work (reads code, checks output, reads the board)
2. Approved: PM confirms in group chat and moves the subtask to done
3. Rejected: PM provides feedback, worker fixes and resubmits

### Phase 4: User Acceptance
1. PM reviews all completed work and the board
2. PM reports to user via `<to_user>` with summary
3. User approved: PM calls `end_collaboration(card="software_dev_team")` to close
4. User requests changes: back to Phase 2

---

## Communication Patterns

### @mention
Use `@agent_name` in group chat to notify specific agents. Example: `@coder please implement the login module`.

### Sleep/Wake
When blocked waiting for another agent:
```xml
<sleep seconds="120">waiting for @coder to finish</sleep>
```
After waking, check group chat for updates before continuing.

### Status Check
Any agent can call `get_team_status()` to see who is idle/working/sleeping across the whole squad.

For PM agents, the recommended pattern is:
1. Call `im.list_groups()` to see which groups you have joined
2. Call `get_group_roster(group_id)` to see which agents are in a specific group — this gives a group-scoped view, which is more relevant in multi-group deployments than the global `get_team_status()` view

---

## Comparison with Previous Architecture

| Aspect | v3.0 (Blueprint System) | v4.0 (Collab Card System) |
|--------|------------------------|--------------------------|
| Protocol storage | `blueprints/{name}/BLUEPRINT.md` (subdirectory) | `collab_cards/{name}.md` (flat file) |
| Role assignment | `roles` field (implied mandatory) | `suggested_roles` (advisory, PM decides) |
| members in start | Required list | Optional — PM decides via group chat |
| State sharing | Blackboard (ProjectBoard) in `workspace/` | Collaboration Board, per machine (`collab_board.py`) |
| Team registry | `workspace/TEAM.md` manual file | `get_team_status()` real-time API |
| PM visibility | Had to parse blackboard | `board_view` / `board_list_tasks` |
| Task persistence | In-memory only | Board items, persisted on the owning machine |
