# OpenSquad Collaborative Development Rules (Git/VCS)

This document defines the standard workflow and behavior expectations for multiple
agents collaborating on shared workspaces or in LAN-distributed environments.

---

## 0. LAN Collaboration Overview

OpenSquad supports multiple Agents deployed on different machines across a LAN, all
connecting to a common self-hosted Git server (e.g. Gitea) to enable distributed
collaboration. Each Agent pulls the repository independently, develops on its own
branch, pushes, and waits for a reviewing Agent to merge. Discussion and decisions
happen in the group chat — no GitHub / pull-request flow is required.

```
[LAN Git server — Gitea :3000]
     ├── Agent coder-001  (branch coder-001/feat-login)
     ├── Agent coder-002  (branch coder-002/feat-payment)
     ├── Agent pm         (owns main, performs merges)
     └── Agent qa         (pulls branches to test)
```

Code collaboration uses the `git_core` plugin; discussion / review happens in the
group chat.

---

## 1. Configuring the LAN Git Server

### 1.1 Visual configuration via the admin panel (recommended)

1. Open the admin panel → **Plugins** → find **Git Core**
2. Click the gear icon on the right to expand the config panel
3. Fill in the following fields and click **Save**:

| Field | Description | Example |
|---|---|---|
| `git_server` | LAN Git server address | `http://192.168.1.100:3000` |
| `default_remote` | Default remote name | `origin` |
| `default_branch` | Default branch name | `main` |
| `username` | HTTPS auth username (leave empty to use `oauth2`) | `teambot` |
| `access_token` | Access token or password (masked) | `gp_xxxx...` |

The configuration takes effect immediately — no Agent restart is required.

> **How to obtain an `access_token` (only once)**
>
> Using Gitea as an example:
> 1. Open the Gitea web UI in your browser (e.g. `http://192.168.1.100:3000`)
> 2. Top-right avatar → **Settings** → **Applications** in the sidebar
> 3. Under "Generate New Token", enter a name (e.g. `opensquad`) and click generate
> 4. Copy the generated token, paste it into the plugin's `access_token` field, and save
>
> After saving, every Agent's clone / push / pull will automatically carry the auth
> credentials — no need to repeat the operation.
>
> **If the LAN Git server has no password** (purely trusted intranet environment),
> make the Gitea repository public or disable mandatory auth; leave `access_token`
> empty and skip the steps above.

### 1.2 Via system_config.json (global default)

Suitable for setting a system-wide default. Values saved through the plugin UI take
priority.

```json
"vcs": {
    "git_server": "http://192.168.1.100:3000",
    "default_remote": "origin",
    "default_branch": "main"
}
```

### 1.3 Override via environment variables

```bash
export VCS_GIT_SERVER=http://192.168.1.100:3000
export VCS_DEFAULT_REMOTE=origin
export VCS_DEFAULT_BRANCH=main
```

**Configuration priority** (high → low):
```
Plugin UI saved value > environment variable > system_config.json > built-in default
```

---

## 2. Core Collaboration Model: Branch Isolation + Group-Chat Discussion

- **Independence**: each Agent operates on its own feature branch on the LAN Git
  server — no interference.
- **Branch-driven development**: never have multiple Agents making uncommitted
  changes on the same branch at the same time.
- **Identity awareness**: every `git.commit` automatically sets `GIT_AUTHOR_NAME`
  to the Agent's `agent_id`; the commit history is contributor-traceable.
- **Discussion replaces PRs**: code review, merge decisions, and conflict
  negotiation all happen in the group chat — no GitHub PR needed.

---

## 3. Role Responsibilities

- **PM (project manager)**: initializes the repository, maintains `main` / `dev`
  branches, assigns tasks, performs final merges.
- **Developer (development Agent)**: cuts a feature branch from the base branch,
  develops, and pushes.
- **Reviewer (reviewing Agent)**: pulls the feature branch, tests, and notifies the
  PM in the group chat when ready to merge.
- **QA (test Agent)**: pulls the assigned branch, runs tests, and reports results
  to the group chat.

---

## 4. Standard Operating Workflow

