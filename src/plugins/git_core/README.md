# Git Core Plugin

## Overview
The `git_core` plugin provides OpenSquad agents with a comprehensive set of local Git repository management tools. It wraps common Git command-line operations, enabling Agents to perform tasks such as initialization, branch management, committing, and synchronization.

## Namespace: `git`

### Available Tools
- `git.init(path)`: Initialize a new Git repository at the specified path.
- `git.status(path)`: Show the working tree status.
- `git.add(path, files)`: Stage files (`files` is a list; supports `["."]`).
- `git.commit(path, message, author_name?, author_email?)`: Record changes. It is recommended to pass `agent_id` as `author_name` for traceability.
- `git.branch(path, name?, delete?)`: List, create, or delete branches.
- `git.checkout(path, target, create?)`: Switch branches or create and switch to a new branch.
- `git.clone(url, path)`: Clone a remote repository locally.
- `git.pull(path, remote?, branch?)`: Fetch and merge remote updates.
- `git.fetch(path, remote?)`: Fetch remote refs without merging.
- `git.rebase(path, upstream)`: Rebase the current branch onto the target branch (recommended for a clean commit history).
- `git.log(path, limit?)`: View a commit history summary.
- `git.diff(path, cached?)`: Show file differences.
- `git.remote_add(path, name, url)`: Add a remote.
- `git.remote_set_url(path, name, url)`: Update the URL of an existing remote.

## Usage Guidelines
1. **Atomic commits**: Each commit should contain changes for a single logical feature.
2. **Branching strategy**: Direct development on the `main` branch is prohibited. Always create a branch with a `feat/` or `fix/` prefix.
3. **Identity**: When calling `commit`, always declare your Agent ID via `author_name`.
4. **Conflict prevention**: Before pushing a PR, always run `git.fetch` + `git.rebase` to sync with the upstream trunk.
