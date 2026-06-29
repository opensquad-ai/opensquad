# -*- coding: utf-8 -*-
from __future__ import annotations
"""
AgentMemory - Unified API for multi-agent shared memory system

This is the sole external interface for the entire memory system. It encapsulates:
- Co-occurrence matrix construction and management (IncrementalCooccurrence)
- PPMI / conditional probability computation (probability)
- Time decay (DecayManager)
- Memory entry storage (MemoryStore, SQLite real-time persistence)
- Multi-level retrieval (MemoryRetriever)
- Hidden-chain inference (chain)

Three memory types:
    knowledge  -- facts, concepts, stable knowledge (semantic memory)
    experience -- lessons learned, pattern summaries (experiential memory)
    log        -- activity events, what happened (episodic memory)

Usage:
    from memory import AgentMemory

    # Initialize (SQLite persistence mode)
    am = AgentMemory(data_dir="./memory_data")

    # 1. Ingest documents (build knowledge graph)
    am.ingest_document("Trump announced additional tariffs on China...")
    am.ingest_documents_from_csv("news.csv")

    # 2. Write memory entries (AI agent writes structured memories)
    entry_id = am.write(topic="US-China trade friction", keywords=["tariff", "export"],
                        summary="...", importance=4)

    # 3. Query memory (inject prompt context)
    result = am.query(keywords=["tariff", "export"], depth="standard", token_budget=1000)
    print(result["prompt_text"])  # inject directly into agent prompt

    # 4. Chain inference (complex problem analysis)
    chain = am.find_chain(["Trump", "A-share"])

    # 5. Memory reconsolidation (correct old knowledge)
    new_id = am.write(topic="New finding", keywords=["AI"],
                      importance=5, supersedes=old_id)

    # 6. Sleep consolidation
    am.consolidate(min_cooccurrence=2, rebuild_from_recent=90)

    # 7. Persist (matrix + config; entries are already saved to SQLite in real time)
    am.save("./memory_data")
    am.load("./memory_data")
"""

import os
import json
import time
import re
import numpy as np
from scipy.sparse import save_npz, load_npz

from .cooccurrence import IncrementalCooccurrence
from .probability import compute_ppmi_matrix, compute_conditional_prob_matrix
from .decay import DecayManager
from .storage import MemoryStore, extract_keywords_jieba, extract_nouns_jieba, parse_time_expression
from .retriever import MemoryRetriever
from .chain import discover_hidden_chain, discover_hidden_chain_with_evidence


