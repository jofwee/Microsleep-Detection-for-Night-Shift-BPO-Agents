"""Per-agent calibration: the numbers every threshold is built from."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

import numpy as np


class CalibrationError(ValueError):
    """Raised when the calibration samples are unusable (e.g. eyes never opened)."""


@dataclass(frozen=True)
class FrameMeasure:
    """One frame's measurements, as collected during calibration."""

    ear: float
    mar: float
    pitch: float
    yaw: float
    roll: float


@dataclass(frozen=True)
class Calibration:
    """Defaults are typical adult values, only used until a real calibration exists."""

    ear_open: float = 0.30
    ear_closed: float = 0.10
    mar_closed: float = 0.03
    mar_open: float | None = None  # optional: mouth wide open (yawn-like)
    neutral_pitch: float = 0.0
    neutral_yaw: float = 0.0
    neutral_roll: float = 0.0
    hysteresis: float = 0.10  # fraction of (open - closed) added before an eye counts as reopened

    @property
    def _range(self) -> float:
        return self.ear_open - self.ear_closed

    @property
    def ear_close_threshold(self) -> float:
        """Eye counts as closed below this: closed + 0.5 * (open - closed)."""
        return self.ear_closed + 0.5 * self._range

    @property
    def ear_reopen_threshold(self) -> float:
        """Eye counts as open again only above this (hysteresis)."""
        return self.ear_close_threshold + self.hysteresis * self._range

    @property
    def ear_perclos_threshold(self) -> float:
        """PERCLOS P80: eye at least 80 % closed, closed + 0.2 * (open - closed)."""
        return self.ear_closed + 0.2 * self._range

    @property
    def yawn_threshold(self) -> float:
        """MAR above this counts as a wide-open mouth."""
        if self.mar_open is not None:
            return max(self.mar_closed + 0.2, self.mar_closed + 0.6 * (self.mar_open - self.mar_closed))
        return max(0.5, self.mar_closed + 0.4)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Calibration":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Calibration":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


MIN_OPEN_SAMPLES = 30
MIN_CLOSED_SAMPLES = 10
MIN_EAR_RANGE = 0.05


def compute_calibration(
    open_samples: Sequence[FrameMeasure],
    closed_samples: Sequence[FrameMeasure],
    mouth_open_samples: Sequence[FrameMeasure] | None = None,
) -> Calibration:
    """Build a Calibration from the two capture phases.

    open_samples:   ~10 s, eyes open, looking at the screen.
    closed_samples: ~3 s, eyes closed.
    mouth_open_samples: optional, mouth wide open.
    """
    if len(open_samples) < MIN_OPEN_SAMPLES:
        raise CalibrationError(f"need at least {MIN_OPEN_SAMPLES} eyes-open frames, got {len(open_samples)}")
    if len(closed_samples) < MIN_CLOSED_SAMPLES:
        raise CalibrationError(f"need at least {MIN_CLOSED_SAMPLES} eyes-closed frames, got {len(closed_samples)}")

    # Blinks during the open phase drag EAR down, so drop the lowest 10 % before the median.
    open_ears = np.sort([s.ear for s in open_samples])
    ear_open = float(np.median(open_ears[int(len(open_ears) * 0.10):]))
    ear_closed = float(np.median([s.ear for s in closed_samples]))

    if ear_open - ear_closed < MIN_EAR_RANGE:
        raise CalibrationError(
            f"eyes-open EAR ({ear_open:.3f}) is too close to eyes-closed EAR ({ear_closed:.3f}); "
            "retry with better light and a clear view of the eyes"
        )

    mar_open = None
    if mouth_open_samples:
        mar_open = float(np.median([s.mar for s in mouth_open_samples]))

    return Calibration(
        ear_open=ear_open,
        ear_closed=ear_closed,
        mar_closed=float(np.median([s.mar for s in open_samples])),
        mar_open=mar_open,
        neutral_pitch=float(np.median([s.pitch for s in open_samples])),
        neutral_yaw=float(np.median([s.yaw for s in open_samples])),
        neutral_roll=float(np.median([s.roll for s in open_samples])),
    )
