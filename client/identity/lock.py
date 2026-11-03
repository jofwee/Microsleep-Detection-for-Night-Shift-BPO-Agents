"""Identity lock: only the enrolled agent's face is ever measured.

Per frame it is given the face boxes in view and two callbacks (embed a face, and
optionally score its liveness). It follows the agent's face from frame to frame, re-checks
who it is every few seconds, and raises:

  ABSENT             no verified face for absent_ms                 (agent stepped away)
  IDENTITY_MISMATCH  a face that is not the agent's, for mismatch_ms  (someone else sat down)
  SPOOF_SUSPECTED    no blink for spoof_no_blink_ms, or repeated failed liveness checks.
                     A face must also pass passive liveness before it is accepted as the agent,
                     so a photo or screen of the agent is never measured as them.
  MULTIPLE_FACES     a bystander in view for a while                  (information only)

IDENTITY_MISMATCH and SPOOF_SUSPECTED are sticky: the lock stays closed until reset(),
which is the forced re-login. All timing is in milliseconds.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Sequence

import numpy as np

from vision.drowsiness import Event, EventType

Box = tuple[float, float, float, float]  # x1, y1, x2, y2 (any consistent units)


def iou(a: Box, b: Box) -> float:
    ix1, iy1, ix2, iy2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


class LockStatus(str, Enum):
    VERIFIED = "VERIFIED"
    SEARCHING = "SEARCHING"  # nobody verified right now, timers still running
    ABSENT = "ABSENT"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    SPOOF_SUSPECTED = "SPOOF_SUSPECTED"


@dataclass(frozen=True)
class LockConfig:
    tau: float = 0.45  # cosine threshold to count as the agent
    reverify_ms: int = 3000
    search_interval_ms: int = 500  # how often to look for the agent among unverified faces
    absent_ms: int = 10_000
    mismatch_ms: int = 5_000
    spoof_no_blink_ms: int = 90_000
    liveness_check_ms: int = 2_000
    liveness_min: float = 0.35  # a real face in dim light scores 0.3 to 0.9; a phone picture 0.01 to 0.18
    liveness_window: int = 5  # look at the last N passive checks...
    liveness_fail_limit: int = 3  # ...and raise SPOOF_SUSPECTED when this many of them failed
    challenge_cooldown_ms: int = 300_000  # after a passed challenge, doubtful passive scores are not raised again for this long
    liveness_grace_ms: int = 30_000  # a face that passed the passive check this recently is re-accepted while the head is turned or down
    track_iou_min: float = 0.3  # below this the agent's face counts as lost
    jump_iou: float = 0.5  # below this the box "jumped": re-verify immediately
    multi_face_ms: int = 2000
    multi_face_cooldown_ms: int = 30_000
    max_dt_ms: int = 200  # gaps between frames longer than this do not count as eyes-open time


@dataclass
class LockResult:
    status: LockStatus
    agent_index: int | None  # index into the boxes passed to update(), None = measure nobody
    events: list[Event] = field(default_factory=list)
    score: float | None = None  # most recent cosine score
    liveness: float | None = None  # most recent passive liveness score
    challenge_needed: bool = False  # once, when the passive check keeps failing: ask the agent for a live action


class IdentityLock:
    def __init__(self, score_fn: Callable[[np.ndarray], float], cfg: LockConfig | None = None) -> None:
        self.score_fn = score_fn  # embedding -> cosine similarity to the enrolled agent
        self.cfg = cfg or LockConfig()
        self.reset()

    def reset(self) -> None:
        """Forced re-login: forget the track and every timer."""
        self._locked: LockStatus | None = None
        self._bound: Box | None = None
        self._prev_ts: int | None = None
        self._prev_count = 0
        self._last_verify = -10**9
        self._last_search = -10**9
        self._last_verified_seen: int | None = None
        self._mismatch_since: int | None = None
        self._absent_fired = False
        self._multi_since: int | None = None
        self._multi_last = -10**9
        self._no_blink_ms = 0.0
        self._last_liveness = -10**9
        self._liveness_hist: deque[bool] = deque(maxlen=self.cfg.liveness_window)  # True = a failed check
        self._liveness_score: float | None = None
        self._live_ok_ts = -10**9  # when a passive check last passed
        self._awaiting_challenge = False  # a live action was asked for and has not been answered yet
        self._challenge_ok_ts = -10**9  # when the agent last passed one
        self._challenge_failed = False
        self._score: float | None = None
        self.verifications = 0  # how many embeddings were computed (cost counter)

    def note_blink(self, ts_ms: int | None = None) -> None:
        """Tell the lock the agent blinked (resets the no-blink spoof timer)."""
        self._no_blink_ms = 0.0

    def _score_face(self, embed_fn: Callable[[int], np.ndarray], i: int) -> float:
        self.verifications += 1
        self._score = float(self.score_fn(embed_fn(i)))
        return self._score

    def update(
        self,
        ts: int,
        boxes: Sequence[Box],
        embed_fn: Callable[[int], np.ndarray],
        liveness_fn: Callable[[int], float] | None = None,
        eyes_active: bool = True,
        frontal: bool = True,
    ) -> LockResult:
        cfg = self.cfg
        if self._challenge_failed and not self._locked:
            return self._lock(LockStatus.SPOOF_SUSPECTED, EventType.SPOOF_SUSPECTED, ts, 0)
        if self._locked:
            return LockResult(self._locked, None, [], self._score, self._liveness_score)
        want_challenge = False

        dt = 0 if self._prev_ts is None else min(ts - self._prev_ts, cfg.max_dt_ms)
        self._prev_ts = ts
        if self._last_verified_seen is None:
            self._last_verified_seen = ts
        n = len(boxes)
        count_changed = n != self._prev_count
        self._prev_count = n
        events: list[Event] = []

        # 1) follow the agent's face from the previous frame
        idx: int | None = None
        force = count_changed
        if self._bound is not None:
            best_i, best_iou = -1, 0.0
            for i, b in enumerate(boxes):
                o = iou(self._bound, b)
                if o > best_iou:
                    best_i, best_iou = i, o
            if best_i >= 0 and best_iou >= cfg.track_iou_min:
                idx = best_i
                force = force or best_iou < cfg.jump_iou
            else:  # track lost: the identity has to be re-established
                self._bound = None
                force = True

        # 2) re-verify the tracked face when due
        verified = False
        spoof_candidate = False  # matches the agent but failed the liveness gate this frame
        failed_idx: int | None = None  # already scored and rejected this frame
        if idx is not None:
            if force or ts - self._last_verify >= cfg.reverify_ms:
                self._last_verify = ts
                if self._score_face(embed_fn, idx) >= cfg.tau:
                    verified = True
                else:
                    failed_idx = idx
                    self._bound, idx = None, None
            else:
                verified = True
            if verified:
                self._bound = boxes[idx]

        # 3) otherwise look for the agent among the faces in view (throttled: ArcFace is not free)
        if not verified and n > 0 and (force or ts - self._last_search >= cfg.search_interval_ms):
            self._last_search = ts
            best_i, best_s = -1, -2.0
            for i in range(n):
                if i == failed_idx:
                    continue
                s = self._score_face(embed_fn, i)
                if s > best_s:
                    best_i, best_s = i, s
            if best_s >= cfg.tau:
                # liveness gate: a face that looks like the agent is only accepted once it also
                # passes the passive liveness check, so a photo or screen is never measured as them
                live_ok = True
                if liveness_fn is not None and self._judging(ts) and frontal:
                    self._liveness_score = float(liveness_fn(best_i))
                    self._last_liveness = ts
                    live_ok = self._liveness_score >= cfg.liveness_min
                    self._liveness_hist.append(not live_ok)
                    if live_ok:
                        self._live_ok_ts = ts
                    want_challenge = self._limit_hit()
                elif liveness_fn is not None:
                    # Not counted: the head is turned or looking down (the model cannot judge it), a live-action challenge
                    # is open, or one was just passed. A face that passed recently (or answered a challenge) is taken
                    # back; a face that never passed waits until it faces the camera and does.
                    live_ok = (ts - self._live_ok_ts <= cfg.liveness_grace_ms
                               or ts - self._challenge_ok_ts < cfg.challenge_cooldown_ms)
                if live_ok:
                    idx, verified = best_i, True
                    self._bound = boxes[idx]
                    self._last_verify = ts
                else:
                    spoof_candidate = True

        # 4) timers and status
        if verified:
            self._last_verified_seen = ts
            self._mismatch_since = None
            self._absent_fired = False
            status = LockStatus.VERIFIED
        else:
            idx = None
            if n > 0 and spoof_candidate:  # the agent's face, but not live: not a stranger either
                self._mismatch_since = None
                status = LockStatus.SEARCHING
            elif n > 0:  # somebody is there, and it is not the agent
                if self._mismatch_since is None:
                    self._mismatch_since = ts
                if ts - self._mismatch_since >= cfg.mismatch_ms:
                    return self._lock(LockStatus.IDENTITY_MISMATCH, EventType.IDENTITY_MISMATCH, ts, ts - self._mismatch_since)
                status = LockStatus.SEARCHING
            else:
                self._mismatch_since = None
                gone = ts - self._last_verified_seen
                if gone >= cfg.absent_ms:
                    if not self._absent_fired:
                        self._absent_fired = True
                        events.append(Event(EventType.ABSENT, ts, gone, severity="warning"))
                    status = LockStatus.ABSENT
                else:
                    status = LockStatus.SEARCHING

        # 5) bystanders (information only)
        if verified and n > 1:
            if self._multi_since is None:
                self._multi_since = ts
            if ts - self._multi_since >= cfg.multi_face_ms and ts - self._multi_last >= cfg.multi_face_cooldown_ms:
                self._multi_last = ts
                events.append(Event(EventType.MULTIPLE_FACES, ts, ts - self._multi_since, float(n), "info"))
        else:
            self._multi_since = None

        # 6) spoof checks, only on the verified face
        if verified:
            if eyes_active:
                self._no_blink_ms += dt
            if self._no_blink_ms >= cfg.spoof_no_blink_ms:
                return self._lock(LockStatus.SPOOF_SUSPECTED, EventType.SPOOF_SUSPECTED, ts, int(self._no_blink_ms))
            # The passive model is trained on faces looking at the camera: while the head is turned or looking
            # down (typing, reading notes) its score is unreliable, so those moments are not counted against the agent.
            if liveness_fn is not None and frontal and self._judging(ts) and ts - self._last_liveness >= cfg.liveness_check_ms:
                self._last_liveness = ts
                self._liveness_score = float(liveness_fn(idx))
                self._liveness_hist.append(self._liveness_score < cfg.liveness_min)
                if self._liveness_score >= cfg.liveness_min:
                    self._live_ok_ts = ts
                want_challenge = want_challenge or self._limit_hit()

        return LockResult(status, idx, events, self._score, self._liveness_score, want_challenge)

    # --- the live-action challenge (the monitor runs it; the lock only decides when it is needed) -------------
    def _judging(self, ts: int) -> bool:
        """Passive scores count only when no challenge is open and none was passed recently."""
        return not self._awaiting_challenge and ts - self._challenge_ok_ts >= self.cfg.challenge_cooldown_ms

    def _limit_hit(self) -> bool:
        if sum(self._liveness_hist) >= self.cfg.liveness_fail_limit:
            self._awaiting_challenge = True
            return True
        return False

    def liveness_passed(self, ts: int) -> None:
        """The agent did the live action asked for: the doubt is cleared."""
        self._awaiting_challenge, self._challenge_failed = False, False
        self._challenge_ok_ts = self._live_ok_ts = ts
        self._liveness_hist.clear()

    def liveness_failed(self, ts: int) -> None:
        """No live action in time: from the next frame the lock is closed with SPOOF_SUSPECTED."""
        self._awaiting_challenge = False
        self._challenge_failed = True

    def _lock(self, status: LockStatus, kind: EventType, ts: int, duration: int) -> LockResult:
        self._locked = status
        self._bound = None
        event = Event(kind, ts, int(duration), self._score or 0.0, "critical")
        return LockResult(status, None, [event], self._score, self._liveness_score)
