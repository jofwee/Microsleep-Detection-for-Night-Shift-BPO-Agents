"""How fast is the detection pipeline? Pure helpers for the benchmark: frame times and resource samples to a summary."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .metrics import describe

TARGET_FPS = 15.0  # plan: at least 15 frames per second on the agent PC, aiming for 30


@dataclass(frozen=True)
class BenchResult:
    mode: str
    frames: int
    mean_ms: float
    median_ms: float
    p95_ms: float
    max_ms: float
    fps_sustained: float  # frames the pipeline could process per second of wall time
    cpu_percent_mean: float | None  # of one core (100 = one full core); the process may use several
    memory_mb_peak: float | None

    @property
    def meets_target(self) -> bool:
        return self.fps_sustained >= TARGET_FPS


def summarize(mode: str, frame_ms: Sequence[float], wall_s: float, cpu: Sequence[float] = (), rss_mb: Sequence[float] = ()) -> BenchResult:
    d = describe(frame_ms)
    return BenchResult(
        mode, int(d.get("n", 0)), d.get("mean", 0.0), d.get("median", 0.0), d.get("p95", 0.0), d.get("max", 0.0),
        (len(frame_ms) / wall_s) if wall_s > 0 and frame_ms else 0.0,
        (sum(cpu) / len(cpu)) if cpu else None, max(rss_mb) if rss_mb else None,
    )
