"""The face the identity lock compares against: the embeddings that just passed the sign-in face check.

They are kept in memory only. The enrolled template stays on the server (encrypted) and never comes
back to a PC, so the lock can only vouch for "the same person who passed the check at sign-in".
"""
from __future__ import annotations

import numpy as np


class SessionReference:
    def __init__(self, embeddings: list[np.ndarray]) -> None:
        if len(embeddings) < 3:
            raise ValueError("a reference needs at least 3 embeddings")
        m = np.stack([np.asarray(e, dtype=np.float32) for e in embeddings])
        m /= np.linalg.norm(m, axis=1, keepdims=True)
        self.embeddings = m
        mean = m.mean(axis=0)
        self.mean = mean / np.linalg.norm(mean)

    def score(self, embedding: np.ndarray) -> float:
        """Cosine similarity: the best of the kept embeddings and their mean (same rule as EnrolledProfile)."""
        e = np.asarray(embedding, dtype=np.float32)
        e = e / (np.linalg.norm(e) or 1.0)
        return float(max((self.embeddings @ e).max(), self.mean @ e))
