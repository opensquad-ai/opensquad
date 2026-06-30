"""
Long-Term Memory System Configuration Plugin

Responsibility: Expose MemoryManager + AgentMemory parameters to the plugin management UI,
so users can adjust core parameters such as memory recall and knowledge graph decay
through the Web interface.

Configuration changes immediately update the running MemoryManager instance (no restart needed).
Underlying matrix parameters (max_dim) require an Agent restart to fully take effect.
"""

import logging

from opensquad.plugin_api import Plugin, register

logger = logging.getLogger(__name__)


@register(
    name="long_memory",
    plugin_type="hook",
    display_name="Long Memory",
    description="Long-term memory management with semantic recall, keyword extraction, and co-occurrence knowledge graph. View and manage stored memory entries.",
    contributes={
        "views": [
            {
                "name": "panel",
                "title": "Memory Management",
                "icon": "BrainCircuit",
                "data_endpoint": "/api/plugins/long_memory/data",
            }
        ]
    },
    config_schema={
        "token_budget": {
            "type": "integer",
            "default": 3000,
            "description": "Maximum token count for memory injected into the prompt per conversation turn. Higher values recall more information but consume more context window.",
        },
        "window_size": {
            "type": "integer",
            "default": 5,
            "description": "Number of turns a memory entry stays in the active window. Entries beyond this are unloaded to save context and reloaded when relevant questions arise.",
        },
        "context_depth": {
            "type": "integer",
            "default": 4,
            "description": "Number of historical messages to look back when performing automatic recall. Higher values improve keyword extraction accuracy but slightly increase latency.",
        },
        "cache_ttl": {
            "type": "integer",
            "default": 8,
            "description": "Number of turns before a query result cache entry expires. Cache hits avoid redundant queries and improve response speed. Set to 0 to disable caching.",
        },
        "min_cooccurrence": {
            "type": "integer",
            "default": 5,
            "description": "Knowledge graph pruning threshold: word pairs with co-occurrence count below this value are excluded from the graph. Higher values produce a sparser graph with lower memory usage.",
        },
        "decay_rate": {
            "type": "number",
            "default": 0.005,
            "description": "Co-occurrence matrix decay rate: the retention ratio per update is (1 - decay_rate). Higher values cause faster forgetting, suitable for rapidly evolving knowledge domains.",
        },
        "decay_interval": {
            "type": "integer",
            "default": 500,
            "description": "Number of documents processed between each matrix decay pass. Lower values cause more frequent forgetting, suitable for scenarios that emphasize recent information.",
        },
        "time_decay_lambda": {
            "type": "number",
            "default": 0.1,
            "description": "Time decay coefficient λ for memory entries; scoring formula: 1 / (1 + λ × days). Higher values prioritize more recent memories during recall.",
        },
        "max_dim": {
            "type": "integer",
            "default": 100000,
            "description": "Maximum dimension of the co-occurrence matrix (vocabulary size limit). Affects memory usage; changes require an Agent restart to take effect.",
        },
    },
)
class LongMemoryPlugin(Plugin):
    """
    Long-term memory parameter configuration plugin.

    Each time the plugin is loaded or hot-reloaded, parameters from
    self.context.config are synced to the running MemoryManager instance
    (takes effect immediately).
    """

    def on_load(self) -> None:
        cfg = self.context.config

        try:
            from opensquad.tools.long_memory import get_memory_manager

            mm = get_memory_manager()

            if mm is not None:
                # ---- MemoryManager parameters (take effect immediately) ----
                mm._token_budget = int(cfg.get("token_budget", 3000))
                mm._window_size = int(cfg.get("window_size", 5))
                mm._context_depth = int(cfg.get("context_depth", 4))
                mm._cache_ttl = int(cfg.get("cache_ttl", 8))

                logger.info(
                    f"[LongMemoryPlugin] MemoryManager updated — "
                    f"token_budget={mm._token_budget}, window_size={mm._window_size}, "
                    f"context_depth={mm._context_depth}, cache_ttl={mm._cache_ttl}"
                )

                # ---- AgentMemory underlying parameters (take effect on next matrix rebuild) ----
                am = getattr(mm, "_am", None)
                if am is not None:
                    am._config["min_cooccurrence"] = float(cfg.get("min_cooccurrence", 5))
                    am._config["decay_rate"] = float(cfg.get("decay_rate", 0.005))
                    am._config["decay_interval"] = int(cfg.get("decay_interval", 500))
                    am._config["time_decay_lambda"] = float(cfg.get("time_decay_lambda", 0.1))

                    # Sync DecayManager runtime parameters
                    decay = getattr(am, "_decay", None)
                    if decay is not None:
                        decay.decay_rate = float(cfg.get("decay_rate", 0.005))
                        decay.decay_interval = int(cfg.get("decay_interval", 500))

                    logger.info(
                        f"[LongMemoryPlugin] AgentMemory updated — "
                        f"min_cooccurrence={am._config['min_cooccurrence']}, "
                        f"decay_rate={am._config['decay_rate']}, "
                        f"time_decay_lambda={am._config['time_decay_lambda']}"
                    )
            else:
                logger.debug(
                    "[LongMemoryPlugin] MemoryManager not yet initialized, "
                    "params will be applied at next boot via agents_boot.py"
                )

        except Exception as e:
            logger.warning(f"[LongMemoryPlugin] Failed to update MemoryManager: {e}")

    def on_unload(self) -> None:
        logger.debug("[LongMemoryPlugin] Unloaded")
