"""Event state machines and the fatigue state (ALERT -> DROWSY -> MICROSLEEP).

Everything is driven by timestamps in milliseconds, never by frame counts, so the
rules behave the same at 15 FPS and at 30 FPS. All thresholds are per-agent and
come from a Calibration; the timing rules live in Thresholds.

Head angles use the convention from metrics.py: pitch is negative when the head drops.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum

from .calibration import Calibration


class EventType(str, Enum):
    MICROSLEEP = "MICROSLEEP"
    LONG_BLINK = "LONG_BLINK"
    YAWN = "YAWN"
    HEAD_NOD = "HEAD_NOD"
    HEAD_TILT = "HEAD_TILT"
    DROWSY = "DROWSY"
    # raised by the identity lock (client/identity/lock.py)
    ABSENT = "ABSENT"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    MULTIPLE_FACES = "MULTIPLE_FACES"
    SPOOF_SUSPECTED = "SPOOF_SUSPECTED"
    BLINK = "BLINK"  # counted for blink rate, but not stored as an event


class FatigueState(str, Enum):
    ALERT = "ALERT"
    DROWSY = "DROWSY"
    MICROSLEEP = "MICROSLEEP"


@dataclass(frozen=True)
class Event:
    """ts_ms is when the event was detected; duration_ms is how long the behaviour lasted."""

    type: EventType
    ts_ms: int
    duration_ms: int = 0
    value: float = 0.0
    severity: str = "info"  # info | warning | critical


@dataclass(frozen=True)
class Thresholds:
    """Timing and angle rules (configurable defaults from plan.md)."""

    # blinks and microsleep
    blink_min_ms: int = 50
    blink_max_ms: int = 500
    long_blink_ms: int = 300
    microsleep_ms: int = 500
    microsleep_hold_ms: int = 5000  # how long the MICROSLEEP state lasts after the eyes reopen
    # windows
    blink_window_ms: int = 60_000
    perclos_window_ms: int = 60_000
    perclos_drowsy: float = 0.15
    drowsy_window_ms: int = 300_000  # 5 min
    drowsy_yawns: int = 2
    drowsy_nods: int = 2
    drowsy_long_blinks: int = 3
    repeat_microsleeps: int = 2
    repeat_window_ms: int = 600_000  # 10 min
    # yawn
    yawn_ms: int = 2000
    jaw_open_min: float = 0.35  # jawOpen blendshape cross-check, used when available
    # head
    tilt_deg: float = 15.0
    tilt_ms: int = 2000
    tilt_release_deg: float = 12.0
    nod_deg: float = 15.0
    nod_return_deg: float = 7.0
    nod_max_ms: int = 1500
    turn_away_deg: float = 30.0
    # sampling
    max_dt_ms: int = 200  # longer gaps do not count towards PERCLOS


@dataclass(frozen=True)
class Sample:
    """One processed frame. None means "no data" (no face, or the value is unusable)."""

    ts_ms: int
    ear: float | None
    mar: float | None = None
    pitch: float | None = None
    yaw: float | None = None
    roll: float | None = None
    jaw_open: float | None = None
    looking_down: bool = False  # eyes + head down: reading the keyboard
    recent_input: bool = False  # keyboard/mouse activity in the last ~2 s


class EyeTracker:
    """Blinks, long blinks and microsleep from the EAR series, with hysteresis."""

    def __init__(self, cal: Calibration, th: Thresholds) -> None:
        self._cal = cal
        self._th = th
        self.closed = False
        self._closed_since = 0
        self.microsleep_fired = False

    @property
    def microsleep_active(self) -> bool:
        return self.closed and self.microsleep_fired

    def pause(self) -> None:
        """Lose the current closure (no face, or a false-alarm guard is active)."""
        self.closed = False
        self.microsleep_fired = False

    def update(self, ts: int, ear: float) -> list[Event]:
        events: list[Event] = []
        cal, th = self._cal, self._th
        threshold = cal.ear_reopen_threshold if self.closed else cal.ear_close_threshold
        now_closed = ear < threshold

        if now_closed and not self.closed:
            self.closed = True
            self._closed_since = ts
            self.microsleep_fired = False
        elif now_closed and self.closed:
            duration = ts - self._closed_since
            if duration >= th.microsleep_ms and not self.microsleep_fired:
                self.microsleep_fired = True
                events.append(Event(EventType.MICROSLEEP, ts, duration, ear, "critical"))
        elif not now_closed and self.closed:
            duration = ts - self._closed_since
            self.closed = False
            if self.microsleep_fired:
                self.microsleep_fired = False  # already reported while the eyes were shut
            elif duration >= th.blink_min_ms:
                if duration <= th.blink_max_ms:
                    events.append(Event(EventType.BLINK, ts, duration))
                if duration >= th.long_blink_ms:
                    events.append(Event(EventType.LONG_BLINK, ts, duration, severity="info"))
        return events


class BlinkRate:
    """Blinks per minute over a sliding window."""

    def __init__(self, window_ms: int) -> None:
        self._window = window_ms
        self._times: deque[int] = deque()
        self._start: int | None = None

    def start(self, ts: int) -> None:
        if self._start is None:
            self._start = ts

    def add(self, ts: int) -> None:
        self._times.append(ts)

    def rate(self, now: int) -> float | None:
        """None until at least 10 s of data exist."""
        while self._times and self._times[0] < now - self._window:
            self._times.popleft()
        if self._start is None:
            return None
        span = min(self._window, now - self._start)
        if span < 10_000:
            return None
        return len(self._times) * 60_000.0 / span


class Perclos:
    """Share of time the eyes are at least 80 % closed, over a sliding window."""

    def __init__(self, cal: Calibration, window_ms: int, max_dt_ms: int) -> None:
        self._thr = cal.ear_perclos_threshold
        self._window = window_ms
        self._max_dt = max_dt_ms
        self._segments: deque[tuple[int, int, bool]] = deque()  # (ts, dt, closed)
        self._prev_ts: int | None = None
        self._total = 0
        self._closed = 0

    def break_stream(self) -> None:
        self._prev_ts = None

    def update(self, ts: int, ear: float) -> None:
        if self._prev_ts is not None:
            dt = min(ts - self._prev_ts, self._max_dt)
            if dt > 0:
                is_closed = ear <= self._thr
                self._segments.append((ts, dt, is_closed))
                self._total += dt
                if is_closed:
                    self._closed += dt
        self._prev_ts = ts
        while self._segments and self._segments[0][0] < ts - self._window:
            _, dt, was_closed = self._segments.popleft()
            self._total -= dt
            if was_closed:
                self._closed -= dt

    def value(self) -> float | None:
        """None until at least 10 s of data exist."""
        if self._total < 10_000:
            return None
        return self._closed / self._total


class YawnTracker:
    """A wide-open mouth held for >= yawn_ms. Shorter openings are speech."""

    def __init__(self, cal: Calibration, th: Thresholds) -> None:
        self._thr = cal.yawn_threshold
        self._th = th
        self._since: int | None = None
        self._fired = False

    def reset(self) -> None:
        self._since = None
        self._fired = False

    def update(self, ts: int, mar: float, jaw_open: float | None) -> list[Event]:
        wide = mar > self._thr and (jaw_open is None or jaw_open >= self._th.jaw_open_min)
        if not wide:
            self.reset()
            return []
        if self._since is None:
            self._since = ts
        duration = ts - self._since
        if duration >= self._th.yawn_ms and not self._fired:
            self._fired = True
            return [Event(EventType.YAWN, ts, duration, mar, "warning")]
        return []


class HeadTracker:
    """Head tilt (sustained roll) and head nod (drop and snap back)."""

    def __init__(self, cal: Calibration, th: Thresholds) -> None:
        self._cal = cal
        self._th = th
        self.reset()

    def reset(self) -> None:
        self._tilt_since: int | None = None
        self._tilt_fired = False
        self._nod_state = "idle"  # idle | down | held
        self._nod_started = 0

    def update(self, ts: int, pitch: float | None, roll: float | None) -> list[Event]:
        events: list[Event] = []
        th = self._th
        if roll is not None:
            dev = abs(roll - self._cal.neutral_roll)
            if dev > th.tilt_deg:
                if self._tilt_since is None:
                    self._tilt_since = ts
                duration = ts - self._tilt_since
                if duration >= th.tilt_ms and not self._tilt_fired:
                    self._tilt_fired = True
                    events.append(Event(EventType.HEAD_TILT, ts, duration, dev, "warning"))
            elif dev < th.tilt_release_deg:
                self._tilt_since = None
                self._tilt_fired = False
        if pitch is not None:
            down = self._cal.neutral_pitch - pitch  # positive when the head is dropped
            if self._nod_state == "idle":
                if down >= th.nod_deg:
                    self._nod_state = "down"
                    self._nod_started = ts
            elif self._nod_state == "down":
                if down <= th.nod_return_deg:
                    duration = ts - self._nod_started
                    self._nod_state = "idle"
                    if duration <= th.nod_max_ms:
                        events.append(Event(EventType.HEAD_NOD, ts, duration, down, "warning"))
                elif ts - self._nod_started > th.nod_max_ms:
                    self._nod_state = "held"  # stayed down: looking at the desk, not a nod
            elif self._nod_state == "held":
                if down <= th.nod_return_deg:
                    self._nod_state = "idle"
        return events


class DrowsinessEngine:
    """Feeds one Sample per frame through every tracker and keeps the fatigue state."""

    def __init__(self, cal: Calibration | None = None, th: Thresholds | None = None) -> None:
        self.cal = cal or Calibration()
        self.th = th or Thresholds()
        self._eyes = EyeTracker(self.cal, self.th)
        self._blinks = BlinkRate(self.th.blink_window_ms)
        self._perclos = Perclos(self.cal, self.th.perclos_window_ms, self.th.max_dt_ms)
        self._yawn = YawnTracker(self.cal, self.th)
        self._head = HeadTracker(self.cal, self.th)
        self._state = FatigueState.ALERT
        self._microsleep_until = -1
        self._microsleeps: deque[int] = deque()
        self._yawns: deque[int] = deque()
        self._nods: deque[int] = deque()
        self._long_blinks: deque[int] = deque()
        self._last_ts = 0

    # --- public reads ---------------------------------------------------------------
    @property
    def state(self) -> FatigueState:
        return self._state

    @property
    def repeated_microsleeps(self) -> bool:
        """True when microsleeps keep happening (>= 2 in 10 min): the team leader is told."""
        self._trim(self._microsleeps, self._last_ts, self.th.repeat_window_ms)
        return len(self._microsleeps) >= self.th.repeat_microsleeps

    def metrics(self) -> dict:
        return {
            "state": self._state.value,
            "blink_rate": self._blinks.rate(self._last_ts),
            "perclos": self._perclos.value(),
            "eyes_closed": self._eyes.closed,
        }

    # --- main entry -----------------------------------------------------------------
    def update(self, s: Sample) -> list[Event]:
        ts = s.ts_ms
        self._last_ts = ts
        events: list[Event] = []

        if s.ear is None:  # no face: nothing is measured, nothing counts as sleep
            self._pause_all()
            self._refresh_state(ts, events)
            return events

        self._blinks.start(ts)
        turned_away = s.yaw is not None and abs(s.yaw - self.cal.neutral_yaw) > self.th.turn_away_deg
        if s.looking_down or s.recent_input or turned_away:
            self._eyes.pause()
            self._perclos.break_stream()
        else:
            events += self._eyes.update(ts, s.ear)
            self._perclos.update(ts, s.ear)

        if s.mar is not None:
            events += self._yawn.update(ts, s.mar, s.jaw_open)
        else:
            self._yawn.reset()
        events += self._head.update(ts, s.pitch, s.roll)

        for e in events:
            if e.type is EventType.BLINK:
                self._blinks.add(e.ts_ms)
            elif e.type is EventType.LONG_BLINK:
                self._long_blinks.append(e.ts_ms)
            elif e.type is EventType.YAWN:
                self._yawns.append(e.ts_ms)
            elif e.type is EventType.HEAD_NOD:
                self._nods.append(e.ts_ms)
            elif e.type is EventType.MICROSLEEP:
                self._microsleeps.append(e.ts_ms)
        if any(e.type is EventType.MICROSLEEP for e in events) or self._eyes.microsleep_active:
            self._microsleep_until = ts + self.th.microsleep_hold_ms

        self._refresh_state(ts, events)
        return events

    # --- internals ------------------------------------------------------------------
    def _pause_all(self) -> None:
        self._eyes.pause()
        self._perclos.break_stream()
        self._yawn.reset()
        self._head.reset()

    @staticmethod
    def _trim(q: deque[int], now: int, window: int) -> None:
        while q and q[0] < now - window:
            q.popleft()

    def _refresh_state(self, ts: int, events: list[Event]) -> None:
        window = self.th.drowsy_window_ms
        for q in (self._yawns, self._nods, self._long_blinks):
            self._trim(q, ts, window)
        perclos = self._perclos.value()
        drowsy = (
            (perclos is not None and perclos > self.th.perclos_drowsy)
            or len(self._yawns) >= self.th.drowsy_yawns
            or len(self._nods) >= self.th.drowsy_nods
            or len(self._long_blinks) >= self.th.drowsy_long_blinks
        )
        previous = self._state
        if ts <= self._microsleep_until:
            self._state = FatigueState.MICROSLEEP
        elif drowsy:
            self._state = FatigueState.DROWSY
        else:
            self._state = FatigueState.ALERT
        if self._state is FatigueState.DROWSY and previous is FatigueState.ALERT:
            events.append(Event(EventType.DROWSY, ts, value=perclos or 0.0, severity="warning"))
