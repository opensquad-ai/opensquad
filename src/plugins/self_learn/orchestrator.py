"""Learn orchestrator: spawn background Self-Learn sub-agent runs."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from . import store

logger = logging.getLogger("plugins.self_learn.orchestrator")


def _current_session_id() -> str:
    """Anchor Self-Learn folds / host UI to the primary ingress session.

    Same rule as external channels (Telegram/Feishu/group): only the primary
    session receives Self-Learn host folds so other parallel panes stay clean.
    """
    try:
        from opensquad import session_manager as sm_mod

        sm = getattr(sm_mod, "session_manager", None)
        if sm is None:
            return ""
        if hasattr(sm, "get_primary_session_id"):
            primary = sm.get_primary_session_id() or ""
            if primary:
                return primary
        if hasattr(sm, "get_current_session_id"):
            return sm.get_current_session_id() or ""
    except Exception:
        pass
    return ""


def _emit_session_event(etype: str, payload: dict, sid: str) -> None:
    """Emit + persist an event so the chat UI can nest Self-Learn under a fold.

    Always target *sid* (primary) for both live bus and disk — never the focused
    pane. Split-view focus must not steal Self-Learn host folds.
    """
    try:
        from opensquad.events import bus

        data = dict(payload)
        if sid:
            bus.emit(etype, {"sid": sid, "data": data})
        else:
            bus.emit(etype, data)
    except Exception:
        logger.debug("[self_learn] bus emit failed", exc_info=True)
    try:
        from opensquad import session_manager as sm_mod

        sm = getattr(sm_mod, "session_manager", None)
        if sm is not None and hasattr(sm, "add_event"):
            # Critical for parallel panes: without sid=, events land on focused session.
            sm.add_event(etype, dict(payload), sid=sid or None)
    except Exception:
        logger.debug("[self_learn] persist event failed", exc_info=True)


def _publish_host_fold(
    *,
    sid: str,
    run_id: str,
    job_id: str,
    task_preview: str,
) -> str:
    """Create a parent tool_call host so SubAgentPanel can open like delegate_task."""
    import json

    call_id = f"self_learn_{run_id}"
    label = f"Self-Learn {run_id}"
    args = {
        "task": task_preview[:2000],
        "run_id": run_id,
    }
    args_json = json.dumps(args, ensure_ascii=False, indent=2)
    _emit_session_event(
        "tool_call",
        {
            "id": call_id,
            "name": "self_learn.start_learn",
            "args": args_json,
            "job_id": job_id,
            "sub_task_label": label,
        },
        sid,
    )
    ack = {"job_id": job_id, "status": "running", "result": None, "run_id": run_id}
    _emit_session_event(
        "tool_result",
        {
            "id": call_id,
            "name": "self_learn.start_learn",
            "args": args_json,
            "result": json.dumps(ack, ensure_ascii=False),
            "job_id": job_id,
            "sub_task_label": label,
        },
        sid,
    )
    return call_id


def _publish_host_fold_done(
    *,
    sid: str,
    run_id: str,
    job_id: str,
    status: str,
    result_text: str,
) -> None:
    """Close the async Self-Learn host fold with a final (non-ack) tool_result."""
    import json

    call_id = f"self_learn_{run_id}"
    label = f"Self-Learn {run_id}"
    payload = {
        "job_id": job_id,
        "status": status,
        "result": (result_text or "")[:8000],
        "run_id": run_id,
    }
    _emit_session_event(
        "tool_result",
        {
            "id": call_id,
            "name": "self_learn.start_learn",
            "result": json.dumps(payload, ensure_ascii=False),
            "job_id": job_id,
            "sub_task_label": label,
        },
        sid,
    )


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _hours_since(iso_ts: str | None) -> float | None:
    dt = _parse_iso(iso_ts)
    if not dt:
        return None
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 3600.0)


def _minutes_since(iso_ts: str | None) -> float | None:
    hours = _hours_since(iso_ts)
    return None if hours is None else hours * 60.0


def is_agent_busy(meta: dict[str, Any] | None = None) -> bool:
    """True when any parallel turn is in flight, or legacy global state is busy.

    With split panes the global state machine can lag; prefer ParallelTurnScheduler.
    """
    try:
        from opensquad import runner as runner_mod

        active = getattr(runner_mod, "_active_runner", None)
        sched = getattr(active, "_parallel_scheduler", None) if active else None
        if sched is not None:
            busy = getattr(sched, "busy_sessions", None)
            if busy:
                return True
    except Exception:
        pass
    state = (meta or {}).get("last_agent_state") or ""
    return state in ("working", "thinking", "sleeping", "awaiting_reply")


def can_start_auto(agent_dir: str) -> tuple[bool, str]:
    meta = store.load_meta(agent_dir)
    if not meta.get("idle_auto_enabled", True):
        return False, "idle_auto_disabled"

    idle_minutes = float(meta.get("idle_minutes") or 30)
    cooldown_hours = float(meta.get("cooldown_hours") or 24)

    mins = _minutes_since(meta.get("last_user_activity_at"))
    if mins is None or mins < idle_minutes:
        return False, "user_not_idle"

    hours = _hours_since(meta.get("last_learn_at"))
    if hours is not None and hours < cooldown_hours:
        return False, "cooldown"

    if is_agent_busy(meta):
        store.update_meta(agent_dir, pending_due_to_busy=True)
        return False, "agent_busy"

    unlearned = store.list_corpus(agent_dir, unlearned_only=True, limit=1)
    if unlearned.get("total", 0) <= 0:
        return False, "no_unlearned_corpus"

    return True, "ok"


def build_learn_task(agent_dir: str, run: dict[str, Any], pipeline: dict[str, Any]) -> str:
    gates = pipeline.get("gates") or {}
    steps = pipeline.get("steps") or []
    step_text = "\n".join(
        f"{i + 1}. **{s.get('title') or s.get('id')}**: {s.get('instruction')}" for i, s in enumerate(steps)
    )
    max_corpus = int(gates.get("max_corpus_per_run") or 20)
    return f"""You are the Self-Learn background sub-agent for this OpenSquad agent.
