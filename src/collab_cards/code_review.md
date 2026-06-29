---
name: code_review
description: A collaboration protocol for code review tasks, defining feedback standards and handling workflows between reviewer and author.
tags: code-review, quality, feedback, development
suggested_roles: reviewer, author
min_members: 2
---

## Participating Roles

- **Author**: The developer submitting code, responsible for explaining the intent of changes and responding to reviewer feedback
- **Reviewer**: The reviewer, responsible for identifying issues and providing actionable improvement suggestions

---

## Review Dimensions

| Dimension | Description |
|-----------|-------------|
| Correctness | Is the logic accurate? Are edge cases handled? |
| Security | Any injection, privilege escalation, or sensitive data exposure risks? |
| Readability | Is naming clear? Is the structure easy to understand? |
| Performance | Obvious performance bottlenecks or unnecessary complexity? |
| Tests | Are critical paths protected by tests? |
| Docs | Are API/README/CHANGELOG updates required and provided? |

---

## Pre-Review Checklist (Author)

- Provide change summary, scope, and known risks
- Note any required migrations or feature flags
- **Self-test required**: run unit/integration checks for your scope
- Include test evidence (commands + results)
- Confirm interface contracts with other roles (API endpoints, data structures, integration method)
- Link related tasks/issues for context

---

## Feedback Format

```
[BLOCKER] Must fix: src/auth/login.py:45
Issue: Missing empty password validation, causes 500 error
Suggestion: Add `if not password: raise ValueError` before DB query

[SUGGESTION] Recommended improvement: src/utils/parser.py:12
Issue: Variable name `x` is unclear
Suggestion: Rename to `token_expiry_seconds`
```

**Severity levels**:
- `[BLOCKER]`: Must be fixed, otherwise not approved
- `[SUGGESTION]`: Recommended but not mandatory

---

## Workflow

1. Author posts `[REVIEW REQUEST] PR#123 description of change scope` in group chat
2. Reviewer completes review and replies with all BLOCKERs and SUGGESTIONs
3. Author addresses BLOCKERs and provides accept/reject rationale for SUGGESTIONs
4. Reviewer confirms BLOCKERs are resolved and replies `[APPROVED]`
