"""
AI Web Gateway - Multi-Agent Management Platform
Embedded in gateway, providing Agent registration management and user conversation services
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

# Agent must answer an application-level ping within this window, or send a
# heartbeat. Longer than Agent's heartbeat interval (20s) + slack.
_DEFAULT_STALE_S = 75.0
_PROBE_TIMEOUT_S = 8.0
_LIVENESS_SWEEP_INTERVAL_S = 30.0


@dataclass
class AgentInfo:
    """Agent information"""

    agent_id: str
    agent_name: str
    agent_type: str
    capabilities: list[str]
    description: str
    status: str = "online"  # online/busy/offline
    load_percent: int = 0
    today_chats: int = 0
    total_chats: int = 0
    registered_at: str = ""
    last_heartbeat: str = ""
    today_date: str = ""  # Records the date corresponding to today_chats, used for cross-day reset
    node_id: str = ""  # Owning node ID (multi-machine deployment, e.g. "node-local" / "node-gpu-01")
    node_label: str = ""  # Human-readable label for the owning node
    busy_sessions: list[str] | None = None  # session ids currently running a turn

    def to_dict(self):
        d = asdict(self)
        if d.get("busy_sessions") is None:
            d["busy_sessions"] = []
        return d


class AgentRegistry:
    """Agent registry - in-memory storage of online Agents"""

    def __init__(self):
        self.agents: dict[str, AgentInfo] = {}  # agent_id -> AgentInfo
        self.connections: dict[str, object] = {}  # agent_id -> WebSocket
        self._busy_sessions: dict[str, set[str]] = {}  # agent_id -> set of session ids
        # Monotonic timestamps for liveness (heartbeat from agent, pong to probe).
        self._last_heartbeat_mono: dict[str, float] = {}
        self._last_pong_mono: dict[str, float] = {}
        self._pong_waiters: dict[str, list[asyncio.Future]] = {}
        self._liveness_task: asyncio.Task | None = None

    def register(self, agent_info: AgentInfo, websocket) -> object | None:
        """Register Agent.

        Returns the previous WebSocket if this agent_id was already
        registered (i.e. a reconnect/replacement), so the caller can close
        the stale connection. Returns None for a fresh registration.
        """
        agent_id = agent_info.agent_id
        old_ws = self.connections.get(agent_id)

        # Check if already exists
        if agent_id in self.agents:
            logger.warning(f"Agent {agent_id} already registered, replacing connection (old will be closed)")

        # Set timestamps
        now = datetime.now().isoformat()
        now_mono = time.monotonic()
        agent_info.registered_at = now
        agent_info.last_heartbeat = now
        if agent_info.busy_sessions is None:
            agent_info.busy_sessions = []

        # Store
        self.agents[agent_id] = agent_info
        self.connections[agent_id] = websocket
        self._busy_sessions.setdefault(agent_id, set())
        self._last_heartbeat_mono[agent_id] = now_mono
        self._last_pong_mono[agent_id] = now_mono

        logger.info(f"Agent {agent_id} ({agent_info.agent_name}) registered")
        return old_ws

    def unregister(self, agent_id: str):
        """Unregister Agent"""
        if agent_id in self.agents:
            del self.agents[agent_id]
        if agent_id in self.connections:
            del self.connections[agent_id]
        self._busy_sessions.pop(agent_id, None)
        self._last_heartbeat_mono.pop(agent_id, None)
        self._last_pong_mono.pop(agent_id, None)
        waiters = self._pong_waiters.pop(agent_id, [])
        for fut in waiters:
            if not fut.done():
                fut.set_result(False)

        logger.info(f"Agent {agent_id} unregistered")

    def update_heartbeat(self, agent_id: str, stats: dict | None = None):
        """Record an agent application heartbeat (proves the agent writer is alive)."""
        agent = self.agents.get(agent_id)
        if not agent:
            return
        now_mono = time.monotonic()
        self._last_heartbeat_mono[agent_id] = now_mono
        agent.last_heartbeat = datetime.now().isoformat()
        if stats and isinstance(stats, dict):
            try:
                agent.load_percent = int(stats.get("load_percent") or agent.load_percent or 0)
            except (TypeError, ValueError):
                pass

    def note_pong(self, agent_id: str) -> None:
        """Record an application-level pong (proves the agent *reader* is alive)."""
        if agent_id not in self.agents:
            return
        self._last_pong_mono[agent_id] = time.monotonic()
        waiters = self._pong_waiters.pop(agent_id, [])
        for fut in waiters:
            if not fut.done():
                fut.set_result(True)

    def is_agent_live(self, agent_id: str, max_stale_s: float = _DEFAULT_STALE_S) -> bool:
        """True if agent has a WS and recent heartbeat or pong."""
        if agent_id not in self.connections or agent_id not in self.agents:
            return False
        now = time.monotonic()
        last = max(
            self._last_pong_mono.get(agent_id, 0.0),
            self._last_heartbeat_mono.get(agent_id, 0.0),
        )
        if last <= 0:
            return True  # just registered, timestamps set in register()
        return (now - last) <= max_stale_s

    def set_busy(self, agent_id: str, busy: bool = True):
        """Set Agent busy status (legacy agent-level)."""
        if agent_id in self.agents:
            if busy:
                self.agents[agent_id].status = "busy"
            else:
                # Only clear agent busy when no session turns remain
                sessions = self._busy_sessions.get(agent_id) or set()
                self.agents[agent_id].status = "busy" if sessions else "online"

    def set_session_busy(self, agent_id: str, session_id: str, busy: bool = True):
        """Mark a single session as busy/idle; agent status follows any busy session."""
        if not session_id:
            self.set_busy(agent_id, busy)
            return
        sessions = self._busy_sessions.setdefault(agent_id, set())
        if busy:
            sessions.add(session_id)
        else:
            sessions.discard(session_id)
        if agent_id in self.agents:
            self.agents[agent_id].busy_sessions = sorted(sessions)
            self.agents[agent_id].status = "busy" if sessions else "online"

    def get_busy_sessions(self, agent_id: str) -> list[str]:
        return sorted(self._busy_sessions.get(agent_id) or set())

    def get_agent(self, agent_id: str) -> AgentInfo | None:
        """Get Agent information"""
        return self.agents.get(agent_id)

    def get_connection(self, agent_id: str) -> object | None:
        """Get Agent's WebSocket connection"""
        return self.connections.get(agent_id)

    def list_agents(self, status: str | None = None, agent_type: str | None = None) -> list[AgentInfo]:
        """List Agents"""
        agents = list(self.agents.values())

        if status:
            agents = [a for a in agents if a.status == status]
        if agent_type:
            agents = [a for a in agents if a.agent_type == agent_type]

        return agents

    def increment_today_chats(self, agent_id: str):
        """Increment today's chat count, auto-reset at day boundary"""
        if agent_id not in self.agents:
            return
        agent = self.agents[agent_id]
        today = datetime.now().strftime("%Y-%m-%d")
        if agent.today_date != today:
            agent.today_chats = 0
            agent.today_date = today
        agent.today_chats += 1
        agent.total_chats += 1
        logger.debug(f"Agent {agent_id} today_chats={agent.today_chats}, total_chats={agent.total_chats}")

    def get_stats(self) -> dict:
        """Get statistics"""
        agents = list(self.agents.values())
        return {
            "total": len(agents),
            "online": sum(1 for a in agents if a.status == "online"),
            "busy": sum(1 for a in agents if a.status == "busy"),
            "offline": sum(1 for a in agents if a.status == "offline"),
        }

    async def send_to_agent(self, agent_id: str, message: dict) -> bool:
        """Send message to Agent"""
        ws = self.connections.get(agent_id)
        if ws:
            try:
                await ws.send_text(json.dumps(message))
                return True
            except Exception as e:
                logger.error(f"Failed to send to agent {agent_id}: {e}")
                return False
        return False

    async def probe_agent(self, agent_id: str, timeout: float = _PROBE_TIMEOUT_S) -> bool:
        """Application-level ping/pong. Requires the agent *message loop* to answer.

        Heartbeat alone is insufficient: a half-dead agent can still *send*
        heartbeats while its recv loop is dead (chat never arrives).
        """
        ws = self.connections.get(agent_id)
        if not ws:
            return False
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pong_waiters.setdefault(agent_id, []).append(fut)
        ts = time.time()
        try:
            await ws.send_text(json.dumps({"type": "ping", "action": "ping", "ts": ts}))
        except Exception as e:
            logger.warning("[Registry] probe send failed agent=%s: %s", agent_id, e)
            waiters = self._pong_waiters.get(agent_id) or []
            if fut in waiters:
                waiters.remove(fut)
            if not fut.done():
                fut.set_result(False)
            return False
        try:
            ok = await asyncio.wait_for(fut, timeout=timeout)
            return bool(ok)
        except asyncio.TimeoutError:
            waiters = self._pong_waiters.get(agent_id) or []
            if fut in waiters:
                waiters.remove(fut)
            if not fut.done():
                fut.set_result(False)
            logger.warning("[Registry] probe timeout agent=%s (%.1fs)", agent_id, timeout)
            return False
        except asyncio.CancelledError:
            waiters = self._pong_waiters.get(agent_id) or []
            if fut in waiters:
                waiters.remove(fut)
            if not fut.done():
                fut.cancel()
            raise

    async def send_to_agent_verified(
        self,
        agent_id: str,
        message: dict,
        *,
        probe: bool = True,
        probe_timeout: float = _PROBE_TIMEOUT_S,
    ) -> bool:
        """Probe (optional) then send. Returns False if agent is unreachable."""
        if probe:
            if not await self.probe_agent(agent_id, timeout=probe_timeout):
                return False
        return await self.send_to_agent(agent_id, message)

    async def drop_stale_agent(self, agent_id: str, reason: str = "liveness") -> None:
        """Close and unregister an agent that failed liveness checks."""
        ws = self.connections.get(agent_id)
        logger.warning("[Registry] Dropping stale agent=%s reason=%s", agent_id, reason)
        self.unregister(agent_id)
        if ws is not None:
            try:
                await ws.close(code=4001, reason=reason[:120])
            except Exception:
                pass

    def ensure_liveness_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Start the background liveness sweeper once (idempotent)."""
        if self._liveness_task is not None and not self._liveness_task.done():
            return
        try:
            running = loop or asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _sweep() -> None:
            while True:
                try:
                    await asyncio.sleep(_LIVENESS_SWEEP_INTERVAL_S)
                    agent_ids = list(self.connections.keys())
                    for aid in agent_ids:
                        # Soft check first (heartbeat/pong age)
                        if self.is_agent_live(aid):
                            # Hard probe occasionally to catch reader-dead zombies
                            # that still emit heartbeats.
                            last_pong = self._last_pong_mono.get(aid, 0.0)
                            if time.monotonic() - last_pong > _DEFAULT_STALE_S * 0.6:
                                ok = await self.probe_agent(aid)
                                if not ok:
                                    await self.drop_stale_agent(aid, reason="probe_failed")
                            continue
                        # Stale heartbeat — one probe chance, then drop
                        ok = await self.probe_agent(aid)
                        if not ok:
                            await self.drop_stale_agent(aid, reason="stale_heartbeat")
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.debug("[Registry] liveness sweep error: %s", e)

        self._liveness_task = running.create_task(_sweep(), name="agent-liveness-sweep")
        logger.info("[Registry] agent liveness sweeper started")


# Global singleton
registry = AgentRegistry()
