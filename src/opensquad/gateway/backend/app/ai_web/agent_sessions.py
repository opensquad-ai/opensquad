"""
Agent Session disk reader - read-only access to Agent session files on disk.

Ports Bridge's _get_session_manager_for() logic so Gateway can directly
read Agent session files without going through Bridge.

Each Agent stores sessions in:
  agents/{name}/data/sessions/current_session.json
  agents/{name}/data/history/{session_id}.json
"""

import asyncio
import copy
import json
import logging
import os
import re
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

from opensquad.system_config import syscfg

# Active workspace directory
ROOT_DIR = syscfg.get_workspace()

# Cap on events returned on the latest page (offset=0). Long sessions can hold
# thousands of events; returning all of them makes the first history request
# multi-MB and stalls the frontend hydrate (10s client abort). The newest
# events pair with the newest messages shown on the first page.
_MAX_FIRST_PAGE_EVENTS = 400


def _build_agent_id_map() -> dict:
    """
    Scan agents/ directory to build agent_id -> agent_dir mapping.
    Also indexes by directory name so /agent-sessions/agent305/list works
    when config.agent_id is agent305-001.
    """
    agents_root = syscfg.workspace_agents_dir()
    id_map = {}
    if os.path.isdir(agents_root):
        for name in os.listdir(agents_root):
            cfg_path = os.path.join(agents_root, name, "config.json")
            if os.path.isfile(cfg_path):
                try:
                    with open(cfg_path, encoding="utf-8") as f:
                        cfg = json.load(f)
                    agent_dir = os.path.join(agents_root, name)
                    aid = cfg.get("agent_id", name)
                    id_map[aid] = agent_dir
                    # dir_name / folder alias (CLI often uses this)
                    id_map[name] = agent_dir
                    aname = cfg.get("agent_name") or cfg.get("name")
                    if aname and aname not in id_map:
                        id_map[str(aname)] = agent_dir
                except Exception:
                    pass
    return id_map


