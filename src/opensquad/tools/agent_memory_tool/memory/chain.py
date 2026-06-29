# -*- coding: utf-8 -*-
"""
Hidden chain inference module (PPR + shortest path)

Core scenario:
    Given sparse anchor words [a, e], discover hidden words b, c, d, f
    in the co-occurrence graph and reconstruct the full conceptual association chain.

Algorithm flow:
    1. PPMI sparse matrix -> NetworkX weighted graph (edge weight = PPMI, distance = 1/PPMI)
    2. Personalized PageRank: use anchor words as seeds to find top-K hidden word candidates
    3. Run pairwise Dijkstra shortest paths between anchors on the full graph
       -> extract intermediate nodes + vote
    4. PPR score x path vote combined ranking
       (path intermediate nodes receive an additional bonus) -> output hidden words + chain structure
"""

import numpy as np
import networkx as nx
from collections import defaultdict


def _build_networkx_graph(ppmi_matrix, idx_to_word, min_weight=0.1):
    """
    Convert a PPMI sparse matrix to a NetworkX weighted undirected graph.

    Edge attributes:
        - weight: PPMI value (higher = stronger association)
        - distance: 1 / (PPMI + epsilon) (used for shortest paths; smaller = closer)

    Args:
        ppmi_matrix: scipy csr_matrix -- PPMI matrix
        idx_to_word: dict -- index-to-word mapping
        min_weight: float -- minimum PPMI threshold; edges below this are excluded
    Returns:
        nx.Graph -- weighted undirected graph
    """
    G = nx.Graph()
    coo = ppmi_matrix.tocoo()

    for r, c, v in zip(coo.row, coo.col, coo.data):
        if r >= c:          # undirected graph: add only one edge; skip diagonal and duplicates
            continue
        if v < min_weight:
            continue

        word_r = idx_to_word.get(r)
        word_c = idx_to_word.get(c)
        if word_r is None or word_c is None:
            continue

        G.add_edge(word_r, word_c,
                   weight=float(v),
                   distance=1.0 / (float(v) + 1e-8))

    return G


def _personalized_pagerank(G, anchor_words, alpha=0.85, top_k=50):
    """
    Run Personalized PageRank on the graph.

    Seed nodes are all anchor words with uniform initial probability.
    Returns top-K non-anchor nodes and their PPR scores.

    Args:
        G: nx.Graph -- weighted graph
        anchor_words: list[str] -- anchor word list
        alpha: float -- damping factor (0.85 is the classic value)
        top_k: int -- number of candidate words to return
    Returns:
        list[tuple(str, float)] -- [(word, ppr_score), ...] sorted by score descending
    """
    # Build personalization vector: uniform share across anchor words
    anchor_set = set(anchor_words) & set(G.nodes())
    if not anchor_set:
        return []

    personalization = {}
    share = 1.0 / len(anchor_set)
    for node in G.nodes():
        personalization[node] = share if node in anchor_set else 0.0

    # Run PPR
    ppr_scores = nx.pagerank(G, alpha=alpha,
                             personalization=personalization,
                             weight='weight',
                             max_iter=100, tol=1e-6)

    # Exclude anchor words; sort by score descending and take top-K
    candidates = [(word, score) for word, score in ppr_scores.items()
                  if word not in anchor_set]
    candidates.sort(key=lambda x: x[1], reverse=True)

    return candidates[:top_k]


def _check_anchors_connected(G, anchor_words):
    """
    Check whether all anchor words belong to the same connected component.

    Args:
        G: nx.Graph -- weighted graph
        anchor_words: list[str] -- anchor word list
    Returns:
        bool -- whether all are connected
    """
    anchors_in = [w for w in anchor_words if w in G.nodes()]
    if len(anchors_in) < 2:
        return False
    # Check whether all anchors are pairwise connected
    first = anchors_in[0]
    comp = nx.node_connected_component(G, first)
    return all(w in comp for w in anchors_in[1:])


def _find_shortest_paths(G, anchor_words):
    """
    Compute pairwise shortest paths between anchor words on the graph
    (Dijkstra, based on the 'distance' attribute).

    Returns all paths and intermediate node statistics.

    Args:
        G: nx.Graph -- weighted graph
        anchor_words: list[str] -- anchor word list
    Returns:
        tuple(list[dict], dict) --
            paths: [{"from": a, "to": b, "path": [...], "total_weight": float}, ...]
            intermediate_counts: {word: number of paths it appears in as an intermediate node}
    """
    anchors_in_graph = [w for w in anchor_words if w in G.nodes()]
    paths = []
    intermediate_counts = defaultdict(int)

    for i in range(len(anchors_in_graph)):
        for j in range(i + 1, len(anchors_in_graph)):
            src = anchors_in_graph[i]
            tgt = anchors_in_graph[j]

            try:
                path = nx.dijkstra_path(G, src, tgt, weight='distance')
                # Compute total PPMI weight for the path
                total_w = 0
                for k in range(len(path) - 1):
                    edge_data = G.get_edge_data(path[k], path[k + 1])
                    total_w += edge_data.get('weight', 0) if edge_data else 0

                paths.append({
                    "from": src,
                    "to": tgt,
                    "path": path,
                    "total_weight": round(total_w, 4),
                    "hops": len(path) - 1,
                })

                # Count intermediate nodes (excluding start and end anchors)
                anchor_set = set(anchor_words)
                for node in path[1:-1]:
                    if node not in anchor_set:
                        intermediate_counts[node] += 1

            except nx.NetworkXNoPath:
                paths.append({
                    "from": src,
                    "to": tgt,
                    "path": None,
                    "total_weight": 0,
                    "hops": -1,
                })

    return paths, dict(intermediate_counts)


