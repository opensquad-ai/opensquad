---
name: collaboration-workflow
description: Complete guide to the OpenSquad multi-agent collaboration system with user authorization gates. Use this skill when starting a collaboration session, coordinating multiple agents, managing the collaboration board, or when you need to understand how to use board_update, board_list, and other collaboration tools. Triggers on "start collaboration", "assign tasks", "update progress", "write report", or when working with other agents in a group chat.
---

# Collaboration Workflow Skill

## Overview

This skill provides a complete guide to the OpenSquad multi-agent collaboration system. It covers the 4-phase lifecycle with **user authorization gates**, collaboration board usage, tool calls, and standard message formats for PM, Researcher, and Analyst roles.

## Core Rule: User Authorization Required at Every Phase

**Each phase must be explicitly approved by the user before proceeding to the next.** The PM must present the phase output to the user, wait for confirmation, and only then move forward. Never skip authorization or proceed without user approval.

```
Phase 1: 确定需求 ──→ 向用户展示需求 ──→ 用户确认 ──→ 进入 Phase 2
Phase 2: 讨论方案 ──→ 向用户展示方案 ──→ 用户确认 ──→ 进入 Phase 3
Phase 3: 开始执行 ──→ 向用户展示进度 ──→ 用户确认 ──→ 进入 Phase 4
Phase 4: 完成验收 ──→ 向用户交付成果 ──→ 用户确认 ──→ 协作结束
```

## When to Use This Skill

**Load this skill when:**
- Starting a new collaboration session
- Coordinating multiple agents on a shared task
- Writing or reading the collaboration board
- Assigning tasks to team members
- Reporting progress or findings
- Synthesizing research results into a report

## Where to Talk: the Task Window, not the Group

**Rule: task-related communication stays in the task window. Send to the group only what
the user must see — necessary notifications, above all anything that needs their attention
or decision.**

A collaboration task has its own **task window** (the collaboration card posted in the
group chat opens it). Everything about the task happens there, not in the group:

- **Task content → the window**: requirements, plan, task assignment, progress, files
  produced, questions and answers between teammates, delivery notes. Send it as
  `im.send_message(content=..., collab_id="<collab_id>")` (files: same call with
  `file_paths=[...]`). The task's members and the user read it in the window; the group
  chat is not touched. Members are woken by a task message, and `@<agent_id>` in it
  addresses one of them in particular.
- **Anything the user must see or decide goes to the group**, not the window: a question,
  a choice, an approval to give, a blocker, a delivery to accept. The window is the team's
  own channel — a request for the user left there is a request nobody answers.
- **Everyone must be 已参与 before work is handed out**: `assign_task` refuses while any
  member is still 已邀请 — have each run `join_collaboration(card=..., collab_id=...)`
  (remind them in the group if needed), then assign.
- **The end of the task is announced in the window**, not the group:
  `end_collaboration(card=..., collab_id=...)` posts the closure into the task thread and
  wakes the members there. The group only ever held the card that opens the window.
- **Everything else → the group**: greetings, notices unrelated to the task, topics for
  the whole group. Send it as `im.send_message(content=..., target_id="<group_id>",
  target_type="group")`.

Decide by what the content is *about*, not by where the message arrived. Leaking task
work into the group buries it in noise; pushing group chatter into the window makes the
task's own record unreadable. Never invent a `collab_id` — take it from the card you were
invited under, or read it with `collaboration.board_list_my_tasks()`.

**One collaboration at a time.** You may create or join one live task; while it is active
the platform refuses a second one (creating, joining, or being assigned into another),
because an agent in two boards updates the wrong one and answers the wrong thread. The
refusal names the task you are in — finish it with
`end_collaboration(card="...", collab_id="...", group_id="...")`, or keep working inside
it — and only then start the next.

## How to Split the Work (PM)

**The split decides whether the project decouples or collides.** Split by **boundary, not
by volume**: separate directories, files, modules, or API interfaces, so every unit has
**exactly one owner** and can be **verified independently**.

- One unit, one owner: never hand the same file to two agents — they overwrite each other
  and neither can be accepted alone. Sharing a directory is fine only if the files inside
  are disjoint, and the assignment should say which.
