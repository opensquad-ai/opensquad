"""
opensquad/memory_manager.py - Long-term memory lifecycle manager

Core responsibilities:
    1. Automatically extract keywords from recent context (user + assistant messages)
    2. Query AgentMemory and manage query cache
    3. Sliding window: auto-evict memories after N turns
    4. Render active memories as prompt text (constrained by token budget)
    5. Asynchronously rebuild PPMI matrix in background after writes (non-blocking)

Data flow:
    [Each conversation turn]
      |- advance_turn()             # Advance turn counter + evict expired memories
      |- auto_recall(messages, query) # Extract keywords -> cache check -> query -> cache
      |- render_active_memories()   # Render all active memories -> inject {{MEMORY_CONTEXT}}
      +- [Agent calls memory_write]
            |- write_memory()       # Synchronous write to SQLite
            +- _background_rebuild()# Background thread rebuilds matrix
"""

import hashlib
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


class MemoryManager:
    """
    Long-term memory lifecycle manager.

    Args:
        agent_memory: AgentMemory instance (from agent_memory_tool)
        agent_name: str - current agent name (shared marker for multi-agent)
        config: dict - optional configuration overrides
            token_budget: int    - token limit per injection (default 3000)
            window_size: int     - number of turns a memory stays active (default 5)
            context_depth: int   - context depth for keyword extraction (default 4)
            cache_ttl: int       - cache expiry in turns (default 8)
    """

    def __init__(self, agent_memory, agent_name: str, config: dict | None = None):
        self._am = agent_memory
        self._agent_name = agent_name

        cfg = config or {}
        self._token_budget = cfg.get("token_budget", 3000)
        self._window_size = cfg.get("window_size", 5)
        self._context_depth = cfg.get("context_depth", 4)
        self._cache_ttl = cfg.get("cache_ttl", 8)

        # State
        self._turn_counter = 0
        # Active memories: [(prompt_text, cache_key, loaded_at_turn, matched_count)]
        self._active_memories: list[tuple] = []
        # Query cache: cache_key -> {"result": dict, "turn": int}
        self._query_cache: dict[str, dict] = {}
        # Injection log: cache_key -> req_len_at_last_injection (persists across evictions)
        # Used to detect whether a memory chunk is already in chat_api.req, avoiding re-injection
        self._injection_log: dict[str, int] = {}
        # Background rebuild lock (prevent concurrent rebuilds)
        self._rebuild_lock = threading.Lock()
        self._rebuilding = False

    # ================================================================
    # Public API
    # ================================================================

    def advance_turn(self):
        """
        Call at the start of each conversation turn.
        Advances the turn counter and cleans up expired cache.
        """
        self._turn_counter += 1
        self._evict_expired_cache()
        self._prune_active_memories()

    def _prune_active_memories(self):
        """BUG-6: bound ``_active_memories`` growth.

        ``window_size`` (default 5 turns) previously never took effect, so the
        active-memory list grew without bound on long-running sessions.  Prune
        entries older than ``window_size`` turns and cap the list at a hard
        maximum so a pathological recall burst cannot blow up memory.
        """
        if not self._active_memories:
            return
        cutoff = self._turn_counter - self._window_size
        self._active_memories = [item for item in self._active_memories if item[2] >= cutoff]
        # Hard cap (e.g. 4x the window) as a defensive ceiling.
        max_entries = max(8, self._window_size * 4)
        if len(self._active_memories) > max_entries:
            self._active_memories = self._active_memories[-max_entries:]

    def auto_recall(self, recent_messages: list, current_query: str, req_length: int = 0) -> str:
        """
        Automatic memory recall (Layer 1).

        1. Extract keywords from recent_messages + current_query
        2. Check cache -> return cached result on hit
        3. Query AgentMemory (fast mode)
        4. Add result to active memory window

        Args:
            recent_messages: list[dict] - recent conversation messages ({"role": ..., "content": ...})
            current_query: str - current user input
            req_length: int - current chat_api.req message count, used for dedup injection detection

        Returns:
            str - memory prompt text to inject this turn (already-in-context memories are filtered out)
        """
        if not current_query or not current_query.strip():
            return self.render_active_memories(current_req_length=req_length)

        # 1. Extract keywords
        keywords = self._extract_keywords_from_context(recent_messages, current_query)
        if not keywords:
            return self.render_active_memories(current_req_length=req_length)

        # 2. Compute cache key
        cache_key = self._make_cache_key(keywords)

        # 3. Check cache
        if cache_key in self._query_cache:
            cached = self._query_cache[cache_key]
            # Cache hit: ensure it is in the active window (may already be)
            if not self._is_active(cache_key):
                entry = cached["result"]
                prompt_text = entry.get("prompt_text", "")
                matched_count = len(entry.get("matched_entries", []))
                if prompt_text.strip() and matched_count > 0:
                    self._active_memories.append((prompt_text, cache_key, self._turn_counter, matched_count))
            logger.debug(f"[MemoryManager] Cache hit for {keywords[:3]}...")
            return self.render_active_memories(current_req_length=req_length)

        # 4. Query AgentMemory
        try:
            result = self._am.query(
                keywords=keywords,
                user_input=current_query,
                depth="fast",
                token_budget=self._token_budget,
                auto_parse_time=True,
            )

            prompt_text = result.get("prompt_text", "")
            matched_count = len(result.get("matched_entries", []))

            # Cache result
            self._query_cache[cache_key] = {
                "result": result,
                "turn": self._turn_counter,
            }

            # Add to active window
            if prompt_text.strip() and matched_count > 0:
                self._active_memories.append((prompt_text, cache_key, self._turn_counter, matched_count))
                logger.info(f"[MemoryManager] Recalled {matched_count} memories, keywords={keywords[:5]}")
            else:
                logger.debug(f"[MemoryManager] No relevant memories for {keywords[:3]}")

        except Exception as e:
            logger.warning(f"[MemoryManager] Auto recall failed: {e}")

        return self.render_active_memories(current_req_length=req_length)

    def render_active_memories(self, current_req_length: int = 0) -> str:
        """
        Render the current memory blocks to be injected as prompt text.

        Uses _injection_log to detect whether a memory is already in chat_api.req:
        - current_req_length > req length at last injection -> injection message still in context -> skip (no re-injection)
        - Otherwise (never injected or already compressed away) -> inject normally, record new injection position

        If current_req_length is not provided (default 0) -> full output, backward compatible.
        """
        if not self._active_memories:
            return ""

        # Deduplicate (keep only the latest entry per cache_key)
        seen_keys = set()
        unique = []
        for item in reversed(self._active_memories):
            if item[1] not in seen_keys:
                seen_keys.add(item[1])
                unique.append(item)
        unique.reverse()

        # Filter memories already in context, collect parts to inject
        parts = []
        total_count = 0
        for item in unique:
            prompt_text, cache_key, _, matched_count = item
            last_inj = self._injection_log.get(cache_key, -1)  # -1 = never injected
            if last_inj >= 0 and current_req_length > last_inj:
                # Last injected message is still in req, skip (avoid re-injection)
                logger.debug(
                    f"[MemoryManager] Skip re-injection for {cache_key[:8]}... "
                    f"(req_len={current_req_length} > last_inj={last_inj})"
                )
                continue
            # Need to inject: record current req length
            self._injection_log[cache_key] = current_req_length
            parts.append(prompt_text)
            total_count += matched_count

        if not parts:
            return ""

        result = [f"[Long-term Memory Auto-Recall | Total: {total_count}]"]
        result.extend(parts)
        result.append("(For deeper memory search, use the memory_query tool with depth='deep')")

        return "\n".join(result)

    def write_memory(
        self,
        topic: str | None = None,
        summary: str | None = None,
        keywords: list | None = None,
        body: str | None = None,
        entry_type: str = "knowledge",
        category: str | None = None,
        importance: int = 3,
        supersedes: str | None = None,
    ) -> dict[str, Any]:
        """
        Write a memory entry + rebuild matrix in background thread.

        Automatically appends source=agent_name.
        Write is synchronous (SQLite); matrix rebuild is asynchronous.
        """
        if not self._am:
            return {"status": "error", "message": "Long-term memory system not initialized"}

        try:
            auto_extract = keywords is None or len(keywords) == 0
            entry_id = self._am.write(
                topic=topic,
                keywords=keywords,
                summary=summary,
                body=body,
                source=self._agent_name,
                auto_extract_keywords=auto_extract,
                entry_type=entry_type,
                category=category,
                importance=importance,
                supersedes=supersedes,
            )

            # Asynchronously rebuild matrix in background
            self._trigger_background_rebuild()

            return {
                "status": "success",
                "entry_id": entry_id,
                "message": f"Written to long-term memory [{entry_type}] topic='{topic}', importance={importance}",
            }
        except Exception as e:
            return {"status": "error", "message": f"Write failed: {e}"}

    def log_memory(
        self,
        content: str,
        detail: str | None = None,
        category: str | None = None,
        tags: list | None = None,
        importance: int = 2,
    ) -> dict[str, Any]:
        """
        Record a log memory entry + rebuild matrix in background.
        """
        if not self._am:
            return {"status": "error", "message": "Long-term memory system not initialized"}

        try:
            entry_id = self._am.log(
                content=content,
                detail=detail,
                category=category,
                tags=tags,
                source=self._agent_name,
                importance=importance,
            )

            self._trigger_background_rebuild()

            return {
                "status": "success",
                "entry_id": entry_id,
                "message": f"Log recorded [{category or 'uncategorized'}] {content[:30]}...",
            }
        except Exception as e:
            return {"status": "error", "message": f"Log recording failed: {e}"}

    def query_deep(
        self,
        query_text: str | None = None,
        keywords: list | None = None,
        depth: str = "standard",
        token_budget: int | None = None,
    ) -> dict[str, Any]:
        """
        Active deep query (Layer 2) - called by agent tool.
        Not managed by sliding window; returns results directly.
        """
        if not self._am:
            return {"status": "error", "message": "Long-term memory system not initialized"}

        budget = token_budget or self._token_budget

        try:
            result = self._am.query(
                keywords=keywords,
                user_input=query_text,
                depth=depth,
                token_budget=budget,
                auto_parse_time=True,
            )

            output = {
                "status": "success",
                "prompt_text": result.get("prompt_text", ""),
                "matched_count": len(result.get("matched_entries", [])),
                "expanded_keywords": result.get("expanded_keywords", []),
            }

            chain = result.get("chain")
            if chain:
                output["reasoning_chain"] = chain

            stats = result.get("search_stats", {})
            if stats:
                output["stats"] = stats

            return output
        except Exception as e:
            return {"status": "error", "message": f"Query failed: {e}"}

    def find_chain(self, anchor_words: list) -> dict[str, Any]:
        """Chain reasoning - discover hidden associations."""
        if not self._am:
            return {"status": "error", "message": "Long-term memory system not initialized"}

        try:
            chain = self._am.find_chain(anchor_words)
            if chain:
                return {
                    "status": "success",
                    "chain": chain,
                    "message": f"Found association chain for {' <-> '.join(anchor_words)}",
                }
            return {
                "status": "success",
                "chain": None,
                "message": f"No association chain found between {' <-> '.join(anchor_words)}",
            }
        except Exception as e:
            return {"status": "error", "message": f"Chain reasoning failed: {e}"}

    def save(self):
        """Persist matrix data (SQLite entries are stored in real time)."""
        if self._am and self._am._data_dir:
            try:
                self._am.save(self._am._data_dir)
                logger.info("[MemoryManager] Memory data saved")
            except Exception as e:
                logger.warning(f"[MemoryManager] Save failed: {e}")

    # ================================================================
    # Internal methods
    # ================================================================

    def _extract_keywords_from_context(self, messages: list, query: str) -> list:
        """
        Extract keywords from recent conversation messages + current user query.

        Strategy:
            1. Combine content from the most recent context_depth messages + current query
            2. Use jieba's extract_keywords_jieba to extract keywords
            3. Deduplicate + sort by weight + take top-15
        """
        from opensquad.tools.agent_memory_tool.memory.storage import extract_keywords_jieba

        # Collect text
        text_parts = []

        # Current user query (highest weight, placed first)
        if query and query.strip():
            text_parts.append(query.strip())

        # Most recent N messages
        if messages:
            recent = messages[-self._context_depth :]
            for msg in recent:
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    # Truncate excessively long single messages (avoid slow jieba processing)
                    text_parts.append(content[:500])

        if not text_parts:
            return []

        combined_text = " ".join(text_parts)

        # jieba keyword extraction (returns list[str])
        try:
            keywords = extract_keywords_jieba(combined_text, min_len=2)
            # Take top-15
            if len(keywords) > 15:
                keywords = keywords[:15]
            return keywords
        except Exception as e:
            logger.warning(f"[MemoryManager] Keyword extraction failed: {e}")
            return []

    def _make_cache_key(self, keywords: list) -> str:
        """Generate a cache key from a keyword list (sorted then hashed)."""
        sorted_kw = sorted(set(keywords))
        raw = "|".join(sorted_kw)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _is_active(self, cache_key: str) -> bool:
        """Check whether a cache_key is already in the active window."""
        return any(item[1] == cache_key for item in self._active_memories)

    def _evict_expired_cache(self):
        """Evict expired query cache entries and their corresponding injection log entries."""
        expired_keys = [k for k, v in self._query_cache.items() if self._turn_counter - v["turn"] > self._cache_ttl]
        for k in expired_keys:
            del self._query_cache[k]
            self._injection_log.pop(k, None)
        if expired_keys:
            logger.debug(f"[MemoryManager] Evicted {len(expired_keys)} cache entries")

    def _trigger_background_rebuild(self):
        """Rebuild the PPMI matrix in a background thread (non-blocking)."""
        if self._rebuilding:
            logger.debug("[MemoryManager] Rebuild already in progress, skipping")
            return

        def _do_rebuild():
            try:
                self._rebuilding = True
                with self._rebuild_lock:
                    self._am.rebuild_matrices()
                    # Persist matrix
                    if self._am._data_dir:
                        self._am.save(self._am._data_dir)
                logger.info("[MemoryManager] Background matrix rebuild completed")
            except Exception as e:
                logger.warning(f"[MemoryManager] Background rebuild failed: {e}")
            finally:
                self._rebuilding = False

        thread = threading.Thread(target=_do_rebuild, daemon=True)
        thread.start()
