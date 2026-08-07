"""
Self-Learn plugin — continuous learning corpus, background learner, scheduler.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from opensquad.plugin_api import Plugin, hook, register, tool

from . import archive as archive_mod
from . import orchestrator, store

logger = logging.getLogger("plugins.self_learn")


async def _async_call(fn, args, kwargs):
    return fn(*args, **kwargs)


def _agent_dir(plugin: SelfLearnPlugin) -> str:
    resolved = store.resolve_agent_dir(getattr(plugin, "_agent_dir_override", None))
    if resolved:
        return resolved
    # Fallback: derive from plugin data_dir parents if possible
    data_dir = getattr(plugin.context, "data_dir", "") or ""
    # .../agents/<name>/data/plugins/self_learn or workspace data/plugins/self_learn
    cand = os.path.abspath(os.path.join(data_dir, "..", "..", ".."))
    if os.path.isfile(os.path.join(cand, "agent.md")) or os.path.isfile(os.path.join(cand, "config.json")):
        return cand
    raise RuntimeError("self_learn: agent_dir not available")


@register(
    name="self_learn",
    author="OpenSquad",
    description="Continuous self-learning: archive compression summaries, background sub-agent learning, idle/interval triggers, import/export.",
    version="1.0.0",
    plugin_type="tool",
    display_name="Self Learn",
    tags=["memory", "learning"],
    contributes={
        "views": [
            {
                "name": "panel",
                "title": "Self Learn",
                "icon": "GraduationCap",
                "data_endpoint": "/api/plugins/self_learn/data",
            }
        ]
    },
    config_schema={
        "idle_auto_enabled": {
            "type": "boolean",
            "default": True,
            "description": "Automatically start a learn run after the user is idle long enough.",
        },
        "idle_minutes": {
            "type": "integer",
            "default": 30,
            "description": "Minutes without user interaction before idle auto-learn may start.",
        },
        "cooldown_hours": {
            "type": "integer",
            "default": 24,
            "description": "Minimum hours between successful auto learn runs.",
        },
        "interval_hours": {
            "type": "integer",
            "default": 0,
            "description": "Optional wall-clock interval in hours (0 = disabled).",
        },
        "allow_agent_md_auto": {
            "type": "boolean",
            "default": False,
            "description": "Allow auto learn runs to patch agent.md (still requires evidence gate).",
        },
        "allow_reminder_auto": {
            "type": "boolean",
            "default": False,
            "description": "Allow auto learn runs to create reminders.",
        },
        "scheduler_tick_seconds": {
            "type": "integer",
            "default": 60,
            "description": "How often the idle/interval scheduler checks conditions.",
        },
    },
)
class SelfLearnPlugin(Plugin):
    # Scheduler ↔ agent-loop bridge: retry then pause (split panes can starve the loop).
    _LOOP_CALL_TIMEOUT_S = 30
    _LOOP_CALL_MAX_ATTEMPTS = 3
    _FORCE_STOP_COOLDOWN_S = 300

    def __init__(self, context):
        super().__init__(context)
        self._agent_dir_override: str | None = None
        self._scheduler_stop = threading.Event()
        self._scheduler_thread: threading.Thread | None = None
        self._current_run_id: str | None = None
        self._loop: Any = None
        self._scheduler_paused_until: float = 0.0
        self._force_stop_reason: str = ""

    def on_load(self) -> None:
        try:
            import asyncio

            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        try:
            agent_dir = store.resolve_agent_dir()
            if agent_dir:
                self._agent_dir_override = agent_dir
                store.ensure_defaults(agent_dir)
                n = store.mark_interrupted_runs(agent_dir)
                if n:
                    logger.info("[self_learn] marked %d interrupted runs", n)
                cfg = self.context.config or {}
                store.update_meta(
                    agent_dir,
                    idle_auto_enabled=bool(cfg.get("idle_auto_enabled", True)),
                    idle_minutes=int(cfg.get("idle_minutes", 30)),
                    cooldown_hours=int(cfg.get("cooldown_hours", 24)),
                    interval_hours=int(cfg.get("interval_hours", 0)),
                )
                # Sync pipeline auto gates defaults from config
                pipeline = store.load_pipeline(agent_dir)
                gates = pipeline.setdefault("gates", {})
                gates["allow_agent_md"] = bool(cfg.get("allow_agent_md_auto", False))
                gates["allow_reminder"] = bool(cfg.get("allow_reminder_auto", False))
                store.save_pipeline(agent_dir, pipeline)
        except Exception:
            logger.warning("[self_learn] on_load init failed", exc_info=True)

        self._start_scheduler()
        # Cache runtime from this agent process so later ticks don't rely only on delegate.
        try:
            from .runtime import resolve_runtime, set_runtime

            cfg, registry, source = resolve_runtime()
            if cfg and registry is not None:
                set_runtime(cfg, registry)
                logger.info("[self_learn] runtime cached from %s", source)
        except Exception:
            logger.debug("[self_learn] runtime cache skipped", exc_info=True)
        logger.info("[self_learn] plugin loaded")

    def _force_stop_scheduler(self, reason: str) -> None:
        """Pause scheduler after repeated loop failures; fail queued UI requests."""
        import time

        self._force_stop_reason = reason
        self._scheduler_paused_until = time.monotonic() + self._FORCE_STOP_COOLDOWN_S
        logger.error(
            "[self_learn] force-stopped scheduler for %ss: %s",
            self._FORCE_STOP_COOLDOWN_S,
            reason,
        )
        try:
            agent_dir = store.resolve_agent_dir(self._agent_dir_override)
            if not agent_dir:
                return
            now = store._utc_now_iso()
            err_payload = {"ok": False, "error": reason, "force_stopped": True}
            for req in store.list_pending_requests(agent_dir):
                store.update_request(
                    agent_dir,
                    req["id"],
                    status="error",
                    result=err_payload,
                    finished_at=now,
                )
            runs = store.list_runs(agent_dir, limit=20).get("items") or []
            for run in runs:
                if run.get("status") not in ("queued", "running"):
                    continue
                job_id = run.get("job_id") or ""
                if job_id:
                    try:
                        from opensquad.sub_agent_runner import job_manager

                        job_manager.cleanup(job_id)
                    except Exception:
                        pass
                store.update_run(
                    agent_dir,
                    run["id"],
                    status="error",
                    finished_at=now,
                    error=reason,
                )
        except Exception:
            logger.debug("[self_learn] force-stop cleanup failed", exc_info=True)

    def _run_on_loop(self, fn, *args, **kwargs):
        """Execute callable on the agent asyncio loop (thread-safe).

        Retries up to 3 times on timeout/failure (common when split panes keep the
        loop busy), then force-stops the scheduler for a cooldown.
        """
        import asyncio
        import concurrent.futures
        import time

        loop = self._loop
        if loop is None or not loop.is_running():
            try:
                loop = asyncio.get_running_loop()
                self._loop = loop
            except RuntimeError:
                return fn(*args, **kwargs)
        try:
            running = asyncio.get_running_loop()
            if running is loop:
                return fn(*args, **kwargs)
        except RuntimeError:
            pass

        last_err = ""
        for attempt in range(1, self._LOOP_CALL_MAX_ATTEMPTS + 1):
            fut = asyncio.run_coroutine_threadsafe(_async_call(fn, args, kwargs), loop)
            try:
                result = fut.result(timeout=self._LOOP_CALL_TIMEOUT_S)
                # Recover from a previous force-stop once a tick succeeds.
                if self._scheduler_paused_until:
                    self._scheduler_paused_until = 0.0
                    self._force_stop_reason = ""
                return result
            except Exception as e:
                # TimeoutError often has an empty str(); name it explicitly.
                if isinstance(e, TimeoutError | concurrent.futures.TimeoutError | asyncio.TimeoutError):
                    last_err = f"TimeoutError ({self._LOOP_CALL_TIMEOUT_S}s waiting for agent loop)"
                else:
                    last_err = str(e) or type(e).__name__
                logger.warning(
                    "[self_learn] loop call failed (attempt %s/%s): %s",
                    attempt,
                    self._LOOP_CALL_MAX_ATTEMPTS,
                    last_err,
                )
                try:
                    fut.cancel()
                except Exception:
                    pass
                if attempt < self._LOOP_CALL_MAX_ATTEMPTS:
                    time.sleep(min(2 * attempt, 5))
                    continue

        self._force_stop_scheduler(f"loop_call_failed_after_{self._LOOP_CALL_MAX_ATTEMPTS}_retries: {last_err}")
        return {"ok": False, "error": last_err, "force_stopped": True}

    def on_unload(self) -> None:
        self._scheduler_stop.set()
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            self._scheduler_thread.join(timeout=2)
        logger.info("[self_learn] plugin unloaded")

    def _start_scheduler(self) -> None:
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            return
        self._scheduler_stop.clear()
        tick = int((self.context.config or {}).get("scheduler_tick_seconds", 60) or 60)

        def _loop():
            import time

            while not self._scheduler_stop.wait(timeout=max(5, min(tick, 15))):
                try:
                    if time.monotonic() < self._scheduler_paused_until:
                        continue
                    agent_dir = store.resolve_agent_dir(self._agent_dir_override)
                    if not agent_dir:
                        continue
                    # Prefer faster drain when UI queued a request.
                    pending = store.list_pending_requests(agent_dir)
                    # job_manager.submit needs the agent asyncio loop
                    self._run_on_loop(orchestrator.tick_scheduler, agent_dir)
                    if pending and time.monotonic() >= self._scheduler_paused_until:
                        # Immediately try again next short cycle
                        continue
                except Exception:
                    logger.debug("[self_learn] scheduler tick failed", exc_info=True)

        self._scheduler_thread = threading.Thread(target=_loop, name="self_learn_scheduler", daemon=True)
        self._scheduler_thread.start()

    # ── Hooks ──────────────────────────────────────────────────────────────

    @hook.on_message_received(priority=10)
    async def _on_message(self, ctx: dict) -> dict:
        try:
            agent_dir = store.resolve_agent_dir(self._agent_dir_override)
            if agent_dir:
                store.update_meta(agent_dir, last_user_activity_at=store._utc_now_iso())
        except Exception:
            logger.debug("[self_learn] activity stamp failed", exc_info=True)
        return ctx

    @hook.on_state_change(priority=10)
    async def _on_state(self, ctx: dict) -> dict:
        try:
            agent_dir = store.resolve_agent_dir(self._agent_dir_override)
            if not agent_dir:
                return ctx
            state = str(ctx.get("new_state") or ctx.get("state") or ctx.get("to") or "")
            if state:
                store.update_meta(
                    agent_dir,
                    last_agent_state=state,
                    last_agent_state_at=store._utc_now_iso(),
                )
                # If we just became idle and something was deferred, nudge scheduler.
                if state in ("idle", "connected", "ready"):
                    # Resume after a prior force-stop once the agent is actually idle.
                    self._scheduler_paused_until = 0.0
                    self._force_stop_reason = ""
                    try:
                        orchestrator.tick_scheduler(agent_dir)
                    except Exception:
                        pass
        except Exception:
            logger.debug("[self_learn] state stamp failed", exc_info=True)
        return ctx

    # ── Tools (materials API) ──────────────────────────────────────────────

    @tool(name="self_learn", description="Self-learning corpus and control APIs", level="extended")
    def corpus_list(self, unlearned_only: bool = True, limit: int = 20, offset: int = 0, session_id: str = "") -> dict:
        """List compression-summary corpus entries for learning."""
        agent_dir = _agent_dir(self)
        return store.list_corpus(
            agent_dir,
            unlearned_only=bool(unlearned_only),
            limit=int(limit),
            offset=int(offset),
            session_id=session_id or None,
        )

    @tool(name="self_learn", description="Self-learning corpus and control APIs", level="extended")
    def corpus_get(self, corpus_id: str) -> dict:
        """Get one corpus entry by id."""
        agent_dir = _agent_dir(self)
        entry = store.get_corpus(agent_dir, corpus_id)
        return entry or {"error": "not_found", "id": corpus_id}

    @tool(name="self_learn", description="Self-learning corpus and control APIs", level="extended")
    def corpus_mark_learned(self, corpus_ids: list[str] | str, run_id: str) -> dict:
        """Mark corpus entries as consumed by a learn run."""
        agent_dir = _agent_dir(self)
        if isinstance(corpus_ids, str):
            parts = [p.strip() for p in corpus_ids.replace(";", ",").split(",") if p.strip()]
        else:
            parts = list(corpus_ids or [])
        result = store.mark_corpus_learned(agent_dir, parts, run_id)
        store.update_run(agent_dir, run_id, corpus_ids=parts)
        return result

    @tool(name="self_learn", description="Self-learning corpus and control APIs", level="extended")
    def memory_snapshot(self, query: str = "", limit: int = 15) -> dict:
        """Snapshot high-value long-term memory entries for learning."""
        try:
            from opensquad.tools.long_memory import get_memory_manager

            mm = get_memory_manager()
            if mm is None:
                return {"items": [], "error": "memory_manager_unavailable"}
            q = (query or "preferences habits lessons playbooks").strip()
            items = []
            # Prefer query API if present
            if hasattr(mm, "query"):
                raw = mm.query(q, limit=int(limit))
                if isinstance(raw, list):
                    items = raw[: int(limit)]
                elif isinstance(raw, dict):
                    items = (raw.get("items") or raw.get("results") or [])[: int(limit)]
            elif hasattr(mm, "auto_recall"):
                text = mm.auto_recall(q) or ""
                items = [{"summary": text}] if text else []
            return {"items": items, "query": q}
        except Exception as e:
            return {"items": [], "error": str(e)}

    @tool(name="self_learn", description="Self-learning corpus and control APIs", level="extended")
    def profile_get_agent_md(self) -> dict:
        """Read current agent.md permanent profile."""
        try:
            from opensquad.context_base import _read_agent_md

            content = _read_agent_md() or ""
        except Exception:
            agent_dir = _agent_dir(self)
            path = os.path.join(agent_dir, "agent.md")
            content = ""
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as f:
                    content = f.read()
        return {"path": "agent.md", "content": content, "chars": len(content)}

    @tool(name="self_learn", description="Self-learning corpus and control APIs", level="extended")
    def profile_append_agent_md(self, section: str, text: str, evidence_refs: list[str] | str | None = None) -> dict:
        """
        Append a short habit/preference note under a section in agent.md.
        Never overwrites the whole file. Requires evidence_refs.
        """
        agent_dir = _agent_dir(self)
        pipeline = store.load_pipeline(agent_dir)
        gates = pipeline.get("gates") or {}
        if not gates.get("allow_agent_md", False):
            return {"ok": False, "error": "allow_agent_md_disabled"}

        refs: list[str]
        if isinstance(evidence_refs, str):
            refs = [p.strip() for p in evidence_refs.replace(";", ",").split(",") if p.strip()]
        else:
            refs = list(evidence_refs or [])
        min_ev = int(gates.get("min_evidence_for_agent_md") or 2)
        if len(refs) < min_ev:
            return {"ok": False, "error": "insufficient_evidence", "required": min_ev, "got": len(refs)}

        section = (section or "User Preferences").strip() or "User Preferences"
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "empty_text"}

        path = os.path.join(agent_dir, "agent.md")
        existing = ""
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                existing = f.read()
        heading = f"## {section}"
        note = f"\n- {text} _(evidence: {', '.join(refs)})_\n"
        if heading in existing:
            # Insert after heading line
            parts = existing.split(heading, 1)
            updated = parts[0] + heading + note + parts[1]
        else:
            updated = existing.rstrip() + f"\n\n{heading}\n{note}"
        with open(path, "w", encoding="utf-8") as f:
            f.write(updated)
        store.append_run_write(
            agent_dir,
            target="agent.md",
            content=f"[{section}] {text}",
            evidence_refs=refs,
            extra={"section": section},
        )
        return {"ok": True, "section": section, "evidence_refs": refs}

    @tool(name="self_learn", description="Self-learning corpus and control APIs", level="extended")
    def memory_write(
        self,
        topic: str,
        summary: str,
        evidence_refs: list[str] | str | None = None,
        keywords: list[str] | str | None = None,
        body: str = "",
        entry_type: str = "experience",
        category: str = "self_learn",
        importance: int = 3,
    ) -> dict:
        """
        Write a lesson into long-term memory and record it on the active learn run.
        Prefer this over long_memory.memory_write during self-learn so the UI can show destinations.
        """
        agent_dir = _agent_dir(self)
        pipeline = store.load_pipeline(agent_dir)
        gates = pipeline.get("gates") or {}
        if not gates.get("allow_memory_write", True):
            return {"ok": False, "error": "allow_memory_write_disabled"}

        refs: list[str]
        if isinstance(evidence_refs, str):
            refs = [p.strip() for p in evidence_refs.replace(";", ",").split(",") if p.strip()]
        else:
            refs = list(evidence_refs or [])
        if not refs:
            return {"ok": False, "error": "evidence_refs_required"}

        kw: list[str] | None
        if isinstance(keywords, str):
            kw = [p.strip() for p in keywords.replace(";", ",").split(",") if p.strip()]
        else:
            kw = list(keywords) if keywords else None

        try:
            from opensquad.tools import long_memory as lm

            result = lm.memory_write(
                topic=topic,
                summary=summary,
                keywords=kw,
                body=body or None,
                entry_type=entry_type or "experience",
                category=category or "self_learn",
                importance=int(importance or 3),
            )
        except Exception as e:
            return {"ok": False, "error": str(e)}

        ok = isinstance(result, dict) and result.get("status") != "error"
        if ok:
            store.append_run_write(
                agent_dir,
                target="memory",
                content=f"{topic}: {summary}",
                evidence_refs=refs,
                extra={
                    "topic": topic,
                    "entry_type": entry_type,
                    "memory_result": {k: result.get(k) for k in ("id", "status", "message") if k in (result or {})},
                },
            )
        return {"ok": ok, "result": result, "evidence_refs": refs}

    @tool(name="self_learn", description="Self-learning corpus and control APIs", level="extended")
    def record_write(
        self,
        target: str,
        content: str,
        evidence_refs: list[str] | str | None = None,
        run_id: str = "",
    ) -> dict:
        """Manually record a write destination (e.g. after reminder.set) on the active learn run."""
        agent_dir = _agent_dir(self)
        if isinstance(evidence_refs, str):
            refs = [p.strip() for p in evidence_refs.replace(";", ",").split(",") if p.strip()]
        else:
            refs = list(evidence_refs or [])
        entry = store.append_run_write(
            agent_dir,
            target=target or "other",
            content=content or "",
            evidence_refs=refs,
            run_id=run_id or None,
        )
        if not entry:
            return {"ok": False, "error": "no_active_run"}
        return {"ok": True, "write": entry}

    @tool(name="self_learn", description="Self-learning corpus and control APIs", level="extended")
    def sessions_recent_snippets(self, limit_messages: int = 30) -> dict:
        """Fetch recent primary-session message snippets (truncated) for learning context."""
        try:
            from opensquad.session_manager import get_session_manager

            sm = get_session_manager()
            sid = ""
            if hasattr(sm, "get_primary_session_id"):
                sid = sm.get_primary_session_id() or ""
            if not sid:
                sid = sm.get_current_session_id() or ""
            msgs = sm.get_messages(sid=sid) if sid and hasattr(sm, "get_messages") else (sm.get_messages() or [])
            title = ""
            try:
                if sid and hasattr(sm, "ensure_session_loaded"):
                    data = sm.ensure_session_loaded(sid) or {}
                elif sid and hasattr(sm, "_resolve_session_data"):
                    data = sm._resolve_session_data(sid) or {}
                else:
                    data = getattr(sm, "session_data", {}) or {}
                title = str(data.get("title") or "")
            except Exception:
                pass
            out = []
            for m in msgs[-int(limit_messages) :]:
                role = m.get("role") or ""
                content = str(m.get("content") or "")
                if len(content) > 800:
                    content = content[:800] + "…"
                out.append({"role": role, "content": content, "type": m.get("type")})
            return {"session_id": sid, "session_title": title, "messages": out, "primary": True}
        except Exception as e:
            return {"messages": [], "error": str(e)}

    @tool(name="self_learn", description="Self-learning corpus and control APIs", level="extended")
    def reminder_list(self) -> dict:
        """List pending reminders if the reminder plugin is available."""
        try:
            # Soft import via registry is hard; read reminder data file if present
            from opensquad.system_config import syscfg

            agent_id = ""
            try:
                from opensquad import context_base

                agent_id = str((getattr(context_base, "_agent_config", None) or {}).get("agent_id") or "")
            except Exception:
                pass
            path = syscfg.workspace_data_dir("plugins", "reminder", f"{agent_id}_reminders.json")
            if os.path.isfile(path):
                import json

                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                return {"reminders": data if isinstance(data, list) else data}
            return {"reminders": [], "note": "no_reminder_file"}
        except Exception as e:
            return {"reminders": [], "error": str(e)}

    @tool(name="self_learn", description="Self-learning corpus and control APIs", level="extended")
    def files_explore(self, relative_path: str = ".", max_entries: int = 40) -> dict:
        """Explore files under the agent directory or cwd (restricted)."""
        agent_dir = _agent_dir(self)
        rel = (relative_path or ".").replace("\\", "/").lstrip("/")
        if ".." in rel.split("/"):
            return {"error": "path_escape_forbidden"}
        # Prefer agent_dir; also allow cwd if under workspace
        base = agent_dir
        target = os.path.abspath(os.path.join(base, rel))
        if not target.startswith(os.path.abspath(base)):
            return {"error": "path_outside_agent_dir"}
        if not os.path.exists(target):
            return {"error": "not_found", "path": rel}
        entries = []
        if os.path.isfile(target):
            size = os.path.getsize(target)
            preview = ""
            if size <= 20000 and target.lower().endswith((".md", ".txt", ".json", ".py", ".ts", ".tsx")):
                with open(target, encoding="utf-8", errors="replace") as f:
                    preview = f.read(4000)
            return {"type": "file", "path": rel, "size": size, "preview": preview}
        for name in sorted(os.listdir(target))[: int(max_entries)]:
            p = os.path.join(target, name)
            entries.append(
                {
                    "name": name,
                    "type": "dir" if os.path.isdir(p) else "file",
                    "size": (os.path.getsize(p) if os.path.isfile(p) else None),
                }
            )
        return {"type": "dir", "path": rel, "entries": entries}

    @tool(name="self_learn", description="Self-learning corpus and control APIs", level="extended")
    def finish_run(self, summary: str, writes_json: str = "") -> dict:
        """Finalize the current learn run with a short summary and optional writes ledger."""
        agent_dir = _agent_dir(self)
        # Prefer latest running run
        runs = store.list_runs(agent_dir, limit=10)["items"]
        active = next((r for r in runs if r.get("status") == "running"), None)
        if not active:
            return {"ok": False, "error": "no_active_run"}
        writes: list[Any] = list(active.get("writes") or [])
        if writes_json:
            try:
                import json

                parsed = json.loads(writes_json)
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict) and item.get("content"):
                            writes.append(item)
            except Exception:
                writes.append({"raw": writes_json[:500], "target": "other"})
        # Deduplicate by (target, content)
        seen: set[str] = set()
        deduped: list[Any] = []
        for w in writes:
            if not isinstance(w, dict):
                continue
            key = f"{w.get('target')}|{w.get('content')}"
            if key in seen:
                continue
            seen.add(key)
            deduped.append(w)
        updated = store.update_run(
            agent_dir,
            active["id"],
            status="done",
            finished_at=store._utc_now_iso(),
            summary=(summary or "")[:4000],
            writes=deduped,
        )
        store.update_meta(agent_dir, last_learn_at=store._utc_now_iso())
        return {"ok": True, "run": updated}

    @tool(name="self_learn", description="Self-learning corpus and control APIs", level="extended")
    def start_learn(self, force: bool = False, allow_agent_md: bool = False, allow_reminder: bool = False) -> dict:
        """Manually start a background self-learn run."""
        agent_dir = _agent_dir(self)
        return orchestrator.start_learn(
            agent_dir,
            trigger="manual",
            force=bool(force),
            allow_agent_md=bool(allow_agent_md),
            allow_reminder=bool(allow_reminder),
        )


# Re-export archive helper for runner imports
archive_compression_summary = archive_mod.archive_compression_summary
