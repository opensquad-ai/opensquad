"""
Time-decay manager
- Applies exponential decay to the global co-occurrence matrix at regular intervals
- Recent events have higher weights; older events naturally fade
- Consistent with the Ebbinghaus forgetting curve in cognitive science
"""


class DecayManager:
    """
    Time-decay manager.

    Principle: every decay_interval documents, the global matrix is multiplied
    by the decay factor (1 - decay_rate).
    This keeps recent co-occurrence weights high while older ones gradually fade.

    Args:
        decay_rate: float -- decay ratio per step, default 0.005 (retains 99.5% each time)
        decay_interval: int -- how many documents between each decay step
    """

    def __init__(self, decay_rate=0.005, decay_interval=500):
        self.decay_rate = decay_rate
        self.decay_interval = decay_interval
        self.last_decay_doc = 0
        self.total_decay_steps = 0

    def maybe_decay(self, cooccurrence_obj):
        """
        Check whether decay should be applied; if the interval has been reached, apply it.

        Args:
            cooccurrence_obj: IncrementalCooccurrence instance
        Returns:
            bool -- whether decay was applied
        """
        current_doc = cooccurrence_obj.total_docs

        if current_doc - self.last_decay_doc >= self.decay_interval:
            steps = (current_doc - self.last_decay_doc) // self.decay_interval
            factor = (1 - self.decay_rate) ** steps

            # Apply decay directly to the DOK matrix data
            # DOK matrix supports element-wise operations
            csr = cooccurrence_obj.matrix.tocsr()
            csr.data *= factor
            # After decay, remove very small values to prevent matrix bloat
            csr.data[csr.data < 0.5] = 0
            csr.eliminate_zeros()
            cooccurrence_obj.matrix = csr.todok()

            self.last_decay_doc = current_doc
            self.total_decay_steps += steps
            return True

        return False

    def get_info(self):
        """Return status information for the decay manager."""
        return {
            "decay_rate": self.decay_rate,
            "decay_interval": self.decay_interval,
            "last_decay_doc": self.last_decay_doc,
            "total_decay_steps": self.total_decay_steps,
            "effective_retention": (1 - self.decay_rate) ** self.total_decay_steps
            if self.total_decay_steps > 0
            else 1.0,
        }