Your job is offline continuous learning. Do NOT chat with the user. Do NOT invent facts without evidence.

## Run metadata
- run_id: {run["id"]}
- trigger: {run.get("trigger")}
- pipeline: {pipeline.get("name", "default")}

## Gates (must obey)
- allow_memory_write: {gates.get("allow_memory_write", True)}
- allow_agent_md: {gates.get("allow_agent_md", False)}
- allow_reminder: {gates.get("allow_reminder", False)}
- min_evidence_for_agent_md: {gates.get("min_evidence_for_agent_md", 2)}
- max_corpus_per_run: {max_corpus}

## Pipeline
{step_text}

## Tools to use
- self_learn.corpus_list / corpus_get / corpus_mark_learned
- self_learn.memory_snapshot / profile_get_agent_md / profile_append_agent_md
- self_learn.memory_write (preferred for lessons; records destination for the UI)
- self_learn.sessions_recent_snippets / files_explore / reminder_list / finish_run
- reminder.set / reminder.set_recurring (only if allow_reminder) — then call self_learn.record_write

## Output contract
1. Process at most {max_corpus} unlearned corpus items.
2. Every write must cite evidence_refs (corpus ids). Prefer self_learn.memory_write so the run log captures what was applied where.
3. Prefer memory for lessons; agent.md only for stable habits with enough evidence and gate on.
4. End by calling corpus_mark_learned then finish_run(
     summary="...",
     writes_json='[{{"target":"memory|agent.md|reminder","content":"...","evidence_refs":["corpus_id"]}}]'
   ). writes_json must list every durable write you made.
