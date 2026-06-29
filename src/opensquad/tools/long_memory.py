# -*- coding: utf-8 -*-
"""
Long Memory Tools v2.0
Long-term memory toolset based on MemoryManager (replaces the old memory.py).

Provides four agent-callable tools:
    1. memory_write      -- Write knowledge/experience memory
    2. memory_query      -- Actively query long-term memory in depth
    3. memory_log        -- Record activity logs (event memory)
    4. memory_find_chain -- Discover hidden association chains between concepts

Automatic memory injection (Layer 1) is implemented by memory_manager + context.py before_input
and is not handled at the tool layer.
"""
from typing import List, Dict, Any

# Global MemoryManager instance, injected by boot.py after initialization
_memory_manager = None


def init_memory_tools(memory_manager):
    """
    Called by boot.py to inject the MemoryManager instance.
    """
    global _memory_manager
    _memory_manager = memory_manager


def _get_mm():
    if _memory_manager is None:
        return None
    return _memory_manager


def get_memory_manager():
    """Public interface: allows plugins (long_memory plugin) to obtain the running MemoryManager instance during hot reload."""
    return _memory_manager


def memory_write(topic: str, summary: str, keywords: List[str] = None,
                 body: str = None, entry_type: str = "knowledge",
                 category: str = None, importance: int = 3,
                 supersedes: str = None) -> Dict[str, Any]:
    """
    [Long-term Memory - Write] Write important knowledge, experience, or discoveries into
    the long-term memory system. After writing, it is automatically incorporated into the
    knowledge graph (async background update; does not block the current task).

    Use cases:
    - User explicitly asks you to "remember" something
    - Discovered an important technical mechanism or pattern
    - Summarizing lessons learned after task completion
    - Correcting a previous misconception (use supersedes to point to the old memory ID)

    Args:
        topic: Topic (short summary, e.g. "Python GIL mechanism").
        summary: Summary (20-50 chars, core points).
        keywords: Keyword list (e.g. ["Python", "GIL", "multithreading"]). Auto-extracted if not provided.
        body: Detailed body text (optional, full description).
        entry_type: Memory type: "knowledge" (factual), "experience" (lessons learned), "log" (event log).
        category: Category label (free text, e.g. "programming", "deployment").
        importance: Importance 1-5, default 3. 5=highly important / hard to forget, 1=low priority.
        supersedes: ID of the old memory being replaced (to correct outdated knowledge; old entry is demoted automatically).
    """
    mm = _get_mm()
    if mm is None:
        return {"status": "error", "message": "Long-term memory system not initialized"}

    return mm.write_memory(
        topic=topic, summary=summary, keywords=keywords,
        body=body, entry_type=entry_type, category=category,
        importance=importance, supersedes=supersedes,
    )


def memory_query(query_text: str = None, keywords: List[str] = None,
                 depth: str = "standard", token_budget: int = 3000) -> Dict[str, Any]:
    """
    [Long-term Memory - Deep Query] Actively search long-term memory to retrieve
    associated knowledge and reasoning chains. More in-depth than automatic injection;
    suitable for scenarios requiring active recall.

    Use cases:
    - Need to recall knowledge or notes from before
    - Analyzing a complex problem that needs associated information
    - User asks "do you remember..." or "previously..."
    - Need to find hidden associations (use depth="deep")

    Args:
        query_text: Natural language query (e.g. "previous experience with deployment").
        keywords: Precise keyword list (takes priority over auto-extraction from query_text).
        depth: Query depth: "fast" (exact match, milliseconds), "standard" (+associative expansion),
               "deep" (+reasoning chains, seconds).
        token_budget: Maximum token count for returned text, default 3000.
    """
    mm = _get_mm()
    if mm is None:
        return {"status": "error", "message": "Long-term memory system not initialized"}

    return mm.query_deep(
        query_text=query_text, keywords=keywords,
        depth=depth, token_budget=token_budget,
    )


def memory_log(content: str, detail: str = None, category: str = None,
               tags: List[str] = None, importance: int = 2) -> Dict[str, Any]:
    """
    [Long-term Memory - Log] Record an activity or event log entry.
    Supports date-based lookback queries and auto-integration into the knowledge graph.

    Use cases:
    - Completed an important operation (deployment, fix, configuration change)
    - Recording user preferences or habits
    - Noting key conversation points
    - Need a time-traceable record

    Args:
        content: Activity summary (required, e.g. "Completed MCP server hot-reload feature development").
        detail: Detailed content (optional, process description).
        category: Category label (e.g. "development", "ops", "conversation").
        tags: Manual tag list (merged with auto-extracted keywords).
        importance: Importance 1-5, default 2 (logs are generally lower priority).
    """
    mm = _get_mm()
    if mm is None:
        return {"status": "error", "message": "Long-term memory system not initialized"}

    return mm.log_memory(
        content=content, detail=detail,
        category=category, tags=tags,
        importance=importance,
    )


def memory_find_chain(anchor_words: List[str]) -> Dict[str, Any]:
    """
    [Long-term Memory - Chain Reasoning] Discover hidden association chains between
    two seemingly unrelated concepts. Uses PPR + shortest-path algorithm to find
    connection paths in the knowledge graph.

    Use cases:
    - "What is the relationship between X and Y?" -> find hidden causal chains
    - Analyzing indirect associations between two concepts
    - Cross-domain knowledge discovery

    Args:
        anchor_words: List of anchor words (2 or more, e.g. ["tariffs", "stock market"]).
    """
    mm = _get_mm()
    if mm is None:
        return {"status": "error", "message": "Long-term memory system not initialized"}

    return mm.find_chain(anchor_words)
