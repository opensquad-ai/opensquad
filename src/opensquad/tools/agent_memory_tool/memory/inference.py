# -*- coding: utf-8 -*-
"""
Multi-hop inference search module
- find_top_inference_paths: single-word 2-hop inference (backward compatible)
- find_group_inference_paths: multi-word joint 2-hop inference (backward compatible)
- beam_search_inference: N-hop adaptive Beam Search inference (new)
"""
import numpy as np
from heapq import nlargest


def _merge_substring_targets(results, top_n):
    """
    Post-process substring merging: merge substring fragments from inference results
    into their corresponding longer words.

    Problem scenario:
        jieba tokenization may produce "Tesla" fragments like "Tes", "sla", "Tesla"
        simultaneously; these fragments each accumulate scores independently,
        diluting the true ranking of the full word.

    Strategy:
        1. Sort by target word length descending
        2. If a short word is a substring of a long word, merge the short word's score
           into the long word
        3. After merging, keep only the top 3 highest-scoring path explanations
    """
    if not results or isinstance(results, str):
        return results

    # Sort by target word length descending, then by confidence descending
    results.sort(key=lambda x: (len(x['target']), x['confidence']), reverse=True)

    merged = []       # kept results
    merged_words = [] # corresponding target words

    for item in results:
        word = item['target']
        # Check if this word is a substring of an already-kept longer word
        merged_into = None
        for i, kept_word in enumerate(merged_words):
            if word in kept_word and word != kept_word:
                merged_into = i
                break

        if merged_into is not None:
            # Merge score into the longer word
            merged[merged_into]['confidence'] += item['confidence']
            merged[merged_into]['confidence'] = round(merged[merged_into]['confidence'], 6)
            # Merge path explanations
            merged[merged_into]['why'].extend(item.get('why', []))
            # Re-sort and keep only top 3
            sort_key = 'score'
            merged[merged_into]['why'] = sorted(
                merged[merged_into]['why'], key=lambda x: x.get(sort_key, 0), reverse=True
            )[:3]
        else:
            merged.append(item)
            merged_words.append(word)

    # Re-sort by merged confidence
    merged.sort(key=lambda x: x['confidence'], reverse=True)
    return merged[:top_n]


def find_top_inference_paths(prob_matrix, vocab_dict, start_word, top_n=5):
    """
    Single-word 2-hop inference path search: A -> B -> C

    Args:
        prob_matrix: probability/PPMI matrix
        vocab_dict: dict -- word-to-index mapping
        start_word: str -- starting word
        top_n: int -- return top N paths
    """
    if start_word not in vocab_dict:
        return f"Word '{start_word}' is not in the vocabulary"

    idx_a = vocab_dict[start_word]
    idx_to_word = {v: k for k, v in vocab_dict.items()}

    row_a = prob_matrix[idx_a, :].toarray().flatten()
    possible_b_indices = np.where(row_a > 0)[0]

    paths = []

    for idx_b in possible_b_indices:
        if idx_b == idx_a:
            continue
        p_ab = row_a[idx_b]

        row_b = prob_matrix[idx_b, :].toarray().flatten()
        possible_c_indices = np.where(row_b > 0)[0]

        for idx_c in possible_c_indices:
            if idx_c == idx_a or idx_c == idx_b:
                continue
            p_bc = row_b[idx_c]
            total_prob = p_ab * p_bc

            paths.append({
                "path": f"{start_word} -> {idx_to_word[idx_b]} -> {idx_to_word[idx_c]}",
                "prob": total_prob,
                "target": idx_to_word[idx_c]
            })

    sorted_paths = sorted(paths, key=lambda x: x['prob'], reverse=True)
    return sorted_paths[:top_n]