- Split along interface seams: "backend implements `POST /convert`, frontend calls it, QA
  verifies through the UI" is decoupled; "both edit `index.html`" is not.
- Put the boundary in the assignment: `file_scope` states exactly what the worker may
  touch, and `acceptance_criteria` must be checkable without other tasks being finished.
  Overlapping scopes mean the split is wrong — fix the split, not the conflict.
- Prefer the smallest unit that still delivers something verifiable.
- **Record the project directory** (`set_project_dir`, or `project_dir=` on
  `start_collaboration`): the folder the project's files live in. The window shows it so
  every worker writes into the same project rather than picking a directory of its own;
  `assign_task` refuses while it is empty.

## Core Concepts

### Collaboration Board — 4 Areas

The collaboration board is the central coordination surface. It has four distinct areas:

| Area | Purpose | Who writes | item_type |
|------|---------|-----------|-----------|
| **需求区** (Requirements) | Research scope, quality expectations | PM | `requirement` |
| **方案区** (Plan) | Final report document, task checklist overview | PM + Analyst | `plan` + `task` |
| **任务分配区** (Tasks) | Editable task list with inline editing | PM (create), any agent (edit) | `task` |
| **任务进度区** (Progress) | Auto-synced per-agent progress, latest tool calls | Auto (runner) | `status` |

### The `item_key` — Multiple Entries Per Type

Every board entry is uniquely identified by `(collab_id, agent_id, item_type, item_key)`. The `item_key` allows multiple independent entries of the same type:

```
PM creates:
  board_update(item_type="requirement", item_key="req_scope")       → 需求1
  board_update(item_type="task", item_key="research_diagnostic")    → 任务1
  board_update(item_type="task", item_key="research_treatment")     → 任务2

Researcher updates:
  board_update(item_type="task", item_key="research_diagnostic", progress=50)
  board_update(item_type="task", item_key="research_diagnostic", done, 100%)

PM writes report:
  board_update(item_type="plan", item_key="final_report", content="...")
```

## Available Tools

| Tool | Description | Usage |
|------|-------------|-------|
| `list_collab_cards()` | List available collaboration card templates | Any agent |
| `start_collaboration()` | Start a collaboration session, load a card, send invitations | PM only |
| `join_collaboration()` | Join an existing collaboration session | Worker agents |
| `end_collaboration()` | End the collaboration session | PM only |
| `leave_collaboration()` | Leave the collaboration session | Worker agents |
| **`board_update()`** | **Core**: Write/update a board entry | Any agent |
| **`board_list()`** | **Core**: Read board entries | Any agent |
| `board_post_public_discussion()` | Post a public discussion/decision memo | Any agent |
| `get_team_status()` | Get real-time status of all collaboration agents | Any agent |
| `get_group_roster()` | Get the group member list | Any agent |

### `board_update` — Full Parameters

```python
board_update(
    collab_id="abc123",  # Collaboration session ID (required)
    task_name="",  # Optional task name
    title="Task title",  # Entry title
    content="Detailed content",  # Entry body (supports markdown)
    status="doing",  # Status: pending/doing/done/blocked
    progress=0,  # Progress 0-100
    visibility="public",  # Visibility: public/private
    item_type="task",  # Type: requirement/plan/task/status/discussion
    item_key="unique_key",  # Key to distinguish multiple entries of same type
)
```

### `board_list` — Full Parameters

```python
board_list(
    collab_id="abc123",  # Collaboration session ID (required)
    agent_id="",  # Optional: filter by agent
    scope="public",  # "public" or "all"
    item_type="",  # Optional: filter by type (requirement/plan/task/status/discussion)
)
```

## 4-Phase Lifecycle with User Authorization

### Phase 1: 确定需求 (Requirements)

**Lead**: PM

**Steps**:
1. Understand the user's request
2. Break down the work into 3-6 subtopics/tasks
3. Write requirements to the board
4. Assign tasks to agents
5. **Present requirements to user and wait for approval**

**PM tool calls:**

