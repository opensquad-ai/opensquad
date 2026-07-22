"""
AI Web Gateway - Multi-Agent Management Platform
Embedded in gateway, providing Agent registration management and user conversation services
"""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


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
        # Heartbeat mechanism removed - no longer needed

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
        agent_info.registered_at = now
        if agent_info.busy_sessions is None:
            agent_info.busy_sessions = []

        # Store
        self.agents[agent_id] = agent_info
        self.connections[agent_id] = websocket
        self._busy_sessions.setdefault(agent_id, set())

        logger.info(f"Agent {agent_id} ({agent_info.agent_name}) registered")
        return old_ws

    def unregister(self, agent_id: str):
        """Unregister Agent"""
        if agent_id in self.agents:
            del self.agents[agent_id]
        if agent_id in self.connections:
            del self.connections[agent_id]
        self._busy_sessions.pop(agent_id, None)

        logger.info(f"Agent {agent_id} unregistered")

    def update_heartbeat(self, agent_id: str, stats: dict | None = None):
        """Update heartbeat - no-op, heartbeat mechanism removed"""
        pass

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


# Global singleton
registry = AgentRegistry()
