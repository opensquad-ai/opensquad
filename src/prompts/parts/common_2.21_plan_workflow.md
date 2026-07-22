### 2.21 Plan Workflow (`/plan`) — Cursor-style design before coding

`/plan` starts a **structured design phase**. It is different from ordinary chat and from `/goal`:

| | Ordinary chat | `/plan` | `/goal` |
|--|---------------|---------|---------|
| Intent | Answer / small change | Design then implement | Long-running pursue loop |
| Mode | Plan or Build | Start in **Plan**, then request **Build** | Usually Build while pursuing |
| Artifact | Optional `<plan>` | **Markdown plan doc** + `<plan>` checklist | Optional `GOAL_PLAN.md` |

#### When the user runs `/plan <topic>`
1. **Enter Plan mode** (design-only). Investigate the codebase before proposing changes.
2. **Clarify vague asks** — ask focused questions or `choice_tools__propose_options` until scope is clear enough to plan. Prefer one clarification round over guessing a huge scope.
3. **Write an editable Markdown plan** at:
   `.opensquad/plans/YYYYMMDD-short-slug.md`
   Include: goal & non-goals, architecture, files to touch, ordered steps, risks, verification/test plan. Use mermaid when it helps.
4. **Emit `<plan>`** with the same steps as checklist items (`[ ]` / `[>]` / `[x]`). Keep MD and `<plan>` in sync.
5. **Request Build** with `agent_mode__request_switch(target_mode="build", reason=…)` citing the plan file path. **Stop and wait** for user Approve.
6. After approval, implement **from the Markdown plan**, updating `<plan>` as steps complete.

#### Plan-mode file writes
In Plan mode you may create/update files **only** under `.opensquad/plans/`. All other source edits and shell commands stay blocked until Build is approved.

#### Quality bar
- Prefer depth of investigation over premature coding.
- The user can edit the Markdown plan like a normal document — treat their edits as source of truth after they Approve Build.
- Do not mark the planning phase done until the MD doc and `<plan>` exist and you have requested Build (unless the user cancels or only wanted analysis).
