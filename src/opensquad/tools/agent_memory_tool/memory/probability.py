# -*- coding: utf-8 -*-
"""
Probability matrix computation module
- PPMI (Positive Pointwise Mutual Information): filters high-frequency stop-word noise,
  more accurate than conditional probability
- Conditional probability matrix: retained for backward compatibility with the original approach
"""
import numpy as np
from scipy.sparse import diags, csr_matrix


def compute_conditional_prob_matrix(cooccurrence_csr):
    """
    Compute the conditional probability matrix (backward compatible with the original approach).

    P[i,j] = M[i,j] / M[i,i]
    i.e. given word i is seen, what is the probability of recalling word j

    Args:
        cooccurrence_csr: csr_matrix -- co-occurrence matrix (already pruned)
    Returns:
        csr_matrix -- conditional probability matrix
    """
    diag = cooccurrence_csr.diagonal().copy().astype(np.float64)
    diag[diag == 0] = 1.0
    inv_diag = diags(1.0 / diag)
    prob_matrix = inv_diag @ cooccurrence_csr
    return prob_matrix


def compute_ppmi_matrix(cooccurrence_csr, total_docs):
    """
    Compute the PPMI (Positive Pointwise Mutual Information) matrix.

    PMI(A,B) = log2(P(A,B) / (P(A) * P(B)))
             = log2((M[A,B] * total_docs) / (M[A,A] * M[B,B]))

    PPMI = max(0, PMI)

    Advantages:
    - Effectively filters out spurious high co-occurrence from high-frequency content-free words
    - Highlights word pairs with genuine semantic associations
    - A classic, widely-validated method in NLP

    Args:
        cooccurrence_csr: csr_matrix -- co-occurrence matrix (already pruned)
        total_docs: int -- total number of documents
    Returns:
        csr_matrix -- PPMI matrix
    """
    if total_docs == 0:
        return cooccurrence_csr.copy()

    # Extract diagonal: document frequency of each word
    diag = cooccurrence_csr.diagonal().copy().astype(np.float64)

    # Iterate over non-zero elements in COO format and compute PMI
    coo = cooccurrence_csr.tocoo()

    new_data = []
    new_row = []
    new_col = []

    for r, c, v in zip(coo.row, coo.col, coo.data):
        # Skip diagonal (a word's co-occurrence with itself is meaningless)
        if r == c:
            continue

        p_a = diag[r]  # document frequency of word A
        p_b = diag[c]  # document frequency of word B

        if p_a == 0 or p_b == 0:
            continue

        # PMI = log2((M[A,B] * total_docs) / (M[A,A] * M[B,B]))
        pmi = np.log2((v * total_docs) / (p_a * p_b))

        # PPMI: keep only positive values
        if pmi > 0:
            new_data.append(pmi)
            new_row.append(r)
            new_col.append(c)

    ppmi_matrix = csr_matrix(
        (new_data, (new_row, new_col)),
        shape=cooccurrence_csr.shape
    )
    return ppmi_matrix


def print_top_associations(prob_matrix, idx_to_word, min_score=0.2, max_score=1.0, top_n=50):
    """
    Print the strongest word-pair associations.

    Args:
        prob_matrix: probability/PPMI matrix
        idx_to_word: dict -- index-to-word mapping
        min_score: float -- minimum score threshold
        max_score: float -- maximum score threshold (excludes diagonal self-associations)
        top_n: int -- maximum number of entries to print
    """
    coo = prob_matrix.tocoo()

    pairs = []
    for r, c, v in zip(coo.row, coo.col, coo.data):
        if r == c:
            continue
        if min_score <= v < max_score:
            if r in idx_to_word and c in idx_to_word:
                pairs.append((idx_to_word[r], idx_to_word[c], v))

    pairs.sort(key=lambda x: x[2], reverse=True)

    print(f"\n===== Top {min(top_n, len(pairs))} strong word-pair associations =====")
    for word_a, word_b, score in pairs[:top_n]:
        print(f"  {word_a} -> {word_b}: {score:.4f}")
    print(f"  ({len(pairs)} pairs above threshold {min_score})")