class AgentMemory:
    """
    Multi-agent shared long-term associative memory system - unified interface.

    Three service layers:
        Layer 1: Auto-inject associated memories on every conversation turn (query)
        Layer 2: On-demand chain inference for complex questions (find_chain)
        Layer 3: Log memory (log / recall_by_date / recall_by_range / summarize)

    Three memory types:
        knowledge  -- facts, concepts, stable knowledge (semantic memory)
        experience -- lessons learned, pattern summaries (experiential memory)
        log        -- activity events, what happened (episodic memory)

    Three brain-inspired mechanisms:
        Retrieval reinforcement -- query hits automatically increment access_count
                                   (statistics only, does not affect ranking)
        Importance grading      -- importance 1~5 affects time decay rate and ranking weight
        Memory reconsolidation  -- supersedes points to the old entry; old entry auto-demoted

    Args:
        data_dir: str | None - Data directory path. If given, SQLite persists in real time
            to data_dir/memory.db; if None, pure in-memory mode (backward compatible).
        max_dim: int - Max dimension of co-occurrence matrix (vocabulary cap)
        min_cooccurrence: int - Pruning threshold (word pairs below this count are filtered)
        decay_rate: float - Co-occurrence matrix decay rate (keep 1-decay_rate each step)
        decay_interval: int - How many documents between decay steps
        time_decay_lambda: float - Memory entry time decay coefficient lambda
            (inverse function 1/(1+lambda*days))
    """

    def __init__(self, data_dir=None, max_dim=100000, min_cooccurrence=5,
                 decay_rate=0.005, decay_interval=500,
                 time_decay_lambda=0.1):
        # Configuration
        self._data_dir = data_dir
        self._config = {
            "max_dim": max_dim,
            "min_cooccurrence": min_cooccurrence,
            "decay_rate": decay_rate,
            "decay_interval": decay_interval,
            "time_decay_lambda": time_decay_lambda,
        }

        # Core components
        self._cooccurrence = IncrementalCooccurrence(max_dim=max_dim)
        self._decay = DecayManager(decay_rate=decay_rate,
                                   decay_interval=decay_interval)

        # SQLite storage: real-time persistence in data_dir mode, otherwise pure in-memory
        if data_dir:
            os.makedirs(data_dir, exist_ok=True)
            db_path = os.path.join(data_dir, "memory.db")
            self._store = MemoryStore(db_path=db_path)
        else:
            self._store = MemoryStore()

        # Matrix cache (available only after rebuild_matrices)
        self._pruned_matrix = None
        self._ppmi_matrix = None

        # Retriever (lazily initialized; auto-created after rebuild_matrices)
        self._retriever = None

        # State tracking
        self._matrices_dirty = True  # flag whether matrices need rebuilding
        self._last_rebuild_docs = 0

        # Episodic Layer (log memory) indexes
        self._date_index = {}        # {"2026-02-09": ["mem_001", ...]} in chronological order
        self._category_index = {}    # {"work": {"mem_001", ...}}
        self._episodic_ids = set()   # marks which entries are log entries

    # ================================================================
    # Document ingestion (build co-occurrence knowledge)
    # ================================================================

    def ingest_document(self, text, min_word_len=2):
        """
        Ingest a document text and learn word co-occurrence relationships.

        Args:
            text: str - raw document text
            min_word_len: int - minimum word length (filter single characters)
        """
        if not text or not isinstance(text, str):
            return

        words = extract_keywords_jieba(text, min_len=min_word_len)
        if len(words) < 2:
            return

        self._cooccurrence.add_document(words)
        self._decay.maybe_decay(self._cooccurrence)
        self._matrices_dirty = True

    def ingest_documents_from_csv(self, csv_path, content_column="content",
                                  min_word_len=2, progress_callback=None):
        """
        Batch-ingest documents from a CSV file.

        Args:
            csv_path: str - path to the CSV file
            content_column: str - name of the content column
            min_word_len: int - minimum word length
            progress_callback: callable | None - progress callback fn(current, total)
                (Note: do NOT use tqdm in pipeline/subprocess; it causes buffer overflow)

        Returns:
            dict - {"docs_processed": int, "docs_skipped": int, "time_seconds": float}
        """
        import pandas as pd

        t0 = time.time()
        df = pd.read_csv(csv_path)

        if content_column not in df.columns:
            raise ValueError(f"Column '{content_column}' not found in CSV, "
                             f"available columns: {list(df.columns)}")

        docs_processed = 0
        docs_skipped = 0
        total = len(df)

        for idx, row in df.iterrows():
            text = row.get(content_column)
            if not isinstance(text, str) or len(text.strip()) < 10:
                docs_skipped += 1
                continue

            self.ingest_document(text, min_word_len=min_word_len)
            docs_processed += 1

            if progress_callback and docs_processed % 100 == 0:
                progress_callback(docs_processed, total)

        elapsed = time.time() - t0
        return {
            "docs_processed": docs_processed,
            "docs_skipped": docs_skipped,
            "time_seconds": round(elapsed, 2),
        }

    # ================================================================
    # Memory entry CRUD
    # ================================================================

    def write(self, topic=None, keywords=None, summary=None, body=None,
              source=None, auto_extract_keywords=False, timestamp=None,
              entry_type="knowledge", category=None, importance=3,
              supersedes=None):
        """
        Write a memory entry.

        Args:
            topic: str | None - topic
            keywords: list[str] | None - keyword list (provided directly by AI)
            summary: str | None - summary
            body: str | None - body text
            source: str | None - source identifier (which agent wrote it)
            auto_extract_keywords: bool - whether to auto-extract keywords via jieba if none given
            timestamp: float | None - write timestamp, defaults to current time
            entry_type: str - entry type 'knowledge'/'experience'/'log'
            category: str | None - category (free text)
            importance: int - importance level 1~5, default 3
            supersedes: str | None - ID of the old entry being superseded (reconsolidation)

        Returns:
            str - entry_id
        """
        ts = timestamp if timestamp is not None else time.time()
        date_str = self._ts_to_date_str(ts)

        entry_id = self._store.add(
            topic=topic, keywords=keywords,
            summary=summary, body=body,
            source=source,
            auto_extract_keywords=auto_extract_keywords,
            timestamp=ts,
            entry_type=entry_type,
            category=category,
            date_str=date_str,
            importance=importance,
            supersedes=supersedes,
        )

        # Synchronous learning: inject entry keywords into co-occurrence matrix (Channel B)
        entry = self._store.get(entry_id)
        if entry:
            ai_gave_keywords = bool(keywords)  # AI explicitly provided keywords
            entry_keywords = entry.get("keywords") or []

            if ai_gave_keywords and len(entry_keywords) >= 2:
                # AI gave keywords explicitly -> trust AI, use only keywords for co-occurrence
                self._cooccurrence.add_keywords(entry_keywords)
                self._matrices_dirty = True
            elif not ai_gave_keywords:
                # Auto mode -> extract nominal concept words from topic + keywords + summary
                # extract_nouns_jieba returns [(word, weight), ...] sorted by TF-IDF weight descending
                text_parts = []
                if entry.get("topic"):
                    text_parts.append(entry["topic"])
                if entry_keywords:
                    text_parts.extend(entry_keywords)
                if entry.get("summary"):
                    text_parts.append(entry["summary"])
                combined_text = " ".join(text_parts)
                noun_pairs = extract_nouns_jieba(combined_text, top_k=20)
                nouns = [w for w, _ in noun_pairs]
                # Also merge auto-extracted keywords (obtained via extract_keywords_jieba,
                # may include non-nouns but already filtered)
                noun_set = set(nouns)
                for kw in entry_keywords:
                    if kw not in noun_set:
                        noun_set.add(kw)
                        nouns.append(kw)
                if len(nouns) >= 2:
                    self._cooccurrence.add_keywords(nouns)
                    self._matrices_dirty = True

        return entry_id

    def read(self, entry_id):
        """
        Read a memory entry.

        Args:
            entry_id: str - entry ID
        Returns:
            dict | None - entry content, or None if not found
        """
        return self._store.get(entry_id)

    def remove(self, entry_id):
        """
        Delete a memory entry.

        Args:
            entry_id: str - entry ID
        Returns:
            bool - whether deletion succeeded
        """
        return self._store.remove(entry_id)

    def list_entries(self, source_filter=None, entry_type=None):
        """
        List all memory entries (optionally filtered by source or type).

        Args:
            source_filter: str | None - list only a specific agent's entries
            entry_type: str | None - list only a specific type 'knowledge'/'experience'/'log'
        Returns:
            list[dict] - list of entries
        """
        return self._store.list_entries(
            source_filter=source_filter,
            entry_type=entry_type,
        )

    # ================================================================
    # Query (main API - for agent prompt injection)
    # ================================================================

    def query(self, keywords=None, user_input=None, depth="standard",
              token_budget=1000, time_recent=None, time_range=None,
              source_filter=None, top_n_expand=10, top_n_entries=10,
              chain_fuzzy=False, auto_parse_time=False,
              long_threshold=80, core_ratio=0.2, important_ratio=0.4):
        """
        Multi-level memory query; returns a result ready to inject into a prompt.

        Args:
            keywords: list[str] | None - AI-provided keywords (higher priority)
            user_input: str | None - raw user input (fallback jieba extraction)
            depth: str - "fast" / "standard" / "deep"
                fast:     exact + fuzzy matching (milliseconds)
                standard: + PPMI association expansion (hundreds of ms)
                deep:     + hidden-chain inference (seconds)
            token_budget: int - max tokens for prompt injection
            time_recent: float | None - search only memories within the last N hours
            time_range: tuple(start_ts, end_ts) | None - exact time range
            source_filter: str | None - search only a specific agent's memories
            top_n_expand: int - top-N associated words for association expansion
            top_n_entries: int - max entries to return
            chain_fuzzy: bool - whether hidden-chain words also do fuzzy matching (default False)
            auto_parse_time: bool - whether to auto-parse time expressions from user_input
                When True, automatically recognizes time expressions (e.g. "last week",
                "two months ago"), converts them to time_range constraints, and strips
                time words from search keywords.
                Only takes effect when user_input is non-empty and time_range/time_recent
                are not explicitly provided.
            long_threshold: int - long-text mode trigger threshold (chars), default 80
                user_input longer than this uses TF-IDF tiered keyword extraction,
                preventing keyword explosion and search noise.
            core_ratio: float - fraction of extracted words that are core (long-text mode), default 0.2
            important_ratio: float - fraction of extracted words that are important (long-text mode), default 0.4

        Returns:
            dict - {
                "prompt_text": str,        # assembled injection text (<=token_budget)
                "matched_entries": list,   # hit entry details
                "expanded_keywords": list, # association-expanded words
                "chain": dict | None,      # inference chain (deep only)
                "search_stats": dict,      # search statistics
                "parsed_time": dict | None, # time parse result (when auto_parse_time=True)
            }
        """
        # ---- Automatic time expression parsing ----
        parsed_time_info = None
        if auto_parse_time and user_input and time_range is None and time_recent is None:
            parsed = parse_time_expression(user_input)
            if parsed["time_range"] is not None:
                time_range = parsed["time_range"]
                user_input = parsed["cleaned_text"]
                parsed_time_info = parsed

        # Ensure retriever is available
        self._ensure_retriever()

        result = self._retriever.retrieve(
            user_input=user_input,
            keywords=keywords,
            depth=depth,
            token_budget=token_budget,
            time_range=time_range,
            time_recent=time_recent,
            time_decay=self._config["time_decay_lambda"],
            source_filter=source_filter,
            top_n_expand=top_n_expand,
            top_n_entries=top_n_entries,
            chain_fuzzy=chain_fuzzy,
            long_threshold=long_threshold,
            core_ratio=core_ratio,
            important_ratio=important_ratio,
        )

        # Attach time parse info
        result["parsed_time"] = parsed_time_info

        # Retrieval reinforcement: increment access_count on hit entries
        matched = result.get("matched_entries", [])
        if matched:
            hit_ids = [e["id"] for e in matched if "id" in e]
            if hit_ids:
                self._store.increment_access(hit_ids)

        return result

    # ================================================================
    # Chain inference (on-demand - for complex problem analysis)
    # ================================================================

    def find_chain(self, anchor_words, top_k_candidates=50, alpha=0.85,
                   min_edge_weight=0.1, top_n=10, with_evidence=False):
        """
        Hidden-chain inference: given anchor words, discover intermediate
        associated concepts and chain paths.

        Args:
            anchor_words: list[str] - anchor words (at least 2)
            top_k_candidates: int - number of PPR candidate words
            alpha: float - PPR damping factor
            min_edge_weight: float - minimum edge weight threshold for the graph
            top_n: int - number of hidden words to return
            with_evidence: bool - whether to attach memory entry text evidence

        Returns:
            dict - {
                "hidden_words": [...],
                "chains": [...],
                "anchors_found": [...],
                "anchors_missing": [...],
                "evidence": [...] (only when with_evidence=True),
            }
        """
        self._ensure_matrices()

        if self._ppmi_matrix is None:
            return {
                "hidden_words": [], "chains": [],
                "anchors_found": [], "anchors_missing": list(anchor_words),
                "error": "PPMI matrix not built; please ingest documents and call rebuild_matrices() first",
            }

        if with_evidence:
            return discover_hidden_chain_with_evidence(
                self._ppmi_matrix,
                self._cooccurrence.vocab_dict,
                anchor_words,
                memory_store=self._store,
                top_k_candidates=top_k_candidates,
                alpha=alpha,
                min_edge_weight=min_edge_weight,
                top_n=top_n,
                fallback_matrix=self._pruned_matrix,
            )
        else:
            return discover_hidden_chain(
                self._ppmi_matrix,
                self._cooccurrence.vocab_dict,
                anchor_words,
                top_k_candidates=top_k_candidates,
                alpha=alpha,
                min_edge_weight=min_edge_weight,
                top_n=top_n,
                fallback_matrix=self._pruned_matrix,
            )

    # ================================================================
    # Log memory - Episodic Layer
    # ================================================================

    @staticmethod
    def _ts_to_date_str(ts):
        """Timestamp -> local date string 'YYYY-MM-DD'"""
        return time.strftime("%Y-%m-%d", time.localtime(ts))

    @staticmethod
    def _ts_to_time_str(ts):
        """Timestamp -> local time string 'HH:MM'"""
        return time.strftime("%H:%M", time.localtime(ts))

    @staticmethod
    def _date_str_to_ts_range(date_str):
        """Date string -> (timestamp of 00:00:00 that day, timestamp of 00:00:00 next day)"""
        import datetime
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        start = dt.timestamp()
        end = start + 86400
        return start, end

    def _index_episodic(self, entry_id, date_str, category):
        """Index a log entry into the date index and category index."""
        # Date index (maintain list; entries are written in chronological order)
        if date_str not in self._date_index:
            self._date_index[date_str] = []
        self._date_index[date_str].append(entry_id)

        # Category index
        if category:
            if category not in self._category_index:
                self._category_index[category] = set()
            self._category_index[category].add(entry_id)

        # Mark as log entry
        self._episodic_ids.add(entry_id)

    def log(self, content, detail=None, category=None, tags=None,
            source=None, timestamp=None, auto_extract_keywords=True,
            importance=3):
        """
        Write a log entry (activity/event record).

        Differences from write():
        - entry_type is automatically set to 'log'
        - Automatically builds date index, supporting recall_by_date() queries
        - Automatically builds category index, supporting category filtering
        - Keyword auto-extraction is enabled by default
        - Keywords are written to the co-occurrence matrix (merged with knowledge graph)

        Args:
            content: str - activity summary (required)
            detail: str | None - detailed content (optional, long text)
            category: str | None - category label (free text, e.g. "work", "life")
            tags: list[str] | None - manual tags (merged with auto-extracted keywords)
            source: str | None - source identifier
            timestamp: float | None - event time, defaults to current time
            auto_extract_keywords: bool - whether to auto-extract keywords from content
            importance: int - importance level 1~5, default 3

        Returns:
            str - entry_id
        """
        if not content or not isinstance(content, str):
            raise ValueError("content cannot be empty")

        ts = timestamp if timestamp is not None else time.time()

        # Merge tags (manual tags take priority; auto extraction fills the rest)
        final_keywords = list(tags) if tags else None

        # Field mapping: category->topic, content->summary, detail->body
        entry_id = self.write(
            topic=category,
            keywords=final_keywords,
            summary=content,
            body=detail,
            source=source,
            auto_extract_keywords=auto_extract_keywords,
            timestamp=ts,
            entry_type="log",
            category=category,
            importance=importance,
        )

        # Build episodic-specific index
        date_str = self._ts_to_date_str(ts)
        self._index_episodic(entry_id, date_str, category)

        return entry_id

    def recall_by_date(self, date_str):
        """
        Query logs by date - pure time-stream query.

        Args:
            date_str: str - date string "YYYY-MM-DD"

        Returns:
            dict - {
                "date": str,
                "entries": [
                    {"id": str, "time": str, "category": str,
                     "content": str, "detail": str|None,
                     "tags": list|None, "source": str|None},
                    ...
                ],  # sorted in chronological order
                "count": int,
                "categories": {str: int},  # category counts for the day
            }
        """
        entry_ids = self._date_index.get(date_str, [])

        entries = []
        cat_counts = {}
        for eid in entry_ids:
            entry = self._store.get(eid)
            if entry is None:
                continue
            category = entry.get("topic")
            entries.append({
                "id": eid,
                "time": self._ts_to_time_str(entry["timestamp"]),
                "category": category,
                "content": entry.get("summary"),
                "detail": entry.get("body"),
                "tags": entry.get("keywords"),
                "source": entry.get("source"),
            })
            if category:
                cat_counts[category] = cat_counts.get(category, 0) + 1

        return {
            "date": date_str,
            "entries": entries,
            "count": len(entries),
            "categories": cat_counts,
        }

    def recall_by_range(self, start_date, end_date, category=None,
                        keyword=None, source_filter=None):
        """
        Query logs by time range (with optional category/keyword filters).

        Args:
            start_date: str - start date "YYYY-MM-DD" (inclusive)
            end_date: str - end date "YYYY-MM-DD" (inclusive)
            category: str | None - look at only a specific category
            keyword: str | None - additional keyword filter (searches content/detail/tags)
            source_filter: str | None - look at only a specific source

        Returns:
            dict - {
                "range": [start_date, end_date],
                "total_count": int,
                "days": {
                    "2026-02-06": [entry_dicts...],
                    "2026-02-07": [entry_dicts...],
                    ...
                },
                "categories": {str: int},  # category counts for the entire range
            }
        """
        import datetime
        dt_start = datetime.datetime.strptime(start_date, "%Y-%m-%d")
        dt_end = datetime.datetime.strptime(end_date, "%Y-%m-%d")

        days = {}
        total_count = 0
        all_cat_counts = {}

        # Iterate over every day in the date range
        dt_cur = dt_start
        while dt_cur <= dt_end:
            ds = dt_cur.strftime("%Y-%m-%d")
            entry_ids = self._date_index.get(ds, [])

            day_entries = []
            for eid in entry_ids:
                entry = self._store.get(eid)
                if entry is None:
                    continue

                # Category filter
                entry_cat = entry.get("topic")
                if category and entry_cat != category:
                    continue

                # Source filter
                if source_filter and entry.get("source") != source_filter:
                    continue

                # Keyword filter (search in content/detail/tags)
                if keyword:
                    found = False
                    if entry.get("summary") and keyword in entry["summary"]:
                        found = True
                    if not found and entry.get("body") and keyword in entry["body"]:
                        found = True
                    if not found and entry.get("keywords"):
                        for kw in entry["keywords"]:
                            if keyword in kw or kw in keyword:
                                found = True
                                break
                    if not found:
                        continue

                day_entries.append({
                    "id": eid,
                    "time": self._ts_to_time_str(entry["timestamp"]),
                    "category": entry_cat,
                    "content": entry.get("summary"),
                    "detail": entry.get("body"),
                    "tags": entry.get("keywords"),
                    "source": entry.get("source"),
                })

                if entry_cat:
                    all_cat_counts[entry_cat] = all_cat_counts.get(entry_cat, 0) + 1

            if day_entries:
                days[ds] = day_entries
                total_count += len(day_entries)

            dt_cur += datetime.timedelta(days=1)

        return {
            "range": [start_date, end_date],
            "total_count": total_count,
            "days": days,
            "categories": all_cat_counts,
        }

    def summarize(self, period="week", end_date=None):
        """
        Aggregate statistics for logs within a specified time period.

        Returns structured data without generating text (leave text generation to AI agent).

        Args:
            period: str - "day" / "week" / "month"
            end_date: str | None - cutoff date "YYYY-MM-DD", defaults to today

        Returns:
            dict - {
                "period": str,             # "2026-02-03 ~ 2026-02-09"
                "period_type": str,        # "week"
                "total_activities": int,
                "by_category": {str: int},
                "by_day": {"2026-02-03": int, ...},
                "entries": [entry_dicts...],  # all entries (for AI summary generation)
            }
        """
        import datetime

        if end_date:
            dt_end = datetime.datetime.strptime(end_date, "%Y-%m-%d")
        else:
            dt_end = datetime.datetime.now()

        if period == "day":
            dt_start = dt_end
        elif period == "week":
            dt_start = dt_end - datetime.timedelta(days=6)
        elif period == "month":
            dt_start = dt_end - datetime.timedelta(days=29)
        else:
            raise ValueError(f"period must be 'day'/'week'/'month', got: {period}")

        start_str = dt_start.strftime("%Y-%m-%d")
        end_str = dt_end.strftime("%Y-%m-%d")

        # Reuse recall_by_range to get all entries
        range_result = self.recall_by_range(start_str, end_str)

        # Count per day
        by_day = {}
        all_entries = []
        for ds, day_entries in sorted(range_result["days"].items()):
            by_day[ds] = len(day_entries)
            all_entries.extend(day_entries)

        return {
            "period": f"{start_str} ~ {end_str}",
            "period_type": period,
            "total_activities": range_result["total_count"],
            "by_category": range_result["categories"],
            "by_day": by_day,
            "entries": all_entries,
        }

    # ================================================================
    # Importance management + memory reconsolidation
    # ================================================================

    def set_importance(self, entry_id, level):
        """
        Set the importance level of an entry.

        Args:
            entry_id: str - entry ID
            level: int - importance 1~5
        Returns:
            bool - whether it succeeded (whether the entry exists)
        """
        return self._store.set_importance(entry_id, level)

    # ================================================================
    # Matrix management
    # ================================================================

    def rebuild_matrices(self, min_cooccurrence=None):
        """
        Rebuild the PPMI matrix from the current co-occurrence matrix.

        Call this method after ingesting a large number of documents.
        Also automatically rebuilds the retriever.

        Args:
            min_cooccurrence: int | None - pruning threshold; None uses the initial config value

        Returns:
            dict - {
                "vocab_size": int,
                "pruned_nnz": int,
                "ppmi_nnz": int,
                "time_ms": float,
            }
        """
        t0 = time.time()

        thresh = min_cooccurrence if min_cooccurrence is not None \
            else self._config["min_cooccurrence"]

        # Prune
        self._pruned_matrix = self._cooccurrence.prune(min_cooccurrence=thresh)

        # PPMI
        self._ppmi_matrix = compute_ppmi_matrix(
            self._pruned_matrix, self._cooccurrence.total_docs)

        # Rebuild retriever
        self._retriever = MemoryRetriever(
            store=self._store,
            ppmi_matrix=self._ppmi_matrix,
            vocab_dict=self._cooccurrence.vocab_dict,
            cooccurrence_matrix=self._pruned_matrix,
        )

        self._matrices_dirty = False
        self._last_rebuild_docs = self._cooccurrence.total_docs

        elapsed_ms = round((time.time() - t0) * 1000, 2)

        return {
            "vocab_size": self._cooccurrence.vocab_count,
            "pruned_nnz": self._pruned_matrix.nnz,
            "ppmi_nnz": self._ppmi_matrix.nnz,
            "time_ms": elapsed_ms,
        }

    def _ensure_matrices(self):
        """Ensure matrices are built (auto-rebuild if dirty)."""
        if self._matrices_dirty and self._cooccurrence.total_docs > 0:
            self.rebuild_matrices()

    def _ensure_retriever(self):
        """Ensure the retriever is initialized."""
        if self._retriever is None:
            if self._cooccurrence.total_docs > 0:
                self._ensure_matrices()
            # Even without documents, create a base retriever (fast mode only)
            if self._retriever is None:
                self._retriever = MemoryRetriever(store=self._store)

    # ================================================================
    # Sleep consolidation
    # ================================================================

    def consolidate(self, min_cooccurrence=None, max_vocab=None,
                    rebuild_from_recent=None):
        """
        Sleep consolidation: vocabulary pruning + matrix rebuild + persistence.

        Simulates the brain's sleep-time memory consolidation. Designed to run
        periodically (e.g. every night at midnight).

        Flow:
            1. cleanup_vocab() -- remove low-frequency words from co-occurrence matrix
            2. Optional: rebuild co-occurrence matrix from the last N days of memories
               (discard long-ago associations)
            3. rebuild_matrices() -- rebuild PPMI matrix
            4. save() -- persist matrices and config

        Args:
            min_cooccurrence: int | None - minimum co-occurrence count; word pairs below
                this threshold are pruned. Defaults to the config value.
            max_vocab: int | None - vocabulary cap; when exceeded, lowest-frequency words
                are trimmed. Default: no limit.
            rebuild_from_recent: int | None - rebuild matrix using only memories from
                the last N days. Default None (use current matrix, no rebuild).

        Returns:
            dict - {
                "vocab_before": int,
                "vocab_after": int,
                "words_removed": int,
                "rebuild_stats": dict,  # return value of rebuild_matrices()
            }
        """
        vocab_before = self._cooccurrence.vocab_count

        # Step 1: if rebuild_from_recent is set, fetch recent entries from SQLite and rebuild
        if rebuild_from_recent is not None and rebuild_from_recent > 0:
            cutoff_ts = time.time() - rebuild_from_recent * 86400
            recent_entries = self._store.get_entries_since(cutoff_ts)

            # Reset co-occurrence matrix (keep vocab structure, clear counts)
            max_dim = self._config["max_dim"]
            from scipy.sparse import dok_matrix
            self._cooccurrence.matrix = dok_matrix(
                (max_dim, max_dim), dtype=np.float64
            )
            self._cooccurrence.total_docs = 0

            # Rebuild from recent entries' keywords
            for entry in recent_entries:
                kws = entry.get("keywords")
                if kws and len(kws) >= 2:
                    self._cooccurrence.add_keywords(kws)

        # Step 2: vocabulary pruning
        words_removed = self.cleanup_vocab(
            min_cooccurrence=min_cooccurrence,
            max_vocab=max_vocab,
        )

        # Step 3: rebuild matrices
        thresh = min_cooccurrence if min_cooccurrence is not None \
            else self._config["min_cooccurrence"]
        rebuild_stats = self.rebuild_matrices(min_cooccurrence=thresh)

        # Step 4: persist (if data_dir is set)
        if self._data_dir:
            self.save(self._data_dir)

        vocab_after = self._cooccurrence.vocab_count

        return {
            "vocab_before": vocab_before,
            "vocab_after": vocab_after,
            "words_removed": words_removed,
            "rebuild_stats": rebuild_stats,
        }

    def cleanup_vocab(self, min_cooccurrence=None, max_vocab=None):
        """
        Vocabulary pruning: remove low-frequency words from the co-occurrence matrix.

        Note: affects only the co-occurrence matrix (association discovery),
        not the SQLite memory entries.
        Analogous to the brain "forgetting" associations between rare concepts,
        while direct queries can still recall them.

        Args:
            min_cooccurrence: int | None - word pairs with co-occurrence below this are pruned
            max_vocab: int | None - vocabulary cap

        Returns:
            int - number of words removed
        """
        if not hasattr(self._cooccurrence, 'remove_words'):
            return 0

        thresh = min_cooccurrence if min_cooccurrence is not None \
            else self._config["min_cooccurrence"]

        # Find low-frequency words (total co-occurrence count below threshold)
        csr = self._cooccurrence.get_csr_matrix()
        vc = self._cooccurrence.vocab_count

        if vc == 0:
            return 0

        # Compute total co-occurrence frequency for each word
        word_freqs = {}
        for idx in range(vc):
            row_sum = csr[idx, :vc].sum()
            col_sum = csr[:vc, idx].sum()
            total = row_sum + col_sum  # bidirectional co-occurrence sum
            word = self._cooccurrence.idx_to_word.get(idx, None)
            if word:
                word_freqs[word] = total

        # Sort by frequency and find words to remove
        words_to_remove = []
        for word, freq in word_freqs.items():
            if freq < thresh:
                words_to_remove.append(word)

        # If max_vocab is set and current vocab exceeds it
        if max_vocab and (vc - len(words_to_remove)) > max_vocab:
            # Sort by frequency and remove more low-frequency words
            remaining = {w: f for w, f in word_freqs.items()
                         if w not in words_to_remove}
            sorted_remaining = sorted(remaining.items(), key=lambda x: x[1])
            excess = (vc - len(words_to_remove)) - max_vocab
            for w, f in sorted_remaining[:excess]:
                words_to_remove.append(w)

        if words_to_remove:
            self._cooccurrence.remove_words(words_to_remove)
            self._matrices_dirty = True

        return len(words_to_remove)

    # ================================================================
    # Statistics
    # ================================================================

    def get_stats(self):
        """
        Return comprehensive system statistics.

        Returns:
            dict - info about co-occurrence matrix, memory store, decay manager,
                   matrix state, etc.
        """
        store_stats = self._store.get_stats()
        cooc_stats = self._cooccurrence.get_stats()
        decay_info = self._decay.get_info()

        # Log statistics
        episodic_cats = {}
        for eid in self._episodic_ids:
            entry = self._store.get(eid)
            if entry and entry.get("topic"):
                cat = entry["topic"]
                episodic_cats[cat] = episodic_cats.get(cat, 0) + 1

        return {
            "config": self._config,
            "data_dir": self._data_dir,
            "cooccurrence": cooc_stats,
            "store": store_stats,
            "decay": decay_info,
            "matrices": {
                "dirty": self._matrices_dirty,
                "last_rebuild_docs": self._last_rebuild_docs,
                "pruned_nnz": self._pruned_matrix.nnz if self._pruned_matrix is not None else 0,
                "ppmi_nnz": self._ppmi_matrix.nnz if self._ppmi_matrix is not None else 0,
            },
            "episodic": {
                "total_logs": len(self._episodic_ids),
                "total_days": len(self._date_index),
                "categories": episodic_cats,
            },
        }

    # ================================================================
    # Persistence
    # ================================================================

    def save(self, directory):
        """
        Save the memory system state to the specified directory.

        Saved files:
            - config.json:        system config + vocab + decay state
            - memory_store.json:  memory entry backup (JSON, for cross-system migration)
            - cooccurrence.npz:   co-occurrence matrix (DOK->CSR->npz)
            - pruned.npz:         pruned matrix
            - ppmi.npz:           PPMI matrix
            - episodic_meta.json: log index

        Note: in data_dir mode, memory entries are already persisted to SQLite in real time.
        This method mainly saves matrix and config state.

        Args:
            directory: str - directory path to save to
        """
        os.makedirs(directory, exist_ok=True)

        # 1. Co-occurrence matrix -> CSR -> npz
        cooc_csr = self._cooccurrence.get_csr_matrix()
        # Trim to actual vocab_count size to avoid saving a huge empty matrix
        vc = self._cooccurrence.vocab_count
        if vc > 0:
            cooc_trimmed = cooc_csr[:vc, :vc]
            save_npz(os.path.join(directory, "cooccurrence.npz"), cooc_trimmed)
        else:
            # No data; skip saving
            pass

        # 2. Pruned matrix
        if self._pruned_matrix is not None and vc > 0:
            pruned_trimmed = self._pruned_matrix[:vc, :vc]
            save_npz(os.path.join(directory, "pruned.npz"), pruned_trimmed)

        # 3. PPMI matrix
        if self._ppmi_matrix is not None and vc > 0:
            ppmi_trimmed = self._ppmi_matrix[:vc, :vc]
            save_npz(os.path.join(directory, "ppmi.npz"), ppmi_trimmed)

        # 4. Memory entries
        store_path = os.path.join(directory, "memory_store.json")
        self._store.save(store_path)

        # 5. Config + vocab + decay state + metadata
        meta = {
            "config": self._config,
            "vocab_dict": self._cooccurrence.vocab_dict,
            "idx_to_word": {str(k): v for k, v in
                            self._cooccurrence.idx_to_word.items()},
            "vocab_count": self._cooccurrence.vocab_count,
            "total_docs": self._cooccurrence.total_docs,
            "decay_state": self._decay.get_info(),
            "matrices_dirty": self._matrices_dirty,
            "last_rebuild_docs": self._last_rebuild_docs,
            "save_timestamp": time.time(),
        }
        meta_path = os.path.join(directory, "config.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        # 6. Log index (Episodic Layer)
        episodic_meta = {
            "date_index": self._date_index,          # {date_str: [entry_ids]}
            "category_index": {k: sorted(v) for k, v
                               in self._category_index.items()},  # set -> list
            "episodic_ids": sorted(self._episodic_ids),            # set -> list
        }
        ep_path = os.path.join(directory, "episodic_meta.json")
        with open(ep_path, "w", encoding="utf-8") as f:
            json.dump(episodic_meta, f, ensure_ascii=False, indent=2)

    def load(self, directory):
        """
        Load the entire memory system from the specified directory.

        Args:
            directory: str - directory path to load from

        Returns:
            bool - whether loading succeeded
        """
        meta_path = os.path.join(directory, "config.json")
        if not os.path.exists(meta_path):
            return False

        # 1. Load config + metadata
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        self._config = meta.get("config", self._config)

        # Restore vocab
        self._cooccurrence.vocab_dict = meta.get("vocab_dict", {})
        # idx_to_word keys need to be converted back to int
        raw_idx = meta.get("idx_to_word", {})
        self._cooccurrence.idx_to_word = {int(k): v for k, v in raw_idx.items()}
        self._cooccurrence.vocab_count = meta.get("vocab_count", 0)
        self._cooccurrence.total_docs = meta.get("total_docs", 0)

        # Restore decay state
        decay_state = meta.get("decay_state", {})
        self._decay.decay_rate = decay_state.get("decay_rate",
                                                  self._config["decay_rate"])
        self._decay.decay_interval = decay_state.get("decay_interval",
                                                      self._config["decay_interval"])
        self._decay.last_decay_doc = decay_state.get("last_decay_doc", 0)
        self._decay.total_decay_steps = decay_state.get("total_decay_steps", 0)

        self._matrices_dirty = meta.get("matrices_dirty", True)
        self._last_rebuild_docs = meta.get("last_rebuild_docs", 0)

        vc = self._cooccurrence.vocab_count

        # 2. Load co-occurrence matrix
        cooc_path = os.path.join(directory, "cooccurrence.npz")
        if os.path.exists(cooc_path) and vc > 0:
            cooc_csr = load_npz(cooc_path)
            # Restore to max_dim-sized DOK matrix
            from scipy.sparse import dok_matrix
            max_dim = self._config["max_dim"]
            full_dok = dok_matrix((max_dim, max_dim), dtype=np.float64)
            # Fill in loaded data
            cooc_coo = cooc_csr.tocoo()
            for r, c, v in zip(cooc_coo.row, cooc_coo.col, cooc_coo.data):
                full_dok[r, c] = v
            self._cooccurrence.matrix = full_dok

        # 3. Load pruned matrix
        pruned_path = os.path.join(directory, "pruned.npz")
        if os.path.exists(pruned_path):
            self._pruned_matrix = load_npz(pruned_path)
            # Expand back to max_dim size to keep compatibility with vocab_dict indexes
            if self._pruned_matrix.shape[0] < self._config["max_dim"]:
                from scipy.sparse import csr_matrix as csr_ctor
                max_dim = self._config["max_dim"]
                self._pruned_matrix.resize((max_dim, max_dim))

        # 4. Load PPMI matrix
        ppmi_path = os.path.join(directory, "ppmi.npz")
        if os.path.exists(ppmi_path):
            self._ppmi_matrix = load_npz(ppmi_path)
            if self._ppmi_matrix.shape[0] < self._config["max_dim"]:
                max_dim = self._config["max_dim"]
                self._ppmi_matrix.resize((max_dim, max_dim))

        # 5. Load memory entries
        store_path = os.path.join(directory, "memory_store.json")
        if os.path.exists(store_path):
            self._store.load(store_path)

        # 6. Rebuild retriever
        if self._ppmi_matrix is not None:
            self._retriever = MemoryRetriever(
                store=self._store,
                ppmi_matrix=self._ppmi_matrix,
                vocab_dict=self._cooccurrence.vocab_dict,
                cooccurrence_matrix=self._pruned_matrix,
            )
        else:
            self._retriever = MemoryRetriever(store=self._store)

        # 7. Load log index (Episodic Layer)
        ep_path = os.path.join(directory, "episodic_meta.json")
        if os.path.exists(ep_path):
            with open(ep_path, "r", encoding="utf-8") as f:
                ep_meta = json.load(f)
            self._date_index = ep_meta.get("date_index", {})
            self._category_index = {
                k: set(v) for k, v in ep_meta.get("category_index", {}).items()
            }
            self._episodic_ids = set(ep_meta.get("episodic_ids", []))

        return True

    # ================================================================
    # Convenience methods
    # ================================================================

    def __repr__(self):
        stats = self.get_stats()
        mode = "sqlite" if self._data_dir else "memory"
        return (
            f"AgentMemory("
            f"mode={mode}, "
            f"docs={stats['cooccurrence']['total_docs']}, "
            f"vocab={stats['cooccurrence']['vocab_size']}, "
            f"entries={stats['store']['total_entries']}, "
            f"ppmi_nnz={stats['matrices']['ppmi_nnz']})"
        )