```python
# 1. Record the research scope in Requirements area
board_update(
    collab_id="abc123",
    item_type="requirement",
    item_key="req_scope",
    title="AI Healthcare Research",
    content="Research scope: diagnostic AI, treatment recommendation, patient monitoring, regulatory policy, ethical challenges",
    status="P0",
)

# 2. Assign tasks in Task Assignment area
board_update(
    collab_id="abc123",
    item_type="task",
    item_key="research_diagnostic",
    title="Diagnostic AI Research",
    content="# 调研FDA批准的AI系统 [ ]\n- [ ] 搜索FDA批准的AI放射系统列表\n- [ ] 收集各系统准确率数据\n# 分析临床案例 [ ]\n- [ ] 查找3个以上案例\n# 总结挑战 [ ]\n- [ ] 算法偏见、数据隐私、监管滞后",
    status="pending",
    progress=0,
)

board_update(
    collab_id="abc123",
    item_type="task",
    item_key="research_treatment",
    title="Treatment Recommendation Research",
    content="# 个性化用药AI [ ]\n- [ ] 研究个性化用药AI系统\n- [ ] 药物相互作用预测\n# 真实世界证据 [ ]\n- [ ] 收集RWE数据",
    status="pending",
    progress=0,
)
```

**Authorization gate — PM must present to user:**

```
@user [PHASE 1 — 需求确认]

以下是本次协作的需求规划，请确认后进入下一阶段：

📋 研究范围: AI Healthcare Research
  优先级: P0
  子课题:
  1. 诊断AI研究 → Researcher-A
  2. 治疗推荐系统研究 → Researcher-B

请回复 "确认" 或提出修改意见。
```

**⚠️ BLOCKING: Do NOT proceed to Phase 2 until user confirms.**
- If user approves → proceed to Phase 2
- If user requests changes → update requirements, re-present, wait again

### Phase 2: 讨论方案 (Plan Discussion)

**Lead**: PM + all agents

**Steps**:
1. Each agent reads their assigned task
2. Agents discuss approach in group chat
3. PM synthesizes discussion into a structured plan
4. Write plan to the board
5. **Present plan to user and wait for approval**

**Agent tool calls:**

```python
# Agents read their tasks
tasks = board_list(collab_id="abc123", item_type="task")

# PM writes the plan
board_update(
    collab_id="abc123",
    item_type="plan",
    item_key="final_report",
    title="AI Healthcare Applications Research Report",
    content="## 报告结构\n\n### 一、执行摘要\n- 3-5条核心发现\n\n### 二、诊断AI\n- FDA批准系统概览\n- 准确率数据对比\n- 临床案例分析\n\n### 三、治疗推荐系统\n- 个性化用药AI\n- 药物相互作用预测\n\n### 四、趋势与展望\n- 2026年趋势\n- 未来方向\n\n### 五、挑战与建议\n- 算法偏见\n- 数据隐私\n- 监管滞后",
    status="doing",
    progress=30,
)
```

**Authorization gate — PM must present to user:**

```
@user [PHASE 2 — 方案确认]

以下是本次协作的研究方案，请确认后开始执行：

📄 报告结构:
  一、执行摘要 (3-5条核心发现)
  二、诊断AI (FDA系统/准确率/案例)
  三、治疗推荐系统 (个性化用药/RWE)
  四、趋势与展望
  五、挑战与建议

请回复 "确认" 或提出修改意见。
```

**⚠️ BLOCKING: Do NOT proceed to Phase 3 until user confirms.**
- If user approves → proceed to Phase 3
- If user requests changes → update plan, re-present, wait again

### Phase 3: 开始执行 (Execution)

**Lead**: All agents (parallel)

**Steps**:
1. Each agent loads the skills relevant to its task (e.g. a research skill, if the skill library has one)
2. Execute research/tools in parallel
3. Update progress regularly
4. Post interim findings for team awareness
5. Report completion with structured summary
6. **Present all findings to user and wait for approval**

**Researcher tool calls:**