def discover_hidden_chain(ppmi_matrix, vocab_dict, anchor_words,
                          top_k_candidates=50, alpha=0.85,
                          min_edge_weight=0.1, top_n=10,
                          auto_reduce_weight=True,
                          fallback_matrix=None):
    """
    Hidden chain inference: given sparse anchor words, discover hidden intermediate
    association words and the chain structure.

    Algorithm:
        1. PPMI matrix -> NetworkX weighted graph
        2. Personalized PageRank to find candidate hidden words
        3. Compute pairwise shortest paths between anchors on the full graph
           (not a subgraph, to avoid losing paths due to sparsity)
        4. Combined score = PPR score x path vote ranking
           (path intermediate nodes receive an additional bonus)

    Args:
        ppmi_matrix: scipy csr_matrix -- PPMI matrix
        vocab_dict: dict -- word-to-index mapping
        anchor_words: list[str] -- anchor word list (at least 2)
        top_k_candidates: int -- number of PPR candidate words
        alpha: float -- PPR damping factor
        min_edge_weight: float -- minimum graph edge weight (PPMI below this is excluded)
        top_n: int -- number of hidden words to return
        auto_reduce_weight: bool -- automatically lower edge-weight threshold when anchors are disconnected
        fallback_matrix: scipy sparse matrix | None -- fallback matrix (e.g. raw co-occurrence matrix)
            used to supplement weak-association edges when anchors are still disconnected in the PPMI graph

    Returns:
        dict -- {
            "hidden_words": [
                {"word": str, "ppr_score": float, "path_count": int,
                 "combined_score": float},
                ...
            ],
            "chains": [
                {"from": str, "to": str, "path": list, "total_weight": float, "hops": int},
                ...
            ],
            "subgraph_nodes": int,
            "subgraph_edges": int,
            "anchors_found": list[str],
            "anchors_missing": list[str],
        }
    """
    idx_to_word = {v: k for k, v in vocab_dict.items()}

    # --- Step 1: Build full graph ---
    G_full = _build_networkx_graph(ppmi_matrix, idx_to_word,
                                   min_weight=min_edge_weight)

    # Check which anchor words are present in the graph
    anchors_found = [w for w in anchor_words if w in G_full.nodes()]
    anchors_missing = [w for w in anchor_words if w not in G_full.nodes()]

    if len(anchors_found) < 2:
        return {
            "hidden_words": [],
            "chains": [],
            "subgraph_nodes": 0,
            "subgraph_edges": 0,
            "anchors_found": anchors_found,
            "anchors_missing": anchors_missing,
            "error": f"At least 2 anchor words are needed in the graph; only {len(anchors_found)} found",
        }

    # --- Step 1.5: Connectivity check + auto weight reduction ---
    # If anchor words are not in the same connected component, attempt recovery:
    #   a) Lower min_edge_weight threshold and rebuild the PPMI graph
    #   b) If still disconnected and fallback_matrix is provided, supplement with raw co-occurrence edges
    if auto_reduce_weight and len(anchors_found) >= 2:
        _all_connected = _check_anchors_connected(G_full, anchors_found)
        if not _all_connected:
            # Try lowering the threshold
            for try_weight in [min_edge_weight * 0.5, 0.05, 0.01, 0.001, 0.0]:
                G_try = _build_networkx_graph(ppmi_matrix, idx_to_word,
                                              min_weight=try_weight)
                if _check_anchors_connected(G_try, anchors_found):
                    G_full = G_try
                    _all_connected = True
                    break

            # If PPMI graph at weight=0 is still disconnected, supplement with fallback_matrix
            if not _all_connected and fallback_matrix is not None:
                G_fallback = _build_networkx_graph(fallback_matrix, idx_to_word,
                                                   min_weight=0.5)
                # Add edges from fallback graph to G_full (only add, never overwrite)
                for u, v, data in G_fallback.edges(data=True):
                    if not G_full.has_edge(u, v):
                        # Fallback edges carry reduced weight, marked as weak associations
                        G_full.add_edge(u, v,
                                        weight=data['weight'] * 0.1,
                                        distance=data['distance'] * 10)
                # Re-check anchors_found (fallback may have added new nodes)
                anchors_found = [w for w in anchor_words if w in G_full.nodes()]
                anchors_missing = [w for w in anchor_words if w not in G_full.nodes()]

    # --- Step 2: Personalized PageRank ---
    ppr_candidates = _personalized_pagerank(G_full, anchors_found,
                                            alpha=alpha,
                                            top_k=top_k_candidates)

    if not ppr_candidates:
        return {
            "hidden_words": [],
            "chains": [],
            "subgraph_nodes": len(anchors_found),
            "subgraph_edges": 0,
            "anchors_found": anchors_found,
            "anchors_missing": anchors_missing,
            "error": "PPR found no candidate words",
        }

    # PPR score map
    ppr_score_map = {word: score for word, score in ppr_candidates}

    # --- Step 3: Compute pairwise shortest paths between anchors on the full graph ---
    # Previously ran shortest paths on the candidate subgraph; the subgraph was too sparse
    # and paths were often missing. Now run on the full graph; PPR scores are used only for
    # ranking bonus, not for restricting the search scope.
    chains, intermediate_counts = _find_shortest_paths(G_full, anchors_found)

    # Record subgraph statistics (for return info only; does not affect logic)
    subgraph_nodes = set(anchors_found) | set(ppr_score_map.keys())
    n_sub_nodes = len(subgraph_nodes & set(G_full.nodes()))
    n_sub_edges = G_full.subgraph(subgraph_nodes).number_of_edges()

    # --- Step 4: Combined scoring ---
    # combined_score = ppr_score x (1 + path_count) + path_only_bonus
    # - High-PPR nodes: even if not on any path, they have base score (ppr_score x 1)
    # - Path intermediate nodes: higher path_count gives larger bonus
    # - Nodes only discovered via path (not in PPR candidates): given a path_only_bonus
    PATH_ONLY_BONUS = 0.001   # base score for nodes discovered only through paths

    # Collect all words that need scoring: PPR candidates + path intermediate nodes
    all_scored_words = set(ppr_score_map.keys()) | set(intermediate_counts.keys())

    hidden_words = []
    for word in all_scored_words:
        ppr_score = ppr_score_map.get(word, 0.0)
        path_count = intermediate_counts.get(word, 0)

        if ppr_score > 0:
            combined = ppr_score * (1.0 + path_count)
        else:
            # Node discovered only through paths
            combined = PATH_ONLY_BONUS * path_count

        hidden_words.append({
            "word": word,
            "ppr_score": round(ppr_score, 6),
            "path_count": path_count,
            "combined_score": round(combined, 6),
        })

    # Sort by combined score
    hidden_words.sort(key=lambda x: x["combined_score"], reverse=True)
    hidden_words = hidden_words[:top_n]

    # Filter out chains with no valid path
    valid_chains = [c for c in chains if c["path"] is not None]

    return {
        "hidden_words": hidden_words,
        "chains": valid_chains,
        "subgraph_nodes": n_sub_nodes,
        "subgraph_edges": n_sub_edges,
        "anchors_found": anchors_found,
        "anchors_missing": anchors_missing,
    }


