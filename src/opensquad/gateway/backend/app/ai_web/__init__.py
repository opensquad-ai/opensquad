"""
AI Web Gateway entry point
Integrates all modules and provides a unified FastAPI application
"""
# Only export necessary components to avoid circular imports
from .registry import registry, AgentInfo
from .sessions import gateway_session_cache
from .websocket import agent_handler, user_handler
from .routes import router as api_router

__all__ = [
    'registry',
    'AgentInfo', 
    'gateway_session_cache',
    'agent_handler',
    'user_handler',
    'api_router'
]