class AgentSessionReader:
    """
    Read-only session reader for a specific Agent's disk data.
    Mirrors opensquad.SessionManager's read APIs but never writes.
    Uses LRU cache with mtime validation to avoid redundant disk reads.
    """

    def __init__(self, save_dir: str, history_dir: str):
        self.save_dir = save_dir
        self.history_dir = history_dir
        self.current_session_file = os.path.join(save_dir, "current_session.json")
        self.primary_session_file = os.path.join(save_dir, "primary_session.json")
        self.session_data: dict[str, Any] = {
            "id": None,
            "title": None,
            "messages": [],
            "events": [],
            "last_updated": None,
            "created_at": None,
        }
        # LRU cache: sid -> {"data": ..., "mtime": ...}
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._cache_max_size = 32
        # Lightweight session-list metadata cache: sid -> {mtime, title, preview, created_at, last_updated}
        self._list_meta_cache: dict[str, dict[str, Any]] = {}
        # mtime cache for current_session.json — avoid re-parsing unchanged large files
        self._current_session_mtime: float | None = None
        self._current_log_mtime: float | None = None
        # Load current session from disk
        self._reload()

    # ---- disk reload ----

    _RELOAD_COUNTER = 0  # class-level counter to force periodic re-reads

    def _reload(self, force: bool = False):
        """Reload current session from disk (read-only).

        Args:
            force: If True, read from disk unconditionally (skips mtime cache).
                   Public API methods use mtime + incremental-log tracking so
                   repeated reads do not re-parse a large current session.
        """
        if not os.path.exists(self.current_session_file):
            return
        try:
            mtime = os.path.getmtime(self.current_session_file)
            _sid = self.session_data.get("id") if isinstance(self.session_data, dict) else None
            _log_path = os.path.join(self.history_dir, f"{_sid}.json.log") if _sid else None
            _log_mtime = os.path.getmtime(_log_path) if _log_path and os.path.exists(_log_path) else None
            if not force and mtime == self._current_session_mtime and _log_mtime == self._current_log_mtime:
                return  # snapshot and incremental log unchanged, skip disk read
            if not force and mtime == self._current_session_mtime and self._current_session_mtime is not None:
                # Snapshot unchanged: only merge new incremental log records.
                if _sid and _sid != "unknown":
                    self._replay_log_into(self.session_data, _sid, int(self.session_data.get("_save_seq") or 0))
                self._current_log_mtime = _log_mtime
                return
            with open(self.current_session_file, encoding="utf-8") as f:
                self.session_data = json.load(f)
            if "events" not in self.session_data:
                self.session_data["events"] = []
            if "id" not in self.session_data:
                self.session_data["id"] = "unknown"
            if "title" not in self.session_data:
                self.session_data["title"] = None
            # Backfill archive fields for sessions written before compression
            # started preserving archived_messages / archived_events.
            if "archived_messages" not in self.session_data:
                self.session_data["archived_messages"] = []
            if "archived_events" not in self.session_data:
                self.session_data["archived_events"] = []
            # Merge incremental log records newer than the last snapshot (the
            # agent throttles full snapshots; between them it appends O(1)
            # records to history/{sid}.json.log).
            _sid = self.session_data.get("id")
            if _sid and _sid != "unknown":
                self._replay_log_into(self.session_data, _sid, int(self.session_data.get("_save_seq") or 0))
            self._current_session_mtime = mtime
            self._current_log_mtime = _log_mtime
        except Exception as e:
            logger.warning(f"Failed to reload session: {e}")

    def invalidate_current_session_cache(self):
        """Force the next read to reload current_session.json from disk."""
        self._current_session_mtime = None
        self._cache.clear()
        self._list_meta_cache.clear()
        self._reload()

    # ---- LRU cache ----

    def _get_history_file_mtime(self, sid: str) -> float | None:
        # Include the incremental log file so cache entries are invalidated
        # when the agent appends records without a full snapshot.
        mtimes = []
        for fp in (
            os.path.join(self.history_dir, f"{sid}.json"),
            os.path.join(self.history_dir, f"{sid}.json.log"),
        ):
            try:
                if os.path.exists(fp):
                    mtimes.append(os.path.getmtime(fp))
            except OSError:
                pass
        return max(mtimes) if mtimes else None

    def _cache_put(self, sid: str, data: dict):
        if not sid:
            return
        if sid in self._cache:
            self._cache.move_to_end(sid)
        mtime = self._get_history_file_mtime(sid)
        self._cache[sid] = {"data": copy.deepcopy(data), "mtime": mtime}
        while len(self._cache) > self._cache_max_size:
            self._cache.popitem(last=False)

    def _cache_get(self, sid: str, *, deep: bool = False) -> dict | None:
        """Return cached session data.

        By default returns a shallow structural copy (dict + list copies of
        messages/events) to avoid expensive deepcopy on every API hit.
        Pass deep=True when the caller will mutate nested message objects.
        """
        if sid not in self._cache:
            return None
        entry = self._cache[sid]
        disk_mtime = self._get_history_file_mtime(sid)
        cached_mtime = entry.get("mtime")
        if disk_mtime is not None and cached_mtime is not None and disk_mtime != cached_mtime:
            del self._cache[sid]
            return None
        self._cache.move_to_end(sid)
        data = entry["data"]
        if deep:
            return copy.deepcopy(data)
        out = dict(data)
        for key in ("messages", "events", "archived_messages", "archived_events"):
            if isinstance(out.get(key), list):
                out[key] = list(out[key])
        return out

    # ---- internal helpers ----

    @staticmethod
    def _filter_events(events: list) -> list:
        """Remove synthetic internal tool calls from events list.
        system__event_pipeline is an internal mechanism injected by
        chat_api.add_pipeline_events() — it is NOT an actual LLM tool call
        and must never be exposed to the frontend or stored in session history.

        tool_call_delta is the streaming partial-input event used only by the
        live WS feed (each frame can carry ~25KB of accumulated arguments).
        History replay renders the final ``tool_call`` (full args) instead, so
        dropping deltas keeps session payloads small — a long turn otherwise
        returns 1.5MB+ of deltas and stalls the frontend hydrate.
        """
        skip_names = ("system__event_pipeline", "system.event_pipeline")
        skip_types = ("tool_call_delta",)
        return [e for e in events if e.get("name") not in skip_names and e.get("type") not in skip_types]

    def _replay_log_into(self, data: dict, sid: str, base_seq: int) -> int:
        """Merge the agent's incremental log (history/{sid}.json.log) into ``data``.

        Mirror of SessionManager._replay_log_into (the gateway reads these
        files cross-process). Applies records with seq > base_seq (the
        snapshot's _save_seq) using the same append/patch semantics, so a
        session served here matches the agent's in-memory state even while
        full snapshots are throttled. Returns the max replayed seq (0 if no
        log / nothing applied). Corrupt tail lines are dropped.
        """
        log_path = os.path.join(self.history_dir, f"{sid}.json.log")
        if not os.path.exists(log_path):
            return 0
        max_seq = 0
        try:
            with open(log_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        logger.warning(f"Dropping corrupt log line (sid={sid}): {line[:120]}")
                        continue
                    seq = rec.get("seq")
                    if not isinstance(seq, int) or seq <= base_seq:
                        continue
                    if seq > max_seq:
                        max_seq = seq
                    op = rec.get("op")
                    if op == "msg_append":
                        msg = rec.get("msg")
                        if isinstance(msg, dict):
                            data.setdefault("messages", []).append(msg)
                            if len(data["messages"]) > 1000:
                                data["messages"] = data["messages"][-1000:]
                            if rec.get("draft") is not None:
                                data["draft"] = rec["draft"]
                            if rec.get("title") and not data.get("title_locked"):
                                data["title"] = rec["title"]
                            data["last_updated"] = rec.get("ts")
                    elif op == "evt_append":
                        evt = rec.get("evt")
                        if isinstance(evt, dict):
                            data.setdefault("events", []).append(evt)
                            if len(data["events"]) > 2000:
                                data["events"] = data["events"][-2000:]
                            data["last_updated"] = rec.get("ts")
                    elif op == "tail_patch":
                        patches = rec.get("patches")
                        if isinstance(patches, dict):
                            messages = data.get("messages") or []
                            for i in range(len(messages) - 1, -1, -1):
                                if messages[i].get("role") == "assistant":
                                    messages[i].update(patches)
                                    break
                    elif op == "meta":
                        fields = rec.get("fields")
                        if isinstance(fields, dict):
                            if "title" in fields and not data.get("title_locked"):
                                data["title"] = fields["title"]
                            if "last_updated" in fields:
                                data["last_updated"] = fields["last_updated"]
            data["_save_seq"] = max(int(data.get("_save_seq") or 0), max_seq)
        except Exception as e:
            logger.warning(f"Log replay failed (sid={sid}): {e}")
        return max_seq

    # ---- public API ----

    def get_current_session_id(self) -> str:
        self._reload()
        return self.session_data.get("id", "unknown")

    def _read_primary_session_id(self) -> str | None:
        try:
            if os.path.isfile(self.primary_session_file):
                with open(self.primary_session_file, encoding="utf-8") as f:
                    meta = json.load(f)
                if isinstance(meta, dict):
                    sid = str(meta.get("primary_session_id") or "").strip()
                    return sid or None
        except Exception:
            pass
        return self.session_data.get("id")

    def get_session_list(self, limit: int | None = None, offset: int = 0) -> list[dict[str, Any]]:
        """Return list of all sessions (current + history), newest first.

        Uses a per-file mtime metadata cache so repeated list refreshes (sidebar
        polling) do not re-parse large history JSON files. ``limit``/``offset``
        let the sidebar render the newest page first and load older pages on
        demand instead of scanning every history file at startup.
        """
        self._reload()
        sessions: list[dict[str, Any]] = []
        seen_ids: set = set()
        visible_index = 0

        def _file_ts(path: str) -> tuple[str | None, str | None]:
            """Return (created_at, last_updated) ISO strings from filesystem."""
            try:
                st = os.stat(path)
                updated = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
                created_epoch = getattr(st, "st_ctime", st.st_mtime)
                created = datetime.fromtimestamp(created_epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")
                return created, updated
            except Exception:
                return None, None

        def _pick_ts_light(data: dict | None, file_path: str | None = None) -> tuple[str | None, str | None]:
            """Prefer top-level fields + file mtime; do not scan all messages/events."""
            created = (data or {}).get("created_at") if isinstance(data, dict) else None
            updated = (data or {}).get("last_updated") if isinstance(data, dict) else None
            if file_path:
                f_created, f_updated = _file_ts(file_path)
                created = created or f_created
                # File mtime is a reliable activity signal without parsing messages.
                updated = f_updated or updated
            if not created and not updated:
                now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                return now, now
            return created or updated, updated or created

        def _extract_title(messages: list, fallback: str) -> str:
            # Only scan a small prefix — enough for title tags / first user turn.
            head = messages[:12] if isinstance(messages, list) else []
            for m in head:
                if not isinstance(m, dict):
                    continue
                if m.get("role") == "assistant":
                    match = re.search(r"<title>(.*?)</title>", m.get("content", "") or "", re.DOTALL)
                    if match:
                        t = match.group(1).strip()
                        if t:
                            return t
            for m in head:
                if not isinstance(m, dict):
                    continue
                if m.get("role") == "user":
                    content = (m.get("content", "") or "").strip()
                    if content:
                        content = re.sub(r"<image>.*?</image>", "[image]", content, flags=re.DOTALL | re.IGNORECASE)
                        content = re.sub(r"\[File:[^\]]*\]", "", content)
                        content = re.sub(r"\s+", " ", content).strip()
                        if content:
                            return content[:80]
            return fallback

        def _extract_preview(messages: list) -> str:
            if not isinstance(messages, list) or not messages:
                return ""
            # Prefer last few messages only (avoid full reverse scan of huge arrays).
            tail = messages[-8:]
            for m in reversed(tail):
                if not isinstance(m, dict):
                    continue
                if m.get("role") == "user":
                    content = (m.get("content", "") or "").strip()
                    if content:
                        content = re.sub(r"<image>.*?</image>", "[image]", content)
                        return content[:80]
            return ""

        def _meta_from_data(sid: str, data: dict | None, fp: str | None, mtime: float | None) -> dict[str, Any]:
            messages = (data or {}).get("messages", []) if isinstance(data, dict) else []
            title = ""
            if isinstance(data, dict) and data.get("title"):
                title = str(data.get("title") or "").strip()
            if not title:
                title = _extract_title(messages if isinstance(messages, list) else [], sid)
            preview = _extract_preview(messages if isinstance(messages, list) else [])
            created_at, last_updated = _pick_ts_light(data if isinstance(data, dict) else None, fp)
            origin = ""
            if isinstance(data, dict):
                origin = str(data.get("origin") or "").strip()
            return {
                "mtime": mtime,
                "title": title or sid,
                "preview": preview,
                "created_at": created_at,
                "last_updated": last_updated,
                "origin": origin,
            }

        def _get_list_meta(sid: str, fp: str) -> dict[str, Any]:
            try:
                mtime = os.path.getmtime(fp)
            except OSError:
                mtime = None
            cached_meta = self._list_meta_cache.get(sid)
            if cached_meta is not None and mtime is not None and cached_meta.get("mtime") == mtime:
                return cached_meta

            # Prefer full-session LRU (already parsed) without re-reading disk.
            session_cached = self._cache_get(sid)
            data_for_meta: dict | None = None
            if session_cached is not None:
                data_for_meta = session_cached
            else:
                try:
                    with open(fp, encoding="utf-8") as jf:
                        raw = json.load(jf)
                    if isinstance(raw, dict):
                        data_for_meta = raw
                        # Merge incremental log records so title/preview stay
                        # fresh between throttled snapshots.
                        self._replay_log_into(data_for_meta, sid, int(data_for_meta.get("_save_seq") or 0))
                    elif isinstance(raw, list):
                        data_for_meta = {"id": sid, "messages": raw, "title": None}
                except Exception:
                    data_for_meta = None

            meta = _meta_from_data(sid, data_for_meta, fp, mtime)
            self._list_meta_cache[sid] = meta
            return meta

        # Hide scheduled-task sessions from the interactive sidebar. Prefer the
        # persisted origin flag; also drop sids known to scheduled executions
        # (legacy sessions created before origin was written).
        try:
            from opensquad.scheduled_tasks import scheduled_execution_session_ids

            _sched_sids = scheduled_execution_session_ids()
        except Exception:
            _sched_sids = set()

        def _hidden(sid: str, origin: str = "", data: dict | None = None) -> bool:
            o = (origin or "").strip()
            if not o and isinstance(data, dict):
                o = str(data.get("origin") or "").strip()
            if o == "scheduled_task":
                return True
            return bool(sid) and sid in _sched_sids

        # 1. Current session
        curr_id = self.session_data.get("id")
        primary_id = self._read_primary_session_id()
        if curr_id:
            if _hidden(curr_id, data=self.session_data if isinstance(self.session_data, dict) else None):
                seen_ids.add(curr_id)
            else:
                messages = self.session_data.get("messages", [])
                title = self.session_data.get("title") or _extract_title(messages, curr_id)
                preview = _extract_preview(messages)
                created_at, last_updated = _pick_ts_light(self.session_data, self.current_session_file)
                if visible_index >= offset:
                    sessions.append(
                        {
                            "id": curr_id,
                            "title": title,
                            "preview": preview,
                            "current": True,
                            "primary": curr_id == primary_id,
                            "created_at": created_at,
                            "last_updated": last_updated,
                        }
                    )
                visible_index += 1
                seen_ids.add(curr_id)

        # 2. History files
        if os.path.exists(self.history_dir):
            try:
                files = [f for f in os.listdir(self.history_dir) if f.endswith(".json")]
                files.sort(
                    key=lambda x: os.path.getmtime(os.path.join(self.history_dir, x)),
                    reverse=True,
                )
                live_sids: set[str] = set()
                for f in files:
                    sid = f.replace(".json", "")
                    live_sids.add(sid)
                    if sid in seen_ids:
                        continue
                    fp = os.path.join(self.history_dir, f)
                    meta = _get_list_meta(sid, fp)
                    if _hidden(sid, origin=str(meta.get("origin") or "")):
                        seen_ids.add(sid)
                        continue
                    if visible_index >= offset:
                        entry = {
                            "id": sid,
                            "title": meta.get("title") or sid,
                            "preview": meta.get("preview") or "",
                            "current": False,
                            "primary": sid == primary_id,
                            "created_at": meta.get("created_at"),
                            "last_updated": meta.get("last_updated"),
                        }
                        if meta.get("origin"):
                            entry["origin"] = meta.get("origin")
                        sessions.append(entry)
                    visible_index += 1
                    seen_ids.add(sid)
                    if limit is not None and len(sessions) >= limit:
                        break
                # Drop stale list-meta entries only after a full scan; a paged
                # scan must not evict metadata for pages not visited yet.
                if limit is None:
                    stale = [k for k in self._list_meta_cache if k not in live_sids and k != curr_id]
                    for k in stale:
                        self._list_meta_cache.pop(k, None)
            except Exception as e:
                logger.error(f"Error scanning history: {e}")

        return sessions

    def rename_session(self, session_id: str, title: str) -> bool:
        """
        Persist a user-chosen session title.
        Sets title_locked so the running agent will not overwrite it via set_title.
        """
        title = (title or "").strip()
        if not title or not session_id:
            return False
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self._reload(force=True)

        def _write_json(path: str, data: dict) -> bool:
            try:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                tmp = f"{path}.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, path)
                return True
            except Exception as e:
                logger.error(f"Failed to write session title to {path}: {e}")
                return False

        # Current in-memory / current_session.json
        if session_id == self.session_data.get("id"):
            self.session_data["title"] = title
            self.session_data["title_locked"] = True
            self.session_data["last_updated"] = now
            ok = _write_json(self.current_session_file, self.session_data)
            if ok:
                self._current_session_mtime = None
                self._list_meta_cache.pop(session_id, None)
            return ok

        # History file
        file_path = os.path.join(self.history_dir, f"{session_id}.json")
        if not os.path.exists(file_path):
            return False
        try:
            with open(file_path, encoding="utf-8") as f:
                content = json.load(f)
            if isinstance(content, list):
                data = {
                    "id": session_id,
                    "messages": content,
                    "events": [],
                    "title": title,
                    "title_locked": True,
                    "last_updated": now,
                    "created_at": now,
                }
            else:
                data = content if isinstance(content, dict) else {}
                data["id"] = session_id
                data["title"] = title
                data["title_locked"] = True
                data["last_updated"] = now
            if not _write_json(file_path, data):
                return False
            self._cache.pop(session_id, None)
            self._list_meta_cache.pop(session_id, None)
            return True
        except Exception as e:
            logger.error(f"Failed to rename session {session_id}: {e}")
            return False

    def get_session_history(self, session_id: str) -> dict[str, Any] | None:
        """Read-only: get a session's full data by id."""
        # Non-force reload: the mtime + incremental-log tracking in _reload()
        # already picks up agent writes; force=True forced a current_session.json
        # read + full log replay on EVERY history-session request, which made a
        # refresh burst (list + current + N paged) re-parse the same current
        # session once per request.
        self._reload()

        # Current session — return a copy with internal events filtered out
        if session_id == self.session_data.get("id"):
            result = dict(self.session_data)
            result["events"] = self._filter_events(result.get("events", []))
            return result

        # Check cache — already a deep copy, filter events
        cached = self._cache_get(session_id)
        if cached is not None:
            cached["events"] = self._filter_events(cached.get("events", []))
            return cached

        # Read from disk
        file_path = os.path.join(self.history_dir, f"{session_id}.json")
        if not os.path.exists(file_path):
            return None

        try:
            with open(file_path, encoding="utf-8") as f:
                content = json.load(f)

            if isinstance(content, list):
                data = {
                    "id": session_id,
                    "messages": content,
                    "events": [],
                    "archived_messages": [],
                    "archived_events": [],
                    "last_updated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                }
            else:
                data = content
                data["id"] = session_id
                if "events" not in data:
                    data["events"] = []
                if "archived_messages" not in data:
                    data["archived_messages"] = []
                if "archived_events" not in data:
                    data["archived_events"] = []

            # Merge incremental log records newer than the snapshot (the agent
            # throttles full snapshots; the log carries the durable tail).
            self._replay_log_into(data, session_id, int(data.get("_save_seq") or 0))

            # Filter out synthetic internal events before caching
            data["events"] = self._filter_events(data.get("events", []))

            self._cache_put(session_id, data)
            return data
        except Exception as e:
            logger.error(f"Failed to read history session {session_id}: {e}")
            return None

    def delete_session(self, session_id: str) -> bool:
        """Delete a history session file from disk.

        Refuses to delete the agent's currently-active session — callers must
        rotate via the WS ``abandon_current_draft`` command (which mints a new
        sid and either archives the old one to history/ or drops it for an
        empty draft) and then call this method against the now-orphan sid.

        The operation is idempotent: a sid that does not exist anywhere
        (already dropped, no history snapshot) is treated as a successful
        no-op, so the delete-on-current flow does not need to special-case
        the empty-draft branch in the runner.
        """
        self._reload(force=True)
        # Cannot delete the current session — agent owns current_session.json
        if session_id == self.session_data.get("id"):
            return False
        # Remove from cache
        self._cache.pop(session_id, None)
        self._list_meta_cache.pop(session_id, None)
        file_path = os.path.join(self.history_dir, f"{session_id}.json")
        log_path = os.path.join(self.history_dir, f"{session_id}.json.log")
        any_existed = False
        for p in (file_path, log_path):
            existed = os.path.exists(p)
            any_existed = any_existed or existed
            if not existed:
                continue
            try:
                os.remove(p)
                logger.info(f"Deleted session file: {p}")
            except Exception as e:
                logger.error(f"Failed to delete session {session_id}: {e}")
                return False
        # Idempotent: caller asked to delete and there is nothing left to
        # remove (runner already dropped the empty draft). Treat as success.
        if not any_existed:
            logger.info(f"Delete session {session_id}: no files present, treating as idempotent success")
        return True

    # ---- async interface (thin wrappers — uniform API for all reader types) ----

    def search_sessions(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """Fuzzy search across user input and agent non-tool text messages.

        Iterates every session (current + history) for this agent and returns
        one hit per matching session, with up to 3 short context snippets
        around the matched substring. Snippets are HTML/markdown-stripped and
        truncated to keep the response small for the sidebar search modal.

        Substring + token-order match (case-insensitive) keeps behavior
        predictable across Chinese / English queries without pulling in a
        full-text search dependency. Heavy work is bounded by ``limit`` and
        the modal only fires once per debounced keystroke.
        """
        if not query or not query.strip():
            return []
        q_norm = query.strip().casefold()
        tokens_cf = [t for t in re.split(r"\s+", query.strip()) if t]

        self._reload()

        try:
            from opensquad.scheduled_tasks import scheduled_execution_session_ids

            _sched_sids = scheduled_execution_session_ids()
        except Exception:
            _sched_sids = set()

        results: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        def _hidden(sid: str, origin: str = "", data: dict | None = None) -> bool:
            o = (origin or "").strip()
            if not o and isinstance(data, dict):
                o = str(data.get("origin") or "").strip()
            if o == "scheduled_task":
                return True
            return bool(sid) and sid in _sched_sids

        def _strip(text: str) -> str:
            if not text:
                return ""
            t = re.sub(r"<image>.*?</image>", "[image]", text, flags=re.IGNORECASE | re.DOTALL)
            t = re.sub(r"\[File:[^\]]*\]", "", t)
            return re.sub(r"\s+", " ", t).strip()

        def _build_snippet(text: str, max_len: int = 90) -> str:
            t = _strip(text)
            if not t:
                return ""
            return t if len(t) <= max_len else t[: max_len - 1] + "…"

        def _message_content(m: Any) -> str:
            if not isinstance(m, dict):
                return ""
            c = m.get("content")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                parts: list[str] = []
                for part in c:
                    if isinstance(part, dict):
                        text = part.get("text")
                        if isinstance(text, str):
                            parts.append(text)
                    elif isinstance(part, str):
                        parts.append(part)
                return "".join(parts)
            return ""

        def _matches(text: str) -> bool:
            if not text:
                return False
            t = text.casefold()
            if q_norm in t:
                return True
            if not tokens_cf:
                return False
            cursor = 0
            for tok in tokens_cf:
                idx = t.find(tok, cursor)
                if idx < 0:
                    return False
                cursor = idx + len(tok)
            return True

        def _extract_matches(session: dict[str, Any]) -> list[dict[str, Any]]:
            if not isinstance(session, dict):
                return []
            out: list[dict[str, Any]] = []
            for key in ("messages", "archived_messages"):
                msgs = session.get(key) or []
                if not isinstance(msgs, list):
                    continue
                for m in msgs:
                    if not isinstance(m, dict):
                        continue
                    role = m.get("role")
                    if role not in ("user", "assistant"):
                        continue
                    content = _message_content(m)
                    if not _matches(content):
                        continue
                    if role == "assistant" and m.get("type") in ("context_summary", "system_prompt"):
                        continue
                    out.append(
                        {
                            "role": role,
                            "snippet": _build_snippet(content),
                            "timestamp": m.get("timestamp"),
                        }
                    )
                    if len(out) >= 3:
                        return out
            return out

        def _file_mtime(path: str) -> float:
            try:
                return os.path.getmtime(path)
            except OSError:
                return 0.0

        def _consider(sid: str, data: dict | None, source_path: str | None) -> None:
            if not sid or sid in seen_ids:
                return
            if not isinstance(data, dict):
                return
            origin = str(data.get("origin") or "")
            if _hidden(sid, origin=origin, data=data):
                seen_ids.add(sid)
                return
            matches = _extract_matches(data)
            if not matches:
                return
            seen_ids.add(sid)
            title = (data.get("title") or "").strip()
            if not title:
                head = (data.get("messages") or [])[:12]
                for m in head:
                    if isinstance(m, dict) and m.get("role") == "user":
                        c = _strip(_message_content(m))
                        if c:
                            title = c[:80]
                            break
            if not title:
                title = sid
            last_updated = data.get("last_updated")
            created_at = data.get("created_at")
            if source_path:
                f_mtime = _file_mtime(source_path)
                if f_mtime:
                    last_updated = datetime.fromtimestamp(f_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
            if not last_updated and not created_at and source_path:
                f_mtime = _file_mtime(source_path)
                if f_mtime:
                    last_updated = datetime.fromtimestamp(f_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
            results.append(
                {
                    "id": sid,
                    "title": title,
                    "matches": matches,
                    "last_updated": last_updated,
                    "created_at": created_at,
                }
            )

        curr_id = self.session_data.get("id")
        if curr_id:
            _consider(curr_id, self.session_data, self.current_session_file)

        if os.path.exists(self.history_dir):
            try:
                files = [f for f in os.listdir(self.history_dir) if f.endswith(".json") and not f.endswith(".json.log")]
            except Exception:
                files = []
            files.sort(
                key=lambda x: _file_mtime(os.path.join(self.history_dir, x)),
                reverse=True,
            )
            for fname in files:
                if len(results) >= limit:
                    break
                sid = fname[: -len(".json")]
                fp = os.path.join(self.history_dir, fname)
                data = self._cache_get(sid)
                if data is None:
                    try:
                        with open(fp, encoding="utf-8") as f:
                            raw = json.load(f)
                        if isinstance(raw, dict):
                            data = raw
                        elif isinstance(raw, list):
                            data = {
                                "id": sid,
                                "messages": raw,
                                "events": [],
                                "archived_messages": [],
                                "archived_events": [],
                            }
                        else:
                            continue
                        if not isinstance(data, dict):
                            continue
                        self._replay_log_into(data, sid, int(data.get("_save_seq") or 0))
                    except Exception:
                        continue
                _consider(sid, data, fp)

        def _ts(entry: dict[str, Any]) -> str:
            return str(entry.get("last_updated") or entry.get("created_at") or "")

        results.sort(key=_ts, reverse=True)
        return results[:limit]

    async def async_get_session_list(self, limit: int | None = None, offset: int = 0) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.get_session_list, limit, offset)

    async def async_get_current_session_id(self) -> str:
        return await asyncio.to_thread(self.get_current_session_id)

    async def async_get_session_history(self, session_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self.get_session_history, session_id)

    async def async_get_session_history_paged(
        self, session_id: str, offset: int = 0, limit: int = 50
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(self.get_session_history_paged, session_id, offset, limit)

    async def async_delete_session(self, session_id: str) -> bool:
        return await asyncio.to_thread(self.delete_session, session_id)

    async def async_rename_session(self, session_id: str, title: str) -> bool:
        return await asyncio.to_thread(self.rename_session, session_id, title)

    async def async_search_sessions(
        self,
        query: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Fuzzy search across user input and agent non-tool text messages."""
        return await asyncio.to_thread(self.search_sessions, query, limit)

    def get_session_history_paged(
        self,
        session_id: str,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any] | None:
        """
        Get a session's data with pagination (from the end, backwards).

        offset=0 means the most recent messages.
        Returns: {
            id, messages, events, total_messages, total_events, has_more,
            last_updated, created_at
        }
        """
        full = self.get_session_history(session_id)
        if full is None:
            return None

        all_messages = full.get("messages", [])
        all_events = full.get("events", [])
        total_messages = len(all_messages)
        total_events = len(all_events)

        # Scheduled-task execution sessions are dedicated per-run panes: the
        # user must be able to review the FULL output + workflow of a run, not
        # just the newest page. Returning only the latest `limit` messages (and
        # the events in that window) made every new output slide the window and
        # hide/overwrite the earlier output + tool-flow. For these sessions,
        # offset=0 returns the complete set so history is never lost from view.
        if offset == 0 and (full.get("origin") or "") == "scheduled_task":
            return {
                "id": session_id,
                "title": full.get("title"),
                "model_card": full.get("model_card"),
                "messages": list(all_messages),
                "events": list(all_events),
                "archived_messages": full.get("archived_messages") or [],
                "archived_events": full.get("archived_events") or [],
                "total_messages": total_messages,
                "total_events": total_events,
                "has_more": False,
                "last_updated": full.get("last_updated"),
                "created_at": full.get("created_at"),
            }

        # Slice messages from end: offset=0 → last `limit` messages
        if total_messages == 0:
            paged_messages = []
        else:
            end_idx = total_messages - offset
            start_idx = max(0, end_idx - limit)
            paged_messages = [] if end_idx <= 0 else all_messages[start_idx:end_idx]

        # Slice events by timestamp range of the paged messages.
        # Proportional slicing is fundamentally wrong: events are not
        # uniformly distributed across messages (a user msg has 0 events,
        # while an assistant msg with complex tool usage may have 20+).
        # Using timestamp-based slicing ensures events always belong to
        # the same time window as the messages in this page.
        #
        # Fallback: if no events match the timestamp window but there ARE
        # events in the full set, include all events within a wider window
        # (±60s) and also events whose round_id falls within range.
        if total_events > 0 and len(paged_messages) > 0:
            try:
                first_ts = paged_messages[0].get("timestamp")
                last_ts = paged_messages[-1].get("timestamp")

                # Collect round_ids from paged messages for secondary matching
                paged_round_ids = set()
                for m in paged_messages:
                    rid = m.get("round_id")
                    if rid is not None:
                        paged_round_ids.add(rid)

                if first_ts and last_ts:
                    from datetime import timedelta

                    def _parse_ts(ts_str: str) -> datetime:
                        s = ts_str.replace("Z", "+00:00")
                        try:
                            return datetime.fromisoformat(s)
                        except ValueError:
                            return datetime.fromisoformat(s[:26])

                    f = _parse_ts(first_ts)
                    l = _parse_ts(last_ts)
                    # Primary window: ±30s around message range.
                    # Latest page (offset=0): also keep trailing in-progress events
                    # after the last message (tool_call_delta / thoughts mid-turn).
                    t_min = f - timedelta(seconds=30)
                    if offset == 0:
                        from datetime import timezone as _tz

                        now_utc = datetime.now(_tz.utc)
                        # Prefer timezone-aware comparison when message ts has tzinfo
                        if l.tzinfo is not None:
                            t_max = max(l + timedelta(seconds=30), now_utc)
                        else:
                            t_max = max(l + timedelta(seconds=30), datetime.utcnow())
                    else:
                        t_max = l + timedelta(seconds=30)
                    paged_events = []
                    for evt in all_events:
                        evt_ts = evt.get("timestamp")
                        if evt_ts:
                            try:
                                if t_min <= _parse_ts(evt_ts) <= t_max:
                                    paged_events.append(evt)
                                    continue
                            except Exception:
                                pass
                        # Fallback: match by round_id if timestamp didn't match
                        evt_round = evt.get("round_id")
                        if evt_round is not None and evt_round in paged_round_ids:
                            paged_events.append(evt)
                else:
                    # No timestamps on messages; fall back to round_id matching
                    paged_events = [
                        evt
                        for evt in all_events
                        if evt.get("round_id") is not None and evt["round_id"] in paged_round_ids
                    ]
                    if not paged_events and all_events:
                        # Last resort: return ALL events attached to the last N messages
                        # (proportional slice proportional to page / total ratio)
                        ratio = len(paged_messages) / max(total_messages, 1)
                        keep = max(1, int(total_events * ratio))
                        paged_events = all_events[-keep:]
            except Exception:
                paged_events = []
        else:
            paged_events = []

        # Latest page: once we have any matched event, keep every subsequent
        # event through end-of-file. Timestamp/round_id windows otherwise drop
        # mid-turn tool_call_delta / thoughts that lack matching metadata.
        if offset == 0 and total_events > 0 and paged_events:
            matched_ids = {id(e) for e in paged_events}
            first_idx = next(
                (i for i, e in enumerate(all_events) if id(e) in matched_ids),
                None,
            )
            if first_idx is not None:
                paged_events = all_events[first_idx:]
        elif offset == 0 and total_events > 0 and len(paged_messages) >= total_messages:
            # Full message set on first page — return all events.
            paged_events = list(all_events)

        # Cap the first-page event payload. Long sessions accumulate thousands
        # of events (tool_call_delta / thought / tool_result …) — returning all
        # of them on offset=0 balloons the response to 1.5MB+ and stalls the
        # frontend hydrate (client aborts after 10s → "加载中…" forever). Keep
        # the newest events, which pair with the newest messages on this page.
        if offset == 0 and len(paged_events) > _MAX_FIRST_PAGE_EVENTS:
            paged_events = paged_events[-_MAX_FIRST_PAGE_EVENTS:]

        has_more = (total_messages - offset - limit) > 0

        # Archived content only on the first page — later pages would duplicate
        # a potentially huge archived_* payload on every scroll-up request.
        if offset == 0:
            archived_messages = full.get("archived_messages") or []
            archived_events = full.get("archived_events") or []
        else:
            archived_messages = []
            archived_events = []

        return {
            "id": session_id,
            "title": full.get("title"),
            "model_card": full.get("model_card"),
            "messages": paged_messages,
            "events": paged_events,
            "archived_messages": archived_messages,
            "archived_events": archived_events,
            "total_messages": total_messages,
            "total_events": total_events,
            "has_more": has_more,
            "last_updated": full.get("last_updated"),
            "created_at": full.get("created_at"),
        }


# ============================================================
# WS tunnel hook — injected from main.py after launcher_handler is ready.
# Allows remote session reads to travel through the WS admin tunnel instead
# of plain HTTP (which would fail when Gateway is on cloud and Launcher is
# at home without a reverse-proxy).
# ============================================================
_ws_rpc = None  # async (node_id, method, path, body=None) -> dict
_ws_node_id_func = None  # () -> str | None  (returns None when no WS peer)


def set_ws_handler(rpc_func, node_id_func):
    """Inject WS RPC capability.  Called from main.py at startup."""
    global _ws_rpc, _ws_node_id_func
    _ws_rpc = rpc_func
    _ws_node_id_func = node_id_func
    logger.info("[agent_sessions] WS handler injected")


# ============================================================
# Global registry: agent_id -> AgentSessionReader (with lock)
# ============================================================
_readers: dict[str, AgentSessionReader] = {}
_lock = threading.Lock()
_agent_id_map: dict | None = None


def _ensure_agent_id_map() -> dict:
    global _agent_id_map
    if _agent_id_map is None:
        _agent_id_map = _build_agent_id_map()
    return _agent_id_map


def refresh_agent_id_map():
    """Force re-scan of agents/ directory (call when agents are added/removed)."""
    global _agent_id_map
    _agent_id_map = _build_agent_id_map()


def invalidate_reader(agent_id: str):
    """Drop cached session snapshot so HTTP reads see fresh disk data."""
    with _lock:
        reader = _readers.get(agent_id)
        if reader is not None and hasattr(reader, "invalidate_current_session_cache"):
            reader.invalidate_current_session_cache()


def get_reader(agent_id: str):
    """
    Get or create a session reader for the given agent_id.

    Strategy:
      1. Try local disk (agents/ directory on this machine).
      2. If not found locally, fall back to _RemoteSessionReader which proxies
         over HTTP to the configured launcher_url.

    This handles all deployment topologies:
      - Same machine: local disk reader.
      - frp/SSH reverse tunnel: launcher_url is localhost on cloud side, but
        local agent files don't exist → remote reader via tunnel.
      - Direct public IP: launcher_url is remote → remote reader.
    """
    with _lock:
        if agent_id in _readers:
            return _readers[agent_id]

        # ── Try local disk ───────────────────────────────────────────────
        id_map = _ensure_agent_id_map()
        agent_dir = id_map.get(agent_id)
        if not agent_dir:
            refresh_agent_id_map()
            id_map = _ensure_agent_id_map()
            agent_dir = id_map.get(agent_id)

        if agent_dir:
            data_dir = os.path.join(agent_dir, "data")
            save_dir = os.path.join(data_dir, "sessions")
            history_dir = os.path.join(data_dir, "history")
            os.makedirs(save_dir, exist_ok=True)
            os.makedirs(history_dir, exist_ok=True)
            reader = AgentSessionReader(save_dir, history_dir)
            _readers[agent_id] = reader
            logger.info(f"Created local AgentSessionReader for {agent_id}: {save_dir}")
            return reader

    # ── Local not found: try remote via launcher_url ─────────────────────
    try:
        launcher = syscfg.launcher_url()
    except Exception:
        launcher = ""
    if launcher:
        logger.info(f"Agent {agent_id} not found locally, using remote reader via {launcher}")
        return _RemoteSessionReader(agent_id)

    logger.warning(f"Agent not found locally and no launcher_url configured: {agent_id}")
    return None


# ============================================================
# Remote session proxy — used when agent files are not local
# (e.g. Gateway on cloud, Launcher+Agents at home via frp/VPN)
# ============================================================


class _RemoteSessionReader:
    """
    When Launcher runs on a remote machine, proxy all session operations
    over HTTP to that Launcher's /api/sessions/* endpoints.

    This class exposes the same public API as AgentSessionReader so callers
    in routes.py can use it transparently.
    """

    def __init__(self, agent_id: str):
        self._agent_id = agent_id
        self._base = f"{syscfg.launcher_url()}/api/sessions/{agent_id}"

    def _get(self, path: str, params: dict | None = None):
        import httpx

        with httpx.Client(timeout=10) as c:
            r = c.get(f"{self._base}{path}", params=params or {})
            r.raise_for_status()
            return r.json()

    def _post(self, path: str):
        import httpx

        with httpx.Client(timeout=10) as c:
            r = c.post(f"{self._base}{path}")
            r.raise_for_status()
            return r.json()

    def get_session_list(self, limit: int | None = None, offset: int = 0):
        params: dict[str, int] = {}
        if limit is not None:
            params["limit"] = limit
            params["offset"] = offset
        return self._get("/list", params).get("sessions", [])

    def get_current_session_id(self) -> str | None:
        return self._get("/list").get("current_session_id")

    def get_session_history(self, session_id: str) -> dict | None:
        try:
            return self._get(f"/{session_id}").get("session")
        except Exception as e:
            logger.error(f"Remote get_session_history failed for {session_id}: {e}")
            return None

    def get_session_history_paged(
        self,
        session_id: str,
        offset: int = 0,
        limit: int = 50,
    ) -> dict | None:
        try:
            return self._get(f"/{session_id}/paged", {"offset": offset, "limit": limit}).get("session")
        except Exception as e:
            logger.error(f"Remote get_session_history_paged failed for {session_id}: {e}")
            return None

    def delete_session(self, session_id: str) -> bool:
        try:
            return self._post(f"/{session_id}/delete").get("ok", False)
        except Exception as e:
            logger.error(f"Remote delete_session failed for {session_id}: {e}")
            return False

    def rename_session(self, session_id: str, title: str) -> bool:
        try:
            import httpx

            with httpx.Client(timeout=10) as c:
                r = c.post(f"{self._base}/{session_id}/rename", json={"title": title})
                r.raise_for_status()
                return r.json().get("ok", False)
        except Exception as e:
            logger.error(f"Remote rename_session failed for {session_id}: {e}")
            return False

    def search_sessions(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        try:
            import httpx

            with httpx.Client(timeout=15) as c:
                r = c.get(
                    f"{self._base}/search",
                    params={"q": query, "limit": limit},
                )
                r.raise_for_status()
                return r.json().get("results", []) or []
        except Exception as e:
            logger.error(f"Remote search_sessions failed: {e}")
            return []

    # ---- async interface (wraps sync HTTP calls via to_thread) ----

    async def async_get_session_list(self, limit: int | None = None, offset: int = 0):
        return await asyncio.to_thread(self.get_session_list, limit, offset)

    async def async_get_current_session_id(self) -> str | None:
        return await asyncio.to_thread(self.get_current_session_id)

    async def async_get_session_history(self, session_id: str) -> dict | None:
        return await asyncio.to_thread(self.get_session_history, session_id)

    async def async_get_session_history_paged(self, session_id: str, offset: int = 0, limit: int = 50) -> dict | None:
        return await asyncio.to_thread(self.get_session_history_paged, session_id, offset, limit)

    async def async_delete_session(self, session_id: str) -> bool:
        return await asyncio.to_thread(self.delete_session, session_id)

    async def async_rename_session(self, session_id: str, title: str) -> bool:
        return await asyncio.to_thread(self.rename_session, session_id, title)

    async def async_search_sessions(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.search_sessions, query, limit)


# ============================================================
# WS session reader — routes all calls through the Launcher admin WS tunnel.
# Used when Gateway runs on cloud and Launcher runs at home: no open inbound
# port needed, the already-established WS tunnel carries the RPC.
# ============================================================


class _WsSessionReader:
    """
    Async session reader that proxies requests through the Launcher WS tunnel.

    The Launcher already exposes /api/sessions/* HTTP endpoints; we simply
    forward them as admin_request RPC messages over the existing WS connection.
    """

    def __init__(self, agent_id: str, rpc, node_id: str):
        self._agent_id = agent_id
        self._rpc = rpc  # async (node_id, method, path, body=None) -> dict
        self._node_id = node_id
        self._base = f"/api/sessions/{agent_id}"

    async def _call(self, method: str, path: str, body=None) -> dict:
        return await self._rpc(self._node_id, method, path, body)

    async def async_get_session_list(self, limit: int | None = None, offset: int = 0) -> list[dict[str, Any]]:
        path = f"{self._base}/list"
        if limit is not None:
            from urllib.parse import urlencode

            path += f"?{urlencode({'limit': limit, 'offset': offset})}"
        result = await self._call("GET", path)
        return result.get("sessions", [])

    async def async_get_current_session_id(self) -> str | None:
        result = await self._call("GET", f"{self._base}/list")
        return result.get("current_session_id")

    async def async_get_session_history(self, session_id: str) -> dict[str, Any] | None:
        try:
            result = await self._call("GET", f"{self._base}/{session_id}")
            return result.get("session")
        except Exception as e:
            logger.error(f"WS get_session_history failed for {session_id}: {e}")
            return None

    async def async_get_session_history_paged(
        self,
        session_id: str,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any] | None:
        try:
            from urllib.parse import urlencode

            qs = urlencode({"offset": offset, "limit": limit})
            result = await self._call("GET", f"{self._base}/{session_id}/paged?{qs}")
            return result.get("session")
        except Exception as e:
            logger.error(f"WS get_session_history_paged failed for {session_id}: {e}")
            return None

    async def async_delete_session(self, session_id: str) -> bool:
        try:
            result = await self._call("POST", f"{self._base}/{session_id}/delete")
            return result.get("ok", False)
        except Exception as e:
            logger.error(f"WS delete_session failed for {session_id}: {e}")
            return False

    async def async_rename_session(self, session_id: str, title: str) -> bool:
        try:
            result = await self._call("POST", f"{self._base}/{session_id}/rename", {"title": title})
            return result.get("ok", False)
        except Exception as e:
            logger.error(f"WS rename_session failed for {session_id}: {e}")
            return False

    async def async_search_sessions(
        self,
        query: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        try:
            from urllib.parse import urlencode

            qs = urlencode({"q": query, "limit": limit})
            result = await self._call("GET", f"{self._base}/search?{qs}")
            return result.get("results", []) or []
        except Exception as e:
            logger.error(f"WS search_sessions failed: {e}")
            return []


# ============================================================
# async_get_reader — async-aware factory
# Priority: local disk  →  WS tunnel  →  HTTP fallback
# ============================================================


async def async_get_reader(agent_id: str):
    """
    Async-aware session reader factory.

    1. Local disk (same machine)    → AgentSessionReader (has async wrappers)
    2. WS tunnel (cloud + home)     → _WsSessionReader   (native async)
    3. HTTP fallback (frp / local)  → _RemoteSessionReader (has async wrappers)
    """
    with _lock:
        if agent_id in _readers:
            return _readers[agent_id]

        id_map = _ensure_agent_id_map()
        agent_dir = id_map.get(agent_id)
        if not agent_dir:
            refresh_agent_id_map()
            id_map = _ensure_agent_id_map()
            agent_dir = id_map.get(agent_id)

        if agent_dir:
            data_dir = os.path.join(agent_dir, "data")
            save_dir = os.path.join(data_dir, "sessions")
            history_dir = os.path.join(data_dir, "history")
            os.makedirs(save_dir, exist_ok=True)
            os.makedirs(history_dir, exist_ok=True)
            reader = AgentSessionReader(save_dir, history_dir)
            _readers[agent_id] = reader
            logger.info(f"Created local AgentSessionReader for {agent_id}: {save_dir}")
            return reader

    # Not found locally — prefer WS tunnel
    if _ws_rpc and _ws_node_id_func:
        node_id = _ws_node_id_func()
        if node_id:
            logger.info(f"Agent {agent_id} not found locally; using WS tunnel reader (node={node_id!r})")
            return _WsSessionReader(agent_id, _ws_rpc, node_id)

    # HTTP fallback (same-machine frp / explicit launcher_url)
    try:
        launcher = syscfg.launcher_url()
    except Exception:
        launcher = ""
    if launcher:
        logger.info(f"Agent {agent_id} not found locally; using HTTP remote reader via {launcher}")
        return _RemoteSessionReader(agent_id)

    logger.warning(f"Agent {agent_id} not found locally and no remote available")
    return None
