"""Self-learn persistent store: corpus, runs, pipelines, meta."""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

_LOCK = threading.RLock()

EXPORT_VERSION = 1
DEFAULT_PIPELINE_NAME = "default"

DEFAULT_PIPELINE: dict[str, Any] = {
    "name": "default",
    "version": 1,
    "description": "Collect unlearned corpus, cluster patterns, distill insights, gate writes, apply, mark learned.",
    "steps": [
        {
            "id": "collect",
            "title": "Collect",
            "instruction": "Call self_learn.corpus_list(unlearned_only=true) and self_learn.memory_snapshot / profile_get_agent_md. Gather evidence.",
        },
        {
            "id": "cluster",
            "title": "Cluster",
            "instruction": "Identify repeated patterns across tasks (shared steps, repeated corrections, stable preferences).",
        },
        {
            "id": "distill",
            "title": "Distill",
            "instruction": "Produce structured candidates: preferences, task_playbooks, facts, anti_patterns, optimization_ideas. Each item MUST include evidence_refs (corpus ids).",
        },
        {
            "id": "gate",
            "title": "Gate",
            "instruction": "Route items: episodic lessons -> memory_write only; stable preferences with >=2 evidence_refs -> agent.md patch only if allow_agent_md; actionable schedules -> reminder only if allow_reminder. Drop items without evidence_refs.",
        },
        {
            "id": "apply",
            "title": "Apply",
            "instruction": "Apply gated writes via tools. Prefer self_learn.memory_write for lessons (records destination). Use self_learn.profile_append_agent_md for habits (never overwrite whole agent.md). Use reminder tools if allowed, then self_learn.record_write.",
        },
        {
            "id": "mark",
            "title": "Mark",
            "instruction": "Call self_learn.corpus_mark_learned with consumed corpus ids and the current run_id, then self_learn.finish_run(summary=..., writes_json=[...]) listing each write target/content/evidence_refs.",
        },
    ],
    "gates": {
        "allow_memory_write": True,
        "allow_agent_md": False,
        "allow_reminder": False,
        "min_evidence_for_agent_md": 2,
        "max_corpus_per_run": 20,
        "max_summary_chars": 12000,
    },
}

DEFAULT_META: dict[str, Any] = {
    "last_learn_at": None,
    "last_user_activity_at": None,
    "last_agent_state": "idle",
    "last_agent_state_at": None,
    "idle_auto_enabled": True,
    "idle_minutes": 30,
    "cooldown_hours": 24,
    "interval_hours": 0,
    "last_interval_fire_at": None,
    "cancel_on_user_input": False,
    "pending_due_to_busy": False,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^\w.\-]+", "_", (value or "").strip())
    return cleaned[:120] or "unknown"


def resolve_agent_dir(explicit: str | None = None) -> str | None:
    if explicit and os.path.isdir(explicit):
        return os.path.abspath(explicit)
    try:
        from opensquad import context_base

        agent_dir = getattr(context_base, "_agent_dir", None)
        if agent_dir and os.path.isdir(agent_dir):
            return agent_dir
    except Exception:
        pass
    return None


def self_learn_root(agent_dir: str) -> str:
    root = os.path.join(agent_dir, "data", "self_learn")
    os.makedirs(root, exist_ok=True)
    return root


def corpus_root(agent_dir: str) -> str:
    path = os.path.join(self_learn_root(agent_dir), "corpus")
    os.makedirs(path, exist_ok=True)
    return path


def runs_root(agent_dir: str) -> str:
    path = os.path.join(self_learn_root(agent_dir), "runs")
    os.makedirs(path, exist_ok=True)
    return path


def pipelines_root(agent_dir: str) -> str:
    path = os.path.join(self_learn_root(agent_dir), "pipelines")
    os.makedirs(path, exist_ok=True)
    return path


def meta_path(agent_dir: str) -> str:
    return os.path.join(self_learn_root(agent_dir), "meta.json")


