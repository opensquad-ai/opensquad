"""
AI Web Gateway entry point
Integrates all modules and provides a unified FastAPI application
"""

# Only export necessary components to avoid circular imports
from .registry import AgentInfo, registry
from .routes import router as api_router
from .sessions import gateway_session_cache
from .websocket import agent_handler, user_handler

__all__ = ["AgentInfo", "agent_handler", "api_router", "gateway_session_cache", "registry", "user_handler"]
