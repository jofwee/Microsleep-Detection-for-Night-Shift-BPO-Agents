"""Face-verification trials, scored offline from saved embeddings (numbers, not photos).

Each volunteer enrolls once (the profile) and then makes verification attempts in different conditions (lighting, glasses).
Every attempt is scored against every enrolled profile with the app's own rule (the median of the best three frames):
against the volunteer's own profile it is a genuine trial, against anyone else's an impostor trial.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from identity.profile import EnrolledProfile
from identity.verify import DEFAULT_TAU, verify_embeddings

from .metrics import det_curve, eer, far_frr, wilson


@dataclass(frozen=True)
class Probe:
    """One verification attempt: the embeddings of about a dozen frames and the conditions it was made in."""

    subject: str
    embeddings: np.ndarray  # (k, 512)
    lighting: str = "normal"
    glasses: str = "no"
    pose: str = "front"
    tag: str = ""


@dataclass(frozen=True)
class Trial:
    profile: str  # whose profile it was compared with
    probe: str  # who made the attempt
    genuine: bool
    score: float
    lighting: str
    glasses: str
    pose: str


def score_probe(profile: EnrolledProfile, probe: Probe) -> float:
    return verify_embeddings(list(probe.embeddings), profile).score


def all_trials(profiles: dict[str, EnrolledProfile], probes: Sequence[Probe]) -> list[Trial]:
    out = []
    for p in probes:
        for name, profile in profiles.items():
            out.append(Trial(name, p.subject, name == p.subject, score_probe(profile, p), p.lighting, p.glasses, p.pose))
    return out


def split(trials: Sequence[Trial]) -> tuple[list[float], list[float]]:
    return [t.score for t in trials if t.genuine], [t.score for t in trials if not t.genuine]


@dataclass(frozen=True)
class Summary:
    genuine: int
    impostor: int
    tau: float
    far: float
    frr: float
    far_ci: tuple[float, float]
    frr_ci: tuple[float, float]
    eer: float
    eer_threshold: float


def summarize(trials: Sequence[Trial], tau: float = DEFAULT_TAU) -> Summary:
    g, i = split(trials)
    far, frr = far_frr(g, i, tau)
    rate, thr = eer(g, i)
    return Summary(
        len(g), len(i), tau, far, frr,
        wilson(sum(1 for s in i if s >= tau), len(i)), wilson(sum(1 for s in g if s < tau), len(g)), rate, thr,
    )


def by_condition(trials: Sequence[Trial], tau: float = DEFAULT_TAU) -> dict[tuple[str, str, str], tuple[int, int]]:
    """Genuine attempts rejected, per (lighting, glasses, pose): (rejected, attempts)."""
    out: dict[tuple[str, str, str], list[int]] = {}
    for t in trials:
        if not t.genuine:
            continue
        cell = out.setdefault((t.lighting, t.glasses, t.pose), [0, 0])
        cell[0] += t.score < tau
        cell[1] += 1
    return {k: (v[0], v[1]) for k, v in out.items()}


def operating_points(trials: Sequence[Trial]) -> list[tuple[float, float, float]]:
    g, i = split(trials)
    return det_curve(g, i)
