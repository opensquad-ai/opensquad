# -*- coding: utf-8 -*-
import subprocess
import json
import logging
import os
import re
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger("plugins.vcs_remote")

def _run_gh(args: List[str], cwd: Optional[str] = None) -> str:
    """Helper to run gh commands."""
    try:
        cmd = ["gh"] + args
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        if result.returncode != 0:
            return f"Error (code {result.returncode}): {result.stderr.strip()}"
        return result.stdout.strip() or "Success"
    except Exception as e:
        logger.error(f"GH command failed: {e}")
        return f"Error: {str(e)}"


def _run_gh_json(args: List[str], cwd: Optional[str] = None) -> Tuple[Any, Optional[str]]:
    """Run a gh command expecting JSON output.
    Returns (parsed_data, error_string). On success error_string is None."""
    try:
        cmd = ["gh"] + args
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        if result.returncode != 0:
            return None, f"Error (code {result.returncode}): {result.stderr.strip()}"
        try:
            return json.loads(result.stdout), None
        except json.JSONDecodeError:
            return result.stdout.strip(), None
    except Exception as e:
        logger.error(f"GH JSON command failed: {e}")
        return None, f"Error: {str(e)}"


def _resolve_repo(repo: str, path: Optional[str]) -> Tuple[str, Optional[str]]:
    """Resolve owner/repo string from argument or git remote.
    Returns (repo_string, error_string)."""
    if repo:
        return repo, None
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=path,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            m = re.search(r'github\.com[:/](.+?/.+?)(?:\.git)?$', url)
            if m:
                return m.group(1), None
    except Exception:
        pass
    return "", "Error: Could not determine repository. Provide 'repo' as owner/name."


def issue_create(title: str, body: str, labels: Optional[List[str]] = None,
                 assignee: Optional[str] = None, path: Optional[str] = None) -> str:
    """Create a new issue on GitHub.
    path: local repo directory (uses current working directory if omitted)."""
    args = ["issue", "create", "--title", title, "--body", body]
    if labels:
        for label in labels:
            args.extend(["--label", label])
    if assignee:
        args.extend(["--assignee", assignee])
    return _run_gh(args, cwd=path)


def issue_list(limit: int = 10, state: str = "open", path: Optional[str] = None) -> str:
    """List issues in the repository.
    path: local repo directory (uses current working directory if omitted)."""
    return _run_gh(["issue", "list", f"--limit={limit}", f"--state={state}"], cwd=path)


def issue_view(issue_id: str, path: Optional[str] = None) -> str:
    """View details of a specific issue, including all comment content.
    path: local repo directory (uses current working directory if omitted)."""
    return _run_gh(["issue", "view", issue_id, "--comments"], cwd=path)


def issue_comment(issue_id: str, body: str, path: Optional[str] = None) -> str:
    """Add a comment to an issue or pull request.
    path: local repo directory (uses current working directory if omitted)."""
    return _run_gh(["issue", "comment", issue_id, "--body", body], cwd=path)


def issue_close(issue_id: str, reason: str = "", path: Optional[str] = None) -> str:
    """Close an issue.
    reason: optional close reason (completed / not_planned / reopened).
    path: local repo directory (uses current working directory if omitted)."""
    args = ["issue", "close", issue_id]
    if reason:
        args.extend(["--reason", reason])
    return _run_gh(args, cwd=path)


def pr_create(title: str, body: str, base: str = "main", draft: bool = False,
              path: Optional[str] = None) -> str:
    """Create a pull request.
    path: local repo directory (uses current working directory if omitted)."""
    args = ["pr", "create", "--title", title, "--body", body, "--base", base]
    if draft:
        args.append("--draft")
    return _run_gh(args, cwd=path)


def pr_list(limit: int = 10, state: str = "open", path: Optional[str] = None) -> str:
    """List pull requests.
    path: local repo directory (uses current working directory if omitted)."""
    return _run_gh(["pr", "list", f"--limit={limit}", f"--state={state}"], cwd=path)


def pr_view(pr_id: str, path: Optional[str] = None) -> str:
    """View details of a specific PR, including all comment content.
    path: local repo directory (uses current working directory if omitted)."""
    return _run_gh(["pr", "view", pr_id, "--comments"], cwd=path)


def pr_merge(pr_id: str, delete_branch: bool = True, path: Optional[str] = None) -> str:
    """Merge a pull request.
    path: local repo directory (uses current working directory if omitted)."""
    args = ["pr", "merge", pr_id, "--merge"]
    if delete_branch:
        args.append("--delete-branch")
    return _run_gh(args, cwd=path)


