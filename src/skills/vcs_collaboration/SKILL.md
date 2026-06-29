---
name: vcs_collaboration
description: Experts in asynchronous Git/GitHub collaborative development flow.
allowed-tools: git, vcs, filesystem, im
---

# VCS Collaboration Skill (OpenSquad Edition)

This skill enables agents to participate in complex, multi-agent software development using Git and GitHub within the OpenSquad local-first architecture. It emphasizes **Local Branching Isolation** and **VCS Audit Transparency**.

## 1. Local Shared Workspace Etiquette

In OpenSquad, agents share the same physical directory. To avoid collision:

- **ALWAYS** check out a new branch for your task: `git.checkout(target="agent_id/feat-name", create=True)`.
- **NEVER** modify files directly on `main` or `dev` unless you are the PM or Reviewer during a merge.
- **NEVER** use `git commit --no-verify` or other commands that bypass hooks.

## 2. Implementation & Handoff Loop

1. **Assigned**: When PM assigns a task via @mention, acknowledge receipt in group chat.
2. **Setup**: Switch to a feature branch: `git.checkout(target="agent_id/feat-name", create=True)`.
3. **Commit**: Perform atomic commits. The `git_core` plugin will automatically inject your Agent ID as the author.
4. **Handoff**: Once complete, notify the Senior/Reviewer in group chat: "@Senior_Bot, feature logic finished on branch agent_id/feat-name. Please review and merge."
5. **Sync**: If someone else merged to main, rebase your branch: `git.checkout(target="main")` -> `git.pull()` -> `git.checkout(target="your-branch")` -> `git.rebase(upstream="main")`.

## 3. Remote Synchronization (VCS Coordinator Only)

Only agents designated as "VCS Coordinator" (holding the master GitHub token) should perform remote operations:

- **Push**: `git.push(remote="origin", branch="main")`.
- **PR**: `vcs.pr_create(repo="owner/repo", title="...", body="...")`.
- **Audit**: All remote actions are logged in the **VCS Audit Timeline**. Review this UI periodically to ensure transparency.

## 4. Communication Examples

For practical messaging patterns and handoff scenarios, refer to [HANDOFF_EXAMPLES.md](./HANDOFF_EXAMPLES.md). Always follow the "@mention + [STATUS/BUG/PHASE]" message format to maintain team visibility.

## 3. Asynchronous Code Review

When you receive a notification (via Group Chat or Event) that a PR has been commented on or created:

### As a Reviewer:
1. Fetch the PR branch: `git.fetch` -> `git.checkout`.
2. Analyze the code using `filesystem.read_file`.
3. Provide feedback using `vcs.issue_comment(issue_id, body="...")`. Focus on logic, security, and style.

### As a Coder (Fixing Feedback):
1. Read the comments using `vcs.issue_view`.
2. Apply fixes in your local branch.
3. Commit and Push again. The PR will update automatically.

## 4. Conflict Resolution

If `git.rebase` fails:
1. Use `git.diff` to identify the conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`).
2. Read the files and determine the best logical merge.
3. Edit the file to resolve the markers.
4. Run `git.add` followed by `git.commit`.

## 5. Collaboration Etiquette

- **Mentions**: Use `@agent_name` in group chat for urgent PR reviews.
- **Atomic Commits**: Don't mix multiple features in one PR.
- **Clear PR Descriptions**: Link to the Issue using "Closes #123" in the body.
