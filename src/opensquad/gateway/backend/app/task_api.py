"""
Task worktree REST API (Gateway proxy layer) — M1 change-report / merge-decision surface.

Thin HTTP wrapper around ``opensquad.workspace.worktree_manager`` so the
frontend task panel (M2) can:
- ``GET  /api/tasks/worktree?repo=<dir>``            — list active worktrees
- ``GET  /api/tasks/worktree/{task_id}/report?repo=`` — change report (diff stat/files/commits)
- ``POST /api/tasks/worktree/{task_id}/merge?repo=``  — squash-merge back (conflict-safe)
- ``POST /api/tasks/worktree/{task_id}/discard?repo=``— drop worktree + branch

All handlers validate that ``repo`` resolves to a git repository containing
the given task (defence-in-depth on top of the path-whitelist tool layer).
"""

from __future__ import annotations

import os
import sys

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

from opensquad.workspace.worktree_manager import manager_for

router = APIRouter(prefix="/api/tasks/worktree", tags=["task-worktree"])


def _manager(repo: str):
    if not repo or not os.path.isdir(repo):
        raise HTTPException(status_code=400, detail=f"invalid repo: {repo}")
    mgr = manager_for(repo)
    if mgr is None:
        raise HTTPException(status_code=400, detail=f"not a git repository: {repo}")
    return mgr


class MergeRequest(BaseModel):
    strategy: str = "squash"


@router.get("")
async def list_worktrees(repo: str = Query(...)):
    mgr = _manager(repo)
    return {"status": "ok", "repo": mgr.repo_root, "worktrees": mgr.list()}


@router.get("/{task_id}/report")
async def diff_report(task_id: str, repo: str = Query(...)):
    mgr = _manager(repo)
    report = mgr.diff_report(task_id)
    if report.get("status") == "error":
        raise HTTPException(status_code=404, detail=report.get("message"))
    return report


@router.post("/{task_id}/merge")
async def merge_task(task_id: str, body: MergeRequest, repo: str = Query(...)):
    mgr = _manager(repo)
    result = mgr.merge(task_id, strategy=body.strategy)
    if result.get("status") == "error":
        raise HTTPException(status_code=409, detail=result)
    return result


@router.post("/{task_id}/discard")
async def discard_task(task_id: str, repo: str = Query(...)):
    mgr = _manager(repo)
    result = mgr.discard(task_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=409, detail=result)
    return result
