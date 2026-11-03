"""Enrollment capture with a fake embedder and synthetic faces (no model, no camera)."""
import numpy as np
import pytest

from identity.enroll import (
    EnrollmentError,
    EnrollmentSession,
    Pose,
    QualityConfig,
    assess_quality,
    pose_matches,
    select_diverse,
)
from test_metrics import rot
from vision.calibration import Calibration
from vision.landmarks import FaceResult

W, H = 640, 480
RNG = np.random.default_rng(0)
FRAME = RNG.integers(0, 255, (H, W, 3), dtype=np.uint8)  # textured, mid-brightness: passes quality


def make_face(turn=0.0, pitch=0.0, width=200.0, cx=320.0, cy=240.0) -> FaceResult:
    """Landmarks whose head_turn() is `turn` and whose reported pitch is `pitch` degrees."""
    px = np.tile([cx, cy], (478, 1)).astype(float)
    px[33], px[263] = (cx - 50, cy - 20), (cx + 50, cy - 20)  # outer eye corners
    px[133], px[362] = (cx - 18, cy - 20), (cx + 18, cy - 20)  # inner eye corners
    px[1] = (cx + turn * 100, cy + 20)  # nose tip
    px[61], px[291] = (cx - 30, cy + 70), (cx + 30, cy + 70)  # mouth corners
    px[10], px[152] = (cx, cy - 90), (cx, cy + 95)  # forehead, chin
    px[234], px[454] = (cx - width / 2, cy), (cx + width / 2, cy)  # face edges
    lm = np.zeros((478, 3))
    lm[:, :2] = px / np.array([W, H])
    m = np.eye(4)
    m[:3, :3] = rot(-pitch, 0, 0)  # the default HeadPoseConfig flips pitch's sign
    return FaceResult(lm, {}, m)


class FakeEmbedder:
    name, version = "fake", "v0"

    def __init__(self, person=0, seed=0):
        base = np.random.default_rng(100 + person).normal(size=64)
        self.base = base / np.linalg.norm(base)
        self.rng = np.random.default_rng(seed)

    def embed(self, frame, face):
        v = self.base + 0.15 * self.rng.normal(size=64) / np.sqrt(64)
        return v / np.linalg.norm(v)


POSE_INPUT = {
    Pose.FRONT: dict(turn=0.0, pitch=0.0),
    Pose.LEFT: dict(turn=0.22, pitch=0.0),
    Pose.RIGHT: dict(turn=-0.22, pitch=0.0),
    Pose.UP: dict(turn=0.0, pitch=12.0),
    Pose.DOWN: dict(turn=0.0, pitch=-12.0),
}


def run_all(session, embedder_for=None, extra_faces=0):
    t = 0
    for _ in range(400):
        if session.done:
            break
        pose = session.stages[session._stage]
        faces = [make_face(**POSE_INPUT[pose])] + [make_face(cx=100) for _ in range(extra_faces)]
        session.feed(t, FRAME, faces)
        t += 300
    return t


# --- pose and quality rules ----------------------------------------------------------
@pytest.mark.parametrize(
    "pose, turn, pitch, ok",
    [
        (Pose.FRONT, 0.0, 0, True), (Pose.FRONT, 0.2, 0, False), (Pose.FRONT, 0.0, 15, False),
        (Pose.LEFT, 0.2, 0, True), (Pose.LEFT, 0.05, 0, False), (Pose.LEFT, -0.2, 0, False),
        (Pose.RIGHT, -0.2, 0, True), (Pose.RIGHT, 0.2, 0, False),
        (Pose.UP, 0, 10, True), (Pose.UP, 0, -10, False),
        (Pose.DOWN, 0, -10, True), (Pose.DOWN, 0, 10, False),
    ],
)
def test_pose_rules(pose, turn, pitch, ok):
    assert pose_matches(pose, turn, pitch) is ok


def aligned(brightness=127, noise=60):
    rng = np.random.default_rng(1)
    img = brightness + rng.normal(0, noise, (112, 112, 3))
    return np.clip(img, 0, 255).astype(np.uint8)


def test_quality_accepts_a_good_frame():
    r = assess_quality(make_face(), aligned())
    assert r.ok and r.reasons == []


