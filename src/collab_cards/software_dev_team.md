---
name: software_dev_team
description: A multi-agent collaboration protocol for complete software development projects, covering PM/Dev/QA full lifecycle roles and message standards.
tags: software, team, pm, dev, qa, full-cycle
suggested_roles: pm, developer, qa
min_members: 2
---

## Project Lifecycle (5 Phases)

| Phase | Lead | Output | Transition Condition |
|-------|------|--------|----------------------|
| P1 Requirements | PM ↔ User | PROJECT.md requirements section | User confirms requirements |
| P2 Architecture & Assignment | PM | Architecture section + task assignments | PM sends task assignment message |
| P3 Parallel Implementation | Dev × N | Code files | All Dev agents report completion |
| P4 Testing & Fixes | QA + Dev | Test report + bug fixes | QA reports all tests pass |
| P5 Delivery | PM → User | Delivery summary | PM announces delivery |

Rule: Only PM can announce phase transitions; each phase output must be updated in `PROJECT.md`.

---

## Collaboration Board 4-Zone Writing Rules (Mandatory)

All agents must follow these strict boundaries when writing to the collaboration board:

1. **Requirements Zone (Markdown doc)**
   - Write only requirements: objectives, scope, constraints, acceptance criteria.
   - Do NOT include task assignment details or progress logs.

2. **Plan Zone (Markdown doc)**
   - Write only the overall solution: architecture, module boundaries, key flows, technical choices, risks and rollback.
   - Do NOT use task-list style here.

3. **Assignment + Progress Zone (merged, per-agent isolated)**
   - Assignment and progress are merged into one board area, isolated by agent.
   - Each detailed task must use checklist markers as the single source of progress truth:
     - `[ ]` pending/blocked
     - `[>]` in progress
     - `[x]` done
   - Backend derives status/progress from these markers automatically.
   - Each task must include: owner agent, deliverables, dependencies, deadline, acceptance criteria, key risks.

---

## Cross-Phase Collaboration Standards

### Definition of Done (DoD)
- Requirements mapped to acceptance criteria
- Code passes lint/unit tests; critical paths have test coverage
- Error handling and logging added for new flows
- User-facing changes documented (API docs/README/CHANGELOG as applicable)
- Security review completed for auth/permission/data exposure impacts

### API and Data Structure Alignment (Mandatory)
- PM MUST define all API interfaces and data structures at P2 before any coding begins
- All devs MUST implement against the same interface definitions — no unilateral deviations
- If an interface needs to change, PM must update the definition and notify all affected devs
- Interface contracts include: function signatures, parameter types, return types, error codes, data field names and types
- **⚠️ CRITICAL**: When Dev0 and Dev1 implement different modules that interact (e.g., strategy ↔ engine), the shared data structures MUST be identical. Mismatched field names, types, or semantics will cause silent integration failures.

### Self-Test Before Handoff (Mandatory)
- **Every dev MUST self-test before reporting task done**: Run unit tests for every function and module you wrote. Verify each function produces correct output for normal inputs, edge cases, and error cases.
- **Per-function verification**: Do NOT just run the top-level module test. Verify each public function independently with concrete inputs and expected outputs.
- **Integration smoke test**: After self-testing individual functions, run a minimal end-to-end test to verify your module integrates correctly with dependent modules.
- **Test evidence required**: When marking a task as `[x]` done, include test evidence in the status note: which tests were run, pass/fail counts, and any edge cases verified.
- **⚠️ WRONG**: Marking a task done with note "code complete" but no test results. This is a process violation.
- **✅ CORRECT**: Marking a task done with note "code complete, unit tests 12/12 passed, verified edge case: empty input returns empty result, integration test with engine module passes"

### PM Result Verification (Mandatory)
- PM is responsible for the final quality of deliverables — this CANNOT be delegated entirely to QA or devs
- Before presenting results to the user, PM MUST personally run the system and verify each requirement is met
- PM checks: every acceptance criterion from Step 1 is satisfied, outputs are correct, no regressions
- If results do not match requirements, PM sends work back for rework — do NOT deliver unverified results
- PM owns the final outcome; QA provides test reports, but PM makes the delivery decision

### Quality Gates
- **P2 → P3**: Architecture reviewed, interfaces frozen, task scopes agreed
- **P3 → P4**: All dev tasks report done; CI green; zero known P0/P1 bugs; every function self-tested
- **P4 → P5**: QA report approved; PM verified results match requirements; release checklist complete; rollback plan documented

### Artifact Checklist
- `PROJECT.md` updated at each phase transition
- API/interface changes recorded with versioning notes
- Interface contracts agreed (API endpoints, data structures, integration methods)
- Test report includes scope, coverage, and known gaps
- Post-release notes summarize impact and remaining risks

### Decision & Risk Logging
- Major decisions recorded with rationale and alternatives
- Risks include likelihood/impact and mitigation owner

---

## Standard Message Formats

**PM → Dev Task Assignment**
```
@Dev-A [TASK] Task Name
File scope: src/auth/
Dependencies: none  Priority: P0
Acceptance criteria: Login/Register API functional, passes unit tests
```

**Dev → PM Status Report**
```
[STATUS] Done: src/auth/login.py  Tests: 8/8 passed  Blocked: no
```

**QA → Dev Bug Report**
```
@Dev-A [BUG] src/auth/login.py:45
Symptom: empty password returns 500  Expected: 400
Reproduce: POST /api/login {"password":""}
```

