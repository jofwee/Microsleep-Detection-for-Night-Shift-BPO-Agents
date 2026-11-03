"""Per-frame glue: frame -> landmarks -> measurements -> DrowsinessEngine events.

The live overlay, the replay harness and (later) the tray app all call this, so the
detection behaves identically everywhere.

With an IdentityLock the pipeline measures only the verified agent's face. When the lock
has nobody verified (agent away, someone else in the seat) the engine gets "no data", so
that time is never counted as sleep and a stranger's eyes are never measured as the agent's.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from .drowsiness import DrowsinessEngine, Event, EventType, Sample
from .landmarks import FaceLandmarker, FaceResult, primary_face
from .metrics import (
    HeadPoseConfig,
    MedianFilter,
    euler_from_matrix,
    looking_down,
    mean_ear,
    mouth_aspect_ratio,
)

if TYPE_CHECKING:  # identity depends on vision, not the other way round
    from identity.embedder import FaceEmbedder
    from identity.liveness import PassiveLiveness
    from identity.lock import IdentityLock


@dataclass
class Measurement:
    ts_ms: int
    face_count: int
    face: FaceResult | None = None
    px: np.ndarray | None = None  # (478, 2) pixel landmarks of the measured face
    ear: float | None = None  # raw EAR
    ear_smooth: float | None = None  # 3-frame median, what the engine sees
    mar: float | None = None
    pitch: float | None = None
    yaw: float | None = None
    roll: float | None = None
    jaw_open: float | None = None
    looking_down: bool = False
    lock_status: str | None = None  # IdentityLock status, when a lock is used
    lock_score: float | None = None  # last cosine score against the enrolled agent
    lock_liveness: float | None = None  # last passive liveness score (1 = real)
    challenge_needed: bool = False  # the passive check keeps failing: the monitor should ask for a live action


class FramePipeline:
    def __init__(
        self,
        landmarker: FaceLandmarker,
        engine: DrowsinessEngine,
        head_cfg: HeadPoseConfig = HeadPoseConfig(),
        lock: "IdentityLock | None" = None,
        embedder: "FaceEmbedder | None" = None,
        liveness: "PassiveLiveness | None" = None,
    ) -> None:
        if lock is not None and embedder is None:
            raise ValueError("an IdentityLock needs an embedder")
        self.landmarker = landmarker
        self.engine = engine
        self.head_cfg = head_cfg
        self.lock = lock
        self.embedder = embedder
        self.liveness = liveness
        self._median = MedianFilter(3)
        self._eyes_active = True
        self._frontal = True  # the face is looking roughly at the camera (not down, not turned away)

    def set_engine(self, engine: DrowsinessEngine) -> None:
        self.engine = engine

    def process(
        self, frame_bgr: np.ndarray, ts_ms: int, recent_input: bool = False
    ) -> tuple[Measurement, list[Event]]:
        faces = self.landmarker.detect(frame_bgr, ts_ms)

        lock_events: list[Event] = []
        lock_status = lock_score = lock_liveness = None
        challenge_needed = False
        if self.lock is not None:
            result = self.lock.update(
                ts_ms,
                [f.box() for f in faces],
                embed_fn=lambda i: self.embedder.embed(frame_bgr, faces[i]),
                liveness_fn=(lambda i: self.liveness.score(frame_bgr, faces[i])) if self.liveness else None,
                eyes_active=self._eyes_active,
                frontal=self._frontal,
            )
            face = faces[result.agent_index] if result.agent_index is not None else None
            lock_events, lock_status, lock_score = result.events, result.status.value, result.score
            lock_liveness = result.liveness
            challenge_needed = result.challenge_needed
        else:
            face = primary_face(faces)

        if face is None:
            self._median.reset()
            events = self.engine.update(Sample(ts_ms, ear=None)) + lock_events
            return Measurement(ts_ms, len(faces), lock_status=lock_status, lock_score=lock_score, lock_liveness=lock_liveness,
                               challenge_needed=challenge_needed), events

        h, w = frame_bgr.shape[:2]
        px = face.pixels(w, h)
        ear = mean_ear(px)
        ear_smooth = self._median.push(ear)
        mar = mouth_aspect_ratio(px)
        pitch = yaw = roll = None
        if face.matrix is not None:
            pitch, yaw, roll = euler_from_matrix(face.matrix, self.head_cfg)
        jaw = face.blendshapes.get("jawOpen")
        cal = self.engine.cal
        pitch_down = (cal.neutral_pitch - pitch) if pitch is not None else 0.0
        down = looking_down(face.blendshapes, pitch_down)

        events = self.engine.update(
            Sample(
                ts_ms,
                ear=ear_smooth,
                mar=mar,
                pitch=pitch,
                yaw=yaw,
                roll=roll,
                jaw_open=jaw,
                looking_down=down,
                recent_input=recent_input,
            )
        )
        if self.lock is not None:
            turned_away = yaw is not None and abs(yaw - cal.neutral_yaw) > self.engine.th.turn_away_deg
            self._eyes_active = not (down or recent_input or turned_away)
            self._frontal = not (down or turned_away)
            if any(e.type is EventType.BLINK for e in events):
                self.lock.note_blink(ts_ms)
        m = Measurement(
            ts_ms, len(faces), face, px, ear, ear_smooth, mar, pitch, yaw, roll, jaw, down, lock_status, lock_score, lock_liveness,
            challenge_needed,
        )
        return m, events + lock_events
