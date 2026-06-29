"""
Agent Session disk reader - read-only access to Agent session files on disk.

Ports Bridge's _get_session_manager_for() logic so Gateway can directly
read Agent session files without going through Bridge.

Each Agent stores sessions in:
  agents/{name}/data/sessions/current_session.json
  agents/{name}/data/history/{session_id}.json
"""
import asyncio
import json
import os
import re
import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
import logging
import copy

logger = logging.getLogger(__name__)

from opensquad.system_config import syscfg

# Active workspace directory
ROOT_DIR = syscfg.get_workspace()


def _build_agent_id_map() -> dict:
    """
    Scan agents/ directory to build agent_id -> agent_dir mapping.
    """
    agents_root = syscfg.workspace_agents_dir()
    id_map = {}
    if os.path.isdir(agents_root):
        for name in os.listdir(agents_root):
            cfg_path = os.path.join(agents_root, name, "config.json")
            if os.path.isfile(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    aid = cfg.get("agent_id", name)
                    id_map[aid] = os.path.join(agents_root, name)
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
        self.session_data: Dict[str, Any] = {
            "id": None, "title": None, "messages": [], "events": [],
            "last_updated": None, "created_at": None,
        }
        # LRU cache: sid -> {"data": ..., "mtime": ...}
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._cache_max_size = 10
        # mtime cache for current_session.json — avoid re-parsing unchanged large files
        self._current_session_mtime: Optional[float] = None
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
            with open(self.current_session_file, "r", encoding="utf-8") as f:
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
        self._reload()

    # ---- LRU cache ----

    def _get_history_file_mtime(self, sid: str) -> Optional[float]:
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

    def _cache_get(self, sid: str) -> Optional[dict]:
        if sid not in self._cache:
            return None
        entry = self._cache[sid]
        disk_mtime = self._get_history_file_mtime(sid)
        cached_mtime = entry.get("mtime")
        if disk_mtime is not None and cached_mtime is not None and disk_mtime != cached_mtime:
            del self._cache[sid]
            return None
        self._cache.move_to_end(sid)
        return copy.deepcopy(entry["data"])

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

    def get_session_list(self) -> List[Dict[str, Any]]:
        """Return list of all sessions (current + history), newest first."""
        self._reload()
        sessions: List[Dict[str, Any]] = []
        seen_ids: set = set()

        def _extract_title(messages: list, fallback: str) -> str:
            for m in messages:
                if m.get("role") == "assistant":
                    match = re.search(r"<title>(.*?)</title>", m.get("content", ""))
                    if match:
                        return match.group(1).strip()
            return fallback

        def _extract_preview(messages: list) -> str:
            for m in reversed(messages):
                if m.get("role") == "user":
                    content = m.get("content", "").strip()
                    if content:
                        content = re.sub(r"<image>.*?</image>", "[image]", content)
                        return content[:80]
            return ""

        # 1. Current session
        curr_id = self.session_data.get("id")
        if curr_id:
            messages = self.session_data.get("messages", [])
            title = self.session_data.get("title") or _extract_title(messages, curr_id)
            preview = _extract_preview(messages)
            sessions.append({
                "id": curr_id,
                "title": title,
                "preview": preview,
                "current": True,
            })
            seen_ids.add(curr_id)

        # 2. History files
        if os.path.exists(self.history_dir):
            try:
                files = [f for f in os.listdir(self.history_dir) if f.endswith(".json")]
                files.sort(
                    key=lambda x: os.path.getmtime(os.path.join(self.history_dir, x)),
                    reverse=True,
                )
                for f in files:
                    sid = f.replace(".json", "")
                    if sid in seen_ids:
                        continue
                    title = sid
                    preview = ""
                    cached = self._cache_get(sid)
                    if cached is not None:
                        messages = cached.get("messages", [])
                        title = cached.get("title") or _extract_title(messages, sid)
                        preview = _extract_preview(messages)
                    else:
                        try:
                            fp = os.path.join(self.history_dir, f)
                            with open(fp, "r", encoding="utf-8") as jf:
                                content = jf.read()
                                try:
                                    data = json.loads(content)
                                    if isinstance(data, dict) and data.get("title"):
                                        title = data.get("title")
                                except Exception:
                                    match = re.search(r"<title>(.*?)</title>", content)
                                    if match:
                                        title = match.group(1).strip()
                        except Exception:
                            pass
                    sessions.append({
                        "id": sid,
                        "title": title,
                        "preview": preview,
                        "current": False,
                    })
                    seen_ids.add(sid)
            except Exception as e:
                logger.error(f"Error scanning history: {e}")

        return sessions

    def get_session_history(self, session_id: str) -> Optional[Dict[str, Any]]:
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
            with open(file_path, "r", encoding="utf-8") as f:
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
        """Delete a history session file from disk."""
        # Cannot delete the current session
        if session_id == self.session_data.get("id"):
            return False
        # Remove from cache
        self._cache.pop(session_id, None)
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

    async def async_get_session_list(self) -> List[Dict[str, Any]]:
        return self.get_session_list()

    async def async_get_current_session_id(self) -> str:
        return self.get_current_session_id()

    async def async_get_session_history(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self.get_session_history(session_id)

    async def async_get_session_history_paged(
        self, session_id: str, offset: int = 0, limit: int = 50
    ) -> Optional[Dict[str, Any]]:
        return self.get_session_history_paged(session_id, offset, limit)

    async def async_delete_session(self, session_id: str) -> bool:
        return self.delete_session(session_id)

    def get_session_history_paged(
        self,
        session_id: str,
        offset: int = 0,
        limit: int = 50,
    ) -> Optional[Dict[str, Any]]:
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
            if end_idx <= 0:
                paged_messages = []
            else:
                paged_messages = all_messages[start_idx:end_idx]

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
                    # Primary window: ±30s around message range
                    t_min = f - timedelta(seconds=30)
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
                        evt for evt in all_events
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

        has_more = (total_messages - offset - limit) > 0

        return {
            "id": session_id,
            "messages": paged_messages,
            "events": paged_events,
            # Archived content is returned in full on every page (no
            # pagination) so the frontend can render the collapsed
            # "已归档" section regardless of which page is loaded.
            "archived_messages": full.get("archived_messages") or [],
            "archived_events": full.get("archived_events") or [],
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
_ws_rpc = None           # async (node_id, method, path, body=None) -> dict
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
_readers: Dict[str, AgentSessionReader] = {}
_lock = threading.Lock()
_agent_id_map: Optional[dict] = None


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

    def _get(self, path: str, params: dict = None):
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

    def get_current_session_id(self) -> Optional[str]:
        return self._get("/list").get("current_session_id")

    def get_session_history(self, session_id: str) -> Optional[dict]:
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
    ) -> Optional[dict]:
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

    # ---- async interface (wraps sync HTTP calls via to_thread) ----

    async def async_get_session_list(self):
        return await asyncio.to_thread(self.get_session_list)

    async def async_get_current_session_id(self) -> Optional[str]:
        return await asyncio.to_thread(self.get_current_session_id)

    async def async_get_session_history(self, session_id: str) -> Optional[dict]:
        return await asyncio.to_thread(self.get_session_history, session_id)

    async def async_get_session_history_paged(
        self, session_id: str, offset: int = 0, limit: int = 50
    ) -> Optional[dict]:
        return await asyncio.to_thread(self.get_session_history_paged, session_id, offset, limit)

    async def async_delete_session(self, session_id: str) -> bool:
        return await asyncio.to_thread(self.delete_session, session_id)


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
        self._rpc = rpc        # async (node_id, method, path, body=None) -> dict
        self._node_id = node_id
        self._base = f"/api/sessions/{agent_id}"

    async def _call(self, method: str, path: str, body=None) -> dict:
        return await self._rpc(self._node_id, method, path, body)

    async def async_get_session_list(self) -> List[Dict[str, Any]]:
        result = await self._call("GET", f"{self._base}/list")
        return result.get("sessions", [])

    async def async_get_current_session_id(self) -> Optional[str]:
        result = await self._call("GET", f"{self._base}/list")
        return result.get("current_session_id")

    async def async_get_session_history(self, session_id: str) -> Optional[Dict[str, Any]]:
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
    ) -> Optional[Dict[str, Any]]:
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
            logger.info(
                f"Agent {agent_id} not found locally; using WS tunnel reader (node={node_id!r})"
            )
            return _WsSessionReader(agent_id, _ws_rpc, node_id)

    # HTTP fallback (same-machine frp / explicit launcher_url)
    try:
        launcher = syscfg.launcher_url()
    except Exception:
        launcher = ""
    if launcher:
        logger.info(
            f"Agent {agent_id} not found locally; using HTTP remote reader via {launcher}"
        )
        return _RemoteSessionReader(agent_id)

    logger.warning(f"Agent {agent_id} not found locally and no remote available")
    return None
