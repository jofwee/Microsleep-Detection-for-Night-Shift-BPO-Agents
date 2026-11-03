import pytest

from evalkit.fatigue import (
    RecordingEngine, calibration_from_samples, detections_from_samples, pool, pool_controls, score_session, sweep,
)
from evalkit.protocol import Cue
from vision.calibration import Calibration
from vision.drowsiness import Sample

FPS = 30


def timeline(seconds: float, rules) -> list[Sample]:
    """A synthetic person: eyes open (EAR 0.31), mouth closed, looking at the screen, except where a rule says otherwise.
    rules: list of (start_s, end_s, dict of Sample fields)"""
    out = []
    for i in range(int(seconds * FPS)):
        t = i / FPS
        f = dict(ear=0.31, mar=0.03, pitch=0.0, yaw=0.0, roll=0.0, jaw_open=0.05)
        for a, b, over in rules:
            if a <= t < b:
                f.update(over)
        out.append(Sample(ts_ms=int(t * 1000), **f))
    return out


CALIBRATION_RULES = [(13.0, 16.0, dict(ear=0.09)), (16.0, 19.0, dict(mar=0.9, jaw_open=0.7))]
CUES = [
    Cue("MICROSLEEP", 30.0, 3.0, "x"), Cue("YAWN", 40.0, 5.0, "x"), Cue("LONG_BLINK", 50.0, 3.0, "x"),
    Cue("TALK", 60.0, 7.0, "x"), Cue("LOOK_DOWN", 70.0, 7.0, "x"),
]
EVENT_RULES = [
    (30.6, 31.8, dict(ear=0.08)),  # eyes closed for 1.2 s
    (40.6, 43.8, dict(mar=0.9, jaw_open=0.7)),  # a 3.2 s yawn
    (50.6, 51.0, dict(ear=0.08)),  # a slow blink
    (61.0, 66.0, dict(mar=0.25, jaw_open=0.2)),  # talking: the mouth moves but never opens wide for long
    (71.0, 76.0, dict(ear=0.07, pitch=-25.0, looking_down=True)),  # looking down: the eyes look closed from above
]
SAMPLES = timeline(85, CALIBRATION_RULES + EVENT_RULES)


def test_the_recording_engine_keeps_every_sample_and_still_detects():
    engine = RecordingEngine(Calibration())
    for s in SAMPLES[:100]:
        engine.update(s)
    assert len(engine.samples) == 100 and engine.samples[0] is SAMPLES[0]


def test_calibration_is_built_from_the_scripted_phases():
    cal = calibration_from_samples(SAMPLES)
    assert cal.ear_open == pytest.approx(0.31, abs=0.01) and cal.ear_closed == pytest.approx(0.09, abs=0.01)
    assert cal.mar_open is not None and cal.mar_open > 0.5


def test_events_in_the_schedule_are_found_and_the_controls_stay_quiet():
    cal = calibration_from_samples(SAMPLES)
    r = score_session("synthetic", CUES, SAMPLES, cal)
    assert r.scores["MICROSLEEP"].tp == 1 and r.scores["MICROSLEEP"].fp == 0
    assert r.scores["YAWN"].tp == 1 and r.scores["YAWN"].fp == 0
    assert r.scores["LONG_BLINK"].tp == 1
    assert r.controls == {"TALK": (0, 1), "LOOK_DOWN": (0, 1)}  # no yawn while talking, no microsleep while reading
    assert 0.0 <= r.scores["MICROSLEEP"].delays_s[0] <= 2.0  # reported within about a second of starting


def test_detections_only_contain_the_events_of_the_schedule():
    cal = calibration_from_samples(SAMPLES)
    kinds = {d.kind for d in detections_from_samples(SAMPLES, cal)}
    assert kinds <= {"MICROSLEEP", "YAWN", "HEAD_NOD", "HEAD_TILT", "LONG_BLINK"}  # no BLINK, DROWSY or identity events


def test_a_silent_recording_scores_misses_not_a_crash():
    cal = Calibration()
    flat = timeline(85, [])
    r = score_session("flat", CUES, flat, cal)
    assert r.total.tp == 0 and r.total.fn == 3 and r.total.fp == 0
    assert r.scores["HEAD_NOD"].recall is None  # nothing was asked for, so there is no recall to report


def test_pooling_adds_sessions_together():
    cal = calibration_from_samples(SAMPLES)
    a = score_session("a", CUES, SAMPLES, cal)
    b = score_session("b", CUES, SAMPLES, cal)
    pooled = pool([a, b])
    assert pooled["MICROSLEEP"].tp == 2 and pooled["YAWN"].tp == 2
    assert pool_controls([a, b]) == {"TALK": (0, 2), "LOOK_DOWN": (0, 2)}


def test_a_threshold_sweep_shows_the_trade_off():
    cal = calibration_from_samples(SAMPLES)
    rows = sweep([("s", CUES, SAMPLES, cal)], "microsleep_ms", [300, 500, 3000])
    ms = {r["value"]: r for r in rows if r["kind"] == "MICROSLEEP"}
    assert ms[300]["tp"] == 1 and ms[500]["tp"] == 1
    assert ms[3000]["tp"] == 0 and ms[3000]["fn"] == 1  # asked to close the eyes for 3 s before it counts: the 1.2 s closure is missed


def test_the_calibration_phases_themselves_are_not_scored_as_detections():
    cal = calibration_from_samples(SAMPLES)
    everything = detections_from_samples(SAMPLES, cal, after_s=0.0)
    scored = detections_from_samples(SAMPLES, cal)
    assert any(d.at_s < 22.0 for d in everything)  # the 3 s closed-eyes phase is a microsleep to the engine...
    assert all(d.at_s >= 22.0 for d in scored)  # ...but it is not a test cue, so it is left out
