"""The pipeline with an IdentityLock: only the verified agent is ever measured."""
import numpy as np
import pytest

from identity.lock import IdentityLock, LockConfig
from test_pipeline import FRAME, STEP, StubLandmarker, make_face
from vision.calibration import Calibration
from vision.drowsiness import DrowsinessEngine, EventType
from vision.pipeline import FramePipeline

A, B = np.array([1.0, 0.0]), np.array([0.0, 1.0])


class WhoEmbedder:
    """Reads the identity tag the test put on the face."""

    name, version = "who", "0"

    def embed(self, frame, face):
        return A if face.who == "agent" else B


def face(who, **kw):
    f = make_face(**kw)
    f.who = who
    return f


def run(script, ms, lock=None):
    lock = lock or IdentityLock(lambda v: float(v @ A), LockConfig())
    pipe = FramePipeline(StubLandmarker(script), DrowsinessEngine(Calibration()), lock=lock, embedder=WhoEmbedder())
    events, measures, t = [], [], 0
    while t < ms:
        m, ev = pipe.process(FRAME, t)
        events += ev
        measures.append(m)
        t += STEP
    return events, measures


def kinds(events):
    return [e.type for e in events]


def test_the_agent_is_measured_and_the_lock_says_verified():
    events, measures = run(lambda t: [face("agent")], 4000)
    assert measures[-1].lock_status == "VERIFIED"
    assert measures[-1].ear == pytest.approx(0.4, abs=0.01)
    assert events == []


def test_the_agents_own_microsleep_is_still_caught_under_the_lock():
    script = lambda t: [face("agent", lid_gap=2.0 if 1000 <= t < 1700 else 16.0)]
    events, _ = run(script, 4000)
    assert kinds(events).count(EventType.MICROSLEEP) == 1


def test_a_bystander_with_shut_eyes_is_never_measured():
    bystander = lambda: face("stranger", cx=500, cy=120, scale=0.4, lid_gap=2.0, seed=5)
    events, measures = run(lambda t: [bystander(), face("agent", scale=1.5)], 6000)
    assert EventType.MICROSLEEP not in kinds(events)
    assert measures[-1].ear == pytest.approx(0.4, abs=0.01)  # the agent's eyes, not the bystander's
    assert measures[-1].face_count == 2


def test_a_stranger_in_the_seat_is_not_measured_and_triggers_a_mismatch():
    def script(t):
        if t < 3000:
            return [face("agent")]
        if t < 4000:
            return []
        return [face("stranger", lid_gap=2.0)]  # shut eyes: would be a microsleep if measured

    events, measures = run(script, 12_000)
    assert EventType.MICROSLEEP not in kinds(events)
    assert kinds(events).count(EventType.IDENTITY_MISMATCH) == 1
    t_mismatch = next(e.ts_ms for e in events if e.type is EventType.IDENTITY_MISMATCH)
    assert 8800 <= t_mismatch <= 9400  # stranger arrives at 4000 + 5 s
    assert all(m.ear is None for m in measures if m.ts_ms >= 4000)
    assert measures[-1].lock_status == "IDENTITY_MISMATCH"


def test_time_away_is_not_counted_as_sleep_and_is_reported_as_absent():
    script = lambda t: [face("agent")] if t < 2000 or t >= 15_000 else []
    events, measures = run(script, 18_000)
    assert kinds(events).count(EventType.ABSENT) == 1
    assert EventType.MICROSLEEP not in kinds(events)
    assert measures[-1].lock_status == "VERIFIED" and measures[-1].ear is not None


def test_a_lock_without_an_embedder_is_rejected():
    with pytest.raises(ValueError):
        FramePipeline(StubLandmarker(lambda t: []), DrowsinessEngine(Calibration()), lock=IdentityLock(lambda v: 0.0))
