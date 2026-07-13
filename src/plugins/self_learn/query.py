"""
Self Learn - Query module for Launcher / admin plugin UI.

Standard entry points:
    query_data(project_root, params) -> dict
    handle_action(project_root, action, data) -> dict
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any

logger = logging.getLogger("plugins.self_learn.query")


def _get_last_workspace() -> str:
    try:
        lw_path = os.path.join(os.path.expanduser("~"), ".opensquad", "last_workspace.json")
        if os.path.isfile(lw_path):
            with open(lw_path, encoding="utf-8") as f:
                data = json.load(f)
            ws = data.get("last_workspace", "")
            if ws and os.path.isdir(os.path.join(ws, "agents")):
                return ws
    except Exception:
        pass
    return ""


def _scan_agents(root: str) -> list[dict[str, str]]:
    agents_root = os.path.join(root, "agents")
    result = []
    if not os.path.isdir(agents_root):
        return result
    for name in sorted(os.listdir(agents_root)):
        cfg_path = os.path.join(agents_root, name, "config.json")
        if not os.path.isfile(cfg_path):
            continue
        try:
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
            result.append(
                {
                    "agent_id": cfg.get("agent_id", name),
                    "agent_name": cfg.get("agent_name", name),
                    "dir_name": name,
                    "root": root,
                }
            )
        except Exception:
            continue
    return result


def _find_agent_dir(project_root: str, agent_id: str) -> str | None:
    for root in [project_root, _get_last_workspace()]:
        if not root:
            continue
        agents_root = os.path.join(root, "agents")
        if not os.path.isdir(agents_root):
            continue
        for name in os.listdir(agents_root):
            cfg_path = os.path.join(agents_root, name, "config.json")
            if not os.path.isfile(cfg_path):
                continue
            try:
                with open(cfg_path, encoding="utf-8") as f:
                    cfg = json.load(f)
                if cfg.get("agent_id") == agent_id or name == agent_id:
                    return os.path.join(agents_root, name)
            except Exception:
                continue
    return None


def _store():
    from plugins.self_learn import store

    return store


def query_data(project_root: str, params: dict) -> dict:
    store = _store()
    agents = _scan_agents(project_root)
    alt = _get_last_workspace()
    if alt and alt != project_root:
        seen = {a["agent_id"] for a in agents}
        for a in _scan_agents(alt):
            if a["agent_id"] not in seen:
                agents.append(a)

    agent_id = (params or {}).get("agent_id") or (agents[0]["agent_id"] if agents else "")
    tab = (params or {}).get("tab") or "overview"
    agent_dir = _find_agent_dir(project_root, agent_id) if agent_id else None

    payload: dict[str, Any] = {
        "agents": agents,
        "agent_id": agent_id,
        "tab": tab,
        "meta": {},
        "corpus": {"items": [], "total": 0},
        "runs": {"items": [], "total": 0},
        "pipeline": {},
    }

    if not agent_dir:
        payload["error"] = "agent_not_found"
        return payload

    store.ensure_defaults(agent_dir)
    payload["meta"] = store.load_meta(agent_dir)
    payload["pipeline"] = store.load_pipeline(agent_dir)

    unlearned_only = str((params or {}).get("unlearned_only", "")).lower() in ("1", "true", "yes")
    limit = int((params or {}).get("limit") or 50)
    offset = int((params or {}).get("offset") or 0)

    if tab in ("overview", "corpus", "all"):
        payload["corpus"] = store.list_corpus(
            agent_dir,
            unlearned_only=unlearned_only,
            limit=limit,
            offset=offset,
        )
    if tab in ("overview", "runs", "all"):
        payload["runs"] = store.list_runs(agent_dir, limit=limit, offset=offset)

    # Quick stats
    all_corpus = store.list_corpus(agent_dir, unlearned_only=False, limit=1)
    unlearned = store.list_corpus(agent_dir, unlearned_only=True, limit=1)
    payload["stats"] = {
        "corpus_total": all_corpus.get("total", 0),
        "corpus_unlearned": unlearned.get("total", 0),
        "runs_total": store.list_runs(agent_dir, limit=1).get("total", 0),
    }
    return payload


def handle_action(project_root: str, action: str, data: dict) -> dict:
    store = _store()
    data = data or {}
    agent_id = data.get("agent_id") or ""
    agent_dir = _find_agent_dir(project_root, agent_id)
    if not agent_dir:
        return {"ok": False, "error": "agent_not_found"}

    store.ensure_defaults(agent_dir)

    if action == "start_learn":
        # UI/Launcher cannot spawn SubAgentRunner (no chat_api in this process).
        # Queue a request for the in-agent self_learn scheduler to pick up.
        req = store.enqueue_learn_request(
            agent_dir,
            trigger="manual",
            force=bool(data.get("force", False)),
            allow_agent_md=bool(data.get("allow_agent_md", False)) if "allow_agent_md" in data else None,
            allow_reminder=bool(data.get("allow_reminder", False)) if "allow_reminder" in data else None,
            pipeline_name=str(data.get("pipeline_name") or "default"),
        )
        return {
            "ok": True,
            "queued": True,
            "request_id": req["id"],
            "message": "Learn request queued. The running agent will pick it up within ~60s.",
        }

    if action == "update_meta":
        allowed = {
            "idle_auto_enabled",
            "idle_minutes",
            "cooldown_hours",
            "interval_hours",
            "cancel_on_user_input",
        }
        patch = {k: data[k] for k in allowed if k in data}
        meta = store.update_meta(agent_dir, **patch)
        return {"ok": True, "meta": meta}

    if action == "save_pipeline":
        pipeline = data.get("pipeline")
        if not isinstance(pipeline, dict):
            return {"ok": False, "error": "pipeline_required"}
        saved = store.save_pipeline(agent_dir, pipeline)
        return {"ok": True, "pipeline": saved}

    if action == "mark_learned":
        ids = data.get("corpus_ids") or []
        run_id = data.get("run_id") or "manual"
        return {"ok": True, **store.mark_corpus_learned(agent_dir, ids, run_id)}

    if action == "export":
        from plugins.self_learn.export_import import export_package

        blob = export_package(agent_dir, include_agent_md_snippet=bool(data.get("include_agent_md", True)))
        return {
            "ok": True,
            "filename": f"self_learn_{os.path.basename(agent_dir)}.zip",
            "content_base64": base64.b64encode(blob).decode("ascii"),
            "bytes": len(blob),
        }

    if action == "import":
        from plugins.self_learn.export_import import import_package

        b64 = data.get("content_base64") or ""
        if not b64:
            return {"ok": False, "error": "content_base64_required"}
        try:
            raw = base64.b64decode(b64)
        except Exception as e:
            return {"ok": False, "error": f"invalid_base64: {e}"}
        report = import_package(agent_dir, raw, dry_run=bool(data.get("dry_run", False)))
        return {"ok": True, **report}

    if action == "get_corpus":
        entry = store.get_corpus(agent_dir, data.get("corpus_id") or "")
        return {"ok": bool(entry), "entry": entry}

    if action == "get_run":
        run = store.get_run(agent_dir, data.get("run_id") or "")
        detail = store.enrich_run_detail(agent_dir, run)
        return {"ok": bool(detail), "run": detail}

    return {"ok": False, "error": f"unknown_action:{action}"}