### 4.1 First-time repository access

```python
# Once git_server is configured, only a relative path is needed — no full URL
git.clone("team/project.git", "/workspace/project")
```

If `git_server` is not configured, use a full URL:
```python
git.clone("http://192.168.1.100:3000/team/project.git", "/workspace/project")
```

### 4.2 Starting a task

```python
# Cut a feature branch prefixed with your own ID
git.checkout("/workspace/project", "coder-001/feat-login", create=True)
```

### 4.3 Development and commit

```python
# After editing files...
git.add("/workspace/project", ["."])
git.commit("/workspace/project", "feat: implement login logic")
# author_name is auto-injected as agent_id; no manual specification needed
```

### 4.4 Pushing the branch

```python
# After default_remote / default_branch are configured, the params can be omitted
git.push("/workspace/project")

# First push: establish tracking
git.push("/workspace/project", set_upstream=True)

# Explicit specification
git.push("/workspace/project", remote="origin", branch="coder-001/feat-login")
```

### 4.5 Pulling others' updates

```python
# Pull latest changes on the main branch
git.pull("/workspace/project", branch="main")

# Or fetch-only, then merge manually
git.fetch("/workspace/project")
git.merge("/workspace/project", "origin/main")
```

### 4.6 Handoff for review (group chat)

The development Agent notifies in the group chat:
> "@pm I've finished `coder-001/feat-login`, please pull and review"

PM or Reviewer pulls and inspects:
```python
git.fetch("/workspace/project")
git.checkout("/workspace/project", "coder-001/feat-login")
git.log("/workspace/project")
```

### 4.7 Merge and cleanup

After review passes, the PM performs the merge:
```python
git.checkout("/workspace/project", "main")
git.merge("/workspace/project", "coder-001/feat-login")
git.push("/workspace/project", branch="main")
git.branch("/workspace/project", "coder-001/feat-login", delete=True)
```

---

## 5. Conflict Resolution Protocol

If conflicts appear during merge or pull:

1. Call `git.status` to identify the conflicting files (shown as `both modified`).
2. Read the conflicting file, parse the `<<<<<<< HEAD` / `=======` / `>>>>>>>`
   markers.
3. Edit based on context, retain the correct content, and remove all conflict
   markers.
4. If the resolution is ambiguous, discuss with the relevant Agent in the group
   chat and decide.
5. Once resolved:

```python
git.add("/workspace/project", ["path/to/resolved_file.py"])
git.commit("/workspace/project", "fix: resolve merge conflict with coder-002")
```

---

## 6. Audit and Transparency

- All Git operations must go through the `git_core` plugin; operation logs are
  automatically written to `data/audit/vcs_footprints.jsonl`.
- Bypassing hooks with raw `bash` Git commands (e.g. `git commit --no-verify`) is
  strictly forbidden.
- A complete operation history is available through the admin panel's **VCS Audit
  Timeline** UI.

---

## 7. Tool Cheat Sheet

| Tool | Purpose | Key parameters |
|---|---|---|
| `git.clone` | Clone a repo (relative path auto-completed with server address) | `url`, `path` |
| `git.checkout` | Switch / create branch | `path`, `target`, `create=True` |
| `git.add` | Stage files | `path`, `files=["."]` |
| `git.commit` | Commit (auto-injects agent_id as author) | `path`, `message` |
| `git.push` | Push to remote (uses configured remote/branch) | `path`, `set_upstream` |
| `git.pull` | Pull and merge | `path`, `remote`, `branch` |
| `git.fetch` | Fetch only, no merge | `path`, `remote` |
| `git.merge` | Merge a branch | `path`, `source` |
| `git.rebase` | Rebase | `path`, `upstream` |
| `git.branch` | List / create / delete branch | `path`, `name`, `delete` |
| `git.status` | Working tree status | `path` |
| `git.diff` | Show changes | `path`, `cached` |
| `git.log` | Commit history | `path`, `limit` |
| `git.remote_add` | Add a remote | `path`, `name`, `url` |
| `git.remote_set_url` | Change remote URL | `path`, `name`, `url` |
