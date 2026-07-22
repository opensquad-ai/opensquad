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
        # Load current session from disk
        self._reload()

    # ---- disk reload ----

    _RELOAD_COUNTER = 0  # class-level counter to force periodic re-reads

    def _reload(self, force: bool = False):
        """Reload current session from disk (read-only).

        Args:
            force: If True, read from disk unconditionally (skips mtime cache).
                   Public API methods (get_session_history*) always pass force=True
                   to prevent stale reads on Windows where mtime granularity is low.
        """
        if not os.path.exists(self.current_session_file):
            return
        try:
            mtime = os.path.getmtime(self.current_session_file)
            if not force and mtime == self._current_session_mtime:
                return  # file unchanged, skip disk read
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
            self._current_session_mtime = mtime
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
        fp = os.path.join(self.history_dir, f"{sid}.json")
        try:
            return os.path.getmtime(fp) if os.path.exists(fp) else None
        except OSError:
            return None

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
        """
        skip_names = ("system__event_pipeline", "system.event_pipeline")
        return [e for e in events if e.get("name") not in skip_names]

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

    def get_session_list(self) -> list[dict[str, Any]]:
        """Return list of all sessions (current + history), newest first.

        Uses a per-file mtime metadata cache so repeated list refreshes (sidebar
        polling) do not re-parse large history JSON files.
        """
        # Always force-reload: after new_session the mtime cache can lag (esp. Windows)
        # and the sidebar would keep showing the previous current session.
        self._reload(force=True)
        sessions: list[dict[str, Any]] = []
        seen_ids: set = set()

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
            return {
                "mtime": mtime,
                "title": title or sid,
                "preview": preview,
                "created_at": created_at,
                "last_updated": last_updated,
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
                    elif isinstance(raw, list):
                        data_for_meta = {"id": sid, "messages": raw, "title": None}
                except Exception:
                    data_for_meta = None

            meta = _meta_from_data(sid, data_for_meta, fp, mtime)
            self._list_meta_cache[sid] = meta
            return meta

        # 1. Current session
        curr_id = self.session_data.get("id")
        primary_id = self._read_primary_session_id()
        if curr_id:
            messages = self.session_data.get("messages", [])
            title = self.session_data.get("title") or _extract_title(messages, curr_id)
            preview = _extract_preview(messages)
            created_at, last_updated = _pick_ts_light(self.session_data, self.current_session_file)
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
                    sessions.append(
                        {
                            "id": sid,
                            "title": meta.get("title") or sid,
                            "preview": meta.get("preview") or "",
                            "current": False,
                            "primary": sid == primary_id,
                            "created_at": meta.get("created_at"),
                            "last_updated": meta.get("last_updated"),
                        }
                    )
                    seen_ids.add(sid)
                # Drop stale list-meta entries for deleted history files
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
        self._reload(force=True)

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

            # Filter out synthetic internal events before caching
            data["events"] = self._filter_events(data.get("events", []))

            self._cache_put(session_id, data)
            return data
        except Exception as e:
            logger.error(f"Failed to read history session {session_id}: {e}")
            return None

    def delete_session(self, session_id: str) -> bool:
        """Delete a history session file from disk.

        Cannot delete the agent's current active session (empty or not).
        Empty current chats are abandoned via Agent ``new_session`` (which
        archives them into history/), then deleted as history files.
        """
        self._reload(force=True)
        # Cannot delete the current session — agent owns current_session.json
        if session_id == self.session_data.get("id"):
            return False
        # Remove from cache
        self._cache.pop(session_id, None)
        self._list_meta_cache.pop(session_id, None)
        file_path = os.path.join(self.history_dir, f"{session_id}.json")
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"Deleted session file: {file_path}")
                return True
            except Exception as e:
                logger.error(f"Failed to delete session {session_id}: {e}")
                return False
        return False

    # ---- async interface (thin wrappers — uniform API for all reader types) ----

    async def async_get_session_list(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.get_session_list)

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

    def get_session_list(self):
        return self._get("/list").get("sessions", [])

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

    # ---- async interface (wraps sync HTTP calls via to_thread) ----

    async def async_get_session_list(self):
        return await asyncio.to_thread(self.get_session_list)

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

    async def async_get_session_list(self) -> list[dict[str, Any]]:
        result = await self._call("GET", f"{self._base}/list")
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
