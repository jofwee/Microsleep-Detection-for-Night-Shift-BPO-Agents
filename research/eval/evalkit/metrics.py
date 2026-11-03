"""The measuring tools of the evaluation: matching detected events to ground truth, and the standard rates.

Pure functions, tested without any camera or video:

  - event matching with a time tolerance: precision, recall, F1 and the detection delay
  - face verification: FAR, FRR and the equal error rate (EER) from genuine and impostor scores
  - presentation attacks (ISO/IEC 30107-3): APCER and BPCER
  - small helpers: summary statistics and a bootstrap confidence interval
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np


# --- events ------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Truth:
    """Something that really happened, in seconds of video time."""

    kind: str
    start_s: float
    end_s: float


@dataclass(frozen=True)
class Detection:
    """Something the system reported. `at_s` is when it reported it; `duration_s` how long the behaviour had lasted."""

    kind: str
    at_s: float
    duration_s: float = 0.0

    @property
    def start_s(self) -> float:
        return max(0.0, self.at_s - self.duration_s)


@dataclass
class Scores:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    delays_s: list[float] = field(default_factory=list)  # detection time minus the start of the real event

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        return None if p is None or r is None or p + r == 0 else 2 * p * r / (p + r)

    def __add__(self, other: "Scores") -> "Scores":
        return Scores(self.tp + other.tp, self.fp + other.fp, self.fn + other.fn, self.delays_s + other.delays_s)


def match_events(truth: Sequence[Truth], found: Sequence[Detection], tolerance_s: float = 1.0, kinds: set[str] | None = None) -> Scores:
    """One-to-one matching. A detection matches a real event of the same kind when the time it covers overlaps the real
    event stretched by `tolerance_s` on both sides. Each real event takes the earliest unmatched detection; the rest are
    false positives, and real events without a detection are misses."""
    kinds = kinds or {t.kind for t in truth} | {d.kind for d in found}
    total = Scores()
    for kind in sorted(kinds):
        events = sorted((t for t in truth if t.kind == kind), key=lambda t: t.start_s)
        dets = sorted((d for d in found if d.kind == kind), key=lambda d: d.at_s)
        used = [False] * len(dets)
        for t in events:
            lo, hi = t.start_s - tolerance_s, t.end_s + tolerance_s
            pick = next((i for i, d in enumerate(dets) if not used[i] and d.at_s >= lo and d.start_s <= hi), None)
            if pick is None:
                total.fn += 1
            else:
                used[pick] = True
                total.tp += 1
                total.delays_s.append(dets[pick].at_s - t.start_s)
        total.fp += used.count(False)
    return total


def per_kind(truth: Sequence[Truth], found: Sequence[Detection], tolerance_s: float = 1.0) -> dict[str, Scores]:
    kinds = {t.kind for t in truth} | {d.kind for d in found}
    return {k: match_events(truth, found, tolerance_s, {k}) for k in sorted(kinds)}


# --- face verification ---------------------------------------------------------------------------------------------
def far_frr(genuine: Sequence[float], impostor: Sequence[float], threshold: float) -> tuple[float, float]:
    """FAR = impostors accepted / impostors; FRR = genuine users rejected / genuine users. Accept when score >= threshold."""
    g, i = np.asarray(genuine, dtype=float), np.asarray(impostor, dtype=float)
    far = float((i >= threshold).mean()) if i.size else math.nan
    frr = float((g < threshold).mean()) if g.size else math.nan
    return far, frr


def det_curve(genuine: Sequence[float], impostor: Sequence[float]) -> list[tuple[float, float, float]]:
    """(threshold, FAR, FRR) at every score that occurs, plus one threshold above all of them."""
    scores = sorted(set(map(float, genuine)) | set(map(float, impostor)))
    if not scores:
        return []
    thresholds = scores + [scores[-1] + 1e-6]
    return [(t, *far_frr(genuine, impostor, t)) for t in thresholds]


def eer(genuine: Sequence[float], impostor: Sequence[float]) -> tuple[float, float]:
    """(equal error rate, the threshold where FAR and FRR are closest). The rate is the mean of the two at that point."""
    curve = det_curve(genuine, impostor)
    if not curve:
        return math.nan, math.nan
    t, far, frr = min(curve, key=lambda row: abs(row[1] - row[2]))
    return (far + frr) / 2, t


# --- presentation attacks ------------------------------------------------------------------------------------------
def apcer_bpcer(attack_accepted: Sequence[bool], bonafide_accepted: Sequence[bool]) -> tuple[float, float]:
    """APCER: attack presentations wrongly accepted. BPCER: genuine presentations wrongly rejected (ISO/IEC 30107-3)."""
    a, b = list(attack_accepted), list(bonafide_accepted)
    apcer = sum(a) / len(a) if a else math.nan
    bpcer = sum(1 for x in b if not x) / len(b) if b else math.nan
    return apcer, bpcer


def attack_table(trials: Sequence[tuple[str, bool]]) -> dict[str, tuple[int, int]]:
    """For each attack type: (accepted, total)."""
    out: dict[str, list[int]] = {}
    for kind, accepted in trials:
        cell = out.setdefault(kind, [0, 0])
        cell[0] += bool(accepted)
        cell[1] += 1
    return {k: (v[0], v[1]) for k, v in out.items()}


# --- statistics ----------------------------------------------------------------------------------------------------
def describe(values: Sequence[float]) -> dict[str, float]:
    v = np.asarray([x for x in values if x is not None and not math.isnan(x)], dtype=float)
    if v.size == 0:
        return {"n": 0}
    return {
        "n": int(v.size), "mean": float(v.mean()), "median": float(np.median(v)), "p95": float(np.percentile(v, 95)),
        "min": float(v.min()), "max": float(v.max()), "std": float(v.std(ddof=1)) if v.size > 1 else 0.0,
    }


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """The 95% Wilson interval for a rate (honest for small samples and for rates near 0 or 1)."""
    if total == 0:
        return math.nan, math.nan
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def bootstrap_ci(values: Sequence[float], stat=np.mean, n: int = 2000, seed: int = 0, alpha: float = 0.05) -> tuple[float, float]:
    v = np.asarray(list(values), dtype=float)
    if v.size == 0:
        return math.nan, math.nan
    rng = random.Random(seed)
    idx = range(v.size)
    stats = sorted(float(stat(v[[rng.choice(idx) for _ in idx]])) for _ in range(n))
    return stats[int(alpha / 2 * n)], stats[int((1 - alpha / 2) * n) - 1]
