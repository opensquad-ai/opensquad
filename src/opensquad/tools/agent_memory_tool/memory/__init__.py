# -*- coding: utf-8 -*-
"""
Agent long-term associative memory module (SQLite edition)

Main entry point:
    from memory import AgentMemory

    # Pure in-memory mode (backward compatible)
    am = AgentMemory()

    # SQLite persistent mode (recommended)
    am = AgentMemory(data_dir="./memory_data")

Three memory types:
    knowledge  -- facts, concepts, stable knowledge (semantic memory)
    experience -- lessons learned, pattern summaries (experiential memory)
    log        -- activity events, what happened (episodic memory)

Three brain-inspired mechanisms:
    Retrieval reinforcement -- query hits automatically increment access_count
    Importance levels       -- importance 1~5 affects decay and ranking
    Memory reconsolidation  -- supersedes corrects old memories

Core components:
- agent_memory: AgentMemory unified API (recommended)
- cooccurrence: incremental co-occurrence matrix management
                (supports full-text document + keyword list dual-channel learning)
- probability: PPMI / conditional probability matrix computation
- decay: time-decay management
- inference: multi-hop inference search (2-hop + Beam Search v3.1)
- storage: SQLite memory entry storage + keyword_index inverted index + fuzzy search
- chain: hidden chain inference (PPR + shortest path)
- retriever: multi-level retriever (fast/standard/deep) + layered loading + token budget
"""

# Unified API (recommended entry point)
from .agent_memory import AgentMemory

# Low-level modules (for advanced users)
from .cooccurrence import IncrementalCooccurrence
from .probability import compute_ppmi_matrix, compute_conditional_prob_matrix
from .decay import DecayManager
from .inference import find_top_inference_paths, find_group_inference_paths, beam_search_inference
from .storage import MemoryStore, extract_keywords_jieba, extract_nouns_jieba, extract_keywords_weighted, parse_time_expression
from .chain import discover_hidden_chain, discover_hidden_chain_with_evidence
from .retriever import MemoryRetriever, count_tokens
