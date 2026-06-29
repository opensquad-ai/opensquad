---
name: Collaboration Management
description: Complete guide to multi-agent collaboration card management, including starting collaborations, joining teams, and creating custom collaboration cards
version: 1.0.0
tags: collaboration, team, collab-card, multi-agent
required_tools:
  - collaboration
  - im
  - filesystem
---

# Collaboration Management Skill

This Skill provides a complete management workflow and best practices for multi-agent collaboration cards (Collab Cards).

## What is a Collaboration Card?

A collaboration card is a predefined multi-agent collaboration pattern that defines:
- **Collaboration phases and workflow**: Complete lifecycle from requirements to delivery
- **Role responsibilities**: Specific responsibilities for PM, Developer, QA, and other roles
- **Message formats and conventions**: Standardized formats for task assignment, status reporting, and bug reports
- **Behavioral constraints**: Avoiding overstepping, silence, and assumptions

**Storage location**: `collab_cards/*.md`

**Core mechanism**:
- Collaboration card content is injected into the Agent's system prompt during the session
- The PM loads the collaboration card when starting a collaboration; Workers load the same card when joining
- The collaboration card is automatically unloaded when leaving the collaboration

---

## Collaboration Card Structure

### Standard Format

```markdown
---
name: card_name
description: Collaboration card description
tags: tag1, tag2
suggested_roles: pm, developer, qa
min_members: 2
---

## Project Lifecycle

| Phase | Lead | Output | Transition Condition |
|-------|------|--------|---------------------|
| P1 Requirements | PM | Requirements doc | User approval |
| P2 Implementation | Dev | Code | Development complete |
| P3 Testing | QA | Test report | Tests pass |

## Standard Message Formats

**Task Assignment**:
\```
@Agent [TASK] Task Name
Description: ...
Acceptance Criteria: ...
\```

**Status Report**:
\```
[STATUS] Completed: XXX  Blocked: YYY
\```

## Behavioral Constraints

- No overstepping
- No silence
- No assumptions
```

---

## Workflows

### Workflow 1: View Available Collaboration Cards

**Scenario**: Learn which collaboration modes are available in the system

```xml
<tool name="collaboration">
  <function>list_collab_cards</function>
  <parameters></parameters>
</tool>
```

**Example Response**:
```json
{
  "status": "success",
  "cards": [
    {
      "name": "software_dev_team",
      "display_name": "Software Development Team",
      "description": "Multi-agent collaboration protocol for complete software development projects",
      "suggested_roles": ["pm", "developer", "qa"],
      "tags": "software, team, pm, dev, qa"
    },
    {
      "name": "code_review",
      "display_name": "Code Review",
      "description": "Collaboration mode focused on code review",
      "suggested_roles": ["reviewer", "author"],
      "tags": "code, review"
    }
  ]
}
```

**Use Cases**:
- Before starting a new project, select an appropriate collaboration mode
- Understand the role requirements for each collaboration card
- Check whether a new collaboration card needs to be created

---

### Workflow 2: Start a Collaboration Session (PM Only)

**Prerequisites**:
- Your `config.json` has `collaboration.role` set to `"pm"`
- You have joined the relevant group
- You know the Agent directory names of team members

#### Step 1: View Collaboration Card List (Optional)

```xml
<tool name="collaboration">
  <function>list_collab_cards</function>
  <parameters></parameters>
</tool>
```

#### Step 2: Start Collaboration

```xml
<tool name="collaboration">
  <function>start_collaboration</function>
  <parameters>
    <card>software_dev_team</card>
    <members>["coder", "qa_agent"]</members>
    <group_id>gXXXXX</group_id>
    <project_name>User Login Module</project_name>
    <project_description>Implement user login, registration, and password reset functionality</project_description>
  </parameters>
</tool>
```

**Parameter Description**:
- `card`: Collaboration card name (without `.md` extension)
- `members`: List of Agent directory names to invite (optional; if not provided, only the collaboration card is loaded)
- `group_id`: Group ID (used to send invitation messages)
- `project_name`: Project name (optional)
- `project_description`: Project description (optional)

#### Step 3: Assign Tasks in Group Chat

After starting the collaboration, the card content has been injected into your system prompt. Assign tasks according to the collaboration card conventions:

```
@coder [TASK] Implement User Login API
File scope: src/auth/
Dependencies: None  Priority: P0
Acceptance Criteria:
1. POST /api/login endpoint is functional
2. Supports email/password login
3. Returns JWT token
4. Passes unit tests

@qa_agent [TASK] Write test cases for login module
File scope: tests/auth/
Dependencies: coder completes development  Priority: P1
Acceptance Criteria:
1. Covers normal login flow
2. Covers error scenarios (wrong password, account not found, etc.)
3. 100% API test pass rate
```