"""


def start_learn(
    agent_dir: str,
    *,
    trigger: str = "manual",
    force: bool = False,
    allow_agent_md: bool | None = None,
    allow_reminder: bool | None = None,
    pipeline_name: str = "default",
) -> dict[str, Any]:
    store.ensure_defaults(agent_dir)
    meta = store.load_meta(agent_dir)

    if trigger != "manual" and not force:
        ok, reason = can_start_auto(agent_dir)
        if not ok:
            return {"ok": False, "error": reason}

    if trigger == "manual" and not force:
        # Still respect busy unless force
        if is_agent_busy(meta):
            store.update_meta(agent_dir, pending_due_to_busy=True)
            return {"ok": False, "error": "agent_busy", "queued": True}

    # Prevent concurrent runs
    active = [r for r in store.list_runs(agent_dir, limit=20)["items"] if r.get("status") in ("queued", "running")]
    if active:
        return {"ok": False, "error": "run_in_progress", "run_id": active[0].get("id")}

    # Nothing to learn — finish immediately without spawning a sub-agent
    # (avoids flipping the chat UI into a stuck "thinking" state).
    unlearned = store.list_corpus(agent_dir, unlearned_only=True, limit=1)
    if unlearned.get("total", 0) <= 0 and not force:
        run = store.create_run(agent_dir, trigger=trigger, pipeline_name=pipeline_name, force=force)
        store.update_run(
            agent_dir,
            run["id"],
            status="done",
            started_at=store._utc_now_iso(),
            finished_at=store._utc_now_iso(),
            summary="No unlearned corpus items. Skipped learning (no sub-agent started).",
        )
        return {
            "ok": True,
            "run_id": run["id"],
            "status": "done",
            "skipped": True,
            "reason": "no_unlearned_corpus",
        }

    pipeline = store.load_pipeline(agent_dir, pipeline_name)
    if allow_agent_md is not None:
        pipeline.setdefault("gates", {})["allow_agent_md"] = bool(allow_agent_md)
    if allow_reminder is not None:
        pipeline.setdefault("gates", {})["allow_reminder"] = bool(allow_reminder)

    run = store.create_run(agent_dir, trigger=trigger, pipeline_name=pipeline.get("name", "default"), force=force)
    task = build_learn_task(agent_dir, run, pipeline)

    try:
        from opensquad.sub_agent_runner import SubAgentRunner, job_manager
        from plugins.self_learn.runtime import resolve_runtime
    except Exception as e:
        store.update_run(
            agent_dir,
            run["id"],
            status="error",
            finished_at=store._utc_now_iso(),
            error=f"Failed to import sub-agent runtime: {e}",
        )
        return {"ok": False, "error": "runtime_unavailable", "detail": str(e), "run_id": run["id"]}

    cfg, registry, source = resolve_runtime()
    if not cfg or registry is None:
        store.update_run(
            agent_dir,
            run["id"],
            status="error",
            finished_at=store._utc_now_iso(),
            error=(
                "Agent runtime not available in this process. "
                "UI Learn Now is queued for the agent; ensure the agent is running."
            ),
        )
        return {"ok": False, "error": "runtime_not_ready", "run_id": run["id"], "source": source}

    # Inject a compact self-learn system preamble into sub-agent prompt.
    cfg = dict(cfg)
    parent_prompt = cfg.get("parent_prompt") or cfg.get("prompt") or ""
    try:
        from opensquad.tools.delegate import _build_sub_prompt

        cleaned = _build_sub_prompt(str(parent_prompt))
    except Exception:
        cleaned = str(parent_prompt)
    cfg["prompt"] = (
        "You are a background Self-Learn sub-agent. Follow the task pipeline strictly. "
        "Never address the end user. Prefer tools over speculation.\n\n" + cleaned
    )
    if "api_protocol" not in cfg and cfg.get("provider"):
        cfg["api_protocol"] = cfg["provider"]

    sid = _current_session_id()
    if not sid:
        logger.warning("[self_learn] no primary session id; host fold may attach to focused pane")
    else:
        logger.info("[self_learn] anchoring run to primary session %s", sid)
    label = f"Self-Learn {run['id']}"
    runner = SubAgentRunner(
        cfg,
        registry,
        delegation_depth=1,
        sid=sid or None,
        sub_task_label=label,
    )
    job_id = job_manager.submit(runner, task)
    # Publish a host fold in the current chat so SubAgentPanel works like delegate_task.
    try:
        _publish_host_fold(
            sid=sid,
            run_id=run["id"],
            job_id=job_id,
            task_preview=task,
        )
    except Exception:
        logger.warning("[self_learn] failed to publish host fold", exc_info=True)

    store.update_run(
        agent_dir,
        run["id"],
        status="running",
        started_at=store._utc_now_iso(),
        job_id=job_id,
        runtime_source=source,
        session_id=sid,
        host_call_id=f"self_learn_{run['id']}",
    )
    store.update_meta(agent_dir, pending_due_to_busy=False)

    # Background watcher to finalize run when job completes
    try:
        import asyncio

        async def _watch():
            from opensquad.sub_agent_runner import job_manager as jm

            while True:
                info = jm.get_result(job_id)
                status = info.get("status")
                if status in ("done", "error", "cancelled", "not_found"):
                    result_text = info.get("result") or ""
                    final_status = "done" if status == "done" else ("cancelled" if status == "cancelled" else "error")
                    existing = store.get_run(agent_dir, run["id"]) or {}
                    # Prefer finish_run() fields when the sub-agent already finalized.
                    already_done = existing.get("status") == "done" and (
                        existing.get("summary") or existing.get("writes") or existing.get("corpus_ids")
                    )
                    if already_done and final_status == "done":
                        patch = {
                            "finished_at": existing.get("finished_at") or store._utc_now_iso(),
                        }
                        if not existing.get("summary") and result_text:
                            patch["summary"] = result_text[:4000]
                    else:
                        patch = {
                            "status": final_status,
                            "finished_at": store._utc_now_iso(),
                            "error": None if final_status == "done" else (result_text or status),
                        }
                        # Keep a richer finish_run summary if present; otherwise use job text.
                        if not (existing.get("summary") or "").strip():
                            patch["summary"] = (result_text or "")[:4000]
                        elif final_status != "done":
                            patch["summary"] = (result_text or existing.get("summary") or "")[:4000]
                    store.update_run(agent_dir, run["id"], **patch)
                    if final_status == "done" or already_done:
                        store.update_meta(agent_dir, last_learn_at=store._utc_now_iso())
                    # Close the chat fold (async ack -> final result) so UI settles.
                    try:
                        summary = (store.get_run(agent_dir, run["id"]) or {}).get("summary") or result_text
                        _publish_host_fold_done(
                            sid=sid,
                            run_id=run["id"],
                            job_id=job_id,
                            status=final_status,
                            result_text=str(summary or ""),
                        )
                    except Exception:
                        logger.debug("[self_learn] host fold close failed", exc_info=True)
                    try:
                        jm.cleanup(job_id)
                    except Exception:
                        pass
                    return
                await asyncio.sleep(2.0)

        loop = asyncio.get_running_loop()
        loop.create_task(_watch())
    except RuntimeError:
        logger.warning("[self_learn] no running loop to watch job %s", job_id)

    logger.info("[self_learn] started run=%s job=%s trigger=%s", run["id"], job_id, trigger)
    return {"ok": True, "run_id": run["id"], "job_id": job_id, "status": "running"}


def tick_scheduler(agent_dir: str) -> dict[str, Any] | None:
    """Called periodically: drain UI requests, then idle-auto / interval triggers."""
    store.ensure_defaults(agent_dir)

    # 1) Process UI/Launcher queued learn requests first (always in agent process).
    pending = store.list_pending_requests(agent_dir)
    for req in pending[:1]:  # one at a time
        store.update_request(
            agent_dir,
            req["id"],
            status="picked",
            picked_at=store._utc_now_iso(),
        )
        result = start_learn(
            agent_dir,
            trigger=str(req.get("trigger") or "manual"),
            force=bool(req.get("force")),
            allow_agent_md=req.get("allow_agent_md"),
            allow_reminder=req.get("allow_reminder"),
            pipeline_name=str(req.get("pipeline_name") or "default"),
        )
        store.update_request(
            agent_dir,
            req["id"],
            status="done" if result.get("ok") else "error",
            result=result,
            finished_at=store._utc_now_iso(),
        )
        return result

    meta = store.load_meta(agent_dir)

    # Interval wall-clock trigger
    interval_hours = float(meta.get("interval_hours") or 0)
    if interval_hours > 0:
        since = _hours_since(meta.get("last_interval_fire_at") or meta.get("last_learn_at"))
        if since is None or since >= interval_hours:
            if not is_agent_busy(meta):
                store.update_meta(agent_dir, last_interval_fire_at=store._utc_now_iso())
                return start_learn(agent_dir, trigger="interval", force=False)
            store.update_meta(agent_dir, pending_due_to_busy=True)
            return {"ok": False, "error": "agent_busy", "deferred": True}

    # Pending after busy
    if meta.get("pending_due_to_busy") and not is_agent_busy(meta):
        ok, reason = can_start_auto(agent_dir)
        if ok or reason in ("cooldown",):  # still respect cooldown for auto
            if ok:
                return start_learn(agent_dir, trigger="idle_auto", force=False)

    ok, reason = can_start_auto(agent_dir)
    if ok:
        return start_learn(agent_dir, trigger="idle_auto", force=False)
    return {"ok": False, "error": reason}
