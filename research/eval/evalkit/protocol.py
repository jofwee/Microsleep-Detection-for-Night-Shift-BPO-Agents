"""The recording protocol: a scripted schedule of cues, so the ground truth is known without hand labelling.

A recording session shows the volunteer what to do and when. The first 19 s are the calibration phases (eyes open, eyes
closed, mouth wide open), then come the test cues in a random order, with calm periods in between. Two kinds of cue are
controls: talking (must not look like a yawn) and looking down at the desk (must not look like a microsleep).

Acted behaviour is not real drowsiness: the thesis states that limit, and uses public yawn data (YawDD) as a second source.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from .metrics import Truth

# what the volunteer is asked to do, how long the window is (s), and the instruction shown
CUE_KINDS: dict[str, tuple[float, str]] = {
    "MICROSLEEP": (3.0, "Close your eyes for about one second, then open them"),
    "YAWN": (5.0, "Yawn widely, for about three seconds"),
    "HEAD_NOD": (3.5, "Let your head drop forward, then lift it"),
    "HEAD_TILT": (5.0, "Tilt your head to one side and hold it"),
    "LONG_BLINK": (3.0, "Blink slowly, once"),
    "TALK": (7.0, "Talk out loud, as on a call"),
    "LOOK_DOWN": (7.0, "Look down at the desk, as if reading or typing"),
}
POSITIVE = ("MICROSLEEP", "YAWN", "HEAD_NOD", "HEAD_TILT", "LONG_BLINK")  # events the system should report
CONTROLS = ("TALK", "LOOK_DOWN")  # periods in which a certain false alarm must not happen
FALSE_ALARM_OF = {"TALK": "YAWN", "LOOK_DOWN": "MICROSLEEP"}

REACTION_S = 0.6  # a person needs a moment to start: the event is taken to begin this long after the cue appears

# calibration phases, in seconds from the start of the recording
PHASES = [("GET READY", 0.0, 3.0, "Look at the screen"), ("OPEN", 3.0, 13.0, "Eyes open, look at the screen"),
          ("CLOSED", 13.0, 16.0, "Close your eyes"), ("MOUTH", 16.0, 19.0, "Open your mouth wide"), ("REST", 19.0, 22.0, "Relax")]
CALIBRATION_END_S = 22.0


@dataclass(frozen=True)
class Cue:
    kind: str
    start_s: float  # when the cue appears
    window_s: float  # how long it stays up
    text: str

    @property
    def end_s(self) -> float:
        return self.start_s + self.window_s


def build_schedule(seed: int = 1, per_kind: int = 6, controls: int = 4, gap_s: tuple[float, float] = (4.0, 7.0)) -> list[Cue]:
    """A repeatable random schedule: `per_kind` of every positive cue and `controls` of every control, shuffled, never overlapping."""
    rng = random.Random(seed)
    kinds = [k for k in POSITIVE for _ in range(per_kind)] + [k for k in CONTROLS for _ in range(controls)]
    rng.shuffle(kinds)
    # no kind twice in a row when it can be avoided: it keeps the volunteer from drifting into a rhythm
    for i in range(1, len(kinds)):
        if kinds[i] == kinds[i - 1]:
            j = next((j for j in range(i + 1, len(kinds)) if kinds[j] != kinds[i] and kinds[j] != kinds[i - 1]), None)
            if j is not None:
                kinds[i], kinds[j] = kinds[j], kinds[i]
    cues, t = [], CALIBRATION_END_S
    for k in kinds:
        window, text = CUE_KINDS[k]
        t += rng.uniform(*gap_s)
        cues.append(Cue(k, round(t, 2), window, text))
        t += window
    return cues


def total_duration(cues: list[Cue]) -> float:
    return (cues[-1].end_s if cues else CALIBRATION_END_S) + 4.0


def truth_from_schedule(cues: list[Cue], reaction_s: float = REACTION_S) -> list[Truth]:
    """The events that really happened, as the schedule says (positive cues only)."""
    return [Truth(c.kind, c.start_s + reaction_s, c.end_s) for c in cues if c.kind in POSITIVE]


def control_windows(cues: list[Cue]) -> list[Cue]:
    return [c for c in cues if c.kind in CONTROLS]


def phase_at(t: float, cues: list[Cue], lead_s: float = 2.5) -> tuple[str, str, float | None]:
    """What to show at video time t: (heading, instruction, seconds until the next cue, if one is coming within `lead_s`)."""
    for name, a, b, text in PHASES:
        if a <= t < b:
            return name, text, None
    for c in cues:
        if c.start_s <= t < c.end_s:
            return c.kind.replace("_", " "), c.text, None
    upcoming = next((c for c in cues if c.start_s > t), None)
    if upcoming is None:
        return ("DONE", "Thank you", None) if t >= total_duration(cues) - 4.0 else ("REST", "Relax", None)
    wait = upcoming.start_s - t
    if wait <= lead_s:
        return "GET READY", f"Next: {upcoming.text}", wait
    return "REST", "Relax, look at the screen", None


def frames_to_write(t_s: float, written: int, fps: int) -> int:
    """How many frames must be written so the video's clock follows the wall clock (duplicates fill gaps)."""
    return max(0, int(round(t_s * fps)) + 1 - written)


def calibration_windows() -> dict[str, tuple[float, float]]:
    return {"open": (4.0, 13.0), "closed": (13.7, 16.0), "mouth": (16.6, 19.0)}  # a margin at each end for reaction time


def false_alarms_in_controls(cues: list[Cue], detections: list, tolerance_s: float = 0.5) -> dict[str, tuple[int, int]]:
    """For each control: (windows with a false alarm of the matching kind, windows). A false alarm = a YAWN reported during
    TALK, or a MICROSLEEP reported during LOOK_DOWN."""
    out: dict[str, list[int]] = {k: [0, 0] for k in CONTROLS}
    for c in control_windows(cues):
        bad = FALSE_ALARM_OF[c.kind]
        hit = any(d.kind == bad and c.start_s - tolerance_s <= d.at_s <= c.end_s + tolerance_s for d in detections)
        out[c.kind][0] += hit
        out[c.kind][1] += 1
    return {k: (v[0], v[1]) for k, v in out.items()}
