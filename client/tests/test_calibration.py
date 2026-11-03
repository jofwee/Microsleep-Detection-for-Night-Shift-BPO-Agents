import numpy as np
import pytest

from vision.calibration import Calibration, CalibrationError, FrameMeasure, compute_calibration


def measures(n, ear, mar=0.03, pitch=0.0, yaw=0.0, roll=0.0, jitter=0.0, seed=0):
    rng = np.random.default_rng(seed)
    return [
        FrameMeasure(ear + rng.normal(0, jitter), mar, pitch + rng.normal(0, jitter * 10), yaw, roll)
        for _ in range(n)
    ]


def test_thresholds_are_ordered():
    cal = Calibration(ear_open=0.30, ear_closed=0.10)
    assert cal.ear_perclos_threshold == pytest.approx(0.14)
    assert cal.ear_close_threshold == pytest.approx(0.20)
    assert cal.ear_reopen_threshold == pytest.approx(0.22)
    assert cal.ear_perclos_threshold < cal.ear_close_threshold < cal.ear_reopen_threshold


def test_compute_calibration_uses_open_median_and_closed_median():
    open_ = measures(300, 0.31, mar=0.04, pitch=-2.0, yaw=1.0, roll=0.5, jitter=0.005)
    # a few blink frames must not drag ear_open down
    blinks = [FrameMeasure(0.08, 0.04, -2.0, 1.0, 0.5) for _ in range(20)]
    closed = measures(90, 0.07, jitter=0.003, seed=1)
    cal = compute_calibration(open_ + blinks, closed)
    assert cal.ear_open == pytest.approx(0.31, abs=0.01)
    assert cal.ear_closed == pytest.approx(0.07, abs=0.01)
    assert cal.mar_closed == pytest.approx(0.04, abs=0.01)
    assert cal.neutral_pitch == pytest.approx(-2.0, abs=0.5)
    assert cal.neutral_yaw == pytest.approx(1.0, abs=0.1)
    assert cal.neutral_roll == pytest.approx(0.5, abs=0.1)
    assert cal.mar_open is None


def test_optional_open_mouth_raises_yawn_threshold_from_default():
    open_, closed = measures(60, 0.30), measures(20, 0.08)
    mouth = measures(30, 0.30, mar=0.9)
    with_mouth = compute_calibration(open_, closed, mouth)
    assert with_mouth.mar_open == pytest.approx(0.9)
    assert with_mouth.yawn_threshold == pytest.approx(0.03 + 0.6 * (0.9 - 0.03))
    assert compute_calibration(open_, closed).yawn_threshold == pytest.approx(0.5)


def test_too_few_samples():
    with pytest.raises(CalibrationError):
        compute_calibration(measures(5, 0.3), measures(20, 0.08))
    with pytest.raises(CalibrationError):
        compute_calibration(measures(60, 0.3), measures(2, 0.08))


def test_eyes_that_never_open_are_rejected():
    with pytest.raises(CalibrationError):
        compute_calibration(measures(60, 0.12), measures(20, 0.10))


def test_save_and_load_roundtrip(tmp_path):
    cal = Calibration(ear_open=0.28, ear_closed=0.09, mar_open=0.8, neutral_pitch=-3.0)
    path = tmp_path / "cal.json"
    cal.save(path)
    assert Calibration.load(path) == cal


def test_load_ignores_unknown_keys():
    assert Calibration.from_dict({"ear_open": 0.25, "future_field": 1}).ear_open == 0.25
