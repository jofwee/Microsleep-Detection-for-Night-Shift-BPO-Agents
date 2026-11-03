"""Pure geometry on MediaPipe landmarks: EAR, MAR, head angles and small helpers.

Landmark arrays are (N, 2) or (N, 3) in *pixel* units (x * width, y * height).
EAR and MAR are ratios of distances, so x and y must share one scale. The z column
is ignored because MediaPipe's z uses a different scale.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

# MediaPipe Face Mesh indices, ordered p1..p6 as in Soukupova & Cech 2016:
# p1/p4 are the eye corners, p2/p6 and p3/p5 are the upper/lower lid pairs.
RIGHT_EYE = (33, 160, 158, 133, 153, 144)
LEFT_EYE = (362, 385, 387, 263, 373, 380)

# Inner lips (top, bottom) and mouth corners (left, right).
MOUTH_TOP, MOUTH_BOTTOM = 13, 14
MOUTH_LEFT, MOUTH_RIGHT = 78, 308


def _dist(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(np.asarray(a[:2], dtype=float) - np.asarray(b[:2], dtype=float)))


def eye_aspect_ratio(landmarks: np.ndarray, idx: Sequence[int]) -> float:
    """EAR = (|p2-p6| + |p3-p5|) / (2 |p1-p4|)."""
    p1, p2, p3, p4, p5, p6 = (landmarks[i] for i in idx)
    width = _dist(p1, p4)
    if width < 1e-6:
        return 0.0
    return (_dist(p2, p6) + _dist(p3, p5)) / (2.0 * width)


def mean_ear(landmarks: np.ndarray) -> float:
    """Average EAR of both eyes."""
    return 0.5 * (eye_aspect_ratio(landmarks, RIGHT_EYE) + eye_aspect_ratio(landmarks, LEFT_EYE))


def mouth_aspect_ratio(landmarks: np.ndarray) -> float:
    """MAR = |lm13-lm14| / |lm78-lm308| (inner lip gap over mouth width)."""
    width = _dist(landmarks[MOUTH_LEFT], landmarks[MOUTH_RIGHT])
    if width < 1e-6:
        return 0.0
    return _dist(landmarks[MOUTH_TOP], landmarks[MOUTH_BOTTOM]) / width


@dataclass(frozen=True)
class HeadPoseConfig:
    """Sign flips so the rest of the code can assume one convention.

    Convention used downstream: pitch is negative when the head drops (nods down),
    yaw and roll are signed but only their magnitude matters for the rules.

    Checked live on a real webcam (2026-11-03): MediaPipe's matrix gives a POSITIVE
    pitch when the head drops, so the default flips it. Yaw and roll only matter by
    size. Pass HeadPoseConfig(1, 1, 1) for the raw decomposition.
    """

    pitch_sign: int = -1
    yaw_sign: int = 1
    roll_sign: int = 1


def euler_from_matrix(matrix: np.ndarray, cfg: HeadPoseConfig = HeadPoseConfig()) -> tuple[float, float, float]:
    """Return (pitch, yaw, roll) in degrees from a 4x4 (or 3x3) transform.

    Decomposes R = Rz(roll) @ Ry(yaw) @ Rx(pitch). The columns are normalised first
    because MediaPipe's face transform can carry a uniform scale.
    """
    m = np.asarray(matrix, dtype=float)
    rot = m[:3, :3].copy()
    norms = np.linalg.norm(rot, axis=0)
    norms[norms < 1e-9] = 1.0
    rot /= norms

    sy = math.hypot(rot[0, 0], rot[1, 0])
    if sy > 1e-6:
        pitch = math.atan2(rot[2, 1], rot[2, 2])
        yaw = math.atan2(-rot[2, 0], sy)
        roll = math.atan2(rot[1, 0], rot[0, 0])
    else:  # gimbal lock: looking straight up/down
        pitch = math.atan2(-rot[1, 2], rot[1, 1])
        yaw = math.atan2(-rot[2, 0], sy)
        roll = 0.0
    return (
        cfg.pitch_sign * math.degrees(pitch),
        cfg.yaw_sign * math.degrees(yaw),
        cfg.roll_sign * math.degrees(roll),
    )


class MedianFilter:
    """Median of the last n values (3 frames by default) to knock out single-frame noise."""

    def __init__(self, n: int = 3) -> None:
        self._buf: deque[float] = deque(maxlen=n)

    def push(self, value: float) -> float:
        self._buf.append(value)
        return float(np.median(self._buf))

    def reset(self) -> None:
        self._buf.clear()


def looking_down(
    blendshapes: Mapping[str, float],
    pitch_down_deg: float,
    gaze_min: float = 0.5,
    pitch_min: float = 8.0,
) -> bool:
    """True when the eyes look down and the head is tilted down (reading the keyboard).

    Closed-looking eyelids in this pose are not a microsleep, so the engine pauses.
    `pitch_down_deg` is positive when the head is dropped.
    """
    gaze = 0.5 * (blendshapes.get("eyeLookDownLeft", 0.0) + blendshapes.get("eyeLookDownRight", 0.0))
    return gaze >= gaze_min and pitch_down_deg >= pitch_min


def head_turn(landmarks: np.ndarray) -> float:
    """Left/right head turn read straight from the landmarks (no matrix, so no sign ambiguity).

    The nose tip's offset from the middle of the outer eye corners, measured along the
    eye line and divided by the eye width. 0 = facing the camera. Positive = the person
    turned toward THEIR left, because in a non-mirrored frame their left is image-right.
    Roughly 0.1 per 10 degrees of yaw near the front.
    """
    right_outer, left_outer, nose = landmarks[33][:2], landmarks[263][:2], landmarks[1][:2]
    axis = np.asarray(left_outer, dtype=float) - np.asarray(right_outer, dtype=float)
    span = float(np.linalg.norm(axis))
    if span < 1e-6:
        return 0.0
    mid = (np.asarray(right_outer, dtype=float) + np.asarray(left_outer, dtype=float)) / 2
    return float((np.asarray(nose, dtype=float) - mid) @ (axis / span)) / span