@pytest.mark.parametrize(
    "cfg, crop, hint",
    [
        (QualityConfig(min_face_width=0.5), aligned(), "closer"),  # the test face is ~0.31 of the frame
        (QualityConfig(max_face_width=0.2), aligned(), "back"),
        (QualityConfig(), aligned(noise=0), "blurry"),
        (QualityConfig(), aligned(brightness=20, noise=15), "dark"),
        (QualityConfig(), aligned(brightness=245, noise=8), "bright"),
        (QualityConfig(), np.full((112, 112, 3), 15, np.uint8), "dark"),  # dark AND blurry: report the cause
    ],
)
def test_quality_gives_a_reason(cfg, crop, hint):
    r = assess_quality(make_face(), crop, cfg)
    assert not r.ok and hint in r.reasons[0].lower()


# --- the session ---------------------------------------------------------------------
def test_full_capture_walks_through_the_five_poses_and_collects_20_frames():
    s = EnrollmentSession(FakeEmbedder())
    seen = []
    t = 0
    while not s.done and t < 200_000:
        pose = s.stages[s._stage]
        status = s.feed(t, FRAME, [make_face(**POSE_INPUT[pose])])
        if not seen or seen[-1] is not status.pose:
            seen.append(status.pose)
        t += 300
    assert s.done
    assert [p for p in seen if p] == [Pose.FRONT, Pose.LEFT, Pose.RIGHT, Pose.UP, Pose.DOWN]
    assert len(s.embeddings) == 20


def test_frames_closer_together_than_the_gap_are_not_all_taken():
    s = EnrollmentSession(FakeEmbedder())
    for t in range(0, 600, 33):  # 18 frames in 0.6 s
        s.feed(t, FRAME, [make_face()])
    assert len(s.embeddings) == 3  # one at t=0, 300, 600-ish (gap 250 ms)


def test_hints_guide_the_person():
    s = EnrollmentSession(FakeEmbedder())
    assert s.feed(0, FRAME, []).message == "I cannot see your face"
    assert "straight" in s.feed(300, FRAME, [make_face(turn=0.3)]).message  # wrong pose: asked for FRONT
    assert "Only you" in s.feed(600, FRAME, [make_face(), make_face(cx=100)]).message
    dark = np.full((H, W, 3), 15, np.uint8)
    assert "dark" in s.feed(900, dark, [make_face()]).message.lower()


def test_second_person_in_view_blocks_capture():
    s = EnrollmentSession(FakeEmbedder())
    run_all(s, extra_faces=1)
    assert len(s.embeddings) == 0


def test_two_rounds_for_with_and_without_glasses():
    s = EnrollmentSession(FakeEmbedder(), rounds=2)
    run_all(s)
    assert s.done and len(s.embeddings) == 40


# --- finalising ----------------------------------------------------------------------
def test_finalize_keeps_five_diverse_embeddings_and_a_unit_mean():
    s = EnrollmentSession(FakeEmbedder())
    run_all(s)
    cal = Calibration(ear_open=0.3)
    p = s.finalize(cal)
    assert p.embeddings.shape == (5, 64)
    assert np.linalg.norm(p.mean) == pytest.approx(1.0)
    assert p.model_name == "fake" and p.calibration is cal and p.enrolled_at
    assert p.score(FakeEmbedder(person=0, seed=9).embed(None, None)) > 0.8
    assert p.score(FakeEmbedder(person=1, seed=9).embed(None, None)) < 0.5


def test_finalize_rejects_a_capture_that_mixes_two_people():
    s = EnrollmentSession(FakeEmbedder(person=0))
    run_all(s)
    other = FakeEmbedder(person=1)
    s.embeddings[3:12] = [other.embed(None, None) for _ in range(9)]  # someone stepped in mid-capture
    with pytest.raises(EnrollmentError, match="one person"):
        s.finalize()


def test_even_one_foreign_frame_is_caught():
    s = EnrollmentSession(FakeEmbedder(person=0))
    run_all(s)
    s.embeddings[7] = FakeEmbedder(person=1).embed(None, None)
    with pytest.raises(EnrollmentError, match="one person"):
        s.finalize()


def test_finalize_needs_enough_frames():
    s = EnrollmentSession(FakeEmbedder())
    for t in range(0, 900, 300):
        s.feed(t, FRAME, [make_face()])
    with pytest.raises(EnrollmentError, match="at least 5"):
        s.finalize()


def test_select_diverse_picks_from_every_cluster():
    rng = np.random.default_rng(0)
    centres = np.eye(4, 16)
    embs = np.vstack([c + 0.02 * rng.normal(size=(6, 16)) for c in centres])
    embs /= np.linalg.norm(embs, axis=1, keepdims=True)
    mean = embs.mean(axis=0)
    mean /= np.linalg.norm(mean)
    picked = select_diverse(embs, mean, 4)
    assert sorted(i // 6 for i in picked) == [0, 1, 2, 3]  # one from each cluster
