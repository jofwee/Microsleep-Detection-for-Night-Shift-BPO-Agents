"""Alignment maths, profile storage, matching, head_turn and the camera guard."""
import numpy as np
import pytest

from capture import camera_guard
from capture.camera_guard import CameraInfo, find_physical, is_virtual_name
from identity.embedder import ALIGNED_SIZE, ARCFACE_TEMPLATE, align_face, five_points, similarity_transform
from identity.profile import EnrolledProfile
from identity.verify import DEFAULT_TAU, verify_embeddings
from test_enroll import make_face
from vision.calibration import Calibration
from vision.landmarks import FaceResult
from vision.metrics import head_turn


# --- head_turn -----------------------------------------------------------------------
def landmarks_with_nose(dx, roll_deg=0.0):
    lm = np.zeros((478, 2))
    lm[33], lm[263] = (-50, 0), (50, 0)
    lm[1] = (dx, 40)
    if roll_deg:
        a = np.radians(roll_deg)
        r = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
        lm = lm @ r.T
    return lm


def test_head_turn_is_zero_facing_front_and_signed_by_direction():
    assert head_turn(landmarks_with_nose(0)) == pytest.approx(0.0)
    assert head_turn(landmarks_with_nose(+20)) == pytest.approx(0.20)  # nose toward image-right = their left
    assert head_turn(landmarks_with_nose(-20)) == pytest.approx(-0.20)


def test_head_turn_ignores_roll():
    assert head_turn(landmarks_with_nose(20, roll_deg=25)) == pytest.approx(0.20, abs=1e-9)


# --- alignment -----------------------------------------------------------------------
def test_similarity_transform_recovers_a_known_transform():
    a = np.radians(20)
    r = 1.7 * np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    src = ARCFACE_TEMPLATE @ r.T + np.array([30.0, -12.0])
    m = similarity_transform(src, ARCFACE_TEMPLATE)
    mapped = src @ m[:, :2].T + m[:, 2]
    assert np.abs(mapped - ARCFACE_TEMPLATE).max() < 1e-9


def test_align_face_puts_the_anchor_points_where_arcface_expects_them():
    face = make_face(turn=0.0)
    h, w = 480, 640
    frame = np.zeros((h, w, 3), np.uint8)
    for x, y in five_points(face.pixels(w, h)):  # white dot on each anchor point
        frame[int(round(y)) - 2: int(round(y)) + 3, int(round(x)) - 2: int(round(x)) + 3] = 255
    out = align_face(frame, face)
    assert out.shape == (ALIGNED_SIZE, ALIGNED_SIZE, 3)
    for x, y in ARCFACE_TEMPLATE:
        patch = out[int(round(y)) - 3: int(round(y)) + 4, int(round(x)) - 3: int(round(x)) + 4]
        assert patch.max() > 150, f"no anchor near template point {(x, y)}"


# --- profile and verification --------------------------------------------------------
def unit(v):
    v = np.asarray(v, dtype=float)
    return v / np.linalg.norm(v)


def make_profile(rng, calibration=None):
    base = unit(rng.normal(size=32))
    embs = np.stack([unit(base + 0.2 * unit(rng.normal(size=32))) for _ in range(5)])
    return EnrolledProfile(embs, unit(embs.mean(axis=0)), "fake", "v1", calibration, "2026-11-03T12:00:00"), base


def test_profile_roundtrip(tmp_path):
    rng = np.random.default_rng(0)
    p, _ = make_profile(rng, Calibration(ear_open=0.28, neutral_pitch=-2.0))
    path = tmp_path / "profile.npz"
    p.save(path)
    q = EnrolledProfile.load(path)
    assert np.allclose(q.embeddings, p.embeddings) and np.allclose(q.mean, p.mean)
    assert (q.model_name, q.model_version, q.enrolled_at) == ("fake", "v1", "2026-11-03T12:00:00")
    assert q.calibration == p.calibration


def test_profile_file_holds_numbers_only_no_pickles(tmp_path):
    p, _ = make_profile(np.random.default_rng(0))
    path = tmp_path / "p.npz"
    p.save(path)
    with np.load(path, allow_pickle=False) as d:  # would raise if anything needed pickle
        assert set(d.files) == {"embeddings", "mean", "meta"}


def test_score_is_high_for_the_agent_and_low_for_a_stranger():
    rng = np.random.default_rng(1)
    p, base = make_profile(rng)
    assert p.score(unit(base + 0.1 * unit(rng.normal(size=32)))) > 0.9
    assert p.score(unit(rng.normal(size=32))) < 0.5


def test_verify_uses_the_median_of_the_best_three():
    rng = np.random.default_rng(2)
    p, base = make_profile(rng)
    good = [unit(base + 0.1 * unit(rng.normal(size=32))) for _ in range(6)]
    stranger = [unit(rng.normal(size=32)) for _ in range(6)]
    assert verify_embeddings(good, p).passed
    assert not verify_embeddings(stranger, p).passed
    # one lucky frame among strangers does not pass; one bad frame among good ones does not fail
    assert not verify_embeddings(stranger[:5] + good[:1], p).passed
    assert verify_embeddings(good[:5] + stranger[:1], p).passed


def test_verify_needs_a_few_frames():
    p, base = make_profile(np.random.default_rng(3))
    r = verify_embeddings([base, base], p)
    assert not r.passed and "at least 3" in r.reason
    assert DEFAULT_TAU == 0.45


# --- camera guard --------------------------------------------------------------------
@pytest.mark.parametrize(
    "name",
    ["OBS Virtual Camera", "OBS-Camera", "DroidCam Video", "ManyCam Virtual Webcam", "Snap Camera",
     "NVIDIA Broadcast", "Iriun Webcam", "XSplit VCam", "DeepFaceLive Virtual Camera", "EpocCam Camera Source"],
)
def test_virtual_cameras_are_recognised(name):
    assert is_virtual_name(name)


@pytest.mark.parametrize(
    "name",
    ["USB2.0 VGA UVC WebCam", "Integrated Camera", "Logitech C920 HD Pro Webcam", "HD Webcam",
     "FaceTime HD Camera", "Brio 4K Stream Edition", "Intel(R) RealSense(TM) Depth Camera 435", "Lindi Cam"],
)
def test_real_cameras_are_not_flagged(name):
    assert not is_virtual_name(name)


def test_find_physical_prefers_a_usb_webcam_and_never_returns_a_virtual_one(monkeypatch):
    cams = [
        CameraInfo(0, "OBS Virtual Camera", 1400, None, None, True),
        CameraInfo(1, "Integrated Camera", 1400, None, None, False),
        CameraInfo(2, "USB2.0 VGA UVC WebCam", 1400, 5075, 23057, False),
    ]
    monkeypatch.setattr(camera_guard, "list_cameras", lambda backend=None: cams)
    assert find_physical().index == 2
    monkeypatch.setattr(camera_guard, "list_cameras", lambda backend=None: cams[:2])
    assert find_physical().index == 1
    monkeypatch.setattr(camera_guard, "list_cameras", lambda backend=None: cams[:1])
    assert find_physical() is None
