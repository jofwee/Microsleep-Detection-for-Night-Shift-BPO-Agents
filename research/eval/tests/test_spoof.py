import pytest

from evalkit.spoof import ATTACKS, BONAFIDE, RULES, Rule, SpoofTrial, accepted, evaluate


def trial(attack, challenge=True, passive=0.9, match=0.9, subject="P1"):
    return SpoofTrial(subject, attack, "normal", challenge, passive, passive, match, 12)


DEPLOYED, LOWERED, NO_PASSIVE, NO_CHALLENGE, MATCH_ONLY = RULES


def test_the_deployed_rule_needs_the_match_the_challenge_and_the_passive_score():
    assert accepted(trial(BONAFIDE), DEPLOYED)
    assert not accepted(trial(BONAFIDE, match=0.3), DEPLOYED)  # a different face
    assert not accepted(trial(BONAFIDE, challenge=False), DEPLOYED)  # no blink or turn
    assert not accepted(trial(BONAFIDE, passive=0.4), DEPLOYED)  # looks like a screen
    assert accepted(trial(BONAFIDE, passive=0.4), LOWERED)  # but a dim real face passes at 0.35


def test_each_rule_asks_only_for_what_it_claims():
    t = trial("print", challenge=False, passive=0.1, match=0.9)  # a photo that matched the face but did nothing and looks fake
    assert accepted(t, MATCH_ONLY)
    assert not accepted(t, NO_PASSIVE) and not accepted(t, NO_CHALLENGE) and not accepted(t, DEPLOYED)
    assert accepted(trial("print", challenge=True, passive=0.1), NO_PASSIVE)  # a challenge done by a person holding up a photo...
    assert accepted(trial("print", challenge=False, passive=0.8), NO_CHALLENGE)  # ...and a photo the passive model likes


def test_missing_numbers_mean_rejection_not_acceptance():
    assert not accepted(SpoofTrial("P", BONAFIDE, "normal", True, None, None, None), MATCH_ONLY)
    assert not accepted(SpoofTrial("P", BONAFIDE, "normal", True, None, None, 0.9), DEPLOYED)
    assert accepted(SpoofTrial("P", BONAFIDE, "normal", True, None, None, 0.9), NO_PASSIVE)


def test_apcer_per_attack_type_the_worst_one_and_bpcer():
    trials = [
        trial(BONAFIDE), trial(BONAFIDE), trial(BONAFIDE, passive=0.2),  # one genuine attempt looks fake: BPCER 1/3
        trial("print", challenge=False, passive=0.1), trial("print", challenge=False, passive=0.1),
        trial("phone_video", challenge=True, passive=0.8),  # a video that happened to match the asked action: accepted
        trial("phone_video", challenge=False, passive=0.8),
    ]
    r = evaluate(trials, DEPLOYED)
    assert r["per_attack"] == {"print": (0, 2), "phone_video": (1, 2)}
    assert r["apcer_worst"] == 0.5
    assert r["bpcer"] == pytest.approx(1 / 3) and (r["bona_rejected"], r["bona_total"]) == (1, 3)


def test_no_trials_gives_no_rates():
    r = evaluate([], DEPLOYED)
    assert r["apcer_worst"] is None and r["bpcer"] is None and r["per_attack"] == {}


def test_the_attack_list_matches_the_protocol():
    assert set(ATTACKS) == {"print", "phone_photo", "phone_video", "screen_replay"} and BONAFIDE not in ATTACKS
    assert Rule("x").tau == 0.45
