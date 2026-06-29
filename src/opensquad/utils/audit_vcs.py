# -*- coding: utf-8 -*-
import os
import json
import logging
import subprocess
from datetime import datetime
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

class AuditLogManager:
    def __init__(self, project_root: str):
        self.project_root = project_root
        self.storage_dir = os.path.join(project_root, "data", "audit")
        self.log_file = os.path.join(self.storage_dir, "vcs_footprints.jsonl")
        os.makedirs(self.storage_dir, exist_ok=True)
        self._repo_cache = {} # local_path -> repo_name

    def resolve_repo_name(self, local_path: str) -> str:
        """Resolve a local path to a 'owner/repo' string via git remote."""
        if not local_path or not os.path.exists(local_path):
            return "Local/Unknown"
            
        # If it's a file, get its directory
        if os.path.isfile(local_path):
            abs_path = os.path.dirname(os.path.abspath(local_path))
        else:
            abs_path = os.path.abspath(local_path)
            
        if abs_path in self._repo_cache:
            return self._repo_cache[abs_path]

        try:
            # Try to get the remote URL
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=abs_path,
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                url = result.stdout.strip()
                # Parse owner/repo from URL
                # Supports: https://github.com/owner/repo.git or git@github.com:owner/repo.git
                parts = url.split("/")
                if len(parts) >= 2:
                    repo_part = parts[-1].replace(".git", "")
                    owner_part = parts[-2].split(":")[-1]
                    full_name = f"{owner_part}/{repo_part}"
                    self._repo_cache[abs_path] = full_name
                    return full_name
        except Exception:
            pass
            
        return "Local/Unlinked"

    def log_footprint(self, agent_id: str, action: str, arguments: Dict, output: str, status: str):
        """Write a footprint record to the JSONL file."""
        # Extract path to resolve repo
        path = arguments.get("path") or arguments.get("repo_path") or "."
        # If path is not absolute, try to make it relative to project root
        if not os.path.isabs(path):
            path = os.path.join(self.project_root, path)
            
        repo_name = self.resolve_repo_name(path) if action.startswith("git.") else "GitHub/API"
        
        # If it's a VCS action, we might have repo info in args
        if action.startswith("vcs."):
            if "repo" in arguments:
                repo_name = arguments["repo"]

        record = {
            "timestamp": datetime.now().isoformat(),
            "agent_id": agent_id,
            "repo_name": repo_name,
            "action": action,
            "arguments": arguments,
            "output": output,
            "status": status
        }
        
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")

    def get_logs(self, repo_name: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """Read logs from the file, optionally filtered by repo."""
        logs = []
        if not os.path.exists(self.log_file):
            return []
            
        try:
            with open(self.log_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
                # Process lines from newest to oldest
                for line in reversed(lines):
                    try:
                        record = json.loads(line)
                        if not repo_name or record.get("repo_name") == repo_name:
                            logs.append(record)
                            if len(logs) >= limit:
                                break
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.error(f"Failed to read audit log: {e}")
            
        return logs

    def get_repos(self) -> List[str]:
        """Get unique repo names from the logs."""
        repos = set()
        if not os.path.exists(self.log_file):
            return []
        try:
            with open(self.log_file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        repos.add(json.loads(line).get("repo_name"))
                    except (json.JSONDecodeError, AttributeError):
                        continue
        except OSError:
            pass
        return sorted(list(repos))