#### Step 4: Manage Collaboration Progress

Use group chat to continuously track:
- Receive status reports from Workers
- Handle bug reports
- Announce phase transitions

**Phase Transition Example**:
```
[PHASE] P2→P3  Note: Development complete, entering test phase  @qa_agent please begin testing
```

---

### Workflow 3: Join a Collaboration Session (Worker Only)

**Scenario**: Invited by PM to join a collaboration, or proactively joining an already-started collaboration

#### Step 1: Confirm Invitation

When the PM starts a collaboration, you will receive a message like this in the group chat:
```
Project started: User Login Module
Collaboration card: software_dev_team
Suggested roles: developer, qa

@coder @qa_agent You are invited to join the collaboration. Please consider calling:
join_collaboration(card="software_dev_team")
```

#### Step 2: Join Collaboration

```xml
<tool name="collaboration">
  <function>join_collaboration</function>
  <parameters>
    <card>software_dev_team</card>
  </parameters>
</tool>
```

**Effect**:
- Collaboration card content is injected into your system prompt
- You start working according to the collaboration card conventions (message format, behavioral constraints, etc.)

#### Step 3: Work According to Conventions

**Receive Task**:
```
@coder [TASK] Implement User Login API
...
```

**Report Status** (per collaboration card conventions):
```
[STATUS] Completed: src/auth/login.py  Tests: 8/8 passed  Blocked: None
```

**Report Bug** (if you are QA):
```
@coder [BUG] src/auth/login.py:45
Symptom: Empty password returns 500  Expected: 400
Reproduce: POST /api/login {"password":""}
```

---

### Workflow 4: End a Collaboration Session

#### PM Ends Collaboration

**Scenario**: Project complete, ready for delivery

```xml
<tool name="collaboration">
  <function>end_collaboration</function>
  <parameters>
    <card>software_dev_team</card>
    <group_id>gXXXXX</group_id>
  </parameters>
</tool>
```

**Effect**:
- Collaboration card is unloaded from the PM's system prompt
- All members are automatically notified in group chat
- Members receive a suggestion to leave the collaboration

#### Worker Leaves Collaboration

**Scenario**: Received PM's notification that collaboration has ended

```xml
<tool name="collaboration">
  <function>leave_collaboration</function>
  <parameters>
    <card>software_dev_team</card>
  </parameters>
</tool>
```

**Effect**:
- Collaboration card is unloaded from your system prompt
- Returns to normal working mode

---

### Workflow 5: Create a Custom Collaboration Card

**Scenario**: Existing collaboration cards do not meet requirements; a custom collaboration mode is needed

#### Step 1: Design the Collaboration Workflow

Determine the following elements:
- **Collaboration phases**: How many phases, the output and transition condition for each
- **Role responsibilities**: What roles exist and their respective duties
- **Message conventions**: Formats for task assignment, status reporting, and bug reports
- **Behavioral constraints**: What issues to avoid

#### Step 2: Create the Collaboration Card File

```xml
<tool name="filesystem">
  <function>write_file</function>
  <parameters>
    <path>collab_cards/my_research_team.md</path>
    <content>---
name: my_research_team
description: Research project collaboration mode, suitable for literature review, data analysis, and report writing
tags: research, analysis, report
suggested_roles: lead, researcher, writer
min_members: 2
---

## Project Lifecycle

| Phase | Lead | Output | Transition Condition |
|-------|------|--------|---------------------|
| P1 Planning | Lead | Research plan | Lead approval |
| P2 Research | Researcher | Literature list, datasets | Research complete |
| P3 Analysis | Researcher | Analysis results | Lead approval |
| P4 Writing | Writer | Research report | Report delivered |

Rules: Only the Lead may announce phase transitions; outputs from each phase must be shared in group chat.

---

## Role Responsibilities

### Lead (Project Lead)
- Create research plan and timeline
- Assign tasks to Researcher and Writer
- Review phase outputs
- Announce phase transitions

### Researcher
- Conduct literature research
- Collect and clean data
- Perform data analysis
- Report progress to Lead

### Writer
- Write report based on analysis results
- Organize references
- Revise report content

---

## Standard Message Formats

**Lead → Researcher Task Assignment**
\```
@Researcher [TASK] Task Name
Topic: Research topic
Data source: Data source
Deliverable: Expected output
Deadline: YYYY-MM-DD
\```

**Researcher → Lead Progress Report**
\```
[STATUS] Completed: XXX  Progress: 80%  Issues: YYY
\```

**Lead → Writer Writing Request**
\```
@Writer [WRITE] Report section
Key points: 1. xxx 2. yyy
References: [links or file paths]
Word count: approx. 2000 words
\```

**Phase Signal (Lead only)**
\```
[PHASE] P2→P3  Note: Research complete, entering analysis phase  @Researcher please begin data analysis
\```

---

## Behavioral Constraints

- **No overstepping**: Researcher does not write reports, Writer does not do data analysis, Lead does not execute detailed tasks directly
- **No silence**: Report progress daily, immediately @Lead when blocked
- **No assumptions**: When unsure about research direction, @Lead to confirm rather than deciding independently
- **Document everything**: All important decisions, data sources, and analysis results must be recorded in group chat or shared documents
- **Quality first**: Do not sacrifice accuracy for speed; raise issues promptly

---

## Delivery Standards

### P1 Research Plan
- [ ] Clear research questions and objectives
- [ ] List of data sources and literature scope
- [ ] Timeline and milestones

### P2 Literature and Data
- [ ] At least 10 relevant papers
- [ ] Cleaned dataset
- [ ] Preliminary research summary

### P3 Analysis Results
- [ ] Data visualization charts
- [ ] Key findings and conclusions
- [ ] Statistical test results (if applicable)

### P4 Research Report
- [ ] Complete report structure (abstract, introduction, methods, results, discussion, references)
- [ ] Clear charts and accurate data
- [ ] References properly formatted
- [ ] Lead review passed</content>
  </parameters>
</tool>
```

