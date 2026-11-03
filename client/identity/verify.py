"""Cosine match of live face embeddings against an enrolled profile."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .profile import EnrolledProfile

# Starting threshold from plan.md (0.4-0.5). On the public sample photo, different people
# scored at most 0.17 and the same face under blur/shift/dimming scored ~0.98. The real value
# is tuned with an ROC (FAR / FRR / EER) on enrollment data in the evaluation milestone.
DEFAULT_TAU = 0.45


@dataclass(frozen=True)
class VerifyResult:
    score: float
    passed: bool
    frames: int
    reason: str = ""


def verify_embeddings(
    embeddings: Sequence[np.ndarray],
    profile: EnrolledProfile,
    tau: float = DEFAULT_TAU,
    top_k: int = 3,
    min_frames: int = 3,
) -> VerifyResult:
    """Decide from several frames: the median of the best `top_k` scores must reach tau.

    One lucky frame cannot pass on its own, and one bad frame cannot fail.
    """
    if len(embeddings) < min_frames:
        return VerifyResult(0.0, False, len(embeddings), f"need at least {min_frames} usable frames")
    scores = sorted((profile.score(e) for e in embeddings), reverse=True)
    score = float(np.median(scores[:top_k]))
    return VerifyResult(score, score >= tau, len(embeddings))
