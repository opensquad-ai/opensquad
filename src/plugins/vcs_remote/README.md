# VCS Remote Plugin

## Overview
The `vcs_remote` plugin is built on top of GitHub CLI (`gh`). It provides Agents with remote collaboration capabilities, including repository forking, Issue tracking, Pull Request creation, automated review feedback, and querying the public GitHub profile of Issue/PR authors.

## Namespace: `vcs`

### Collaboration Toolset
- `vcs.issue_create(title, body, labels?, assignee?)`: Create a new Issue.
- `vcs.issue_list(limit?, state?)`: List Issues.
- `vcs.issue_view(issue_id)`: View details of a specific Issue.
- `vcs.issue_comment(issue_id, body)`: Post a comment on an Issue or PR (commonly used for code review feedback).
- `vcs.issue_close(issue_id, reason?)`: Close an Issue.
- `vcs.pr_create(title, body, base?, draft?)`: Create a Pull Request.
- `vcs.pr_list(limit?, state?)`: List Pull Requests.
- `vcs.pr_view(pr_id)`: View details of a specific PR.
- `vcs.pr_status()`: Check the status of currently relevant PRs.
- `vcs.pr_merge(pr_id, delete_branch?)`: Merge a PR.
- `vcs.pr_checkout(pr_id)`: Check out a PR branch locally.
- `vcs.repo_fork(repo_url)`: Fork a target repository to the Agent's account.
- `vcs.repo_clone(repo, path)`: Clone a repository using the gh protocol.
- `vcs.repo_view(repo?)`: View repository information.
- `vcs.repo_create(name, public?)`: Create a new repository on GitHub.
- `vcs.auth_check()`: Check GitHub authentication status.
- `vcs.push(remote?, branch?)`: Push a local branch to the remote.

---

### Public User Info Query Tools

This set of tools calls the GitHub REST API to query the public account information of Issue/PR authors and repository contributors.
They are useful for Agents making decisions in review workflows, contributor screening, and collaboration routing.

#### `vcs.user_info(username)`
Query the public profile of any GitHub user.

```
Fields: login, name, bio, company, location, email (if public), blog,
        twitter, account type, followers, following, public_repos,
        public_gists, hireable, created_at, updated_at, html_url
```

Example output:
```
GitHub User: @alice
Name        : Alice Smith
Bio         : Open source developer
Followers   : 1234
Public Repos: 87
Created At  : 2015-03-10T08:00:00Z
Profile URL : https://github.com/alice
```

---

#### `vcs.issue_author_info(issue_id, path?)`
View the public profile of the author of a given Issue. Automatically parses the author login from the Issue, then calls `user_info`.

```python
vcs.issue_author_info("42")
# or specify a repository directory
vcs.issue_author_info("42", path="/path/to/repo")
```

---

#### `vcs.pr_author_info(pr_id, path?)`
View the public profile of the author of a given PR. Automatically parses the author login from the PR, then calls `user_info`.

```python
vcs.pr_author_info("7")
```

Example output:
```
PR #7 — feat: add dark mode toggle
State   : open  |  Created: 2026-01-15T12:00:00Z
Branch  : feat/dark-mode → main
────────────────────────────────────────────────────────
Author profile (@bob):
GitHub User: @bob
Followers   : 320
Public Repos: 42
...
```

---

#### `vcs.repo_contributors(repo?, limit?, path?)`
List repository contributors sorted by commit count in descending order.

```python
vcs.repo_contributors()                          # auto-infer repo from git remote
vcs.repo_contributors("opensquad-ai/opensuqad")  # specify repository
vcs.repo_contributors(limit=10)                  # show only top 10
```

Example output:
```
Contributors for opensquad-ai/opensuqad (showing top 10):

    1. @alice                          commits:   523  type: User
    2. @bob                            commits:   187  type: User
    3. @dependabot[bot]                commits:    42  type: Bot
```

---

#### `vcs.user_repo_activity(username, repo?, path?)`
Query all Issues and PRs created by a given user in this repository (including closed/merged history).

Common use cases:
- Review a contributor's historical contribution quality before merging a PR
- Determine whether a user is an active contributor
- Help an Agent decide whether to automatically assign a Reviewer during collaboration routing

```python
vcs.user_repo_activity("alice")
vcs.user_repo_activity("alice", repo="opensquad-ai/opensuqad")
```

Example output:
```
Activity of @alice in opensquad-ai/opensuqad:

── Issues ─────────────────────────────────────
    #23 [closed]  Bug: config not loaded on Windows
    #41 [open  ]  Feature request: dark mode

── Pull Requests ───────────────────────────────
     #7 [open  ]  feat: add dark mode toggle
    #12 [merged]  fix: config path on Windows
```

---

## Async Collaboration Mechanism (EventBus)
This plugin integrates with OpenSquad's `EventBus` and broadcasts events named `vcs_activity`:
- **Trigger**: Automatically published when any Agent successfully calls `issue_create`, `issue_comment`, `pr_create`, or `pr_merge`.
- **Data structure**:
  ```json
  {
    "action": "pr_create",
    "agent_id": "coder-001",
    "result": "...",
    "payload": { ... }
  }
  ```
- **Response logic**: Other Agents' plugins listen for this event. If a PR is relevant to them, the Agent can be automatically awakened or notified to enter the review phase via group chat.

> User info query tools (`user_info`, etc.) are read-only operations and do not trigger EventBus events.

## Prerequisites
1. **Environment**: The system must have [GitHub CLI](https://cli.github.com/) installed.
2. **Authentication**: Run `gh auth login` in the terminal before use, or ensure a valid token is present in `config.json` in the plugin configuration directory.
3. **API Limits**: GitHub REST API allows 60 unauthenticated requests per hour and 5,000 authenticated requests per hour. It is recommended to stay logged in with `gh auth login`.
