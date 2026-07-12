"""
Session Manager — Gateway-side in-memory cache.

Only caches conversation history in RAM for fast WS history delivery.
Runner disk session (session_manager.py) is the authoritative durable source.

Design:
- No file persistence: all I/O goes through Runner's async batch writer
- In-memory cache for both user and assistant messages
- Cache is invalidated via current_session events from Runner
- Self-invalidating: 15-minute TTL on cached sessions prevents stale data
- Thread-safe (threading.Lock) — safe from any thread
"""

import asyncio
import logging
import threading
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# TTL for cached sessions (15 minutes). After this, the cache entry is
# considered stale and will be cleared on next read.
_CACHE_TTL_SECONDS = 900


class GatewaySessionCache:
    """
    Gateway session cache — pure in-memory cache.

    All session data is ephemeral (survives page refresh, not Gateway restart).
    Runner disk is the authoritative source — Gateway cache is a read-through
    cache for fast history delivery on WebSocket connect.

    Thread-safety: ``self.lock`` (threading.RLock) protects ``self.sessions``.
    Reentrant lock is required because methods like ``add_message()`` call
    ``get_or_create_session()`` internally while holding the lock.
    All public methods are safe to call from any thread.
    """

    def __init__(self, **kwargs):
        # Accept legacy kwargs for backward compatibility (no-op).
        self.sessions: dict[str, dict] = {}
        # RLock needed because add_message()/clear_session()/etc. call
        # get_or_create_session() internally while holding the lock.
        self.lock = threading.RLock()
        logger.info("GatewaySessionCache (cache-only) initialized. Runner disk is the authoritative session source.")

    # ── Internal helpers ──────────────────────────────────────────

    def _get_session_key(self, user_id: str, agent_id: str) -> str:
        return f"{user_id}:{agent_id}"

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _is_stale(self, session: dict) -> bool:
        """Check if a cached session has exceeded the TTL."""
        updated = session.get("updated_at") or session.get("created_at") or ""
        if not updated:
            return True
        try:
            ts = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            return (datetime.now(timezone.utc) - ts).total_seconds() > _CACHE_TTL_SECONDS
        except Exception:
            return False

    # ── Session CRUD ──────────────────────────────────────────────

    def get_or_create_session(self, user_id: str, agent_id: str) -> dict:
        """Get or create a session. Each user+Agent pair has one session."""
        session_key = self._get_session_key(user_id, agent_id)
        with self.lock:
            if session_key not in self.sessions or self._is_stale(self.sessions[session_key]):
                now = self._now_iso()
                self.sessions[session_key] = {
                    "session_key": session_key,
                    "user_id": user_id,
                    "agent_id": agent_id,
                    "messages": [],
                    "created_at": now,
                    "updated_at": now,
                    "message_count": 0,
                    "last_message": "",
                }
                logger.debug("Created/renewed cached session: %s", session_key)
            return self.sessions[session_key]

    def add_message(
        self,
        user_id: str,
        agent_id: str,
        role: str,
        content: str,
        msg_type: str = "text",
        extra: dict | None = None,
        message_id: str | None = None,
        images: list | None = None,
        attachments: list | None = None,
        files: list | None = None,
        end_task: bool = False,
    ):
        """Add a message to the in-memory cache.

        Deduplication (same as before):
        - Strong dedup: matching message_id is always skipped
        - Weak dedup: same role+content within 3s window is skipped

        No file I/O involved — this only updates the in-memory cache.
        Runner handles all durable persistence.
        """
        session_key = self._get_session_key(user_id, agent_id)

        with self.lock:
            if session_key not in self.sessions or self._is_stale(self.sessions[session_key]):
                self.get_or_create_session(user_id, agent_id)

            session = self.sessions[session_key]
            messages = session.get("messages", [])

            # ── Dedup ──────────────────────────────────────────
            if messages:
                last = messages[-1]
                # Strong dedup: matching message_id
                if message_id and last.get("message_id") == message_id:
                    logger.debug("Dedup (msg_id): %s id=%s", session_key, message_id)
                    return
                # Weak dedup: same role+content within a short window.
                # Use a tighter window (1s) so a user intentionally sending
                # the same text twice in a row is not silently dropped.
                if not message_id and last.get("role") == role and last.get("content") == content:
                    try:
                        last_ts = datetime.fromisoformat(last.get("timestamp", ""))
                        now_ts = datetime.fromisoformat(self._now_iso())
                        if (now_ts - last_ts).total_seconds() < 1.0:
                            logger.debug(
                                "Dedup (content): %s role=%s len=%d",
                                session_key,
                                role,
                                len(content),
                            )
                            return
                    except Exception:
                        pass

            # ── Build message ──────────────────────────────────
            resolved_id = (message_id or "").strip() if isinstance(message_id, str) else ""
            if not resolved_id:
                resolved_id = f"msg_{uuid.uuid4().hex[:16]}"

            message = {
                "message_id": resolved_id,
                "role": role,
                "content": content,
                "type": msg_type,
                "timestamp": self._now_iso(),
            }
            if message_id:
                message["message_id"] = message_id
            if end_task:
                message["end_task"] = True

            # Media fields
            msg_images = images if isinstance(images, list) else []
            msg_attachments = attachments if isinstance(attachments, list) else []
            msg_files = files if isinstance(files, list) else []
            if msg_images:
                message["images"] = msg_images
            if msg_attachments:
                message["attachments"] = msg_attachments
            if msg_files:
                message["files"] = msg_files

            # Extra dict
            if isinstance(extra, dict) and extra:
                message["extra"] = extra
                if "images" not in message and isinstance(extra.get("images"), list) and extra.get("images"):
                    message["images"] = extra.get("images")
                if (
                    "attachments" not in message
                    and isinstance(extra.get("attachments"), list)
                    and extra.get("attachments")
                ):
                    message["attachments"] = extra.get("attachments")
                if "files" not in message and isinstance(extra.get("files"), list) and extra.get("files"):
                    message["files"] = extra.get("files")

            session["messages"].append(message)
            session["message_count"] = len(session["messages"])
            session["last_message"] = content[:100]
            session["updated_at"] = self._now_iso()

            logger.debug(
                "Cached message: %s role=%s total=%d",
                session_key,
                role,
                session["message_count"],
            )

    # ── Read APIs ────────────────────────────────────────────────

    def get_history(self, user_id: str, agent_id: str, limit: int | None = None) -> list[dict]:
        """Get cached message history. Returns empty list if no cache or stale."""
        session_key = self._get_session_key(user_id, agent_id)
        with self.lock:
            session = self.sessions.get(session_key)
            if not session or self._is_stale(session):
                return []
            messages = session.get("messages", [])
            if limit:
                return messages[-limit:]
            return list(messages)

    def get_session(self, user_id: str, agent_id: str) -> dict | None:
        """Get a cached session dict, or None."""
        session_key = self._get_session_key(user_id, agent_id)
        session = self.sessions.get(session_key)
        if session and self._is_stale(session):
            return None
        return session

    def get_user_sessions(self, user_id: str) -> list[dict]:
        """Get all cached sessions for a user, sorted by recency (stale entries removed)."""
        results = []
        stale_keys = []
        with self.lock:
            for key, session in self.sessions.items():
                if session.get("user_id") == user_id:
                    if self._is_stale(session):
                        stale_keys.append(key)
                    else:
                        results.append(session)
            # Prune stale entries
            for key in stale_keys:
                del self.sessions[key]

        results.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return results

    # ── Mutation APIs ─────────────────────────────────────────────

    def invalidate(self, user_id: str, agent_id: str):
        """Invalidate cached session — clear messages but keep session metadata.

        Called on current_session event from Runner to prevent stale cache
        from being served after session switch or context compression.
        """
        session_key = self._get_session_key(user_id, agent_id)
        with self.lock:
            if session_key in self.sessions:
                self.sessions[session_key]["messages"] = []
                self.sessions[session_key]["message_count"] = 0
                self.sessions[session_key]["last_message"] = ""
                self.sessions[session_key]["updated_at"] = self._now_iso()
                logger.debug("Invalidated cache: %s", session_key)
            else:
                logger.debug("Invalidate called but no cache for: %s", session_key)

    def clear_session(self, user_id: str, agent_id: str):
        """Clear cached messages for a user:agent pair."""
        session_key = self._get_session_key(user_id, agent_id)
        with self.lock:
            if session_key in self.sessions:
                self.sessions[session_key]["messages"] = []
                self.sessions[session_key]["message_count"] = 0
                self.sessions[session_key]["last_message"] = ""
                self.sessions[session_key]["updated_at"] = self._now_iso()
                logger.debug("Cleared cache: %s", session_key)

    def delete_session(self, user_id: str, agent_id: str):
        """Delete a session from cache entirely."""
        session_key = self._get_session_key(user_id, agent_id)
        with self.lock:
            if session_key in self.sessions:
                del self.sessions[session_key]
                logger.debug("Deleted cache: %s", session_key)

    def flush_now(self):
        """No-op. Previously flushed to disk; now cache-only.
        Kept for backward compatibility."""
        pass

    def get_stats(self) -> dict:
        """Get cache statistics."""
        with self.lock:
            total_messages = sum(s.get("message_count", 0) for s in self.sessions.values())
            return {
                "total_sessions": len(self.sessions),
                "total_messages": total_messages,
                "avg_messages_per_session": total_messages / len(self.sessions) if self.sessions else 0,
            }

    # ── Async wrappers ────────────────────────────────────────────
    # Run the sync counterpart in a thread pool so the event loop is
    # never blocked by the threading.Lock inside each method.

    async def async_get_or_create_session(self, user_id: str, agent_id: str) -> dict:
        return await asyncio.to_thread(self.get_or_create_session, user_id, agent_id)

    async def async_add_message(
        self,
        user_id: str,
        agent_id: str,
        role: str,
        content: str,
        msg_type: str = "text",
        extra: dict | None = None,
        message_id: str | None = None,
        images: list | None = None,
        attachments: list | None = None,
        files: list | None = None,
        end_task: bool = False,
    ):
        return await asyncio.to_thread(
            self.add_message,
            user_id,
            agent_id,
            role,
            content,
            msg_type,
            extra,
            message_id,
            images,
            attachments,
            files,
            end_task,
        )

    async def async_get_history(self, user_id: str, agent_id: str, limit: int | None = None) -> list[dict]:
        return await asyncio.to_thread(self.get_history, user_id, agent_id, limit)

    async def async_get_session(self, user_id: str, agent_id: str) -> dict | None:
        return await asyncio.to_thread(self.get_session, user_id, agent_id)

    async def async_get_user_sessions(self, user_id: str) -> list[dict]:
        return await asyncio.to_thread(self.get_user_sessions, user_id)

    async def async_invalidate(self, user_id: str, agent_id: str):
        return await asyncio.to_thread(self.invalidate, user_id, agent_id)

    async def async_clear_session(self, user_id: str, agent_id: str):
        return await asyncio.to_thread(self.clear_session, user_id, agent_id)

    async def async_delete_session(self, user_id: str, agent_id: str):
        return await asyncio.to_thread(self.delete_session, user_id, agent_id)

    async def async_get_stats(self) -> dict:
        return await asyncio.to_thread(self.get_stats)


# Global singleton
gateway_session_cache = GatewaySessionCache()
