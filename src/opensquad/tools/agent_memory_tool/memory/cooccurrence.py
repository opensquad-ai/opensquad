"""
Incremental co-occurrence matrix manager
- Incremental accumulation on the global matrix; replaces deepcopy + list storage
- Memory reduced from O(N * nnz) to O(nnz)
"""

import numpy as np
from scipy.sparse import dok_matrix


class IncrementalCooccurrence:
    """
    Incremental word co-occurrence matrix manager.

    Key improvement: each document is directly applied as += 1 on the global matrix
    rather than deepcopying a full matrix for every document.
    """

    def __init__(self, max_dim=100000):
        self.max_dim = max_dim
        self.matrix = dok_matrix((max_dim, max_dim), dtype=np.float64)
        self.vocab_dict = {}  # word -> idx
        self.idx_to_word = {}  # idx -> word
        self.vocab_count = 0
        self.total_docs = 0

    def _get_or_create_idx(self, word):
        """Get the index of a word, creating it automatically if it does not exist."""
        if word not in self.vocab_dict:
            idx = self.vocab_count
            self.vocab_dict[word] = idx
            self.idx_to_word[idx] = word
            self.vocab_count += 1
            return idx
        return self.vocab_dict[word]

    def add_document(self, words):
        """
        Process a document's word list and incrementally accumulate it into the global co-occurrence matrix.

        Args:
            words: list[str] -- tokenized and deduplicated word list
        """
        # Get indices for all words
        indices = [self._get_or_create_idx(w) for w in words]

        # Accumulate co-occurrence relationships directly on the global matrix
        for i in indices:
            for j in indices:
                self.matrix[i, j] += 1

        self.total_docs += 1

    def add_keywords(self, keywords):
        """
        Learn co-occurrence relationships from a memory entry's keyword list.

        Difference from add_document:
        - keywords are already clean; no tokenization needed
        - Higher semantic density (every word is an important concept)
        - Does not count toward total_docs (avoids skewing the PPMI document-frequency baseline)

        Args:
            keywords: list[str] -- keyword list
        """
        if not keywords or len(keywords) < 2:
            return

        indices = [self._get_or_create_idx(w) for w in keywords]

        for i in indices:
            for j in indices:
                self.matrix[i, j] += 1

    def get_csr_matrix(self):
        """Convert the current matrix to CSR format (for efficient computation)."""
        return self.matrix.tocsr()

    def prune(self, min_cooccurrence=10):
        """
        Pruning: remove elements with co-occurrence counts below the threshold.

        Args:
            min_cooccurrence: int -- minimum co-occurrence count threshold
        Returns:
            csr_matrix -- pruned CSR matrix
        """
        m_csr = self.matrix.tocsr().copy()
        m_csr.data[m_csr.data < min_cooccurrence] = 0
        m_csr.eliminate_zeros()
        return m_csr

    def get_stats(self):
        """Return statistics for the current matrix."""
        csr = self.get_csr_matrix()
        return {
            "total_docs": self.total_docs,
            "vocab_size": self.vocab_count,
            "nonzero_pairs": csr.nnz,
            "matrix_density": csr.nnz / (self.vocab_count**2) if self.vocab_count > 0 else 0,
        }

    def remove_words(self, words_to_remove):
        """
        Remove specified words from the co-occurrence matrix, shrinking the matrix dimensions.

        Used for vocabulary pruning during consolidate(). Removed words:
        - Are deleted from vocab_dict / idx_to_word
        - Have their corresponding matrix rows and columns zeroed out
        - Cause vocab_count to decrease accordingly
        - Matrix indices are re-compacted

        Note: This operation does not affect SQLite memory entries. Pruned words
        can still be found via exact keyword matching (fast query).

        Args:
            words_to_remove: list[str] -- list of words to remove

        Returns:
            int -- actual number of words removed
        """
        if not words_to_remove:
            return 0

        # Find the indices to remove
        indices_to_remove = set()
        for w in words_to_remove:
            if w in self.vocab_dict:
                indices_to_remove.add(self.vocab_dict[w])

        if not indices_to_remove:
            return 0

        # Build list of indices to keep (preserving original order)
        indices_to_keep = [i for i in range(self.vocab_count) if i not in indices_to_remove]

        if not indices_to_keep:
            # Remove all = reset
            removed = self.vocab_count
            self.matrix = dok_matrix((self.max_dim, self.max_dim), dtype=np.float64)
            self.vocab_dict = {}
            self.idx_to_word = {}
            self.vocab_count = 0
            return removed

        # Extract the sub-matrix for the kept portion
        csr = self.matrix.tocsr()
        # Take the cross sub-matrix of the kept rows and columns
        sub = csr[indices_to_keep, :][:, indices_to_keep]

        # Rebuild vocab mapping
        new_vocab_dict = {}
        new_idx_to_word = {}
        for new_idx, old_idx in enumerate(indices_to_keep):
            word = self.idx_to_word[old_idx]
            new_vocab_dict[word] = new_idx
            new_idx_to_word[new_idx] = word

        # Rebuild DOK matrix
        new_count = len(indices_to_keep)
        new_dok = dok_matrix((self.max_dim, self.max_dim), dtype=np.float64)

        # Fill the sub-matrix data into the new DOK
        sub_coo = sub.tocoo()
        for r, c, v in zip(sub_coo.row, sub_coo.col, sub_coo.data, strict=False):
            new_dok[r, c] = v

        removed = self.vocab_count - new_count

        # Update state
        self.matrix = new_dok
        self.vocab_dict = new_vocab_dict
        self.idx_to_word = new_idx_to_word
        self.vocab_count = new_count

        return removed
