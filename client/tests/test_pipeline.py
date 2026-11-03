"""FramePipeline wiring, tested with a stand-in landmarker and synthetic faces (no camera)."""
import numpy as np
import pytest

from test_metrics import set_eye
from vision.calibration import Calibration
from vision.drowsiness import DrowsinessEngine, EventType
from vision.landmarks import FaceResult
from vision.metrics import LEFT_EYE, MOUTH_BOTTOM, MOUTH_LEFT, MOUTH_RIGHT, MOUTH_TOP, RIGHT_EYE
from vision.pipeline import FramePipeline

W, H = 640, 480
FRAME = np.zeros((H, W, 3), np.uint8)
STEP = 33


def make_face(cx=320.0, cy=240.0, scale=1.0, lid_gap=16.0, mouth_gap=1.0, seed=0) -> FaceResult:
    """A face whose EAR is lid_gap/40 (0.4 open, 0.05 shut) and whose size grows with scale."""
    rng = np.random.default_rng(seed)
    px = np.array([cx, cy]) + rng.normal(0, 40 * scale, (478, 2))
    set_eye(px, RIGHT_EYE, lid_gap * scale, width=40 * scale, x0=cx - 90 * scale)
    set_eye(px, LEFT_EYE, lid_gap * scale, width=40 * scale, x0=cx + 50 * scale)
    px[MOUTH_LEFT], px[MOUTH_RIGHT] = (cx - 20 * scale, cy + 80 * scale), (cx + 20 * scale, cy + 80 * scale)
    px[MOUTH_TOP] = (cx, cy + 80 * scale - mouth_gap * scale / 2)
    px[MOUTH_BOTTOM] = (cx, cy + 80 * scale + mouth_gap * scale / 2)
    lm = np.zeros((478, 3))
    lm[:, :2] = px / np.array([W, H])
    return FaceResult(lm, {}, np.eye(4))


class StubLandmarker:
    def __init__(self, script):
        self.script = script  # callable(ts_ms) -> list[FaceResult]

    def detect(self, frame, ts_ms):
        return self.script(ts_ms)


def run(script, ms, engine=None):
    pipe = FramePipeline(StubLandmarker(script), engine or DrowsinessEngine(Calibration()))
    events, measures, t = [], [], 0
    while t < ms:
        m, ev = pipe.process(FRAME, t)
        events += ev
        measures.append(m)
        t += STEP
    return events, measures


def test_open_eyes_produce_no_events_and_sane_numbers():
    events, measures = run(lambda t: [make_face()], 3000)
    assert events == []
    m = measures[-1]
    assert m.face_count == 1
    assert m.ear == pytest.approx(0.4, abs=0.01)
    assert m.mar == pytest.approx(1 / 40, abs=0.01)
    assert (m.pitch, m.yaw, m.roll) == pytest.approx((0, 0, 0), abs=1e-6)


def test_closed_eyes_for_700ms_give_one_microsleep():
    script = lambda t: [make_face(lid_gap=2.0 if 1000 <= t < 1700 else 16.0)]
    events, _ = run(script, 3000)
    assert [e.type for e in events if e.type is EventType.MICROSLEEP] == [EventType.MICROSLEEP]


def test_no_face_means_no_events_and_resets_the_closure():
    def script(t):
        if 1300 <= t < 2300:
            return []  # agent leaves the frame
        return [make_face(lid_gap=2.0 if 1000 <= t < 1300 or 2300 <= t < 2600 else 16.0)]

    events, measures = run(script, 4000)
    assert not [e for e in events if e.type is EventType.MICROSLEEP]
    assert any(m.face_count == 0 and m.ear is None for m in measures)


def test_main_face_is_the_largest_one():
    big_open = make_face(scale=1.5, lid_gap=16.0, seed=1)
    small_shut = make_face(cx=500, cy=120, scale=0.4, lid_gap=2.0, seed=2)  # a bystander with shut eyes
    events, measures = run(lambda t: [small_shut, big_open], 3000)
    assert measures[-1].face_count == 2
    assert measures[-1].ear == pytest.approx(0.4, abs=0.01)
    assert events == []  # the bystander is never measured as the agent
