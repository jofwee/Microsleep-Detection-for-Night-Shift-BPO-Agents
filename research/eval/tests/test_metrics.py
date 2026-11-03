import math

import pytest

from evalkit.metrics import (
    Detection, Scores, Truth, apcer_bpcer, attack_table, bootstrap_ci, describe, det_curve, eer, far_frr, match_events, per_kind, wilson,
)


# --- event matching ---
def test_a_detection_inside_the_event_is_a_hit_and_its_delay_is_measured():
    s = match_events([Truth("YAWN", 10.0, 13.0)], [Detection("YAWN", 12.1, 2.0)])
    assert (s.tp, s.fp, s.fn) == (1, 0, 0)
    assert s.delays_s == [pytest.approx(2.1)]
    assert s.precision == s.recall == s.f1 == 1.0


def test_tolerance_lets_a_slightly_late_or_early_detection_match():
    truth = [Truth("MICROSLEEP", 20.0, 20.8)]
    assert match_events(truth, [Detection("MICROSLEEP", 21.4, 0.5)], tolerance_s=1.0).tp == 1
    assert match_events(truth, [Detection("MICROSLEEP", 23.0, 0.5)], tolerance_s=1.0).tp == 0
    assert match_events(truth, [Detection("MICROSLEEP", 19.5, 0.0)], tolerance_s=1.0).tp == 1


def test_wrong_kind_and_extra_detections_are_false_positives_and_missed_events_are_misses():
    s = match_events(
        [Truth("YAWN", 5, 7), Truth("YAWN", 30, 32), Truth("MICROSLEEP", 50, 51)],
        [Detection("YAWN", 6, 1), Detection("HEAD_NOD", 31, 0), Detection("YAWN", 80, 1)],
    )
    assert (s.tp, s.fp, s.fn) == (1, 2, 2)
    assert s.precision == pytest.approx(1 / 3) and s.recall == pytest.approx(1 / 3)


def test_each_detection_matches_one_event_only():
    two_events = [Truth("YAWN", 10, 11), Truth("YAWN", 11.5, 12.5)]
    s = match_events(two_events, [Detection("YAWN", 11.2, 0.5)], tolerance_s=1.0)
    assert (s.tp, s.fp, s.fn) == (1, 0, 1)  # one detection cannot explain two events


def test_the_earliest_detection_is_used_and_the_rest_count_as_false_alarms():
    s = match_events([Truth("YAWN", 10, 14)], [Detection("YAWN", 13, 1), Detection("YAWN", 11, 1)])
    assert (s.tp, s.fp) == (1, 1) and s.delays_s == [pytest.approx(1.0)]


def test_nothing_to_find_and_nothing_found_has_no_rates_instead_of_a_division_error():
    s = match_events([], [])
    assert s.precision is None and s.recall is None and s.f1 is None
    assert Scores(tp=0, fp=3, fn=0).recall is None and Scores(tp=0, fp=3, fn=0).precision == 0.0


def test_per_kind_and_adding_scores():
    truth = [Truth("YAWN", 1, 2), Truth("MICROSLEEP", 9, 10)]
    found = [Detection("YAWN", 2, 1)]
    out = per_kind(truth, found)
    assert out["YAWN"].tp == 1 and out["MICROSLEEP"].fn == 1
    total = out["YAWN"] + out["MICROSLEEP"]
    assert (total.tp, total.fn) == (1, 1)


# --- face verification ---
GENUINE = [0.9, 0.85, 0.8, 0.75, 0.6, 0.55]
IMPOSTOR = [0.1, 0.2, 0.15, 0.3, 0.6, 0.05]


def test_far_and_frr_at_a_threshold():
    far, frr = far_frr(GENUINE, IMPOSTOR, 0.45)
    assert far == pytest.approx(1 / 6) and frr == 0.0
    far, frr = far_frr(GENUINE, IMPOSTOR, 0.7)
    assert far == 0.0 and frr == pytest.approx(2 / 6)
    assert far_frr([], IMPOSTOR, 0.5)[1] is not None and math.isnan(far_frr([], IMPOSTOR, 0.5)[1])


def test_the_eer_of_perfectly_separated_scores_is_zero():
    rate, thr = eer([0.9, 0.8, 0.85], [0.1, 0.2, 0.3])
    assert rate == 0.0 and 0.3 < thr <= 0.8


def test_the_eer_of_overlapping_scores_is_between_zero_and_one():
    genuine = [0.9, 0.85, 0.8, 0.7, 0.55, 0.4]  # one genuine attempt scores low...
    impostor = [0.05, 0.1, 0.2, 0.3, 0.45, 0.6]  # ...and one impostor scores high
    rate, thr = eer(genuine, impostor)
    assert 0.0 < rate < 0.5
    far, frr = far_frr(genuine, impostor, thr)
    assert abs(far - frr) <= 1 / 6 + 1e-9


def test_scores_with_a_gap_between_them_have_an_eer_of_zero_and_the_gap_is_where_the_threshold_goes():
    rate, thr = eer([0.9, 0.8, 0.55], [0.5, 0.3, 0.1])  # lowest genuine 0.55 > highest impostor 0.50
    assert rate == 0.0 and 0.5 < thr <= 0.55


def test_the_overlap_in_the_shared_test_data_gives_the_expected_eer():
    rate, _ = eer(GENUINE, IMPOSTOR)  # one impostor (0.6) scores above one genuine (0.55)
    assert rate == pytest.approx(1 / 6)


def test_the_det_curve_runs_from_accept_everyone_to_accept_nobody():
    curve = det_curve(GENUINE, IMPOSTOR)
    first, last = curve[0], curve[-1]
    assert first[1] == 1.0 and first[2] == 0.0  # lowest threshold: everyone accepted
    assert last[1] == 0.0 and last[2] == 1.0  # above every score: nobody accepted
    assert det_curve([], []) == [] and math.isnan(eer([], [])[0])


# --- attacks ---
def test_apcer_and_bpcer():
    apcer, bpcer = apcer_bpcer([True, False, False, False], [True, True, False, True, True])
    assert apcer == 0.25 and bpcer == pytest.approx(0.2)
    a, b = apcer_bpcer([], [])
    assert math.isnan(a) and math.isnan(b)


def test_attack_table_counts_per_type():
    t = attack_table([("print", False), ("print", True), ("phone", False), ("print", False)])
    assert t == {"print": (1, 3), "phone": (0, 1)}


# --- statistics ---
def test_describe_ignores_missing_values():
    d = describe([1.0, 2.0, 3.0, 4.0, float("nan"), None])
    assert d["n"] == 4 and d["mean"] == 2.5 and d["median"] == 2.5 and d["min"] == 1.0 and d["max"] == 4.0
    assert describe([]) == {"n": 0}


def test_wilson_interval_is_honest_for_small_samples_and_extremes():
    lo, hi = wilson(0, 10)
    assert lo == 0.0 and 0.2 < hi < 0.4  # zero of ten does not prove a rate of zero
    lo, hi = wilson(5, 10)
    assert lo < 0.5 < hi and lo > 0.2 and hi < 0.8
    assert all(math.isnan(x) for x in wilson(0, 0))


def test_bootstrap_interval_contains_the_mean_and_is_repeatable():
    data = [0.1 * i for i in range(1, 30)]
    lo, hi = bootstrap_ci(data, seed=1)
    assert lo < sum(data) / len(data) < hi
    assert (lo, hi) == bootstrap_ci(data, seed=1)
    assert math.isnan(bootstrap_ci([])[0])
