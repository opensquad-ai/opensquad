# Skill Development Guide

A Skill is a reusable task instruction package in OpenSquad. Agents can load skills on demand to gain specialized knowledge and operational workflows for specific tasks.

The skill system is compatible with the Claude Code / AgentSkills.io open standard.

---

## Skill System Architecture

### Public Skills vs Private Skills

| Type | Storage | Loading | Injection |
|------|----------|--------|----------|
| Public Skills | `src/skills/` | Auto-discovery | Summary injection (name + description), activated on demand |
| Private Skills | `skills/` in Agent dir | Specified in config | Full injection into Prompt |

### Loading Flow

```
Agent Startup
    │
    ├─ 1. Load private skills (agent_dir/skills/)
    │     Skills listed in config.json skills.active are fully injected
    │
    └─ 2. Load public skills (src/skills/)
          Auto-discover all SKILL.md files, inject summaries only
          Agent can activate via read_skill() on demand
```

---

## Skill Directory Structure

```
skills/
├── my-skill/           # Skill directory name (skill identifier)
│   ├── SKILL.md        # Skill instruction file (required)
│   ├── skill.json      # Skill metadata (optional, for marketplace)
│   ├── tools.py        # Additional tool module (optional)
│   └── scripts/        # Script directory (optional)
│       └── helper.py
```

---

## SKILL.md Format

SKILL.md uses YAML frontmatter + Markdown body format:

```markdown
---
name: my-skill
description: A brief description of what this skill does
disable-model-invocation: false
allowed-tools: filesystem, web
---

# Skill Title

## Overview

This skill is used for...

## Workflow

### Step 1: ...

### Step 2: ...

## Notes

- ...
```

### Frontmatter Fields

| Field | Type | Required | Description |
|------|------|------|------|
| `name` | string | No | Display name; defaults to directory name |
| `description` | string | No | Skill description for summary display |
| `disable-model-invocation` | bool | No | Disable automatic model invocation; default false |
| `user-invocable` | bool | No | Allow manual user invocation; default true |
| `allowed-tools` | string | No | Comma-separated list of allowed tools |

> If `description` is not provided in the frontmatter, the system will use the first paragraph of the body as the description.

---

## skill.json Format (Optional)

Metadata for skill marketplace display:

```json
{
  "name": "data-analysis",
  "display_name": "Data Analysis",
  "version": "1.0.0",
  "description": "Analyze Excel (.xlsx/.xls) or CSV files...",
  "author": "OpenSquad",
  "tags": ["data", "excel", "csv", "analysis"],
  "category": "analysis"
}
```

| Field | Description |
|------|------|
| `name` | Skill identifier, matches directory name |
| `display_name` | Display name |
| `version` | Version number |
| `description` | Skill description |
| `author` | Author |
| `tags` | Tag list |
| `category` | Category |

---

## Full Example: Code Review Skill

### Directory Structure

```
skills/code_reviewer_lite/
├── SKILL.md
└── tools.py
```

### SKILL.md

```markdown
---
name: code-reviewer
description: Code review assistant, detects code smells, security issues, and TODO comments
allowed-tools: filesystem
---

# Code Review Skill

## Overview

Automated code review for Python and TypeScript, detecting common issues.

## Workflow

1. Identify the file or directory to review
2. Call review_file() or review_directory() for analysis
3. Organize the issue list by severity
4. Report results to user with fix suggestions

## Available Tools

- `review_file(path)` — Review a single file
- `review_directory(path)` — Review an entire directory
- `find_todos(path)` — Find TODO comments
- `estimate_complexity(path)` — Estimate cyclomatic complexity
```

### tools.py

```python
def review_file(path: str) -> dict:
    """
    Review a single file and return a list of issues.

    Args:
        path: File path (supports .py / .ts / .tsx)

    Returns:
        {"file": str, "issues": [...], "summary": str}
    """
    # Implementation...
    ...

def review_directory(path: str) -> dict:
    """
    Batch review all files in a directory.

    Args:
        path: Directory path

    Returns:
        {"files_reviewed": int, "total_issues": int, "results": [...]}
    """
    ...
```

> **Key**: Functions in `tools.py` are automatically registered as Agent-callable tools. Function name = tool name, docstring = tool description.

---

## Skill Injection Mechanism

### Full Injection (Private Skills)

All content of private skills is injected directly into the Agent's system prompt:

```markdown
## Skills

### Full-injected Skills (2)

#### my-skill - Description
*Allowed tools: filesystem, web*

(Full SKILL.md content)
```

### Summary Injection (Public Skills)

Public skills inject only name and description; Agents activate on demand:

```markdown
### Summary Skills (15, activate/read on demand)

**Important: Before starting any complex task, first check if a relevant skill exists in the library.**

How to use skills:
- Use `agent_setup.list_skills()` to see all available skills
- Use `agent_setup.read_skill(skill_name)` for one-time lookup
- Use `agent_setup.publish_skill(skill_dir)` to contribute a skill

- **Data Analysis** (`data-analysis`): Analyze Excel/CSV files...
- **Code Reviewer** (`code_reviewer_lite`): Code review assistant...
```

### Controlling Injection Level

In Agent config, use `prompt_preload` to control:

```json
{
  "prompt_preload": {
    "full_skills": ["code_reviewer_lite"],
    "hidden_skills": ["deprecated-skill"],
    "include_skills": true
  }
}
```

- `full_skills`: Fully inject these public skills
- `hidden_skills`: Hide these skills (not listed in summary)
- `include_skills`: Whether to include summaries; default true

---

## Runtime API

Agents can manage skills at runtime via these tools:

| Method | Description |
|------|------|
| `list_skills()` | List all loaded skills |
| `read_skill(name)` | Read and activate a specific skill's full content |
| `publish_skill(dir)` | Publish a skill to the public library (immediate, no restart) |
| `add_skill(dir, name)` | Hot-load a new skill |
| `remove_skill(name)` | Remove a loaded skill |

---

## Best Practices

### 1. Skills Should Be Self-Contained

A skill should contain all information needed to complete a task, so the Agent can execute independently after loading.

### 2. Provide Clear Workflows

Describe workflows in step-by-step fashion so the Agent can follow:

```markdown
### Step 1: Understand requirements
### Step 2: Inspect files
### Step 3: Run analysis
### Step 4: Report results
```

### 3. Use Concrete Examples

Provide specific command examples in SKILL.md to help the Agent understand how to invoke tools.

### 4. Use tools.py Wisely

- Encapsulate complex logic in `tools.py`
- Function docstrings should clearly describe parameters and return values
- Avoid writing large amounts of code in SKILL.md; put it in tools.py instead

### 5. Skill Granularity

- One skill focuses on one task domain
- Don't create an "all-purpose" skill
- Skills can be combined through Agent decision-making