```python
# 1. Read assigned tasks
tasks = board_list(collab_id="abc123", item_type="task")

# 2. Execute research tools (runner auto-syncs to Progress area)
web_search("AI radiology FDA approved systems 2026")
# → Runner automatically calls:
#   board_update(item_type="status", latest_tool_name="web_search", ...)
# → Progress area shows: 🔧 web_search

web_fetch("https://www.fda.gov/...")
# → Progress area updates: 🔧 web_fetch

# 3. Update task progress with sub-step checklist
board_update(
    collab_id="abc123",
    item_type="task",
    item_key="research_diagnostic",
    title="Diagnostic AI Research",
    content="# 调研FDA批准的AI系统 [x]\n- [x] 搜索FDA批准的AI放射系统列表（12个）\n- [x] 收集各系统准确率数据（最高94%）\n# 分析临床案例 [>]\n- [x] Mayo Clinic诊断时间缩短40%\n- [>] 正在读 Johns Hopkins 报告\n# 总结挑战 [ ]\n- [ ] 算法偏见、数据隐私、监管滞后",
    status="doing",
    progress=50,
)

# 4. Post interim findings for team awareness
board_post_public_discussion(
    collab_id="abc123",
    title="诊断AI发现：FDA已批准12个AI放射系统",
    content="关键数据点：\n1. Aidoc颅内出血检测准确率94%\n2. Mayo Clinic诊断时间缩短40%\n3. 主要挑战：算法偏见、数据隐私、监管滞后",
)

# 5. Mark task as complete
board_update(
    collab_id="abc123",
    item_type="task",
    item_key="research_diagnostic",
    title="Diagnostic AI Research",
    content="# 调研FDA批准的AI系统 [x]\n- [x] 搜索FDA批准的AI放射系统列表（12个）\n- [x] 收集各系统准确率数据（最高94%）\n# 分析临床案例 [x]\n- [x] Mayo Clinic诊断时间缩短40%\n- [x] Johns Hopkins每年挽救120条生命\n# 总结挑战 [x]\n- [x] 算法偏见\n- [x] 数据隐私\n- [x] 监管滞后",
    status="done",
    progress=100,
)
```

**Authorization gate — PM must present to user:**

```
@user [PHASE 3 — 执行完成确认]

所有子课题已完成研究，以下是汇总结果：

✅ Researcher-A — 诊断AI研究 (100%)
  关键发现: FDA批准12个系统, 最高准确率94%
  来源: 15个来源, 8个全文读取

✅ Researcher-B — 治疗推荐系统 (100%)
  关键发现: 个性化用药AI准确率78-85%
  来源: 12个来源, 6个全文读取

请确认是否进入报告撰写阶段，或需要补充研究。
```

**⚠️ BLOCKING: Do NOT proceed to Phase 4 until user confirms.**
- If user approves → proceed to Phase 4
- If user requests more research → return to Phase 3 for specific gaps

### Phase 4: 完成验收 (Delivery & Acceptance)

**Lead**: PM + Analyst

**Steps**:
1. PM reads all researcher findings
2. Synthesize into final report
3. Analyst reviews for completeness
4. Incorporate feedback
5. **Deliver final report to user and wait for acceptance**

**PM tool calls:**

```python
# 1. Read all task findings
tasks = board_list(collab_id="abc123", item_type="task")

# 2. Read public discussions for interim findings
discussions = board_list(collab_id="abc123", item_type="discussion")

# 3. Write final report
board_update(
    collab_id="abc123",
    item_type="plan",
    item_key="final_report",
    title="AI Healthcare Applications Research Report",
    content="## 执行摘要\n\nAI在医疗领域的应用正在快速扩展...\n\n## 一、诊断AI\n\n### 1.1 FDA批准系统\n截至2026年，FDA已批准12个AI放射诊断系统...\n\n| 系统 | 适应症 | 准确率 |\n|------|--------|--------|\n| Aidoc | 颅内出血 | 94% |\n| Viz.ai | 大血管闭塞 | 91% |\n\n### 1.2 临床影响\n- Johns Hopkins: AI卒中检测每年挽救120条生命\n- Cleveland Clinic: 假阳性率降低35%\n\n## 二、治疗推荐系统\n...\n\n## 三、趋势与展望\n...\n\n## 四、挑战与建议\n...",
    status="done",
    progress=100,
)
# → Old version automatically saved as plan history snapshot

# 4. Mark requirement as confirmed
board_update(collab_id="abc123", item_type="requirement", item_key="req_scope", status="已确认")
```

