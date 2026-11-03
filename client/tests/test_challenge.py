"""Active liveness challenge on synthetic (ear, turn) series at ~30 FPS."""
import random

import pytest

from identity.liveness import Action, ActiveChallenge, ChallengeStatus, random_actions

STEP = 33
OPEN, SHUT = 0.30, 0.05  # default calibration: close below 0.20


class Driver:
    def __init__(self, actions, **kw):
        self.ch = ActiveChallenge(actions, **kw)
        self.t = 0

    def run(self, ms, ear=OPEN, turn=0.02):
        end = self.t + ms
        while self.t < end:
            self.ch.update(self.t, ear, turn)
            self.t += STEP
        return self

    def blink(self, shut_ms=150):
        return self.run(shut_ms, ear=SHUT).run(400)

    @property
    def status(self):
        return self.ch.status


def test_blink_twice_then_turn_left_passes():
    d = Driver([Action.BLINK_TWICE, Action.TURN_LEFT]).run(1200)
    assert d.ch.prompt == "Please blink twice"
    d.blink().blink()
    assert d.ch.current is Action.TURN_LEFT
    d.run(600, turn=0.25)
    assert d.status is ChallengeStatus.PASSED


def test_turn_right_needs_a_negative_turn():
    d = Driver([Action.TURN_RIGHT]).run(1200).run(800, turn=0.25)  # wrong way: toward their left
    assert d.status is ChallengeStatus.PENDING
    d.run(800, turn=-0.25)
    assert d.status is ChallengeStatus.PASSED


def test_a_photo_that_does_nothing_fails_on_timeout():
    d = Driver([Action.BLINK_TWICE, Action.TURN_LEFT]).run(12_000)
    assert d.status is ChallengeStatus.FAILED


def test_one_blink_is_not_two():
    d = Driver([Action.BLINK_TWICE]).run(1200).blink().run(9000)
    assert d.status is ChallengeStatus.FAILED


def test_a_long_eye_closure_is_not_a_blink():
    d = Driver([Action.BLINK_TWICE]).run(1200)
    for _ in range(2):
        d.run(900, ear=SHUT).run(400)  # eyes shut ~1 s: that is a microsleep, not a blink
    d.run(9000)
    assert d.status is ChallengeStatus.FAILED


def test_a_quick_flick_of_the_head_is_not_held_long_enough():
    d = Driver([Action.TURN_LEFT]).run(1200).run(100, turn=0.3).run(9000)
    assert d.status is ChallengeStatus.FAILED


def test_a_small_turn_is_not_enough():
    d = Driver([Action.TURN_LEFT]).run(1200).run(9000, turn=0.08)
    assert d.status is ChallengeStatus.FAILED


def test_the_second_turn_must_start_from_the_centre():
    d = Driver([Action.TURN_LEFT, Action.TURN_RIGHT]).run(1200).run(600, turn=0.3)
    assert d.ch.current is Action.TURN_RIGHT
    # staying turned left must not satisfy "turn right", and the clock has not started yet
    d.run(20_000, turn=0.3)
    assert d.status is ChallengeStatus.PENDING
    d.run(300, turn=0.02).run(600, turn=-0.3)
    assert d.status is ChallengeStatus.PASSED


def test_the_front_reference_is_learned_per_person():
    # this person reads 0.20 when facing the camera, so a turn is measured from there
    d = Driver([Action.TURN_LEFT]).run(1200, turn=0.20)
    d.run(800, turn=0.25)
    assert d.status is ChallengeStatus.PENDING
    d.run(800, turn=0.40)
    assert d.status is ChallengeStatus.PASSED


def test_losing_the_face_does_not_crash_and_times_out():
    d = Driver([Action.BLINK_TWICE]).run(1200)
    d.ch.update(d.t, None, None)
    d.run(9000, ear=None, turn=None)
    assert d.status is ChallengeStatus.FAILED


def test_random_actions_are_distinct_and_reproducible_with_a_seed():
    a = random_actions(2, random.Random(1))
    assert len(set(a)) == 2
    assert a == random_actions(2, random.Random(1))
    assert len({tuple(random_actions(2)) for _ in range(60)}) > 1  # unseeded: not always the same order
