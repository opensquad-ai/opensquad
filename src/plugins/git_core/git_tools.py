import logging
import os
import subprocess

logger = logging.getLogger("plugins.git_core")


def _run_git(repo_path: str, args: list[str], env: dict[str, str] | None = None) -> str:
    """Helper to run git commands."""
    try:
        # Resolve path to absolute
        abs_repo_path = os.path.abspath(repo_path)

        # Ensure directory exists for init, or is a git repo for others
        if not os.path.exists(abs_repo_path) and "init" not in args:
            return f"Error: Path {repo_path} does not exist."

        cmd = ["git", *args]
        result = subprocess.run(
            cmd,
            cwd=abs_repo_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, **(env or {})},
        )

        if result.returncode != 0:
            return f"Error (code {result.returncode}): {result.stderr.strip()}"
        return result.stdout.strip() or "Success"
    except Exception as e:
        logger.error(f"Git command failed: {e}")
        return f"Error: {e!s}"


def init(path: str) -> str:
    """Initialize a new Git repository at the specified path."""
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)
    return _run_git(path, ["init"])


def status(path: str) -> str:
    """Show the working tree status."""
    return _run_git(path, ["status"])


def add(path: str, files: list[str]) -> str:
    """Add file contents to the index."""
    return _run_git(path, ["add", *files])


def commit(path: str, message: str, author_name: str | None = None, author_email: str | None = None) -> str:
    """Record changes to the repository."""
    env = {}
    if author_name:
        env["GIT_AUTHOR_NAME"] = author_name
        env["GIT_COMMITTER_NAME"] = author_name
    if author_email:
        env["GIT_AUTHOR_EMAIL"] = author_email
        env["GIT_COMMITTER_EMAIL"] = author_email

    return _run_git(path, ["commit", "-m", message], env=env)


def branch(path: str, name: str | None = None, delete: bool = False) -> str:
    """List, create, or delete branches."""
    if delete and name:
        return _run_git(path, ["branch", "-d", name])
    if name:
        return _run_git(path, ["branch", name])
    return _run_git(path, ["branch"])


def checkout(path: str, target: str, create: bool = False) -> str:
    """Switch branches or restore working tree files."""
    args = ["checkout"]
    if create:
        args.append("-b")
    args.append(target)
    return _run_git(path, args)


def log(path: str, limit: int = 10) -> str:
    """Show commit logs."""
    return _run_git(path, ["log", f"-n {limit}", "--oneline", "--decorate", "--graph"])


def diff(path: str, cached: bool = False) -> str:
    """Show changes between commits, commit and working tree, etc."""
    args = ["diff"]
    if cached:
        args.append("--cached")
    return _run_git(path, args)


def merge(path: str, source: str) -> str:
    """Join two or more development histories together."""
    return _run_git(path, ["merge", source])


def clone(url: str, path: str) -> str:
    """Clone a repository into a new directory."""
    return _run_git(".", ["clone", url, path])


def pull(path: str, remote: str = "origin", branch: str | None = None) -> str:
    """Fetch from and integrate with another repository or a local branch."""
    args = ["pull", remote]
    if branch:
        args.append(branch)
    return _run_git(path, args)


def fetch(path: str, remote: str = "origin") -> str:
    """Download objects and refs from another repository."""
    return _run_git(path, ["fetch", remote])


def remote_add(path: str, name: str, url: str) -> str:
    """Add a remote named <name> for the repository at <url>."""
    return _run_git(path, ["remote", "add", name, url])


def remote_set_url(path: str, name: str, url: str) -> str:
    """Change the URL of an existing remote."""
    return _run_git(path, ["remote", "set-url", name, url])


def push(path: str, remote: str = "origin", branch: str | None = None, set_upstream: bool = False) -> str:
    """Push local commits to a remote repository."""
    args = ["push"]
    if set_upstream:
        args.append("-u")
    args.append(remote)
    if branch:
        args.append(branch)
    return _run_git(path, args)


def rebase(path: str, upstream: str) -> str:
    """Reapply commits on top of another base tip."""
    return _run_git(path, ["rebase", upstream])