def discover_hidden_chain_with_evidence(ppmi_matrix, vocab_dict, anchor_words,
                                        memory_store=None, top_k_candidates=50,
                                        alpha=0.85, min_edge_weight=0.1,
                                        top_n=10, fallback_matrix=None):
    """
    Hidden chain inference + text evidence association.

    Builds on discover_hidden_chain by retrieving associated text fragments
    (from the summary layer) in the MemoryStore for each hidden word,
    providing supporting evidence for AI judgment.

    Args:
        memory_store: MemoryStore | None -- memory storage instance
        fallback_matrix: fallback matrix (same as in discover_hidden_chain)
        all other args: same as discover_hidden_chain

    Returns:
        dict -- the return value of discover_hidden_chain extended with:
            "evidence": [
                {"word": str, "entry_id": str, "topic": str, "summary": str},
                ...
            ]
    """
    result = discover_hidden_chain(
        ppmi_matrix, vocab_dict, anchor_words,
        top_k_candidates=top_k_candidates,
        alpha=alpha, min_edge_weight=min_edge_weight,
        top_n=top_n, fallback_matrix=fallback_matrix
    )

    # If no MemoryStore or no hidden words found, return directly
    if memory_store is None or not result.get("hidden_words"):
        result["evidence"] = []
        return result

    # Retrieve associated memory entries for each hidden word
    evidence = []
    for hw in result["hidden_words"]:
        word = hw["word"]
        # Exact search + fuzzy search
        exact_hits = memory_store.search_exact([word])
        fuzzy_hits = memory_store.search_fuzzy([word])

        # Merge; take the best hit
        best_eid = None
        if exact_hits:
            best_eid = list(exact_hits.keys())[0]
        elif fuzzy_hits:
            best_eid = list(fuzzy_hits.keys())[0]

        if best_eid:
            entry = memory_store.get(best_eid)
            if entry:
                evidence.append({
                    "word": word,
                    "entry_id": best_eid,
                    "topic": entry.get("topic"),
                    "summary": entry.get("summary"),
                })

    result["evidence"] = evidence
    return result
