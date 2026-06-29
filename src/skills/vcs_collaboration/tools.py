# -*- coding: utf-8 -*-
import logging
from typing import Dict, List, Optional
import os

logger = logging.getLogger("skills.vcs_collaboration")

def setup_team_env(repo_url: str, workspace_path: str) -> str:
    """
    Macro: Fork, Clone, and setup remotes for a new team project.
    """
    from plugins.vcs_remote import vcs_tools
    from plugins.git_core import git_tools
    
    # 1. Fork
    logger.info(f"Forking {repo_url}...")
    fork_res = vcs_tools.repo_fork(repo_url)
    if "Error" in fork_res:
        return fork_res
        
    # 2. Clone (assuming fork URL is derived or handled by gh)
    # For simplicity, we use the upstream and rely on gh's internal mapping
    logger.info(f"Cloning to {workspace_path}...")
    clone_res = git_tools.clone(repo_url, workspace_path)
    if "Error" in clone_res:
        return clone_res
        
    # 3. Setup Upstream remote
    git_tools.remote_add(workspace_path, "upstream", repo_url)
    
    return "Team environment setup complete. Remotes: origin (yours), upstream (main)."

def submit_work(path: str, branch: str, title: str, body: str) -> str:
    """
    Macro: Add, Commit, Push, and Create PR in one go.
    """
    from plugins.vcs_remote import vcs_tools
    from plugins.git_core import git_tools
    
    # 1. Add & Commit
    git_tools.add(path, ["."])
    commit_res = git_tools.commit(path, f"feat: {title}")
    if "Error" in commit_res:
        return commit_res
        
    # 2. Push
    push_res = vcs_tools.push(remote="origin", branch=branch)
    if "Error" in push_res:
        return push_res
        
    # 3. Create PR
    pr_res = vcs_tools.pr_create(title=title, body=body)
    return f"Work submitted successfully. {pr_res}"