def pr_status(path: Optional[str] = None) -> str:
    """Check the status of relevant pull requests.
    path: local repo directory (uses current working directory if omitted)."""
    return _run_gh(["pr", "status"], cwd=path)


def pr_checkout(pr_id: str, path: Optional[str] = None) -> str:
    """Check out a pull request branch locally.
    path: local repo directory (uses current working directory if omitted)."""
    return _run_gh(["pr", "checkout", pr_id], cwd=path)


def push(remote: str = "origin", branch: str = "main", path: Optional[str] = None) -> str:
    """Push local commits to remote.
    path: local repo directory (uses current working directory if omitted)."""
    try:
        result = subprocess.run(
            ["git", "push", remote, branch],
            cwd=path,
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            return f"Error: {result.stderr.strip()}"
        return result.stdout.strip() or "Success"
    except Exception as e:
        return f"Error: {str(e)}"


def repo_fork(repo_url: str) -> str:
    """Create a fork of a repository (no local path needed)."""
    return _run_gh(["repo", "fork", repo_url, "--clone=false"])


def repo_clone(repo: str, path: str) -> str:
    """Clone a GitHub repository to the given local path."""
    return _run_gh(["repo", "clone", repo, path])


def repo_view(repo: str = "", path: Optional[str] = None) -> str:
    """View repository information.
    repo: owner/name or URL (omit to use repo inferred from path).
    path: local repo directory (uses current working directory if omitted)."""
    args = ["repo", "view"]
    if repo:
        args.append(repo)
    return _run_gh(args, cwd=path)


def repo_create(name: str, public: bool = False) -> str:
    """Create a new repository on GitHub."""
    visibility = "--public" if public else "--private"
    return _run_gh(["repo", "create", name, visibility, "--confirm"])


def auth_check() -> str:
    """Check if the GitHub CLI is authenticated."""
    result = _run_gh(["auth", "status"])
    if "Logged in to github.com" in result:
        return "Authenticated"
    return f"Not Authenticated: {result}"


# ── Public User Info Query ────────────────────────────────────────────────────

def user_info(username: str) -> str:
    """Query the public profile of a GitHub user.

    Returns login, name, bio, company, location, email (if public),
    followers/following counts, public repo count, account age, and profile URL.
    username: GitHub login name (e.g. 'torvalds')."""
    data, err = _run_gh_json(["api", f"users/{username}"])
    if err:
        return err
    if not isinstance(data, dict):
        return f"Unexpected response: {data}"

    lines = [
        f"GitHub User: @{data.get('login', username)}",
        f"Name        : {data.get('name') or '(not set)'}",
        f"Bio         : {data.get('bio') or '(not set)'}",
        f"Company     : {data.get('company') or '(not set)'}",
        f"Location    : {data.get('location') or '(not set)'}",
        f"Email       : {data.get('email') or '(not public)'}",
        f"Website     : {data.get('blog') or '(not set)'}",
        f"Twitter     : {'@' + data['twitter_username'] if data.get('twitter_username') else '(not set)'}",
        f"Account Type: {data.get('type', 'User')}",
        f"Followers   : {data.get('followers', 0)}",
        f"Following   : {data.get('following', 0)}",
        f"Public Repos: {data.get('public_repos', 0)}",
        f"Public Gists: {data.get('public_gists', 0)}",
        f"Hireable    : {'Yes' if data.get('hireable') else 'No / not specified'}",
        f"Created At  : {data.get('created_at', 'unknown')}",
        f"Updated At  : {data.get('updated_at', 'unknown')}",
        f"Profile URL : {data.get('html_url', '')}",
    ]
    return "\n".join(lines)


def issue_author_info(issue_id: str, path: Optional[str] = None) -> str:
    """Get the public GitHub profile of an issue's author.

    Fetches the issue to identify the author login, then queries the user's
    public profile via the GitHub API.
    issue_id: issue number (e.g. '42').
    path    : local repo directory (uses current working directory if omitted)."""
    data, err = _run_gh_json(
        ["issue", "view", issue_id,
         "--json", "author,title,number,state,createdAt"],
        cwd=path
    )
    if err:
        return err
    if not isinstance(data, dict):
        return f"Unexpected response: {data}"

    author = data.get("author") or {}
    login = author.get("login", "")
    if not login:
        return "Error: Could not determine issue author login."

    header = (
        f"Issue #{data.get('number')} — {data.get('title')}\n"
        f"State   : {data.get('state')}  |  Created: {data.get('createdAt')}\n"
        f"{'─' * 56}\n"
        f"Author profile (@{login}):\n"
    )
    return header + user_info(login)


def pr_author_info(pr_id: str, path: Optional[str] = None) -> str:
    """Get the public GitHub profile of a pull request's author.

    Fetches the PR to identify the author login, then queries the user's
    public profile via the GitHub API.
    pr_id: pull request number (e.g. '7').
    path : local repo directory (uses current working directory if omitted)."""
    data, err = _run_gh_json(
        ["pr", "view", pr_id,
         "--json", "author,title,number,state,createdAt,headRefName,baseRefName"],
        cwd=path
    )
    if err:
        return err
    if not isinstance(data, dict):
        return f"Unexpected response: {data}"

    author = data.get("author") or {}
    login = author.get("login", "")
    if not login:
        return "Error: Could not determine PR author login."

    header = (
        f"PR #{data.get('number')} — {data.get('title')}\n"
        f"State   : {data.get('state')}  |  Created: {data.get('createdAt')}\n"
        f"Branch  : {data.get('headRefName')} → {data.get('baseRefName')}\n"
        f"{'─' * 56}\n"
        f"Author profile (@{login}):\n"
    )
    return header + user_info(login)


def repo_contributors(repo: str = "", limit: int = 30,
                      path: Optional[str] = None) -> str:
    """List contributors to the repository sorted by commit count.

    Shows login, contribution count, and account type for each contributor.
    repo : owner/name (e.g. 'opensquad-ai/opensuqad'). Inferred from git
           remote if omitted.
    limit: maximum number of contributors to return (default 30, max 100).
    path : local repo directory (uses current working directory if omitted)."""
    repo, err = _resolve_repo(repo, path)
    if err:
        return err

    per_page = min(limit, 100)
    data, err = _run_gh_json(
        ["api", f"repos/{repo}/contributors?per_page={per_page}&anon=false"]
    )
    if err:
        return err
    if not isinstance(data, list):
        return f"Unexpected response: {data}"

    lines = [f"Contributors for {repo} (showing top {min(limit, len(data))}):\n"]
    for i, c in enumerate(data[:limit], 1):
        lines.append(
            f"  {i:>3}. @{c.get('login', '?'):<28}"
            f"  commits: {c.get('contributions', 0):>6}"
            f"  type: {c.get('type', 'User')}"
        )
    if not data:
        lines.append("  (no contributors found)")
    return "\n".join(lines)


def user_repo_activity(username: str, repo: str = "",
                       path: Optional[str] = None) -> str:
    """Query a user's issues and pull requests in this repository.

    Useful for understanding a contributor's history before reviewing their
    PR or assigning a task.
    username: GitHub login to query (e.g. 'alice').
    repo    : owner/name (e.g. 'opensquad-ai/opensuqad'). Inferred from git
              remote if omitted.
    path    : local repo directory (uses current working directory if omitted)."""
    repo, err = _resolve_repo(repo, path)
    if err:
        return err

    lines = [f"Activity of @{username} in {repo}:\n"]

    # Issues (GitHub /issues endpoint also returns PRs; exclude them)
    issues_data, issues_err = _run_gh_json([
        "api",
        f"repos/{repo}/issues?creator={username}&state=all&per_page=20"
    ])
    lines.append("── Issues ─────────────────────────────────────")
    if issues_err:
        lines.append(f"  {issues_err}")
    elif isinstance(issues_data, list):
        real_issues = [i for i in issues_data if not i.get("pull_request")]
        if real_issues:
            for issue in real_issues[:10]:
                lines.append(
                    f"  #{issue['number']:>5} [{issue['state']:<6}]  {issue['title']}"
                )
            if len(real_issues) > 10:
                lines.append(f"  … and {len(real_issues) - 10} more")
        else:
            lines.append("  (none)")
    else:
        lines.append(f"  Unexpected response: {issues_data}")

    lines.append("")

    # Pull Requests — use search/issues API (supports author: filter for PRs)
    pr_data, pr_err = _run_gh_json([
        "api",
        f"search/issues?q=repo:{repo}+type:pr+author:{username}&per_page=20"
    ])
    lines.append("── Pull Requests ───────────────────────────────")
    if pr_err:
        lines.append(f"  {pr_err}")
    elif isinstance(pr_data, dict):
        prs = pr_data.get("items", [])
        if prs:
            for pr in prs[:10]:
                lines.append(
                    f"  #{pr['number']:>5} [{pr['state']:<6}]  {pr['title']}"
                )
            if len(prs) > 10:
                lines.append(f"  … and {len(prs) - 10} more")
        else:
            lines.append("  (none)")
    else:
        lines.append(f"  Unexpected response: {pr_data}")

    return "\n".join(lines)
