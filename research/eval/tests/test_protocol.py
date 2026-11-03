from evalkit.metrics import Detection
from evalkit.protocol import (
    CALIBRATION_END_S, CONTROLS, POSITIVE, REACTION_S, build_schedule, calibration_windows, false_alarms_in_controls, frames_to_write,
    phase_at, total_duration, truth_from_schedule,
)


def test_the_schedule_is_repeatable_and_has_the_right_cues():
    a, b = build_schedule(seed=3, per_kind=4, controls=3), build_schedule(seed=3, per_kind=4, controls=3)
    assert a == b and a != build_schedule(seed=4, per_kind=4, controls=3)
    kinds = [c.kind for c in a]
    assert all(kinds.count(k) == 4 for k in POSITIVE) and all(kinds.count(k) == 3 for k in CONTROLS)


def test_cues_never_overlap_and_start_after_the_calibration_phases():
    cues = build_schedule(seed=1)
    assert cues[0].start_s > CALIBRATION_END_S
    for a, b in zip(cues, cues[1:]):
        assert b.start_s >= a.end_s + 3.9  # a real gap between cues, so detections can be told apart
    assert total_duration(cues) > cues[-1].end_s


def test_the_same_kind_rarely_comes_twice_in_a_row():
    cues = build_schedule(seed=2, per_kind=6, controls=4)
    repeats = sum(1 for a, b in zip(cues, cues[1:]) if a.kind == b.kind)
    assert repeats <= 2


def test_truth_has_positive_cues_only_and_starts_after_the_reaction_time():
    cues = build_schedule(seed=1, per_kind=2, controls=2)
    truth = truth_from_schedule(cues)
    assert len(truth) == 2 * len(POSITIVE) and all(t.kind in POSITIVE for t in truth)
    first = next(c for c in cues if c.kind in POSITIVE)
    t = next(t for t in truth if t.kind == first.kind and abs(t.end_s - first.end_s) < 1e-9)
    assert t.start_s == first.start_s + REACTION_S


def test_what_to_show_at_each_moment():
    cues = build_schedule(seed=1, per_kind=1, controls=1)
    assert phase_at(1.0, cues)[0] == "GET READY"
    assert phase_at(5.0, cues)[0] == "OPEN"
    assert phase_at(14.0, cues)[0] == "CLOSED"
    c = cues[0]
    heading, text, wait = phase_at(c.start_s - 1.0, cues)
    assert heading == "GET READY" and "Next:" in text and abs(wait - 1.0) < 1e-9
    assert phase_at(c.start_s + 0.5, cues)[0] == c.kind.replace("_", " ")
    assert phase_at(c.end_s + 0.1, cues)[0] in ("REST", "GET READY")
    assert phase_at(total_duration(cues), cues)[0] == "DONE"


def test_the_video_clock_follows_the_wall_clock():
    written = 0
    written += frames_to_write(0.0, written, 30)
    assert written == 1
    written += frames_to_write(1.0, written, 30)  # a second later: the video has 31 frames
    assert written == 31
    assert frames_to_write(1.0, written, 30) == 0  # nothing to add if it is up to date
    written += frames_to_write(1.5, written, 30)  # a gap of half a second: the last frame is repeated to fill it
    assert written == 46


def test_calibration_windows_sit_inside_their_phases():
    w = calibration_windows()
    assert 3.0 <= w["open"][0] and w["open"][1] <= 13.0
    assert 13.0 <= w["closed"][0] and w["closed"][1] <= 16.0
    assert 16.0 <= w["mouth"][0] and w["mouth"][1] <= 19.0


def test_false_alarms_in_the_control_periods():
    cues = build_schedule(seed=1, per_kind=0, controls=2)
    talk = [c for c in cues if c.kind == "TALK"]
    down = [c for c in cues if c.kind == "LOOK_DOWN"]
    detections = [
        Detection("YAWN", talk[0].start_s + 2, 2.0),  # a yawn reported while talking: a false alarm
        Detection("MICROSLEEP", down[1].start_s + 1, 0.5),  # a microsleep while looking down: a false alarm
        Detection("YAWN", down[0].start_s + 1, 2.0),  # a yawn while looking down is not what that control tests
    ]
    out = false_alarms_in_controls(cues, detections)
    assert out == {"TALK": (1, 2), "LOOK_DOWN": (1, 2)}
