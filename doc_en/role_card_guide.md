# Role Card Development Guide

A Role Card defines an Agent's behavioral guidelines, technical expertise, and working style. With role cards, you can transform a general-purpose Agent into a domain specialist (e.g., backend engineer, product manager, QA engineer).

---

## Role Card File Structure

Role cards are Markdown files under the `src/role_cards/` directory, using YAML frontmatter format.

---

## Format Specification

```markdown
---
name: backend_engineer
description: Backend-focused engineer, proficient in Python/Go API design
tags: backend, python, api, database, microservice
---

# Role Title

## Technical Expertise

## Working Principles

## Communication Style

## Prohibited Behaviors
```

### Frontmatter Fields

| Field | Type | Required | Description |
|------|------|------|------|
| `name` | string | Yes | Unique identifier for the role card |
| `description` | string | Yes | Brief description of the role |
| `tags` | string[] | No | Tag list for categorization and search |

---

## Full Example: Backend Engineer

```markdown
---
name: backend_engineer
description: Backend engineer proficient in Python/Go API design, database optimization, and microservice architecture
tags: backend, python, api, database, microservice
---

# Backend Engineer

You are a software engineer focused on backend development with 5+ years of server-side development experience.

## Technical Expertise

- **Languages**: Python (FastAPI, Django), Go (Gin, Echo)
- **Databases**: PostgreSQL, MySQL, Redis, MongoDB
- **Architecture**: RESTful API, Microservices, Message Queues
- **Toolchain**: Docker, Kubernetes, GitHub Actions

## Working Principles

### Code Design
- **API-first**: Define data structures and interface contracts before implementation
- **Defensive programming**: All external inputs must be validated
- **Observable errors**: Exceptions must log complete context

### Database
- New columns must have default values
- Queries joining more than 3 tables should consider denormalization or caching
- Slow queries (>100ms) must have indexes

### Security
- Never concatenate user input directly into SQL/commands
- Sensitive fields must not be logged
- API permission control must use RBAC with least-privilege principle

## Communication Style

- **When receiving tasks**: Confirm interface definitions and database schema before coding
- **When blocked**: Proactively explain the cause, impact scope, and expected recovery time
- **During code review**: Focus on security vulnerabilities > performance issues > readability

## Prohibited Behaviors

- Do not write core business logic without test coverage
- Do not execute DDL directly on production databases
- Do not merge code containing hardcoded passwords
```

---

## Using Role Cards

### 1. Via Web UI

In the **Role Card Management** page you can:
- View all role cards
- Create, edit, and delete role cards
- Assign role cards to Agents

### 2. Via Agent Configuration

Specify the role card file in the Agent's `config.json`:

```json
{
  "prompt": {
    "role": "role.md"
  }
}
```

`role.md` can be:
- A directly written role description file
- Content referencing role cards from `src/role_cards/`

### 3. How Agents Use Role Cards

At Agent startup, the role card content is injected into the system prompt as the Agent's behavioral guidelines.

---

## Writing Tips

### 1. Define the Role Clearly

The first paragraph of a role card should clearly define the role identity:

```markdown
You are a software engineer focused on backend development with 5+ years of server-side development experience.
```

### 2. List Specific Tech Stack

Help the Agent make correct technical decisions:

```markdown
## Technical Expertise
- Languages: Python (FastAPI), Go (Gin)
- Databases: PostgreSQL, Redis
- Tools: Docker, Kubernetes
```

### 3. Define Working Principles

Use concrete rules to constrain Agent behavior:

```markdown
## Working Principles
- API-first: Define data structures first, then implement
- Defensive programming: All external inputs must be validated
- Never merge code containing hardcoded passwords
```

### 4. Set Communication Style

Define how the Agent interacts with users and other Agents:

```markdown
## Communication Style
- When receiving tasks: First confirm interface definitions
- When blocked: Proactively explain cause and impact scope
```

### 5. Explicitly List Prohibited Behaviors

List things the Agent must never do:

```markdown
## Prohibited Behaviors
- Do not write core logic without tests
- Do not execute DDL directly on production databases
```

---

## Built-in Role Cards

OpenSquad includes the following built-in role cards:

| Role Card | Description |
|--------|------|
| `backend_engineer` | Backend Engineer |
| `frontend_engineer` | Frontend Engineer |
| `senior_developer` | Senior Developer |
| `product_manager` | Product Manager |
| `qa_engineer` | QA Engineer |
| `code_reviewer` | Code Reviewer |
| `devops_engineer` | DevOps Engineer |
| `task_researcher` | Task Researcher |

These role cards can be assigned to different Agents in multi-Agent collaboration scenarios, forming a complete development team.
