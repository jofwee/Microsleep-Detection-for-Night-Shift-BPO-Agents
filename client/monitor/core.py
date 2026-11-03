"""The monitor, without the camera thread: frames in, queued events and per-minute metrics out.

MonitorCore is plain code (no threads, no hardware) so it is fully testable. runner.py wraps it with
a camera and a thread. The agent's face reference is the set of embeddings that just passed the
sign-in face check; they live in memory only (see reference.py).
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import numpy as np

from identity.liveness import Action, ActiveChallenge, ChallengeStatus
from identity.lock import LockConfig
from storage.queue import OfflineQueue
from vision.drowsiness import Event, EventType
from vision.calibration import Calibration
from vision.metrics import head_turn
from vision.pipeline import FramePipeline, Measurement

# lock statuses that mean "this is not (any longer) the enrolled agent at the desk"
IDENTITY_BAD = ("ABSENT", "IDENTITY_MISMATCH", "SPOOF_SUSPECTED")
FRESH_FRAME_S = 3.0  # no frame for this long = the monitor is not live


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class Readings:
    """The numbers the camera monitor is working with right now (shown to the agent next to the picture)."""

    ear: float | None = None  # eye openness (eye aspect ratio)
    ear_open: float = 0.30  # this agent's calibrated "open" and "closed" values, to scale the bar
    ear_closed: float = 0.10
    perclos: float | None = None  # share of the last minute the eyes were mostly closed
    blink_rate: float | None = None  # blinks per minute
    mar: float | None = None  # mouth opening (mouth aspect ratio)
    mar_yawn: float = 0.6  # above this for about two seconds counts as a yawn
    pitch: float | None = None  # head angles in degrees, relative to this agent's calibrated neutral pose
    yaw: float | None = None
    roll: float | None = None
    match: float | None = None  # similarity to the face that passed the sign-in check (0..1)
    liveness: float | None = None  # passive liveness score (1 = a real face)
    faces: int = 0
    looking_down: bool = False
    eyes_closed: bool = False
    brightness: float | None = None  # how bright the face is, 0..255 (a dim face makes the real-face check unreliable)


@dataclass
class Snapshot:
    fatigue: str = "ALERT"
    lock_status: str | None = None
    lock_score: float | None = None
    lock_liveness: float | None = None
    face_count: int = 0
    ear: float | None = None
    last_frame_at: float = 0.0
    prompt: str | None = None  # a live action the agent is being asked to do ("Please blink twice")
    readings: Readings | None = None


class MinuteAggregator:
    """Collects one row per wall-clock minute (mean EAR, blink rate, PERCLOS, yawns, nods).
    Minutes with less than `min_seconds` of measured time (camera off, agent away) produce no row."""

    def __init__(self, min_seconds: float = 10.0) -> None:
        self.min_seconds = min_seconds
        self._minute: int | None = None
        self._reset()

    def _reset(self) -> None:
        self._ear_sum = 0.0
        self._ear_n = 0
        self._yawns = 0
        self._nods = 0
        self._first: float | None = None
        self._last: float | None = None
        self._blink_rate = None
        self._perclos = None

    def add(self, wall: float, m: Measurement, events: list[Event], engine_metrics: dict, status: str | None) -> dict | None:
        minute = int(wall // 60)
        out = None
        if self._minute is not None and minute != self._minute:
            out = self.flush(status)
        self._minute = minute
        for e in events:
            self._yawns += e.type is EventType.YAWN
            self._nods += e.type is EventType.HEAD_NOD
        if m.ear_smooth is not None and not m.looking_down:
            self._ear_sum += m.ear_smooth
            self._ear_n += 1
            self._first = wall if self._first is None else self._first
            self._last = wall
            self._blink_rate = engine_metrics.get("blink_rate")
            self._perclos = engine_metrics.get("perclos")
        return out

    def flush(self, status: str | None = None) -> dict | None:
        if self._minute is None:
            return None
        measured = (self._last - self._first) if self._first is not None and self._last is not None else 0.0
        row = None
        if self._ear_n and measured >= self.min_seconds:
            row = {
                "minute": iso(self._minute * 60),
                "blink_rate": None if self._blink_rate is None else round(float(self._blink_rate), 2),
                "perclos": None if self._perclos is None else round(min(max(float(self._perclos), 0.0), 1.0), 4),
                "mean_ear": round(self._ear_sum / self._ear_n, 4),
                "yawns": self._yawns,
                "nods": self._nods,
                "status": status,
            }
        self._reset()
        self._minute = None
        return row


class MonitorCore:
    def __init__(
        self,
        pipeline: FramePipeline,
        queue: OfflineQueue,
        session_id: Callable[[], str | None],
        call_id: Callable[[], str | None] = lambda: None,
        status: Callable[[], str | None] = lambda: None,
        on_event: Callable[[Event], None] | None = None,
        wall: Callable[[], float] = time.time,
        trace: Callable[[str], None] | None = None,
        make_challenge: Callable[[], ActiveChallenge] | None = None,
    ) -> None:
        self.pipeline, self.queue = pipeline, queue
        self._make_challenge = make_challenge or self._random_challenge
        self._challenge: ActiveChallenge | None = None
        self._challenge_passed = False  # the agent did a live action in this camera session: that proves it, whatever the last passive score was
        self.trace = trace
        self._traced: tuple[str | None, float | None] = (None, None)
        self.session_id, self.call_id, self.status = session_id, call_id, status
        self.on_event, self.wall = on_event, wall
        self.snapshot = Snapshot()
        self._agg = MinuteAggregator()
        self._base: tuple[float, int] | None = None  # (wall time, frame ts) of the first frame

    def _wall_of(self, ts_ms: int) -> float:
        if self._base is None:
            self._base = (self.wall(), ts_ms)
        return self._base[0] + (ts_ms - self._base[1]) / 1000.0

    def process(self, frame: np.ndarray, ts_ms: int, recent_input: bool = False) -> Measurement:
        m, events = self.pipeline.process(frame, ts_ms, recent_input)
        wall = self._wall_of(ts_ms)
        sid = self.session_id()
        stored = [e for e in events if e.type is not EventType.BLINK]
        for e in stored:
            if sid:
                row = {
                    "session_id": sid, "type": e.type.value, "ts": iso(self._wall_of(e.ts_ms)),
                    "duration_ms": int(e.duration_ms), "value": float(e.value), "severity": e.severity,
                }
                cid = self.call_id()
                if cid:
                    row["call_id"] = cid
                self.queue.put("event", row)
            if self.on_event:
                self.on_event(e)
        minute_row = self._agg.add(wall, m, events, self.pipeline.engine.metrics(), self.status())
        if minute_row and sid:
            self.queue.put("metric", {"session_id": sid, **minute_row})
        self._note(m)
        self._step_up(m, ts_ms)
        self.snapshot = Snapshot(
            prompt=self._challenge.prompt if self._challenge is not None else None,
            readings=self._readings(m, frame),
            fatigue=self.pipeline.engine.state.value, lock_status=m.lock_status, lock_score=m.lock_score,
            lock_liveness=m.lock_liveness, face_count=m.face_count, ear=m.ear_smooth, last_frame_at=self.wall(),
        )
        return m

    @staticmethod
    def _brightness(m: Measurement, frame: np.ndarray) -> float | None:
        """Mean brightness of the face region (a cheap, sub-sampled estimate)."""
        if m.px is None or frame is None or frame.ndim != 3:
            return None
        h, w = frame.shape[:2]
        x1, y1 = np.clip(m.px.min(axis=0), 0, [w - 1, h - 1]).astype(int)
        x2, y2 = np.clip(m.px.max(axis=0), 0, [w - 1, h - 1]).astype(int)
        patch = frame[y1:y2 + 1:4, x1:x2 + 1:4]
        return float(patch.mean()) if patch.size else None

    def _readings(self, m: Measurement, frame: np.ndarray | None = None) -> Readings:
        """The numbers behind the picture. A display feature: it must never be able to stop the monitor."""
        try:
            engine = self.pipeline.engine
            cal, metrics = getattr(engine, "cal", None) or Calibration(), engine.metrics()
            yawn_at = getattr(cal, "yawn_threshold", 0.6)
            return Readings(
                ear=m.ear_smooth, ear_open=cal.ear_open, ear_closed=cal.ear_closed,
                perclos=metrics.get("perclos"), blink_rate=metrics.get("blink_rate"),
                mar=m.mar, mar_yawn=float(yawn_at) if isinstance(yawn_at, (int, float)) else 0.6,
                pitch=None if m.pitch is None else m.pitch - cal.neutral_pitch,
                yaw=None if m.yaw is None else m.yaw - cal.neutral_yaw,
                roll=None if m.roll is None else m.roll - cal.neutral_roll,
                match=m.lock_score, liveness=m.lock_liveness, faces=m.face_count,
                looking_down=m.looking_down, eyes_closed=bool(metrics.get("eyes_closed")),
                brightness=self._brightness(m, frame) if frame is not None else None,
            )
        except Exception:  # noqa: BLE001
            return Readings(faces=m.face_count)

    def _random_challenge(self) -> ActiveChallenge:
        return ActiveChallenge([random.SystemRandom().choice(list(Action))], self.pipeline.engine.cal)

    def _step_up(self, m: Measurement, ts_ms: int) -> None:
        """The passive photo check keeps failing: instead of closing the lock on a doubtful score, ask the agent for
        one live action (blink twice, or turn the head). Doing it clears the doubt; not doing it closes the lock."""
        lock = getattr(self.pipeline, "lock", None)
        if lock is None:
            return
        if self._challenge is None:
            if m.challenge_needed:
                self._challenge = self._make_challenge()
                if self.trace:
                    self.trace("challenge asked: the passive check keeps failing")
            return
        turn = head_turn(m.px) if m.px is not None else None
        status = self._challenge.update(ts_ms, m.ear_smooth, turn)
        if status is ChallengeStatus.PASSED:
            lock.liveness_passed(ts_ms)
            self._challenge_passed = True
        elif status is ChallengeStatus.FAILED:
            lock.liveness_failed(ts_ms)
        if status is not ChallengeStatus.PENDING:
            if self.trace:
                self.trace(f"challenge {status.value.lower()}")
            self._challenge = None

    def _note(self, m: Measurement) -> None:
        """Trace lock status changes and low passive-liveness scores (with the head pose) for later explanation."""
        if self.trace is None:
            return
        low = m.lock_liveness is not None and m.lock_liveness < 0.5
        key = (m.lock_status, m.lock_liveness if low else None)
        if key == self._traced:
            return
        self._traced = key
        pose = "" if m.pitch is None else f" pitch={m.pitch:.0f} yaw={m.yaw:.0f}"
        score = "" if m.lock_score is None else f" match={m.lock_score:.2f}"
        live = "" if m.lock_liveness is None else f" liveness={m.lock_liveness:.2f}"
        self.trace(f"lock={m.lock_status} faces={m.face_count}{score}{live}{pose} looking_down={m.looking_down}")

    def finish(self) -> None:
        """Write the minute that was in progress (the camera is being switched off)."""
        row, sid = self._agg.flush(self.status()), self.session_id()
        if row and sid:
            self.queue.put("metric", {"session_id": sid, **row})
        self.snapshot = Snapshot()
        self._challenge = None
        self._base = None

    # --- what the heartbeat reports -------------------------------------------------------------
    def heartbeat_state(self) -> tuple[str, bool, str]:
        s = self.snapshot
        live = (self.wall() - s.last_frame_at) < FRESH_FRAME_S
        if not live:
            return "OFF", False, "ALERT"
        return "LIVE", s.lock_status not in IDENTITY_BAD, s.fatigue

    def verified(self) -> bool:
        """The agent is at the desk, matches the sign-in face, and passed the passive liveness check."""
        s = self.snapshot
        live = self._challenge_passed or s.lock_liveness is None or s.lock_liveness >= LockConfig().liveness_min
        return s.lock_status == "VERIFIED" and live