def find_group_inference_paths(prob_matrix, vocab_dict, input_words, top_n=5):
    """
    Multi-word joint 2-hop inference path search.
    Given a group of keywords, find the strongest inference targets jointly pointed to
    by all input words.

    Core design: for a given target word, path scores from different input words
    are accumulated; thus target words pointed to by multiple input words simultaneously
    receive higher confidence.

    Args:
        prob_matrix: probability/PPMI matrix
        vocab_dict: dict -- word-to-index mapping
        input_words: list[str] -- input keyword list
        top_n: int -- return top N target words
    """
    idx_to_word = {v: k for k, v in vocab_dict.items()}
    input_indices = set()
    for w in input_words:
        if w in vocab_dict:
            input_indices.add(vocab_dict[w])

    if not input_indices:
        return "None of the input words are in the vocabulary"

    results = {}

    for idx_start in input_indices:
        start_word = idx_to_word[idx_start]

        row_start = prob_matrix[idx_start, :].toarray().flatten()
        mid_indices = np.where(row_start > 0.01)[0]

        for idx_mid in mid_indices:
            if idx_mid in input_indices:
                continue
            p1 = row_start[idx_mid]
            mid_word = idx_to_word[idx_mid]

            row_mid = prob_matrix[idx_mid, :].toarray().flatten()
            target_indices = np.where(row_mid > 0.01)[0]

            for idx_target in target_indices:
                if idx_target in input_indices:
                    continue

                p2 = row_mid[idx_target]
                path_score = p1 * p2

                if idx_target not in results:
                    results[idx_target] = {"total_score": 0, "explanations": []}

                results[idx_target]["total_score"] += path_score
                results[idx_target]["explanations"].append({
                    "from": start_word,
                    "bridge": mid_word,
                    "score": round(path_score, 6)
                })

    final_output = []
    for idx_target, data in results.items():
        sorted_expl = sorted(data["explanations"], key=lambda x: x["score"], reverse=True)
        final_output.append({
            "target": idx_to_word[idx_target],
            "confidence": round(data["total_score"], 6),
            "why": sorted_expl[:3]
        })

    final_output = sorted(final_output, key=lambda x: x["confidence"], reverse=True)
    return _merge_substring_targets(final_output[:top_n * 2], top_n)