def _read_json(path: str, default: Any) -> Any:
    if not os.path.isfile(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def requests_root(agent_dir: str) -> str:
    path = os.path.join(self_learn_root(agent_dir), "requests")
    os.makedirs(path, exist_ok=True)
    return path


def enqueue_learn_request(
    agent_dir: str,
    *,
    trigger: str = "manual",
    force: bool = False,
    allow_agent_md: bool | None = None,
    allow_reminder: bool | None = None,
    pipeline_name: str = DEFAULT_PIPELINE_NAME,
) -> dict[str, Any]:
    """Queue a learn request for the in-agent scheduler (UI/Launcher safe)."""
    ensure_defaults(agent_dir)
    req_id = uuid.uuid4().hex[:12]
    req = {
        "id": req_id,
        "status": "pending",
        "trigger": trigger,
        "force": bool(force),
        "allow_agent_md": allow_agent_md,
        "allow_reminder": allow_reminder,
        "pipeline_name": pipeline_name,
        "created_at": _utc_now_iso(),
        "picked_at": None,
        "result": None,
    }
    path = os.path.join(requests_root(agent_dir), f"{req_id}.json")
    with _LOCK:
        _write_json(path, req)
    return req


def list_pending_requests(agent_dir: str) -> list[dict[str, Any]]:
    ensure_defaults(agent_dir)
    items: list[dict[str, Any]] = []
    root = requests_root(agent_dir)
    for name in sorted(os.listdir(root)):
        if not name.endswith(".json"):
            continue
        entry = _read_json(os.path.join(root, name), None)
        if isinstance(entry, dict) and entry.get("status") == "pending":
            items.append(entry)
    items.sort(key=lambda e: e.get("created_at") or "")
    return items


def update_request(agent_dir: str, req_id: str, **kwargs: Any) -> dict[str, Any] | None:
    path = os.path.join(requests_root(agent_dir), f"{_safe_id(req_id)}.json")
    with _LOCK:
        req = _read_json(path, None)
        if not isinstance(req, dict):
            return None
        req.update(kwargs)
        _write_json(path, req)
        return req


def ensure_defaults(agent_dir: str) -> None:
    with _LOCK:
        self_learn_root(agent_dir)
        corpus_root(agent_dir)
        runs_root(agent_dir)
        requests_root(agent_dir)
        p_root = pipelines_root(agent_dir)
        default_path = os.path.join(p_root, f"{DEFAULT_PIPELINE_NAME}.json")
        if not os.path.isfile(default_path):
            _write_json(default_path, DEFAULT_PIPELINE)
        if not os.path.isfile(meta_path(agent_dir)):
            meta = dict(DEFAULT_META)
            meta["last_user_activity_at"] = _utc_now_iso()
            _write_json(meta_path(agent_dir), meta)


def load_meta(agent_dir: str) -> dict[str, Any]:
    ensure_defaults(agent_dir)
    meta = dict(DEFAULT_META)
    meta.update(_read_json(meta_path(agent_dir), {}) or {})
    return meta


def save_meta(agent_dir: str, meta: dict[str, Any]) -> dict[str, Any]:
    ensure_defaults(agent_dir)
    merged = dict(DEFAULT_META)
    merged.update(meta or {})
    with _LOCK:
        _write_json(meta_path(agent_dir), merged)
    return merged


def update_meta(agent_dir: str, **kwargs: Any) -> dict[str, Any]:
    meta = load_meta(agent_dir)
    meta.update(kwargs)
    return save_meta(agent_dir, meta)


def load_pipeline(agent_dir: str, name: str = DEFAULT_PIPELINE_NAME) -> dict[str, Any]:
    ensure_defaults(agent_dir)
    path = os.path.join(pipelines_root(agent_dir), f"{_safe_id(name)}.json")
    data = _read_json(path, None)
    if not isinstance(data, dict):
        data = dict(DEFAULT_PIPELINE)
        _write_json(path, data)
    gates = dict(DEFAULT_PIPELINE["gates"])
    gates.update(data.get("gates") or {})
    data["gates"] = gates
    return data


def save_pipeline(agent_dir: str, pipeline: dict[str, Any], name: str | None = None) -> dict[str, Any]:
    ensure_defaults(agent_dir)
    pname = _safe_id(name or pipeline.get("name") or DEFAULT_PIPELINE_NAME)
    pipeline = dict(pipeline)
    pipeline["name"] = pname
    gates = dict(DEFAULT_PIPELINE["gates"])
    gates.update(pipeline.get("gates") or {})
    pipeline["gates"] = gates
    path = os.path.join(pipelines_root(agent_dir), f"{pname}.json")
    with _LOCK:
        _write_json(path, pipeline)
    return pipeline


def append_corpus_entry(
    agent_dir: str,
    *,
    summary: str,
    session_id: str = "",
    session_title: str = "",
    source: str = "compress",
    agent_id: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_defaults(agent_dir)
    summary = (summary or "").strip()
    if not summary:
        raise ValueError("summary is empty")

    created_at = _utc_now_iso()
    entry_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"
    sid = _safe_id(session_id or "nosession")
    entry = {
        "id": entry_id,
        "agent_id": agent_id or os.path.basename(agent_dir.rstrip("/\\")),
        "session_id": session_id or "",
        "session_title": session_title or "",
        "created_at": created_at,
        "source": source or "compress",
        "summary": summary,
        "learned_by": None,
        **(extra or {}),
    }
    path = os.path.join(corpus_root(agent_dir), sid, f"{entry_id}.json")
    with _LOCK:
        _write_json(path, entry)
    return entry


def _iter_corpus_files(agent_dir: str):
    root = corpus_root(agent_dir)
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.endswith(".json"):
                yield os.path.join(dirpath, name)


def list_corpus(
    agent_dir: str,
    *,
    unlearned_only: bool = False,
    limit: int = 100,
    offset: int = 0,
    session_id: str | None = None,
) -> dict[str, Any]:
    ensure_defaults(agent_dir)
    items: list[dict[str, Any]] = []
    for path in _iter_corpus_files(agent_dir):
        entry = _read_json(path, None)
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        if session_id and entry.get("session_id") != session_id:
            continue
        if unlearned_only and entry.get("learned_by"):
            continue
        items.append(entry)
    items.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    total = len(items)
    sliced = items[offset : offset + max(0, limit)]
    return {"items": sliced, "total": total, "limit": limit, "offset": offset}


def get_corpus(agent_dir: str, corpus_id: str) -> dict[str, Any] | None:
    ensure_defaults(agent_dir)
    for path in _iter_corpus_files(agent_dir):
        entry = _read_json(path, None)
        if isinstance(entry, dict) and entry.get("id") == corpus_id:
            return entry
    return None


def mark_corpus_learned(agent_dir: str, corpus_ids: list[str], run_id: str) -> dict[str, Any]:
    ensure_defaults(agent_dir)
    wanted = {cid for cid in corpus_ids if cid}
    updated = []
    with _LOCK:
        for path in _iter_corpus_files(agent_dir):
            entry = _read_json(path, None)
            if not isinstance(entry, dict):
                continue
            if entry.get("id") not in wanted:
                continue
            entry["learned_by"] = run_id
            entry["learned_at"] = _utc_now_iso()
            _write_json(path, entry)
            updated.append(entry["id"])
    return {"updated": updated, "run_id": run_id, "count": len(updated)}


def create_run(
    agent_dir: str,
    *,
    trigger: str = "manual",
    pipeline_name: str = DEFAULT_PIPELINE_NAME,
    force: bool = False,
) -> dict[str, Any]:
    ensure_defaults(agent_dir)
    run_id = uuid.uuid4().hex[:12]
    run = {
        "id": run_id,
        "status": "queued",
        "trigger": trigger,
        "pipeline": pipeline_name,
        "force": force,
        "created_at": _utc_now_iso(),
        "started_at": None,
        "finished_at": None,
        "job_id": None,
        "summary": "",
        "writes": [],
        "corpus_ids": [],
        "error": None,
    }
    path = os.path.join(runs_root(agent_dir), f"{run_id}.json")
    with _LOCK:
        _write_json(path, run)
    return run


def update_run(agent_dir: str, run_id: str, **kwargs: Any) -> dict[str, Any] | None:
    path = os.path.join(runs_root(agent_dir), f"{_safe_id(run_id)}.json")
    with _LOCK:
        run = _read_json(path, None)
        if not isinstance(run, dict):
            return None
        run.update(kwargs)
        _write_json(path, run)
        return run


def get_active_run(agent_dir: str) -> dict[str, Any] | None:
    """Return the newest queued/running learn run, if any."""
    for item in list_runs(agent_dir, limit=20)["items"]:
        if item.get("status") in ("queued", "running"):
            return item
    return None


def append_run_write(
    agent_dir: str,
    *,
    target: str,
    content: str,
    evidence_refs: list[str] | None = None,
    run_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Append a structured write record to a learn run.
    target examples: memory | agent.md | reminder | other
    """
    ensure_defaults(agent_dir)
    active = None
    if run_id:
        active = get_run(agent_dir, run_id)
    if not active:
        active = get_active_run(agent_dir)
    if not active:
        return None
    entry: dict[str, Any] = {
        "target": (target or "other").strip() or "other",
        "content": (content or "")[:4000],
        "evidence_refs": list(evidence_refs or []),
        "at": _utc_now_iso(),
    }
    if extra:
        entry.update(extra)
    path = os.path.join(runs_root(agent_dir), f"{_safe_id(active['id'])}.json")
    with _LOCK:
        run = _read_json(path, None)
        if not isinstance(run, dict):
            return None
        writes = list(run.get("writes") or [])
        writes.append(entry)
        run["writes"] = writes
        _write_json(path, run)
        return entry


def enrich_run_detail(agent_dir: str, run: dict[str, Any] | None) -> dict[str, Any] | None:
    """Attach resolved corpus entries used by this run (for UI detail)."""
    if not isinstance(run, dict):
        return None
    corpus_items: list[dict[str, Any]] = []
    for cid in run.get("corpus_ids") or []:
        entry = get_corpus(agent_dir, str(cid))
        if entry:
            corpus_items.append(
                {
                    "id": entry.get("id"),
                    "session_id": entry.get("session_id"),
                    "session_title": entry.get("session_title"),
                    "created_at": entry.get("created_at"),
                    "source": entry.get("source"),
                    "summary": entry.get("summary"),
                    "learned_by": entry.get("learned_by"),
                    "learned_at": entry.get("learned_at"),
                }
            )
        else:
            corpus_items.append({"id": cid, "missing": True})
    detail = dict(run)
    detail["corpus_items"] = corpus_items
    return detail


def get_run(agent_dir: str, run_id: str) -> dict[str, Any] | None:
    path = os.path.join(runs_root(agent_dir), f"{_safe_id(run_id)}.json")
    data = _read_json(path, None)
    return data if isinstance(data, dict) else None


def list_runs(agent_dir: str, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    ensure_defaults(agent_dir)
    items: list[dict[str, Any]] = []
    root = runs_root(agent_dir)
    for name in os.listdir(root):
        if not name.endswith(".json"):
            continue
        entry = _read_json(os.path.join(root, name), None)
        if isinstance(entry, dict) and entry.get("id"):
            items.append(entry)
    items.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    total = len(items)
    return {"items": items[offset : offset + max(0, limit)], "total": total, "limit": limit, "offset": offset}


def mark_interrupted_runs(agent_dir: str) -> int:
    """On plugin load, mark in-flight runs as interrupted."""
    ensure_defaults(agent_dir)
    n = 0
    for item in list_runs(agent_dir, limit=500)["items"]:
        if item.get("status") in ("queued", "running"):
            update_run(
                agent_dir,
                item["id"],
                status="interrupted",
                finished_at=_utc_now_iso(),
                error="Process restarted before run finished",
            )
            n += 1
    return n
