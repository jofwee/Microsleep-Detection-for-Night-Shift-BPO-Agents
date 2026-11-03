"""Presentation-attack trials (ISO/IEC 30107-3 vocabulary).

A trial is one run of the app's sign-in check: the active challenge (blink / turn) with the passive photo check and the
face match measured on the way. A genuine person is a bona fide presentation; a printed photo, a phone photo, a phone video
or a screen replay of the enrolled person is an attack. The raw numbers are logged once, and any acceptance rule can be
applied to them afterwards, so the rules can be compared on the same trials.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .metrics import wilson

BONAFIDE = "bonafide"
ATTACKS = ("print", "phone_photo", "phone_video", "screen_replay")


@dataclass(frozen=True)
class SpoofTrial:
    subject: str  # whose profile is being attacked, or who is the real user
    attack: str  # "bonafide" or one of ATTACKS
    lighting: str
    challenge_passed: bool
    passive_median: float | None
    passive_min: float | None
    match: float | None  # similarity to the enrolled profile
    frames: int = 0


@dataclass(frozen=True)
class Rule:
    """When a presentation is accepted."""

    name: str
    tau: float = 0.45  # face match threshold
    challenge: bool = True  # the live action must have been done
    passive_min: float | None = None  # the median passive score must reach this (None = not checked)


# the app's real sign-in rule (server: LIVENESS_MIN 0.5), the same with the lowered client threshold, and two ablations
RULES = (
    Rule("sign-in as deployed (challenge + passive 0.5 + match)", challenge=True, passive_min=0.5),
    Rule("sign-in, passive 0.35 (challenge + passive + match)", challenge=True, passive_min=0.35),
    Rule("challenge + match only (no passive model)", challenge=True, passive_min=None),
    Rule("passive 0.35 + match only (no challenge: the in-shift gate)", challenge=False, passive_min=0.35),
    Rule("match only (no liveness at all)", challenge=False, passive_min=None),
)


def accepted(t: SpoofTrial, rule: Rule) -> bool:
    if t.match is None or t.match < rule.tau:
        return False
    if rule.challenge and not t.challenge_passed:
        return False
    if rule.passive_min is not None and (t.passive_median is None or t.passive_median < rule.passive_min):
        return False
    return True


def evaluate(trials: Sequence[SpoofTrial], rule: Rule) -> dict:
    """APCER per attack type (accepted attacks / attacks), the worst of them, and BPCER (rejected genuine / genuine)."""
    per_attack: dict[str, tuple[int, int]] = {}
    bona = [t for t in trials if t.attack == BONAFIDE]
    for t in trials:
        if t.attack == BONAFIDE:
            continue
        cell = per_attack.get(t.attack, (0, 0))
        per_attack[t.attack] = (cell[0] + accepted(t, rule), cell[1] + 1)
    rates = {k: a / n for k, (a, n) in per_attack.items() if n}
    rejected = sum(1 for t in bona if not accepted(t, rule))
    return {
        "per_attack": per_attack,
        "apcer_worst": max(rates.values()) if rates else None,
        "bpcer": rejected / len(bona) if bona else None,
        "bona_rejected": rejected,
        "bona_total": len(bona),
    }


def interval(successes: int, total: int) -> tuple[float, float]:
    return wilson(successes, total)