def beam_search_inference(prob_matrix, vocab_dict, input_words,
                          max_depth=3, beam_width=20,
                          min_prob_threshold=0.01, top_n=10,
                          hop_decay=0.5, diversity_penalty=0.6,
                          hub_penalty_percentile=95):
    """
    N-hop adaptive Beam Search inference (v3.1 - multi-source reachability + hub penalty).

    Core filtering logic (v3.1 improvement):
    When there are >= 2 input words, a target word is kept only if it satisfies one of:
      A) Full path-source coverage: beam search paths from every input word can reach the target
         (e.g. "market" is reachable from both "China" and "export" -> kept)
      B) Full neighbor coverage: the target is a direct neighbor of every input word
         (even if paths only originate from some input words, the co-occurrence matrix
         confirms it is related to all input words)
    Words failing both criteria are filtered. This eliminates spurious words:
      "epicenter" has a path only from "China" and is not a direct neighbor of "export" -> filtered

    Other mechanisms:
    - Hop decay (hop_decay)
    - Hub node penalty (down-weight paths through high-degree nodes)
    - Bridge diversity penalty
    - Multi-source path reward

    Args:
        prob_matrix: probability/PPMI matrix
        vocab_dict: dict -- word-to-index mapping
        input_words: list[str] -- input keyword list
        max_depth: int -- maximum number of hops
        beam_width: int -- maximum number of paths kept per layer
        min_prob_threshold: float -- prune paths below this cumulative probability
        top_n: int -- number of target words to return
        hop_decay: float -- decay factor per hop (0~1); smaller means faster decay
        diversity_penalty: float -- penalty coefficient when bridge nodes are non-diverse (0~1)
        hub_penalty_percentile: int -- degree percentile threshold for hub node penalty
    """
    idx_to_word = {v: k for k, v in vocab_dict.items()}
    input_indices = set()
    for w in input_words:
        if w in vocab_dict:
            input_indices.add(vocab_dict[w])

    if not input_indices:
        return "None of the input words are in the vocabulary"

    n_inputs = len(input_indices)

    # --- Pre-computation 1: direct neighbor set for each input word (for bidirectional relevance) ---
    per_input_neighbors = {}
    for idx_s in input_indices:
        row_s = prob_matrix[idx_s, :].toarray().flatten()
        neighbors = set(np.where(row_s > 0)[0])
        per_input_neighbors[idx_s] = neighbors

    # --- Pre-computation 2: node degree & hub node identification ---
    # Compute out-degree (non-zero connection count) for each node
    node_degrees = np.diff(prob_matrix.indptr) if hasattr(prob_matrix, 'indptr') else None
    hub_threshold = None
    hub_nodes = set()
    if node_degrees is not None and len(node_degrees) > 0:
        hub_threshold = np.percentile(node_degrees[node_degrees > 0], hub_penalty_percentile)
        hub_nodes = set(np.where(node_degrees > hub_threshold)[0])

    hub_penalty_factor = 0.3  # edge-weight penalty coefficient for paths through hub nodes

    # Store aggregated info for each target word
    target_scores = {}

    # Run Beam Search separately for each input word
    for idx_start in input_indices:
        start_word = idx_to_word[idx_start]

        # Active paths for the current layer
        # Each path: (cumulative_prob, path_word_list, current_end_node_index, first_hop_bridge_word)
        active_paths = [(1.0, [start_word], idx_start, None)]

        for depth in range(max_depth):
            next_paths = []
            # Decay factor for this layer
            layer_decay = hop_decay ** depth

            for cum_prob, path_words, current_idx, first_bridge in active_paths:
                # Get all successors of the current node
                row = prob_matrix[current_idx, :].toarray().flatten()
                nonzero_indices = np.where(row > min_prob_threshold)[0]

                for idx_next in nonzero_indices:
                    if idx_next in input_indices:
                        continue

                    next_word = idx_to_word.get(idx_next)
                    if next_word is None or next_word in path_words:
                        continue

                    # Apply hop decay
                    edge_prob = row[idx_next] * layer_decay

                    # Hub node penalty: down-weight edges through high-degree nodes
                    if current_idx in hub_nodes:
                        edge_prob *= hub_penalty_factor

                    new_cum_prob = cum_prob * edge_prob

                    if new_cum_prob < min_prob_threshold * 0.1:
                        continue

                    # Record the first-hop bridge word
                    new_bridge = first_bridge if first_bridge is not None else next_word

                    new_path = path_words + [next_word]
                    next_paths.append((new_cum_prob, new_path, idx_next, new_bridge))

                    # Score only at the final depth layer
                    if depth == max_depth - 1:
                        if idx_next not in target_scores:
                            target_scores[idx_next] = {
                                "total_score": 0,
                                "paths": [],
                                "bridges": set(),
                                "source_words": set()
                            }

                        target_scores[idx_next]["total_score"] += new_cum_prob
                        target_scores[idx_next]["bridges"].add(new_bridge)
                        target_scores[idx_next]["source_words"].add(start_word)
                        target_scores[idx_next]["paths"].append({
                            "from": start_word,
                            "chain": " -> ".join(new_path),
                            "depth": depth + 1,
                            "score": round(new_cum_prob, 6),
                            "bridge": new_bridge
                        })

            if not next_paths:
                break
            next_paths.sort(key=lambda x: x[0], reverse=True)
            active_paths = next_paths[:beam_width]

    # --- Post-processing ---
    final_output = []
    for idx_target, data in target_scores.items():
        target_word = idx_to_word[idx_target]

        # === Core filter: multi-source reachability + neighbor coverage joint check ===

        # 1. Compute how many input words have this target as a direct neighbor
        n_connected_inputs = 0
        for idx_s in input_indices:
            if idx_target in per_input_neighbors[idx_s]:
                n_connected_inputs += 1
        coverage = n_connected_inputs / n_inputs  # 0~1

        # 2. Compute how many distinct input words have paths to this target
        n_sources = len(data["source_words"])
        source_coverage = n_sources / n_inputs  # 0~1

        # 3. Joint filter rule (multi-input-word scenario)
        if n_inputs >= 2:
            # Hard filter: keep the target only if it satisfies at least one of:
            #   A) Paths reach it from all input words (source_coverage == 1.0):
            #      every input word can reach it via multi-hop -- strongest relevance signal
            #   B) Paths reach it from some input words, but target is a direct neighbor
            #      of all input words (coverage == 1.0): direct co-occurrence with all
            if source_coverage < 1.0 and coverage < 1.0:
                # Both conditions fail -> target is only related to some input words, filter
                continue

        # 4. Single-input-word scenario: must be a direct neighbor of that input word
        if n_inputs == 1 and n_connected_inputs == 0:
            continue

        score = data["total_score"]

        # === Coverage weighting ===
        # Full coverage (coverage=1.0) gets maximum score;
        # partial direct-neighbor coverage is down-weighted proportionally
        coverage_weight = coverage ** 0.5  # square root prevents too-heavy partial-coverage penalty
        score *= coverage_weight

        # === Multi-source path reward ===
        # Targets reachable from all input words receive a bonus
        if n_sources >= n_inputs and n_inputs >= 2:
            score *= 1.5  # all-source reachable: +50%
        elif n_sources > 1:
            score *= (1.0 + 0.2 * (n_sources - 1))  # partial multi-source: +20% per source

        # === Bridge diversity penalty ===
        n_bridges = len(data["bridges"])
        if n_bridges <= 1:
            score *= diversity_penalty

        sorted_paths = sorted(data["paths"], key=lambda x: x["score"], reverse=True)
        clean_paths = [{k: v for k, v in p.items() if k != "bridge"} for p in sorted_paths[:3]]

        final_output.append({
            "target": target_word,
            "confidence": round(score, 6),
            "why": clean_paths
        })

    final_output.sort(key=lambda x: x["confidence"], reverse=True)
    return _merge_substring_targets(final_output[:top_n * 2], top_n)
