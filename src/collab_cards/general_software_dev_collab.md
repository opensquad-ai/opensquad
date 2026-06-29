---
name: general_software_dev_collab
description: General collaboration protocol for common software development tasks, emphasizing cross-role handoffs, quality gates, and risk controls.
tags: software, collaboration, requirements, design, implementation, testing, release
suggested_roles: pm, backend, frontend, qa, devops, reviewer
min_members: 2
---

# General Software Development Collaboration Guide

## 1. Role Interfaces

- **PM**: Requirements owner; confirms scope and acceptance criteria
- **Dev (Frontend/Backend)**: Implements assigned tasks and maintains technical quality
- **QA**: Owns test plan and release sign-off
- **DevOps**: Owns pipelines, deployment safety, and observability
- **Reviewer**: Ensures code quality and compliance with standards

## 2. Lifecycle Handoffs

### Requirements → Design
- PM provides PRD + acceptance criteria
- Dev reviews feasibility and suggests architecture
- QA reviews testability and edge cases

### Design → Implementation
- Interfaces and data contracts frozen
- Task list assigned with file scope and dependencies

### Implementation → Testing
- **Self-test required**: every dev role runs unit/integration checks for their scope before handoff
- Dev provides test evidence and change summary
- QA executes test plan; logs defects with reproduction steps

### Testing → Release
- QA approves with report; DevOps validates release checklist
- **Frontend acceptance**: run browser-based user-flow validation (e.g., Browser MCP) to simulate real user actions and confirm UX matches requirements
- Rollback plan documented for any risk change

## 3. Communication Templates

**Task Assignment (PM)**
```
@Dev-A [TASK] Implement feature X
Scope: src/feature_x/  Dependencies: T-001  Priority: P1
Acceptance: API returns 200, UI renders correctly, tests updated
```

**Status Update (Dev)**
```
[STATUS] In Progress: src/feature_x/api.py  Tests: 3/5 passed  Blocked: no
```

**Bug Report (QA)**
```
@Dev-A [BUG] src/feature_x/api.py:88
Symptom: 500 on invalid input  Expected: 400
Reproduce: POST /api/x {"foo": ""}
```

## 4. Collaboration Rules

- No role overreach: PM does not code; QA does not modify code; Dev does not redefine requirements
- Change scope must be confirmed by PM and documented
- Every agent reports blockers within 30 minutes
- Only assigned files may be modified without explicit approval

## 5. Deliverable Checklist

- Requirements documented and confirmed
- Code meets linting and test requirements
- QA report includes coverage and known risks
- Release checklist complete with rollback plan
- Final delivery summary provided to user
