"""An enrolled agent: face embeddings plus their calibration.

Local files are for development only and are NOT encrypted. In the Supabase milestone the
template is encrypted (AES-256-GCM) on the server and never stored on the PC.
Only embeddings are kept, never photos.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from vision.calibration import Calibration


@dataclass
class EnrolledProfile:
    embeddings: np.ndarray  # (k, 512), each L2-normalised
    mean: np.ndarray  # (512,), L2-normalised mean template
    model_name: str
    model_version: str  # embeddings from different models cannot be compared
    calibration: Calibration | None = None
    enrolled_at: str = ""
    extra: dict = field(default_factory=dict)

    def score(self, embedding: np.ndarray) -> float:
        """Cosine similarity to this agent: the best of the kept embeddings and the mean template."""
        sims = self.embeddings @ embedding
        return float(max(sims.max(), self.mean @ embedding))

    def save(self, path: str | Path) -> None:
        meta = {
            "model_name": self.model_name,
            "model_version": self.model_version,
            "enrolled_at": self.enrolled_at,
            "calibration": self.calibration.to_dict() if self.calibration else None,
            "extra": self.extra,
        }
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, embeddings=self.embeddings, mean=self.mean, meta=np.array(json.dumps(meta)))

    @classmethod
    def load(cls, path: str | Path) -> "EnrolledProfile":
        with np.load(Path(path), allow_pickle=False) as data:
            meta = json.loads(str(data["meta"]))
            return cls(
                embeddings=data["embeddings"],
                mean=data["mean"],
                model_name=meta["model_name"],
                model_version=meta["model_version"],
                calibration=Calibration.from_dict(meta["calibration"]) if meta.get("calibration") else None,
                enrolled_at=meta.get("enrolled_at", ""),
                extra=meta.get("extra", {}),
            )