#### Step 3: Verify the Collaboration Card

```xml
<tool name="collaboration">
  <function>list_collab_cards</function>
  <parameters></parameters>
</tool>
```

Check that the returned list includes the newly created `my_research_team`.

#### Step 4: Test the Collaboration Card

```xml
<tool name="collaboration">
  <function>start_collaboration</function>
  <parameters>
    <card>my_research_team</card>
    <members>["researcher1", "writer1"]</members>
    <group_id>gXXXXX</group_id>
    <project_name>AI Research Report</project_name>
  </parameters>
</tool>
```

---

## Best Practices

### Before Starting a Collaboration

- [ ] Confirm all members have joined the group
- [ ] Confirm members have `collaboration.enabled = true`
- [ ] Confirm members have the necessary tools (e.g., Developer needs `filesystem`, `vcs_remote`)
- [ ] Prepare project requirements document or description

### During Collaboration

1. **Clear division of responsibilities**:
   - PM is responsible for planning, assigning, coordinating, and acceptance
   - Workers are responsible for executing, reporting, and providing feedback

2. **Timely communication**:
   - Report immediately upon completing a task
   - Immediately @PM when blocked
   - At least one progress update per day

3. **Follow conventions**:
   - Use the message formats defined by the collaboration card
   - Do not overstep and execute another role's tasks
   - Do not assume requirements; confirm immediately when in doubt

4. **Documentation**:
   - Record important decisions in group chat
   - Submit code changes via Git commits
   - Record test results in a test report

### After Collaboration Ends

- [ ] PM confirms all deliverables are complete
- [ ] Send project summary to group chat
- [ ] Call `end_collaboration` to formally end
- [ ] Workers call `leave_collaboration` to leave
- [ ] Archive project documents and code

### Collaboration Card Design Principles

1. **Concise and clear**:
   - Clear phase division (3–5 phases)
   - Clear role responsibilities (2–4 roles)
   - Consistent message format

2. **Actionable**:
   - Each phase has a clear output
   - Each role has clear acceptance criteria
   - Phase transition conditions are verifiable

3. **Flexible**:
   - Allow PM to adjust based on actual conditions
   - Do not over-constrain work details
   - Support asynchronous collaboration

4. **Defensive**:
   - Explicitly prohibited actions (no overstepping, silence, or assumptions)
   - Define conflict resolution mechanisms
   - Set communication frequency requirements

---

## Collaboration Role Configuration

### PM Configuration

```json
{
  "collaboration": {
    "enabled": true,
    "role": "pm"
  },
  "tools": [
    "collaboration",
    "im",
    "delegate_task",
    "filesystem"
  ]
}
```

### Developer Configuration

```json
{
  "collaboration": {
    "enabled": true,
    "role": "developer"
  },
  "tools": [
    "collaboration",
    "filesystem",
    "vcs_remote",
    "im",
    "api_browser"
  ]
}
```

### QA Configuration

```json
{
  "collaboration": {
    "enabled": true,
    "role": "qa"
  },
  "tools": [
    "collaboration",
    "filesystem",
    "api_browser",
    "im"
  ]
}
```

### Reviewer Configuration

```json
{
  "collaboration": {
    "enabled": true,
    "role": "reviewer"
  },
  "tools": [
    "collaboration",
    "vcs_remote",
    "filesystem",
    "im"
  ]
}
```

### Worker (General) Configuration

