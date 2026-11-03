import math

import numpy as np
import pytest

from vision.metrics import (
    LEFT_EYE,
    MOUTH_BOTTOM,
    MOUTH_LEFT,
    MOUTH_RIGHT,
    MOUTH_TOP,
    RIGHT_EYE,
    HeadPoseConfig,
    MedianFilter,
    eye_aspect_ratio,
    euler_from_matrix,
    looking_down,
    mean_ear,
    mouth_aspect_ratio,
)


RAW = HeadPoseConfig(1, 1, 1)  # the unflipped decomposition, for checking the maths


def blank() -> np.ndarray:
    return np.zeros((478, 2))


def set_eye(lm: np.ndarray, idx, lid_gap: float, width: float = 10.0, x0: float = 0.0) -> None:
    """Eye of the given width; lid_gap is the full opening between upper and lower lid."""
    p1, p2, p3, p4, p5, p6 = idx
    half = lid_gap / 2
    lm[p1] = (x0, 0)
    lm[p4] = (x0 + width, 0)
    lm[p2] = (x0 + 0.3 * width, half)
    lm[p6] = (x0 + 0.3 * width, -half)
    lm[p3] = (x0 + 0.7 * width, half)
    lm[p5] = (x0 + 0.7 * width, -half)


def test_ear_open_eye():
    lm = blank()
    set_eye(lm, RIGHT_EYE, lid_gap=4.0)
    assert eye_aspect_ratio(lm, RIGHT_EYE) == pytest.approx(0.4)  # (4 + 4) / (2 * 10)


def test_ear_closed_eye_is_small():
    lm = blank()
    set_eye(lm, LEFT_EYE, lid_gap=0.4)
    assert eye_aspect_ratio(lm, LEFT_EYE) == pytest.approx(0.04)


def test_ear_ignores_scale():
    a, b = blank(), blank()
    set_eye(a, RIGHT_EYE, 4.0, width=10.0)
    set_eye(b, RIGHT_EYE, 8.0, width=20.0)
    assert eye_aspect_ratio(a, RIGHT_EYE) == pytest.approx(eye_aspect_ratio(b, RIGHT_EYE))


def test_ear_uses_only_xy_of_3d_landmarks():
    lm = np.zeros((478, 3))
    set_eye(lm[:, :2], RIGHT_EYE, 4.0)
    lm[:, 2] = 99.0
    assert eye_aspect_ratio(lm, RIGHT_EYE) == pytest.approx(0.4)


def test_ear_degenerate_width_is_zero():
    assert eye_aspect_ratio(blank(), RIGHT_EYE) == 0.0


def test_mean_ear_averages_both_eyes():
    lm = blank()
    set_eye(lm, RIGHT_EYE, 4.0)  # 0.4
    set_eye(lm, LEFT_EYE, 2.0, x0=30.0)  # 0.2
    assert mean_ear(lm) == pytest.approx(0.3)


def test_mar():
    lm = blank()
    lm[MOUTH_LEFT], lm[MOUTH_RIGHT] = (0, 0), (10, 0)
    lm[MOUTH_TOP], lm[MOUTH_BOTTOM] = (5, -3), (5, 3)
    assert mouth_aspect_ratio(lm) == pytest.approx(0.6)


def rot(pitch, yaw, roll) -> np.ndarray:
    """R = Rz(roll) @ Ry(yaw) @ Rx(pitch), angles in degrees."""
    x, y, z = map(math.radians, (pitch, yaw, roll))
    rx = np.array([[1, 0, 0], [0, math.cos(x), -math.sin(x)], [0, math.sin(x), math.cos(x)]])
    ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    rz = np.array([[math.cos(z), -math.sin(z), 0], [math.sin(z), math.cos(z), 0], [0, 0, 1]])
    return rz @ ry @ rx


@pytest.mark.parametrize("angles", [(0, 0, 0), (15, 0, 0), (-20, 10, 5), (5, -35, 12), (-10, 20, -25)])
def test_euler_roundtrip(angles):
    m = np.eye(4)
    m[:3, :3] = rot(*angles)
    assert euler_from_matrix(m, RAW) == pytest.approx(angles, abs=1e-6)


def test_euler_ignores_scale_and_translation():
    m = np.eye(4)
    m[:3, :3] = 1.7 * rot(-12, 8, 20)
    m[:3, 3] = (3.0, -4.0, 55.0)
    assert euler_from_matrix(m, RAW) == pytest.approx((-12, 8, 20), abs=1e-6)


def test_euler_sign_flips():
    m = np.eye(4)
    m[:3, :3] = rot(10, 20, 30)
    cfg = HeadPoseConfig(pitch_sign=-1, yaw_sign=1, roll_sign=-1)
    assert euler_from_matrix(m, cfg) == pytest.approx((-10, 20, -30), abs=1e-6)


def test_euler_gimbal_lock_does_not_crash():
    m = np.eye(4)
    m[:3, :3] = rot(0, 90, 0)
    pitch, yaw, roll = euler_from_matrix(m, RAW)
    assert yaw == pytest.approx(90, abs=1e-3)


def test_median_filter_removes_single_frame_spike():
    f = MedianFilter(3)
    out = [f.push(v) for v in (0.30, 0.30, 0.05, 0.30, 0.30)]
    assert out[2] == pytest.approx(0.30)  # the one-frame dip never reaches the engine
    assert out[-1] == pytest.approx(0.30)


def test_median_filter_follows_a_real_closure():
    f = MedianFilter(3)
    out = [f.push(v) for v in (0.30, 0.05, 0.05, 0.05)]
    assert out[-1] == pytest.approx(0.05)


def test_looking_down_needs_gaze_and_head():
    shapes = {"eyeLookDownLeft": 0.7, "eyeLookDownRight": 0.6}
    assert looking_down(shapes, pitch_down_deg=15)
    assert not looking_down(shapes, pitch_down_deg=2)  # eyes down but head level: not the keyboard pose
    assert not looking_down({"eyeLookDownLeft": 0.1}, pitch_down_deg=15)
    assert not looking_down({}, pitch_down_deg=15)


def test_default_config_makes_a_dropped_head_negative():
    m = np.eye(4)
    m[:3, :3] = rot(12, 0, 0)  # MediaPipe reports a positive pitch when the head drops
    pitch, _, _ = euler_from_matrix(m)
    assert pitch == pytest.approx(-12, abs=1e-6)