**Authorization gate — PM must present to user:**

```
@user [PHASE 4 — 成果验收]

协作任务已完成，以下是最终交付物：

📄 研究报告: AI Healthcare Applications Research Report
  - 执行摘要: 5条核心发现
  - 诊断AI: 12个FDA系统, 准确率数据, 临床案例
  - 治疗推荐: 个性化用药AI, RWE数据
  - 趋势展望: 2026年趋势与未来方向
  - 挑战建议: 算法偏见、数据隐私、监管滞后

📊 数据来源: 27个来源, 14个全文读取
👥 参与Agent: Researcher-A, Researcher-B

请验收并确认是否完成，或需要修改。
```

**⚠️ BLOCKING: Collaboration ends only after user confirms acceptance.**
- If user accepts → end collaboration
- If user requests changes → return to appropriate phase

```python
# After user accepts:
end_collaboration(collab_id="abc123")
```

## Standard Message Formats

### PM → User Authorization Request
```
@user [PHASE N — {Phase Name}]

{Phase output summary}

请回复 "确认" 或提出修改意见。
```

### PM → Researcher Task Assignment
```
@Researcher-A [TASK] Diagnostic AI Research
Research angles: FDA approvals, accuracy data, clinical cases, challenges
Key questions: What systems are approved? What are the accuracy rates?
Output format: Structured findings with data, examples, expert opinions
```

### Researcher → PM Progress Update
```
[STATUS] Task: Diagnostic AI  Progress: 50%
Completed: FDA survey, accuracy data collection
In progress: Clinical case analysis
Blocked: no
```

### Researcher → PM Findings Complete
```
[DONE] Task: Diagnostic AI
Key findings:
1. FDA approved 12 AI radiology systems, max accuracy 94%
2. Mayo Clinic reduced diagnosis time by 40%
3. Top challenges: algorithm bias, data privacy, regulatory lag
Sources: 15 sources reviewed, 8 fetched in full
Gaps: none
```

### Phase Signal (PM only)
```
[PHASE] P2→P3  Note: All research complete, entering synthesis  @Analyst please review
```

## Task Content Format — Plan-Style Checklist

When writing task content, use the `# heading [x]/[>]/[ ]` and `- [x]/[>]/[ ] item` format. The board renders these as a plan-style checklist:

```
# 调研FDA批准的AI系统 [x]
- [x] 搜索FDA批准的AI放射系统列表（12个）
- [x] 收集各系统准确率数据（最高94%）
# 分析临床案例 [>]
- [x] Mayo Clinic诊断时间缩短40%
- [>] 正在读 Johns Hopkins 报告
# 总结挑战 [ ]
- [ ] 算法偏见、数据隐私、监管滞后
```

The board renders:
- `# xxx [x]` → green (completed section)
- `# xxx [>]` → blue (in-progress section)
- `# xxx [ ]` → gray (pending section)
- `- [x]/[>]/[ ] item` → corresponding color sub-items

## Common Mistakes to Avoid

- ❌ **Skipping user authorization** — never proceed to next phase without explicit user approval
- ❌ PM writes the report before all researchers finish
- ❌ Researchers skip the research methodology and do single searches
- ❌ Not updating progress — team can't see where things stand
- ❌ Not posting interim findings — other researchers miss overlaps
- ❌ Using the same item_key for different entries (causes overwrites)
- ❌ Forgetting to load relevant skills before starting research

## Quality Bar

The collaboration is successful when:
- [ ] All 4 phases completed with user authorization at each gate
- [ ] All subtopics researched from 3+ angles with specific data points
- [ ] At least 2 concrete real-world examples per subtopic
- [ ] Expert perspectives or authoritative sources cited
- [ ] Report structured with clear sections and actionable insights
- [ ] Executive summary prepared (3-5 key findings)
- [ ] Plan history preserved for version tracking
- [ ] User has explicitly accepted the final deliverable