**Phase Signal (PM only)**
```
[PHASE] P3→P4  Note: Development complete, entering testing  @QA please start testing
```

---

## 任务分配指南（结构化函数调用 — 推荐）

PM分配任务时，**必须使用`assign_task`函数**（结构化参数方式），不要手动写Markdown content。

### PM分配任务示例

```python
# 为coder分配任务
assign_task(
    collab_id="a8K2pQ",
    worker_id="coder",
    task_name="用户认证模块",
    description="实现完整的用户认证功能",
    file_scope="src/auth/",
    dependencies="none",
    deadline="2h",
    acceptance_criteria="单元测试全部通过，错误处理完善",
    subtasks=[
        {"title": "登录API接口", "description": "POST /api/login, 参数验证username/password, 返回JWT token"},
        {"title": "注册API接口", "description": "POST /api/register, bcrypt加密, 邮箱验证"},
        {"title": "Token刷新", "description": "POST /api/token/refresh, access_token 15min, refresh_token 7days"},
    ],
    item_key="task_coder_auth",
)
# 返回: {subtask_ids: {'登录API接口': 'st_task_coder_auth_1', '注册API接口': 'st_task_coder_auth_2', ...}}

# 为qa分配任务
assign_task(
    collab_id="a8K2pQ",
    worker_id="qa",
    task_name="认证模块测试",
    file_scope="tests/",
    dependencies="task_coder_auth",
    deadline="1h",
    acceptance_criteria="测试覆盖率 > 80%",
    subtasks=[
        {"title": "编写登录API单元测试", "description": "正常登录、错误密码、空字段等场景"},
        {"title": "编写注册API集成测试", "description": "重复注册检测、密码强度验证"},
    ],
    item_key="task_qa_test",
)
```

### Worker工作流程（强制顺序）

**Worker加入协作后，必须严格按以下顺序执行**：

```python
# 步骤1: 查看完整协作看板，了解全局上下文（需求、方案、任务分配）
board = collaboration.board_view(collab_id="a8K2pQ")
# 返回:
# {
#   "zones": {
#     "requirements": [...],   # PM写的需求
#     "plan": [...],           # PM写的方案/架构
#     "tasks": [...],          # 所有任务分配情况
#     "discussions": [...]     # 讨论记录
#   }
# }

# 步骤2: 查看分配给自己的任务（必须先执行此步才能开始工作）
my_tasks = collaboration.board_list_my_tasks(collab_id="a8K2pQ")
# 返回示例:
# {
#   "items": [{
#     "item_key": "task_coder_auth",
#     "subtasks": [
#       {"id": "st_task_coder_auth_1", "title": "登录API接口", "status": "pending"},
#       {"id": "st_task_coder_auth_2", "title": "注册API接口", "status": "pending"},
#       {"id": "st_task_coder_auth_3", "title": "Token刷新", "status": "pending"},
#     ]
#   }]
# }

# 步骤3: 开始执行第一个子任务，先标记为进行中
update_task_progress(
    collab_id="a8K2pQ",
    item_key="task_coder_auth",
    subtask_id="st_task_coder_auth_1",
    status="doing",
    note="开始实现登录API",
)

# ... 执行实际工作 ...

# 步骤4: 完成后标记为done
update_task_progress(
    collab_id="a8K2pQ",
    item_key="task_coder_auth",
    subtask_id="st_task_coder_auth_1",
    status="done",
    progress=100,
    note="API已实现并通过本地测试",
)

# 步骤5: 批量更新多个子任务（可选）
batch_update_tasks(
    collab_id="a8K2pQ",
    item_key="task_coder_auth",
    updates=[
        {"subtask_id": "st_task_coder_auth_2", "status": "done", "progress": 100, "note": "注册API完成"},
        {"subtask_id": "st_task_coder_auth_3", "status": "doing", "progress": 30, "note": "正在实现Token刷新"},
    ],
)
```

### 关键规则

1. **PM使用`assign_task()`** — 结构化参数，无需手写Markdown
2. **Worker必须先调用`board_view()`了解全局上下文，再调用`board_list_my_tasks()`查看自己的任务** — 禁止未读任务就执行
3. **Worker使用`update_task_progress()`** — 只需传subtask_id和status，无需重写content
4. **每个子task有唯一ID** — 格式`st_{item_key}_{index}`，由`assign_task`返回
5. **总进度自动计算** — 根据子任务状态自动推导整体进度
6. **状态流转** — pending → doing → done（或blocked），不可跳过

---

## Behavioral Constraints

- **No overstepping**: Dev does not make architecture decisions, QA does not change code, PM does not write code directly
- **No silence**: Report completion or blockers in group chat; send a progress update if no output for 15+ minutes
- **No assumptions**: Ask @PM when requirements are unclear; do not guess independently
- **File isolation**: Dev only modifies files within PM-assigned scope; request @PM for anything outside scope
- **Git Collaboration Standards**:
    - **Branch isolation**: Direct commits to `main/master` are forbidden. Create a feature branch before development, format: `{agent_id}/feat-{task_name}`.
    - **Local commits**: Run `git.commit` after completing a logical block; the system automatically injects your identity.
    - **Merge handoff**: After development, request review and merge via group chat @Senior_Bot or @PM.
    - **Single exit point**: Only agents with `vcs_remote` permission may push to remote GitHub and create PRs.
