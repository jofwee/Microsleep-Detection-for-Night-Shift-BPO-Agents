"""Enrollment capture: ~20 good frames across 5 head poses, then 5 diverse embeddings + a mean.

Drive it with feed() once per camera frame and show `status.message` to the person. The
calibration (eyes open / closed / mouth) is a separate step: vision.calibration.compute_calibration.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

import cv2
import numpy as np

from vision.calibration import Calibration
from vision.landmarks import FaceResult
from vision.metrics import euler_from_matrix, head_turn

from .embedder import FaceEmbedder, align_face
from .profile import EnrolledProfile


class EnrollmentError(ValueError):
    pass


class Pose(str, Enum):
    FRONT = "front"
    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"


DEFAULT_POSES = (Pose.FRONT, Pose.LEFT, Pose.RIGHT, Pose.UP, Pose.DOWN)
HINTS = {
    Pose.FRONT: "Look straight at the camera",
    Pose.LEFT: "Turn your head a little to your LEFT",
    Pose.RIGHT: "Turn your head a little to your RIGHT",
    Pose.UP: "Tilt your chin up a little",
    Pose.DOWN: "Tilt your chin down a little",
}


@dataclass(frozen=True)
class QualityConfig:
    min_face_width: float = 0.15  # face width as a fraction of the frame width
    max_face_width: float = 0.85
    min_sharpness: float = 25.0  # variance of the Laplacian of the aligned face
    brightness: tuple[float, float] = (50.0, 210.0)  # mean grey level of the aligned face


@dataclass
class QualityReport:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    face_width: float = 0.0
    sharpness: float = 0.0
    brightness: float = 0.0


def assess_quality(face: FaceResult, aligned_bgr: np.ndarray, cfg: QualityConfig = QualityConfig()) -> QualityReport:
    x1, _, x2, _ = face.box()
    width = x2 - x1
    gray = cv2.cvtColor(aligned_bgr, cv2.COLOR_BGR2GRAY)
    sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    bright = float(gray.mean())
    reasons = []
    if width < cfg.min_face_width:
        reasons.append("Move closer to the camera")
    elif width > cfg.max_face_width:
        reasons.append("Move back a little")
    # brightness before sharpness: a dark picture is also low-contrast, and light is the fix
    if bright < cfg.brightness[0]:
        reasons.append("Too dark: add light in front of you")
    elif bright > cfg.brightness[1]:
        reasons.append("Too bright: avoid a light right behind or beside the camera")
    if sharp < cfg.min_sharpness:
        reasons.append("Hold still, the picture is blurry")
    return QualityReport(not reasons, reasons, width, sharp, bright)


def pose_matches(pose: Pose, turn_rel: float, pitch_rel: float) -> bool:
    """turn_rel: head_turn() minus the frontal reading (positive = their left).
    pitch_rel: pitch minus the frontal reading (negative = chin down)."""
    if pose is Pose.FRONT:
        return abs(turn_rel) <= 0.06 and abs(pitch_rel) <= 8
    if pose is Pose.LEFT:
        return 0.12 <= turn_rel <= 0.50 and abs(pitch_rel) <= 12
    if pose is Pose.RIGHT:
        return -0.50 <= turn_rel <= -0.12 and abs(pitch_rel) <= 12
    if pose is Pose.UP:
        return pitch_rel >= 8 and abs(turn_rel) <= 0.10
    return pitch_rel <= -8 and abs(turn_rel) <= 0.10  # DOWN


@dataclass
class EnrollmentStatus:
    pose: Pose | None
    collected: int  # good frames in this pose
    per_pose: int
    total: int
    total_needed: int
    message: str
    done: bool = False


class EnrollmentSession:
    """rounds=2 repeats the poses (for example once with glasses, once without)."""

    def __init__(
        self,
        embedder: FaceEmbedder,
        poses: Sequence[Pose] = DEFAULT_POSES,
        frames_per_pose: int = 4,
        rounds: int = 1,
        min_gap_ms: int = 250,
        quality: QualityConfig = QualityConfig(),
    ) -> None:
        self.embedder = embedder
        self.stages = list(poses) * rounds
        self.per_pose = frames_per_pose
        self.min_gap_ms = min_gap_ms
        self.quality = quality
        self._stage = 0
        self._in_stage = 0
        self._last_capture = -10**9
        self._frontal: list[tuple[float, float]] = []
        self._turn0: float | None = None
        self._pitch0: float | None = None
        self.embeddings: list[np.ndarray] = []
        self.last_quality: QualityReport | None = None

    @property
    def done(self) -> bool:
        return self._stage >= len(self.stages)

    @property
    def total_needed(self) -> int:
        return len(self.stages) * self.per_pose

    def _status(self, message: str) -> EnrollmentStatus:
        pose = None if self.done else self.stages[self._stage]
        return EnrollmentStatus(pose, self._in_stage, self.per_pose, len(self.embeddings), self.total_needed, message, self.done)

    def feed(self, ts_ms: int, frame_bgr: np.ndarray, faces: Sequence[FaceResult]) -> EnrollmentStatus:
        if self.done:
            return self._status("All done")
        pose = self.stages[self._stage]
        if len(faces) != 1:
            return self._status("Only you should be in view" if faces else "I cannot see your face")
        face = faces[0]
        if face.matrix is None:
            return self._status("I cannot see your face")

        h, w = frame_bgr.shape[:2]
        turn = head_turn(face.pixels(w, h))
        pitch = euler_from_matrix(face.matrix)[0]

        # the first FRONT stage defines what "facing the camera" reads for this person
        if self._turn0 is None:
            turn_rel, pitch_rel = turn, pitch - float(np.median([p for _, p in self._frontal] or [pitch]))
        else:
            turn_rel, pitch_rel = turn - self._turn0, pitch - self._pitch0
        if not pose_matches(pose, turn_rel, pitch_rel):
            return self._status(HINTS[pose])

        aligned = align_face(frame_bgr, face)
        report = assess_quality(face, aligned, self.quality)
        self.last_quality = report
        if not report.ok:
            return self._status(report.reasons[0])
        if ts_ms - self._last_capture < self.min_gap_ms:
            return self._status("Hold that position")

        self._last_capture = ts_ms
        self.embeddings.append(self.embedder.embed(frame_bgr, face))
        if self._turn0 is None:
            self._frontal.append((turn, pitch))
        self._in_stage += 1
        if self._in_stage >= self.per_pose:
            if self._turn0 is None:  # first stage finished: fix the frontal reference
                self._turn0 = float(np.median([t for t, _ in self._frontal]))
                self._pitch0 = float(np.median([p for _, p in self._frontal]))
            self._stage += 1
            self._in_stage = 0
        return self._status("Good" if not self.done else "All done")

    def finalize(
        self,
        calibration: Calibration | None = None,
        keep: int = 5,
        pair_min_similarity: float = 0.25,
        max_bad_pair_fraction: float = 0.05,
    ) -> EnrolledProfile:
        """Pick `keep` diverse embeddings and the mean template.

        Raises EnrollmentError if the frames do not look like one person (for example
        someone else stepped in mid-capture). In one person's capture almost no two frames
        look like different people, even across head poses, so any real share of
        low-similarity pairs means a stranger or a badly aligned frame got in. Comparing
        each frame with the average would miss it: the average of two people sits between them.
        """
        if len(self.embeddings) < keep:
            raise EnrollmentError(f"need at least {keep} good frames, have {len(self.embeddings)}")
        embs = np.stack(self.embeddings)
        pairs = (embs @ embs.T)[np.triu_indices(len(embs), 1)]
        bad = float((pairs < pair_min_similarity).mean())
        if bad > max_bad_pair_fraction:
            raise EnrollmentError(
                f"the captured faces do not look like one person ({bad:.0%} of frame pairs disagree); "
                "make sure only you are in front of the camera, and start again"
            )
        mean = embs.mean(axis=0)
        mean /= np.linalg.norm(mean)
        return EnrolledProfile(
            embeddings=embs[select_diverse(embs, mean, keep)],
            mean=mean,
            model_name=self.embedder.name,
            model_version=self.embedder.version,
            calibration=calibration,
            enrolled_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        )


def select_diverse(embs: np.ndarray, mean: np.ndarray, keep: int) -> list[int]:
    """Farthest-point selection: start with the most typical embedding, then keep adding
    the one least similar to everything chosen so far."""
    chosen = [int(np.argmax(embs @ mean))]
    while len(chosen) < min(keep, len(embs)):
        best_sim_to_chosen = (embs @ embs[chosen].T).max(axis=1)
        best_sim_to_chosen[chosen] = np.inf
        chosen.append(int(np.argmin(best_sim_to_chosen)))
    return chosen
