from fastapi import APIRouter, Depends, Query

from app.api import get_current_user_dep
from app.models import User
from opensquad.system_config import syscfg
from opensquad.utils.audit_vcs import AuditLogManager

router = APIRouter(prefix="/audit")
_PROJECT_ROOT = syscfg.project_root()
audit_mgr = AuditLogManager(_PROJECT_ROOT)


@router.get("/repos")
async def list_audit_repos(current_user: User = Depends(get_current_user_dep)):
    """List all GitHub repositories that have tracked footprints."""
    return {"repos": audit_mgr.get_repos()}


@router.get("/logs")
async def get_audit_logs(
    repo: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(get_current_user_dep),
):
    """Get footprints for a specific repository or all logs."""
    logs = audit_mgr.get_logs(repo_name=repo, limit=limit)
    return {"repo": repo, "logs": logs, "count": len(logs)}
