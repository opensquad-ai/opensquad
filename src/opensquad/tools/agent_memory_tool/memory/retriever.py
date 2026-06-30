from __future__ import annotations

"""
Multi-level memory retriever

Four retrieval depths:
    fast     -- Tier 0 exact + Tier 1 fuzzy                      (milliseconds)
    standard -- Tier 0+1 + Tier 2 PPMI association expansion     (hundreds of ms)
    deep     -- Tier 0+1+2 + Tier 3 hidden-chain inference       (seconds)

Long-text intelligent handling:
    When input exceeds long_threshold characters, TF-IDF extracts weighted keywords
    split into core / important / supplement tiers; each Tier uses a different subset:
        Tier 0 exact:  core + important
        Tier 1 fuzzy:  core only
        Tier 2 expand: core only
        Tier 3 chain:  core only
    Supplement words are used for secondary verification during scoring (bonus only, no penalty).

Layered loading strategy (pack as much info as possible within the token budget):
    Most relevant entry:  topic -> keywords -> summary -> body (truncated)
    Next relevant:        topic -> keywords -> summary
    Then:                 topic -> keywords
    Last:                 association hint

Time weight: inverse decay 1/(1+lambda*days)
"""

import time

import numpy as np
import tiktoken

from .chain import discover_hidden_chain
from .storage import MemoryStore, extract_keywords_jieba, extract_keywords_weighted

# ========================
# tiktoken encoder (lazy init)
# ========================
_encoder = None


def _get_encoder():
    global _encoder
    if _encoder is None:
        _encoder = tiktoken.get_encoding("cl100k_base")  # GPT-4 / GPT-3.5 universal
    return _encoder


def count_tokens(text):
    """Precisely count the number of tokens in text."""
    if not text:
        return 0
    return len(_get_encoder().encode(text))


