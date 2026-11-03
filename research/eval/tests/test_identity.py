import numpy as np
import pytest

from evalkit.identity import Probe, all_trials, by_condition, operating_points, score_probe, split, summarize
from identity.profile import EnrolledProfile

rng = np.random.default_rng(5)


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def person():
    return unit(rng.normal(size=512))


def profile_of(face):
    embs = unit(np.stack([face + 0.01 * rng.normal(size=512) for _ in range(5)]))
    mean = unit(embs.mean(axis=0))
    return EnrolledProfile(embeddings=embs, mean=mean, model_name="test", model_version="1")


def probe_of(subject, face, noise=0.02, **k):
    return Probe(subject, unit(np.stack([face + noise * rng.normal(size=512) for _ in range(12)])), **k)


A, B, C = person(), person(), person()
PROFILES = {"A": profile_of(A), "B": profile_of(B), "C": profile_of(C)}


def test_the_same_person_scores_high_and_another_person_low():
    assert score_probe(PROFILES["A"], probe_of("A", A)) > 0.8
    assert score_probe(PROFILES["A"], probe_of("B", B)) < 0.3


def test_every_probe_is_compared_with_every_profile_and_labelled_genuine_or_impostor():
    probes = [probe_of("A", A), probe_of("B", B, lighting="dim"), probe_of("C", C)]
    trials = all_trials(PROFILES, probes)
    assert len(trials) == 9
    assert sum(t.genuine for t in trials) == 3
    assert all(t.genuine == (t.profile == t.probe) for t in trials)
    g, i = split(trials)
    assert len(g) == 3 and len(i) == 6 and min(g) > max(i)


def test_summary_of_cleanly_separated_people_has_no_errors_at_the_apps_threshold():
    trials = all_trials(PROFILES, [probe_of(n, f) for n, f in (("A", A), ("B", B), ("C", C)) for _ in range(3)])
    s = summarize(trials)
    assert (s.genuine, s.impostor) == (9, 18) and s.tau == 0.45
    assert s.far == 0.0 and s.frr == 0.0 and s.eer == 0.0
    assert s.far_ci[1] < 0.2  # zero errors in 18 impostor trials still leaves room for a rate of about 10%: it is not proof of zero


def test_a_bad_condition_shows_up_as_rejected_genuine_attempts():
    noisy = probe_of("A", A, noise=3.0, lighting="dim", glasses="yes")  # so much noise it no longer looks like A
    trials = all_trials(PROFILES, [probe_of("A", A), noisy])
    table = by_condition(trials)
    assert table[("normal", "no", "front")] == (0, 1)
    assert table[("dim", "yes", "front")] == (1, 1)
    s = summarize(trials)
    assert s.frr == pytest.approx(0.5)


def test_the_operating_points_run_from_accepting_everyone_to_nobody():
    trials = all_trials(PROFILES, [probe_of("A", A), probe_of("B", B)])
    pts = operating_points(trials)
    assert pts[0][1] == 1.0 and pts[-1][2] == 1.0
