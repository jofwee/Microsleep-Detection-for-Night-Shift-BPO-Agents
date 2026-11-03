"""Replay a recording through the real detection pipeline and score it against the schedule.

Pass 1 runs the video with the default calibration only to measure the calibration phases (eyes open, eyes closed, mouth
open). Pass 2 runs it again with that person's calibration, and records every Sample the pipeline hands to the engine.
Those samples can then be replayed through engines with other thresholds in a fraction of a second (threshold sweeps).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path

from vision.calibration import Calibration, CalibrationError, FrameMeasure, compute_calibration
from vision.drowsiness import DrowsinessEngine, Sample, Thresholds

from .metrics import Detection, Scores, Truth, match_events, per_kind
from .protocol import CALIBRATION_END_S, POSITIVE, Cue, calibration_windows, false_alarms_in_controls, truth_from_schedule

TOLERANCE_S = 1.0  # a detection counts when it falls within a second of the cue window


class RecordingEngine(DrowsinessEngine):
    """The normal engine, plus a copy of every Sample the pipeline gives it (so it can be replayed later)."""

    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        self.samples: list[Sample] = []

    def update(self, s: Sample):
        self.samples.append(s)
        return super().update(s)


def calibration_from_samples(samples: list[Sample]) -> Calibration:
    """Build the person's calibration from the three scripted phases."""
    def phase(name: str) -> list[FrameMeasure]:
        a, b = calibration_windows()[name]
        return [FrameMeasure(s.ear, s.mar, s.pitch, s.yaw, s.roll) for s in samples
                if a * 1000 <= s.ts_ms <= b * 1000 and None not in (s.ear, s.mar, s.pitch, s.yaw, s.roll)]

    return compute_calibration(phase("open"), phase("closed"), phase("mouth") or None)


def detections_from_samples(samples: list[Sample], cal: Calibration, th: Thresholds | None = None,
                            after_s: float = CALIBRATION_END_S) -> list[Detection]:
    """Run recorded samples through a fresh engine. Blinks and other bookkeeping events are not part of the schedule, and
    nothing before `after_s` counts: the calibration phases (3 s of closed eyes, 3 s of open mouth) trigger events on purpose."""
    engine = DrowsinessEngine(cal, th or Thresholds())
    found: list[Detection] = []
    for s in samples:
        for e in engine.update(s):
            if e.type.value in POSITIVE and e.ts_ms / 1000.0 >= after_s:
                found.append(Detection(e.type.value, e.ts_ms / 1000.0, e.duration_ms / 1000.0))
    return found


def replay_video(video: Path, cal: Calibration | None, model: Path | None = None) -> tuple[list[Sample], float]:
    """Run a video through the pipeline (landmarks, measurements) and return the recorded samples and the video length."""
    import cv2

    from vision.landmarks import DEFAULT_MODEL, FaceLandmarker
    from vision.pipeline import FramePipeline

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"cannot open video: {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    engine = RecordingEngine(cal)
    frames = 0
    with FaceLandmarker(model or DEFAULT_MODEL) as lm:
        pipeline = FramePipeline(lm, engine)
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            pipeline.process(frame, int(round(frames * 1000 / fps)), recent_input=False)
            frames += 1
    cap.release()
    return engine.samples, frames / fps


@dataclass
class SessionResult:
    name: str
    calibration: Calibration | None
    truth: list[Truth]
    detections: list[Detection]
    scores: dict[str, Scores]
    controls: dict[str, tuple[int, int]]
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> Scores:
        t = Scores()
        for s in self.scores.values():
            t = t + s
        return t


def score_session(name: str, cues: list[Cue], samples: list[Sample], cal: Calibration, th: Thresholds | None = None,
                  tolerance_s: float = TOLERANCE_S) -> SessionResult:
    truth = truth_from_schedule(cues)
    found = detections_from_samples(samples, cal, th)
    scores = per_kind(truth, found, tolerance_s)
    for k in POSITIVE:  # a kind with no events and no detections still gets a row
        scores.setdefault(k, Scores())
    return SessionResult(name, cal, truth, found, scores, false_alarms_in_controls(cues, found), [])


def evaluate_recording(name: str, video: Path, cues: list[Cue], th: Thresholds | None = None) -> tuple[SessionResult, list[Sample]]:
    first, _ = replay_video(video, None)
    try:
        cal = calibration_from_samples(first)
        note = []
    except CalibrationError as exc:  # keep going with typical values, and say so
        cal, note = Calibration(), [f"calibration failed ({exc}); default values were used"]
    samples, _ = replay_video(video, cal)
    result = score_session(name, cues, samples, cal, th)
    result.notes += note
    return result, samples


def pool(results: list[SessionResult]) -> dict[str, Scores]:
    out: dict[str, Scores] = {k: Scores() for k in POSITIVE}
    for r in results:
        for k, s in r.scores.items():
            out[k] = out.get(k, Scores()) + s
    return out


def pool_controls(results: list[SessionResult]) -> dict[str, tuple[int, int]]:
    out: dict[str, list[int]] = {}
    for r in results:
        for k, (bad, n) in r.controls.items():
            cell = out.setdefault(k, [0, 0])
            cell[0] += bad
            cell[1] += n
    return {k: (v[0], v[1]) for k, v in out.items()}


def sweep(samples_by_session: list[tuple[str, list[Cue], list[Sample], Calibration]], field_name: str, values: list[float]) -> list[dict]:
    """Replay the recorded samples with one threshold changed at a time: the data behind a threshold-tuning chart."""
    rows = []
    base = Thresholds()
    for v in values:
        th = dataclasses.replace(base, **{field_name: type(getattr(base, field_name))(v)})
        results = [score_session(n, cues, samples, cal, th) for n, cues, samples, cal in samples_by_session]
        for kind, s in pool(results).items():
            rows.append({"threshold": field_name, "value": v, "kind": kind, "tp": s.tp, "fp": s.fp, "fn": s.fn,
                         "precision": s.precision, "recall": s.recall, "f1": s.f1})
    return rows