```json
{
  "collaboration": {
    "enabled": true,
    "role": "worker"
  },
  "tools": [
    "collaboration",
    "im",
    "filesystem"
  ]
}
```

---

## Built-in Collaboration Cards

### software_dev_team

**Applicable Scenarios**: Complete software development projects

**Lifecycle**:
1. P1 Requirements - PM ↔ User
2. P2 Architecture & Division - PM
3. P3 Parallel Implementation - Dev × N
4. P4 Testing & Fixes - QA + Dev
5. P5 Delivery - PM → User

**Suggested Roles**: pm, developer, qa

**Features**:
- Complete software development workflow
- Strict Git collaboration conventions
- Clear task assignment and status reporting formats

### code_review

**Applicable Scenarios**: Code review

**Lifecycle**:
1. Submit code
2. Review feedback
3. Revise code
4. Approve and merge

**Suggested Roles**: reviewer, author

**Features**:
- Focuses on code quality
- Fast feedback loop
- Lightweight process

### autonomous_vcs_dev

**Applicable Scenarios**: Autonomous VCS development workflow

**Lifecycle**:
1. Feature development
2. Self-testing and validation
3. PR submission

**Suggested Roles**: developer

**Features**:
- Solo development scenario
- Complete Git workflow
- Autonomous testing and validation

---

## Troubleshooting

### Issue 1: Cannot Start Collaboration

**Check**:
1. Is your `collaboration.role` set to `"pm"`?
2. Is the `collaboration` tool in the `tools` list?
3. Does the collaboration card file exist?

**Resolution**:
```xml
<!-- Check configuration -->
<tool name="filesystem">
  <function>read_file</function>
  <parameters>
    <path>agents/my_agent/config.json</path>
  </parameters>
</tool>

<!-- If role is not pm, modify and restart -->
<!-- If collaboration card does not exist, create it first -->
```

### Issue 2: Worker Cannot Join Collaboration

**Check**:
1. Is the Worker's `collaboration.enabled` set to `true`?
2. Does the Worker have the `collaboration` tool?
3. Is the collaboration card name correct?

**Resolution**:
```xml
<!-- Check Worker configuration -->
<tool name="filesystem">
  <function>read_file</function>
  <parameters>
    <path>agents/worker_agent/config.json</path>
  </parameters>
</tool>
```

### Issue 3: Collaboration Card Content Not Taking Effect

**Cause**: The collaboration card only affects the current session after loading; it only takes effect in conversations after starting/joining the collaboration.

**Resolution**:
- PM: After starting collaboration, begin a new conversation
- Worker: After joining collaboration, begin a new conversation

### Issue 4: Collaboration Card Format Error

**Check**:
1. Is the YAML front matter format correct (starts and ends with `---`)?
2. Is the Markdown content valid?

**Resolution**:
```xml
<!-- Read the collaboration card to check format -->
<tool name="filesystem">
  <function>read_file</function>
  <parameters>
    <path>collab_cards/my_card.md</path>
  </parameters>
</tool>
```

---

## Quick Reference

### Common Operations

```xml
<!-- PM: Start collaboration -->
<tool name="collaboration">
  <function>start_collaboration</function>
  <parameters>
    <card>card_name</card>
    <members>["agent1", "agent2"]</members>
    <group_id>gXXXXX</group_id>
    <project_name>project_name</project_name>
  </parameters>
</tool>

<!-- Worker: Join collaboration -->
<tool name="collaboration">
  <function>join_collaboration</function>
  <parameters>
    <card>card_name</card>
  </parameters>
</tool>

<!-- PM: End collaboration -->
<tool name="collaboration">
  <function>end_collaboration</function>
  <parameters>
    <card>card_name</card>
    <group_id>gXXXXX</group_id>
  </parameters>
</tool>

<!-- Worker: Leave collaboration -->
<tool name="collaboration">
  <function>leave_collaboration</function>
  <parameters>
    <card>card_name</card>
  </parameters>
</tool>

<!-- View available collaboration cards -->
<tool name="collaboration">
  <function>list_collab_cards</function>
  <parameters></parameters>
</tool>

<!-- Create new collaboration card -->
<tool name="filesystem">
  <function>write_file</function>
  <parameters>
    <path>collab_cards/my_card.md</path>
    <content>---
name: my_card
description: Description
tags: tag1, tag2
suggested_roles: role1, role2
---

Collaboration card content...</content>
  </parameters>
</tool>
```

### Collaboration Flowchart

```
PM:
  View cards → Start collaboration → Assign tasks → Track progress → Announce phase transitions → End collaboration
                     ↓
Worker:
               Join collaboration → Execute tasks → Report status → Leave collaboration
```

---

**Skill Version**: v1.0.0  
**Last Updated**: 2026-03-01  
**Related Skills**: agent_config_management, vcs_collaboration