class MemoryRetriever:
    """
    Multi-level memory retriever.

    Binds a MemoryStore and an optional co-occurrence matrix at init time;
    call retrieve() afterwards to query.
    The association matrix and vocab_dict can be set or updated later via
    set_association_matrix().

    Usage:
        retriever = MemoryRetriever(store)
        retriever.set_association_matrix(ppmi_matrix, vocab_dict)

        # Everyday conversation: fast
        result = retriever.retrieve(keywords=["tariff"], depth="fast")

        # Needs association: standard
        result = retriever.retrieve(keywords=["tariff", "trade"], depth="standard")

        # Complex reasoning: deep
        result = retriever.retrieve(keywords=["trump", "tariff", "A-share"], depth="deep")
    """

    def __init__(self, store, ppmi_matrix=None, vocab_dict=None, cooccurrence_matrix=None):
        """
        Args:
            store: MemoryStore -- memory storage instance
            ppmi_matrix: scipy csr_matrix | None -- PPMI matrix (needed for standard/deep)
            vocab_dict: dict | None -- word-to-index mapping
            cooccurrence_matrix: scipy sparse matrix | None -- raw co-occurrence matrix (deep fallback)
        """
        self.store = store
        self.ppmi_matrix = ppmi_matrix
        self.vocab_dict = vocab_dict
        self.cooccurrence_matrix = cooccurrence_matrix
        # Cache reverse mapping idx->word to avoid rebuilding on every _expand_keywords call
        self._idx_to_word = {v: k for k, v in vocab_dict.items()} if vocab_dict else {}

    def set_association_matrix(self, ppmi_matrix, vocab_dict, cooccurrence_matrix=None):
        """Set or update the PPMI association matrix."""
        self.ppmi_matrix = ppmi_matrix
        self.vocab_dict = vocab_dict
        # Update reverse mapping cache
        self._idx_to_word = {v: k for k, v in vocab_dict.items()} if vocab_dict else {}
        if cooccurrence_matrix is not None:
            self.cooccurrence_matrix = cooccurrence_matrix

    # ========================
    # Main entry point
    # ========================

    def retrieve(
        self,
        user_input=None,
        keywords=None,
        depth="standard",
        token_budget=1000,
        time_range=None,
        time_recent=None,
        time_decay=0.1,
        source_filter=None,
        top_n_expand=10,
        top_n_entries=10,
        chain_fuzzy=False,
        long_threshold=80,
        core_ratio=0.2,
        important_ratio=0.4,
    ):
        """
        Multi-level memory retrieval.

        Args:
            user_input:    str | None -- raw user input text (keywords auto-extracted)
            keywords:      list[str] | None -- keywords given directly by AI (higher priority)
            depth:         str -- "fast" / "standard" / "deep"
            token_budget:  int -- max tokens for prompt injection
            time_range:    tuple(start_ts, end_ts) | None -- exact time-range filter
            time_recent:   float | None -- last N hours
            time_decay:    float -- time decay coefficient lambda (inverse fn 1/(1+lambda*days))
            source_filter: str | None -- search only a specific agent's memories
            top_n_expand:  int -- take top-N associated words during association expansion
            top_n_entries: int -- max memory entries to return
            chain_fuzzy:   bool -- whether hidden-chain words also do fuzzy matching (default False)
            long_threshold: int -- long-text mode trigger threshold (chars), default 80
            core_ratio:    float -- fraction of extracted words that are core, default 0.2
            important_ratio: float -- fraction of extracted words that are important, default 0.4

        Returns:
            dict -- {
                "prompt_text":       str,       # assembled injection text (<=token_budget)
                "matched_entries":   list[dict], # hit entry details
                "expanded_keywords": list[str],  # association-expanded words (standard/deep)
                "chain":             dict|None,  # inference chain result (deep only)
                "search_stats":      dict,       # search statistics
            }
        """
        t_start = time.time()
        stats = {
            "depth_used": depth,
            "total_entries_scanned": len(self.store),
            "time_filtered": 0,
            "exact_hits": 0,
            "fuzzy_hits": 0,
            "assoc_hits": 0,
            "long_text_mode": False,
            "time_ms": 0,
        }

        # === Step 0: Determine query keywords (with long-text intelligent tiering) ===

        # Keywords provided directly by AI (already judged by AI, treated as core)
        ai_keywords = list(keywords) if keywords else []

        # Determine whether long-text mode
        is_long = user_input and isinstance(user_input, str) and len(user_input) > long_threshold

        if is_long:
            # ---- Long-text mode: TF-IDF extraction + tiering ----
            stats["long_text_mode"] = True
            weighted_kws = extract_keywords_weighted(user_input, top_k=25, long_threshold=long_threshold)

            # Remove keywords already given by AI (avoid duplicates)
            ai_set = set(ai_keywords)
            weighted_kws = [(w, s) for w, s in weighted_kws if w not in ai_set]

            total = len(weighted_kws)
            core_count = max(3, int(total * core_ratio))
            important_count = max(3, int(total * important_ratio))

            # Split into three tiers (weighted_kws already sorted by weight descending)
            core_words = [w for w, _ in weighted_kws[:core_count]]
            important_words = [w for w, _ in weighted_kws[core_count : core_count + important_count]]
            supplement_words = [w for w, _ in weighted_kws[core_count + important_count :]]

            # Merge AI keywords into core words (they are key terms already judged by AI)
            core_keywords = ai_keywords + core_words

            # Keyword sets used by each Tier
            tier0_keywords = core_keywords + important_words  # exact: core + important
            tier1_keywords = core_keywords  # fuzzy: core only
            tier2_keywords = core_keywords  # expand: core only
            tier3_keywords = core_keywords  # chain: core only

            stats["core_keywords"] = core_keywords
            stats["important_keywords"] = important_words
            stats["supplement_keywords"] = supplement_words

        else:
            # ---- Short-text mode: original logic unchanged ----
            if ai_keywords and user_input:
                # Both given: merge and deduplicate
                auto_kw = extract_keywords_jieba(user_input)
                merged = list(ai_keywords)
                for w in auto_kw:
                    if w not in merged:
                        merged.append(w)
                query_keywords = merged
            elif ai_keywords:
                query_keywords = ai_keywords
            elif user_input:
                query_keywords = extract_keywords_jieba(user_input)
            else:
                query_keywords = []

            if not query_keywords:
                return self._empty_result(stats, t_start)

            # Short text: all Tiers use the same keyword set
            tier0_keywords = query_keywords
            tier1_keywords = query_keywords
            tier2_keywords = query_keywords
            tier3_keywords = query_keywords
            core_keywords = query_keywords
            supplement_words = []

        if not tier0_keywords:
            return self._empty_result(stats, t_start)

        # === Step 1: Time-range pre-filter (determine candidate pool) ===
        if time_range or time_recent:
            all_ids = list(self.store.entries.keys())
            # Apply source filter first if set
            if source_filter:
                all_ids = [eid for eid in all_ids if self.store.entries[eid].get("source") == source_filter]
            candidate_pool = set(self.store.filter_by_time(all_ids, time_range=time_range, time_recent=time_recent))
            stats["time_filtered"] = len(all_ids) - len(candidate_pool)
        else:
            candidate_pool = None  # None means no restriction

        # === Step 2: Tier 0 exact matching ===
        exact_hits = self.store.search_exact(tier0_keywords)
        if candidate_pool is not None:
            exact_hits = {eid: n for eid, n in exact_hits.items() if eid in candidate_pool}
        if source_filter and candidate_pool is None:
            exact_hits = {
                eid: n
                for eid, n in exact_hits.items()
                if self.store.entries.get(eid, {}).get("source") == source_filter
            }
        stats["exact_hits"] = len(exact_hits)

        # === Step 3: Tier 1 fuzzy matching ===
        fuzzy_hits = self.store.search_fuzzy(tier1_keywords)
        if candidate_pool is not None:
            fuzzy_hits = {eid: s for eid, s in fuzzy_hits.items() if eid in candidate_pool}
        if source_filter and candidate_pool is None:
            fuzzy_hits = {
                eid: s
                for eid, s in fuzzy_hits.items()
                if self.store.entries.get(eid, {}).get("source") == source_filter
            }
        stats["fuzzy_hits"] = len(fuzzy_hits)

        # === Step 4: Tier 2 association expansion (standard / deep) ===
        expanded_keywords = []
        assoc_hits = {}

        if depth in ("standard", "deep") and self.ppmi_matrix is not None and self.vocab_dict is not None:
            expanded_keywords = self._expand_keywords(tier2_keywords, top_n=top_n_expand)
            if expanded_keywords:
                assoc_exact = self.store.search_exact(expanded_keywords)
                assoc_fuzzy = self.store.search_fuzzy(expanded_keywords)
                # Merge; association-expanded hits have half weight
                for eid, n in assoc_exact.items():
                    if candidate_pool is not None and eid not in candidate_pool:
                        continue
                    assoc_hits[eid] = assoc_hits.get(eid, 0) + n * 0.5
                for eid, s in assoc_fuzzy.items():
                    if candidate_pool is not None and eid not in candidate_pool:
                        continue
                    assoc_hits[eid] = max(assoc_hits.get(eid, 0), s * 0.3)
                stats["assoc_hits"] = len(assoc_hits)

        # === Step 5: Tier 3 hidden-chain inference (deep) ===
        chain_result = None
        if depth == "deep" and self.ppmi_matrix is not None and self.vocab_dict is not None:
            if len(tier3_keywords) >= 2:
                chain_result = discover_hidden_chain(
                    self.ppmi_matrix,
                    self.vocab_dict,
                    tier3_keywords,
                    top_k_candidates=50,
                    alpha=0.85,
                    min_edge_weight=0.1,
                    top_n=10,
                    fallback_matrix=self.cooccurrence_matrix,
                )
                # Also query memory for hidden words on the chain
                if chain_result and chain_result.get("hidden_words"):
                    chain_words = [hw["word"] for hw in chain_result["hidden_words"][:5]]
                    chain_hits = self.store.search_exact(chain_words)
                    for eid, n in chain_hits.items():
                        if candidate_pool is not None and eid not in candidate_pool:
                            continue
                        assoc_hits[eid] = assoc_hits.get(eid, 0) + n * 0.4
                    # Optional: fuzzy match hidden-chain words as well
                    if chain_fuzzy:
                        chain_fuzzy_hits = self.store.search_fuzzy(chain_words)
                        for eid, s in chain_fuzzy_hits.items():
                            if candidate_pool is not None and eid not in candidate_pool:
                                continue
                            assoc_hits[eid] = max(assoc_hits.get(eid, 0), s * 0.2)

        # === Step 6: Combined scoring ===
        all_entry_ids = set(exact_hits.keys()) | set(fuzzy_hits.keys()) | set(assoc_hits.keys())
        if not all_entry_ids:
            return self._empty_result(stats, t_start, expanded_keywords, chain_result)

        now = time.time()
        scored_entries = []
        for eid in all_entry_ids:
            entry = self.store.get(eid)
            if entry is None:
                continue

            # Relevance dimension
            exact_score = exact_hits.get(eid, 0)
            fuzzy_score = fuzzy_hits.get(eid, 0)
            assoc_score = assoc_hits.get(eid, 0)
            relevance = exact_score * 1.0 + fuzzy_score * 0.6 + assoc_score * 0.4

            # Long-text mode: supplement-word secondary verification (bonus only, no penalty)
            if supplement_words:
                entry_kws = set(entry.get("keywords") or [])
                entry_text = (entry.get("summary") or "") + (entry.get("body") or "")
                supplement_match = 0
                for sw in supplement_words:
                    if sw in entry_kws or sw in entry_text:
                        supplement_match += 1
                # Verification multiplier: 1.0 ~ 1.5 (1.5 when all supplement words hit)
                verify_bonus = 1.0 + 0.5 * (supplement_match / len(supplement_words))
                relevance *= verify_bonus

            # Time dimension (inverse decay, importance-aware)
            entry_importance = entry.get("importance", 3)
            tw = MemoryStore.compute_time_weight(
                entry["timestamp"], decay_lambda=time_decay, now=now, importance=entry_importance
            )

            # Importance factor: importance=3 -> 1.0 (neutral), 5 -> 1.67, 1 -> 0.33
            importance_factor = entry_importance / 3.0

            final_score = relevance * tw * importance_factor

            age_hours = (now - entry["timestamp"]) / 3600.0

            scored_entries.append(
                {
                    "entry_id": eid,
                    "entry": entry,
                    "relevance_score": round(relevance, 4),
                    "time_weight": round(tw, 4),
                    "final_score": round(final_score, 4),
                    "age_hours": round(age_hours, 2),
                }
            )

        # Sort by combined score descending
        scored_entries.sort(key=lambda x: x["final_score"], reverse=True)
        scored_entries = scored_entries[:top_n_entries]

        # === Step 7: Layered loading (within token budget) ===
        prompt_text, loaded_info = self._layered_load(scored_entries, token_budget, expanded_keywords, chain_result)

        stats["time_ms"] = round((time.time() - t_start) * 1000, 2)

        # Build matched_entries output (exclude raw entry full text to avoid redundancy)
        matched_output = []
        for se in scored_entries:
            entry = se["entry"]
            matched_output.append(
                {
                    "id": se["entry_id"],
                    "entry_id": se["entry_id"],
                    "topic": entry.get("topic"),
                    "keywords": entry.get("keywords"),
                    "relevance_score": se["relevance_score"],
                    "time_weight": se["time_weight"],
                    "importance": entry.get("importance", 3),
                    "final_score": se["final_score"],
                    "timestamp": entry.get("timestamp"),
                    "age_hours": se["age_hours"],
                    "loaded_layers": loaded_info.get(se["entry_id"], []),
                }
            )

        return {
            "prompt_text": prompt_text,
            "matched_entries": matched_output,
            "expanded_keywords": expanded_keywords,
            "chain": chain_result,
            "search_stats": stats,
        }

    # ========================
    # Association expansion
    # ========================

    def _expand_keywords(self, keywords, top_n=10):
        """
        Expand keywords via the PPMI matrix.

        For each input word, find the top_n most associated words by PPMI score
        (excluding the input words themselves).
        Association scores from multiple input words are summed, then top_n are returned.
        """
        if self.ppmi_matrix is None or self.vocab_dict is None:
            return []

        input_set = set(keywords)
        assoc_scores = {}

        for word in keywords:
            if word not in self.vocab_dict:
                continue
            idx = self.vocab_dict[word]
            row = self.ppmi_matrix[idx, :].toarray().flatten()

            # Find non-zero entries
            nonzero = np.where(row > 0)[0]

            for nz_idx in nonzero:
                w = self._idx_to_word.get(nz_idx)
                if w is None or w in input_set:
                    continue
                score = row[nz_idx]
                assoc_scores[w] = assoc_scores.get(w, 0) + score

        # Sort by score descending and take top_n
        sorted_assoc = sorted(assoc_scores.items(), key=lambda x: x[1], reverse=True)
        return [w for w, s in sorted_assoc[:top_n]]

    # ========================
    # Layered loading
    # ========================

    def _layered_load(self, scored_entries, token_budget, expanded_keywords=None, chain_result=None):
        """
        Layered loading of memory entries within the token budget.

        Load priority: topic -> keywords -> summary -> body (truncated)
        Missing fields are skipped automatically.
        Association word hints are appended at the end if budget allows.

        Returns:
            tuple(str, dict) -- (assembled prompt text, {entry_id: [loaded layer names]})
        """
        parts = []
        used_tokens = 0
        loaded_info = {}  # entry_id -> [layer_names]

        # Header marker
        header = "[Memory Context]\n"
        header_tokens = count_tokens(header)
        if header_tokens < token_budget:
            parts.append(header)
            used_tokens += header_tokens

        for i, se in enumerate(scored_entries):
            entry = se["entry"]
            eid = se["entry_id"]
            loaded_info[eid] = []
            remaining = token_budget - used_tokens

            if remaining <= 20:  # Too little space remaining, stop
                break

            entry_parts = []
            entry_prefix = f"\n--- Memory #{i + 1} "
            if entry.get("source"):
                entry_prefix += f"[source:{entry['source']}] "
            # Absolute time
            import time as _time

            ts = entry.get("timestamp")
            if ts:
                abs_time = _time.strftime("%Y-%m-%d %H:%M", _time.localtime(ts))
                entry_prefix += f"[{abs_time}] "

            # Relative time
            age_h = se["age_hours"]
            if age_h < 1:
                entry_prefix += f"[{age_h * 60:.0f}min ago]"
            elif age_h < 24:
                entry_prefix += f"[{age_h:.1f}h ago]"
            else:
                entry_prefix += f"[{age_h / 24:.1f}d ago]"
            entry_prefix += " ---\n"

            prefix_tokens = count_tokens(entry_prefix)
            if prefix_tokens >= remaining:
                break

            entry_parts.append(entry_prefix)
            used_tokens += prefix_tokens
            remaining -= prefix_tokens

            # Layer 1: topic
            if entry.get("topic"):
                text = f"Topic: {entry['topic']}\n"
                t = count_tokens(text)
                if t <= remaining:
                    entry_parts.append(text)
                    used_tokens += t
                    remaining -= t
                    loaded_info[eid].append("topic")

            # Layer 2: keywords
            if entry.get("keywords"):
                text = f"Keywords: {', '.join(entry['keywords'])}\n"
                t = count_tokens(text)
                if t <= remaining:
                    entry_parts.append(text)
                    used_tokens += t
                    remaining -= t
                    loaded_info[eid].append("keywords")

            # Layer 3: summary
            if entry.get("summary"):
                text = f"Summary: {entry['summary']}\n"
                t = count_tokens(text)
                if t <= remaining:
                    entry_parts.append(text)
                    used_tokens += t
                    remaining -= t
                    loaded_info[eid].append("summary")
                elif remaining > 30:
                    # Not enough budget for full summary, truncate
                    truncated = self._truncate_to_tokens(f"Summary: {entry['summary']}", remaining - 5)
                    if truncated:
                        text = truncated + "...\n"
                        t = count_tokens(text)
                        entry_parts.append(text)
                        used_tokens += t
                        remaining -= t
                        loaded_info[eid].append("summary(truncated)")

            # Layer 4: body (only for the top-ranked entry, and only with sufficient budget)
            if i == 0 and entry.get("body") and remaining > 50:
                body_text = f"Detail: {entry['body']}\n"
                t = count_tokens(body_text)
                if t <= remaining:
                    entry_parts.append(body_text)
                    used_tokens += t
                    remaining -= t
                    loaded_info[eid].append("body")
                elif remaining > 80:
                    truncated = self._truncate_to_tokens(f"Detail: {entry['body']}", remaining - 5)
                    if truncated:
                        text = truncated + "...\n"
                        t = count_tokens(text)
                        entry_parts.append(text)
                        used_tokens += t
                        remaining -= t
                        loaded_info[eid].append("body(truncated)")

            parts.extend(entry_parts)

        # Append association word hints (if budget allows)
        if expanded_keywords and (token_budget - used_tokens) > 20:
            hint = f"\n[Associated concepts] {', '.join(expanded_keywords[:8])}\n"
            t = count_tokens(hint)
            if t <= token_budget - used_tokens:
                parts.append(hint)
                used_tokens += t

        # Append inference chain hints (deep mode, if budget allows)
        if chain_result and (token_budget - used_tokens) > 30:
            chain_parts = []

            # Hidden associated words
            hidden = chain_result.get("hidden_words", [])
            if hidden:
                hw_list = [hw["word"] for hw in hidden[:6]]
                chain_parts.append(f"[Hidden associations] {', '.join(hw_list)}")

            # Inference paths
            valid_chains = chain_result.get("chains", [])
            if valid_chains:
                chain_parts.append("[Inference paths]")
                for c in valid_chains[:3]:  # Show at most 3 paths
                    path = c.get("path")
                    if path:
                        chain_parts.append(
                            f"  {c['from']} -> {' -> '.join(path[1:-1])} -> {c['to']}"
                            f" (association strength:{c['total_weight']:.3f})"
                        )

            if chain_parts:
                chain_text = "\n" + "\n".join(chain_parts) + "\n"
                t = count_tokens(chain_text)
                if t <= token_budget - used_tokens:
                    parts.append(chain_text)
                    used_tokens += t

        prompt_text = "".join(parts)
        return prompt_text, loaded_info

    def _truncate_to_tokens(self, text, max_tokens):
        """Truncate text to no more than max_tokens."""
        encoder = _get_encoder()
        tokens = encoder.encode(text)
        if len(tokens) <= max_tokens:
            return text
        truncated_tokens = tokens[:max_tokens]
        return encoder.decode(truncated_tokens)

    # ========================
    # Utilities
    # ========================

    def _empty_result(self, stats, t_start, expanded_keywords=None, chain=None):
        stats["time_ms"] = round((time.time() - t_start) * 1000, 2)
        return {
            "prompt_text": "",
            "matched_entries": [],
            "expanded_keywords": expanded_keywords or [],
            "chain": chain,
            "search_stats": stats,
        }
