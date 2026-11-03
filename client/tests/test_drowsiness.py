"""State-machine tests on synthetic series (30 FPS unless stated).

Default calibration: ear_open 0.30, ear_closed 0.10 -> close below 0.20, reopen above 0.22.
"""
import pytest

from vision.calibration import Calibration
from vision.drowsiness import DrowsinessEngine, EventType, FatigueState, Sample, Thresholds

OPEN, CLOSED = 0.30, 0.10
STEP = 33  # ms per frame, ~30 FPS


class Feeder:
    """Pushes constant-valued stretches of frames into an engine and collects events."""

    def __init__(self, engine=None):
        self.engine = engine or DrowsinessEngine(Calibration())
        self.t = 0
        self.events = []

    def run(self, ms, **fields):
        fields.setdefault("ear", OPEN)
        end = self.t + ms
        while self.t < end:
            self.events += self.engine.update(Sample(self.t, **fields))
            self.t += STEP
        return self

    def count(self, kind):
        return sum(1 for e in self.events if e.type is kind)


def types(feeder):
    return [e.type for e in feeder.events]


# --- eyes ----------------------------------------------------------------------------
def test_open_eyes_make_no_events():
    f = Feeder().run(5000)
    assert f.events == []
    assert f.engine.state is FatigueState.ALERT


def test_600ms_closure_is_exactly_one_microsleep():
    f = Feeder().run(1000).run(600, ear=CLOSED).run(1000)
    assert f.count(EventType.MICROSLEEP) == 1
    assert f.count(EventType.BLINK) == 0 and f.count(EventType.LONG_BLINK) == 0
    ms = next(e for e in f.events if e.type is EventType.MICROSLEEP)
    assert ms.severity == "critical" and ms.duration_ms >= 500


def test_long_closure_still_fires_once():
    f = Feeder().run(1000).run(3000, ear=CLOSED).run(1000)
    assert f.count(EventType.MICROSLEEP) == 1


def test_microsleep_is_reported_while_eyes_are_still_shut():
    f = Feeder().run(1000).run(540, ear=CLOSED)
    assert f.count(EventType.MICROSLEEP) == 1  # no need to wait for the eyes to reopen
    assert f.engine.state is FatigueState.MICROSLEEP


def test_short_closure_is_a_blink_not_a_long_blink():
    f = Feeder().run(1000).run(150, ear=CLOSED).run(1000)
    assert f.count(EventType.BLINK) == 1
    assert f.count(EventType.LONG_BLINK) == 0 and f.count(EventType.MICROSLEEP) == 0


def test_400ms_closure_is_a_blink_and_a_long_blink():
    f = Feeder().run(1000).run(400, ear=CLOSED).run(1000)
    assert f.count(EventType.BLINK) == 1
    assert f.count(EventType.LONG_BLINK) == 1
    assert f.count(EventType.MICROSLEEP) == 0


def test_single_noisy_frame_is_ignored():
    f = Feeder().run(1000)
    f.events += f.engine.update(Sample(f.t, ear=CLOSED))
    f.t += 30
    f.run(1000)
    assert f.events == []


def test_hysteresis_stops_flicker_around_the_threshold():
    f = Feeder().run(1000).run(100, ear=0.15)  # closed
    for _ in range(2):  # hover between close (0.20) and reopen (0.22) thresholds
        f.run(2 * STEP, ear=0.21)  # without hysteresis this would end the closure (a 66 ms "blink")
        f.run(2 * STEP, ear=0.19)
    f.run(1000)
    assert f.count(EventType.BLINK) == 1  # one closure, not three
    assert f.count(EventType.MICROSLEEP) == 0


def test_recovered_state_after_microsleep_hold():
    f = Feeder().run(1000).run(700, ear=CLOSED).run(1000)
    assert f.engine.state is FatigueState.MICROSLEEP
    f.run(6000)
    assert f.engine.state is FatigueState.ALERT


def test_two_microsleeps_in_ten_minutes_is_a_repeat():
    f = Feeder().run(1000).run(700, ear=CLOSED).run(60_000)
    assert not f.engine.repeated_microsleeps
    f.run(700, ear=CLOSED).run(1000)
    assert f.engine.repeated_microsleeps
    f.run(11 * 60_000)  # both age out of the 10 min window
    assert not f.engine.repeated_microsleeps


# --- false-alarm guards --------------------------------------------------------------
@pytest.mark.parametrize(
    "guard",
    [{"looking_down": True}, {"recent_input": True}, {"yaw": 45.0}],
    ids=["keyboard-glance", "recent-input", "head-turned-away"],
)
def test_guards_suppress_microsleep(guard):
    f = Feeder().run(1000).run(2000, ear=CLOSED, **guard).run(1000)
    assert f.count(EventType.MICROSLEEP) == 0


def test_closure_starting_under_a_guard_does_not_inherit_its_duration():
    f = Feeder().run(1000).run(2000, ear=CLOSED, recent_input=True)
    f.run(300, ear=CLOSED)  # guard released, eyes still shut: the clock starts here
    assert f.count(EventType.MICROSLEEP) == 0
    f.run(400, ear=CLOSED)
    assert f.count(EventType.MICROSLEEP) == 1


def test_losing_the_face_resets_the_closure():
    f = Feeder().run(1000).run(300, ear=CLOSED)
    f.run(1000, ear=None)  # face gone: not counted as sleep
    f.run(300, ear=CLOSED).run(1000)
    assert f.count(EventType.MICROSLEEP) == 0


# --- blink rate and PERCLOS ----------------------------------------------------------
def blink_train(f, count, every_ms, closed_ms):
    for _ in range(count):
        f.run(closed_ms, ear=CLOSED)
        f.run(every_ms - closed_ms)


def test_blink_rate_per_minute():
    f = Feeder().run(1000)
    blink_train(f, 20, every_ms=3000, closed_ms=150)
    assert f.engine.metrics()["blink_rate"] == pytest.approx(20, abs=3)


def test_blink_rate_needs_ten_seconds_of_data():
    f = Feeder().run(5000)
    assert f.engine.metrics()["blink_rate"] is None


def test_perclos_above_15_percent_means_drowsy():
    f = Feeder().run(1000)
    blink_train(f, 60, every_ms=1200, closed_ms=250)  # ~21 % closed, each blink under 300 ms
    assert f.engine.metrics()["perclos"] > 0.15
    assert f.engine.state is FatigueState.DROWSY
    assert f.count(EventType.DROWSY) == 1
    assert f.count(EventType.LONG_BLINK) == 0  # PERCLOS alone caused it


def test_normal_blinking_stays_alert():
    f = Feeder().run(1000)
    blink_train(f, 30, every_ms=3000, closed_ms=150)  # ~5 % closed
    assert f.engine.metrics()["perclos"] < 0.15
    assert f.engine.state is FatigueState.ALERT


def test_three_long_blinks_make_the_agent_drowsy():
    f = Feeder().run(1000)
    blink_train(f, 3, every_ms=4000, closed_ms=400)
    assert f.engine.state is FatigueState.DROWSY


# --- yawn ----------------------------------------------------------------------------
WIDE = 0.8


def test_yawn_needs_two_seconds():
    f = Feeder().run(1000, mar=0.03).run(2500, mar=WIDE).run(1000, mar=0.03)
    assert f.count(EventType.YAWN) == 1


def test_speech_length_mouth_opening_is_not_a_yawn():
    f = Feeder().run(1000, mar=0.03).run(1000, mar=WIDE).run(1000, mar=0.03)
    assert f.count(EventType.YAWN) == 0


def test_two_separate_yawns_are_two_events():
    f = Feeder()
    for _ in range(2):
        f.run(1000, mar=0.03).run(2300, mar=WIDE)
    assert f.count(EventType.YAWN) == 2


def test_jaw_open_blendshape_must_agree():
    f = Feeder().run(1000, mar=0.03).run(3000, mar=WIDE, jaw_open=0.05)
    assert f.count(EventType.YAWN) == 0
    f.run(3000, mar=WIDE, jaw_open=0.7)
    assert f.count(EventType.YAWN) == 1


def test_two_yawns_in_five_minutes_make_the_agent_drowsy():
    f = Feeder()
    f.run(1000, mar=0.03).run(2300, mar=WIDE).run(30_000, mar=0.03).run(2300, mar=WIDE).run(500, mar=0.03)
    assert f.engine.state is FatigueState.DROWSY
    f.run(6 * 60_000, mar=0.03)  # window passes with nothing new
    assert f.engine.state is FatigueState.ALERT


# --- head ----------------------------------------------------------------------------
def test_sustained_tilt():
    f = Feeder().run(1000, roll=0.0).run(2500, roll=20.0).run(1000, roll=0.0)
    assert f.count(EventType.HEAD_TILT) == 1


def test_brief_or_small_tilts_are_ignored():
    f = Feeder().run(1000, roll=0.0).run(1000, roll=20.0).run(1000, roll=0.0).run(4000, roll=10.0)
    assert f.count(EventType.HEAD_TILT) == 0


def test_tilt_is_measured_from_the_calibrated_neutral():
    f = Feeder(DrowsinessEngine(Calibration(neutral_roll=18.0)))
    f.run(4000, roll=20.0)  # only 2 degrees off this agent's normal
    assert f.count(EventType.HEAD_TILT) == 0


def test_nod_is_a_drop_and_snap_back():
    f = Feeder().run(1000, pitch=0.0).run(400, pitch=-22.0).run(1000, pitch=0.0)
    assert f.count(EventType.HEAD_NOD) == 1


def test_staying_down_is_not_a_nod():
    f = Feeder().run(1000, pitch=0.0).run(4000, pitch=-22.0).run(1000, pitch=0.0)
    assert f.count(EventType.HEAD_NOD) == 0


def test_small_pitch_change_is_not_a_nod():
    f = Feeder().run(1000, pitch=0.0).run(400, pitch=-8.0).run(1000, pitch=0.0)
    assert f.count(EventType.HEAD_NOD) == 0


def test_two_nods_make_the_agent_drowsy():
    f = Feeder().run(1000, pitch=0.0)
    for _ in range(2):
        f.run(400, pitch=-22.0).run(2000, pitch=0.0)
    assert f.count(EventType.HEAD_NOD) == 2
    assert f.engine.state is FatigueState.DROWSY


# --- frame-rate independence ---------------------------------------------------------
@pytest.mark.parametrize("step", [16, 33, 66])
def test_microsleep_timing_does_not_depend_on_frame_rate(step):
    engine = DrowsinessEngine(Calibration(), Thresholds())
    events, t = [], 0
    while t < 1000:
        events += engine.update(Sample(t, ear=OPEN)); t += step
    while t < 1600:
        events += engine.update(Sample(t, ear=CLOSED)); t += step
    while t < 2600:
        events += engine.update(Sample(t, ear=OPEN)); t += step
    assert sum(e.type is EventType.MICROSLEEP for e in events) == 1
