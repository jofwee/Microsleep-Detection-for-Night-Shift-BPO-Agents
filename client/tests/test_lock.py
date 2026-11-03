"""Identity-lock scenarios with synthetic faces: embeddings are unit vectors, so cosine is exact."""
import numpy as np
import pytest

from identity.lock import IdentityLock, LockConfig, LockStatus, iou
from vision.drowsiness import EventType

STEP = 33
A = np.array([1.0, 0.0, 0.0])  # the enrolled agent
B = np.array([0.0, 1.0, 0.0])  # a stranger (cosine 0 to the agent)
AGENT_BOX = (0.30, 0.20, 0.60, 0.70)
SIDE_BOX = (0.70, 0.10, 0.85, 0.40)
FAR_BOX = (0.05, 0.60, 0.20, 0.95)


def with_cosine(c: float) -> np.ndarray:
    return c * A + np.sqrt(1 - c * c) * B


class Harness:
    def __init__(self, cfg=None, liveness=None):
        self.lock = IdentityLock(lambda v: float(v @ A), cfg or LockConfig())
        self.liveness = liveness
        self.t = 0
        self.events = []  # (ts, Event)
        self.last = None
        self.challenges = []  # timestamps at which the lock asked for a live action
        self._since_blink = 0

    def run(self, ms, faces=(), blink_every=5000, eyes_active=True, frontal=True):
        """faces: list of (box, embedding), held constant for ms."""
        end = self.t + ms
        boxes = [b for b, _ in faces]
        vecs = [v for _, v in faces]
        while self.t < end:
            self._since_blink += STEP
            if blink_every and self._since_blink >= blink_every:
                self.lock.note_blink()
                self._since_blink = 0
            self.last = self.lock.update(
                self.t, boxes, lambda i: vecs[i], liveness_fn=self.liveness, eyes_active=eyes_active, frontal=frontal
            )
            self.events += [(self.t, e) for e in self.last.events]
            if self.last.challenge_needed:
                self.challenges.append(self.t)
            self.t += STEP
        return self

    def kinds(self):
        return [e.type for _, e in self.events]

    def times(self, kind):
        return [ts for ts, e in self.events if e.type is kind]


# --- the agent -----------------------------------------------------------------------
def test_agent_alone_stays_verified_and_reverification_is_throttled():
    h = Harness().run(21_000, [(AGENT_BOX, A)])
    assert h.last.status is LockStatus.VERIFIED and h.last.agent_index == 0
    assert h.events == []
    assert 6 <= h.lock.verifications <= 9  # once to bind, then about every 3 s, not every frame


def test_iou():
    assert iou(AGENT_BOX, AGENT_BOX) == pytest.approx(1.0)
    assert iou(AGENT_BOX, FAR_BOX) == 0.0


# --- bystanders ----------------------------------------------------------------------
def test_bystander_is_ignored_and_reported_once():
    # the bystander is listed first, so "the agent" is index 1, not 0
    scene = [(SIDE_BOX, B), (AGENT_BOX, A)]
    h = Harness().run(15_000, scene)
    assert h.last.status is LockStatus.VERIFIED
    assert h.last.agent_index == 1
    assert h.kinds() == [EventType.MULTIPLE_FACES]


def test_bystander_walking_in_and_out_never_becomes_the_agent():
    h = Harness().run(4000, [(AGENT_BOX, A)])
    h.run(6000, [(AGENT_BOX, A), (SIDE_BOX, B)])
    assert h.last.agent_index == 0
    h.run(4000, [(AGENT_BOX, A)])
    assert h.last.agent_index == 0
    assert EventType.IDENTITY_MISMATCH not in h.kinds()


# --- absence -------------------------------------------------------------------------
def test_leaving_for_more_than_10s_is_absent_once_and_recovers():
    h = Harness().run(3000, [(AGENT_BOX, A)]).run(12_000, [])
    assert h.kinds() == [EventType.ABSENT]
    assert h.last.status is LockStatus.ABSENT and h.last.agent_index is None
    gone_for = h.events[0][1].duration_ms
    assert 10_000 <= gone_for <= 10_100
    h.run(5000, [(AGENT_BOX, A)])
    assert h.last.status is LockStatus.VERIFIED
    h.run(12_000, [])
    assert h.kinds().count(EventType.ABSENT) == 2  # re-armed after the agent came back


def test_short_absence_is_not_absent_and_the_agent_is_rechecked_on_return():
    h = Harness().run(3000, [(AGENT_BOX, A)])
    before = h.lock.verifications
    h.run(4000, [])
    assert h.last.status is LockStatus.SEARCHING and h.last.agent_index is None  # not measured meanwhile
    h.run(3000, [(AGENT_BOX, A)])
    assert h.last.status is LockStatus.VERIFIED
    assert EventType.ABSENT not in h.kinds()
    assert h.lock.verifications > before


# --- someone else in the seat --------------------------------------------------------
def test_stranger_in_the_seat_triggers_mismatch_after_5s_and_stays_locked():
    h = Harness().run(3000, [(AGENT_BOX, A)]).run(1000, [])
    start = h.t
    h.run(6000, [(AGENT_BOX, B)])
    assert h.kinds() == [EventType.IDENTITY_MISMATCH]
    delay = h.times(EventType.IDENTITY_MISMATCH)[0] - start
    assert 4900 <= delay <= 5300
    assert h.events[0][1].severity == "critical"
    assert h.last.status is LockStatus.IDENTITY_MISMATCH and h.last.agent_index is None
    # even the real agent coming back does not reopen it: that needs the forced re-login
    h.run(5000, [(AGENT_BOX, A)])
    assert h.last.status is LockStatus.IDENTITY_MISMATCH and h.last.agent_index is None
    h.lock.reset()
    h.run(2000, [(AGENT_BOX, A)])
    assert h.last.status is LockStatus.VERIFIED


def test_stranger_is_never_measured_as_the_agent():
    h = Harness().run(3000, [(AGENT_BOX, A)]).run(1000, [])
    indexes = []
    for _ in range(300):
        h.run(STEP, [(AGENT_BOX, B)])
        indexes.append(h.last.agent_index)
    assert all(i is None for i in indexes)


def test_stranger_swapping_in_without_a_gap_is_caught_by_the_recheck():
    h = Harness().run(5000, [(AGENT_BOX, A)])  # same box, same face count, no gap at all
    swap = h.t
    h.run(12_000, [(AGENT_BOX, B)])
    assert h.last.status is LockStatus.IDENTITY_MISMATCH
    delay = h.times(EventType.IDENTITY_MISMATCH)[0] - swap
    assert 4900 <= delay <= 8500  # up to 3 s to notice + 5 s of mismatch


def test_stranger_alone_is_searched_at_a_throttled_rate():
    h = Harness().run(1000, []).run(4000, [(AGENT_BOX, B)])
    assert h.lock.verifications <= 4000 // 500 + 2


# --- immediate re-verification triggers ----------------------------------------------
def test_box_jump_forces_an_immediate_recheck():
    h = Harness().run(1000, [(AGENT_BOX, A)])
    before = h.lock.verifications
    shifted = (0.42, 0.20, 0.72, 0.70)  # IoU about 0.43: tracked, but it "jumped"
    h.run(STEP, [(shifted, A)])
    assert h.lock.verifications == before + 1
    assert h.last.status is LockStatus.VERIFIED


def test_face_count_change_forces_an_immediate_recheck():
    h = Harness().run(1000, [(AGENT_BOX, A)])
    before = h.lock.verifications
    h.run(STEP, [(AGENT_BOX, A), (SIDE_BOX, B)])
    assert h.lock.verifications > before


def test_losing_the_track_and_finding_the_agent_elsewhere_rebinds():
    h = Harness().run(1000, [(AGENT_BOX, A)]).run(1000, [(FAR_BOX, A)])
    assert h.last.status is LockStatus.VERIFIED and h.last.agent_index == 0


# --- spoof ---------------------------------------------------------------------------
def test_no_blink_for_90s_is_spoof_suspected():
    h = Harness().run(100_000, [(AGENT_BOX, A)], blink_every=None)
    assert h.kinds() == [EventType.SPOOF_SUSPECTED]
    t = h.times(EventType.SPOOF_SUSPECTED)[0]
    assert 90_000 <= t <= 91_000
    assert h.last.status is LockStatus.SPOOF_SUSPECTED


def test_regular_blinks_never_raise_a_spoof_alarm():
    h = Harness().run(300_000, [(AGENT_BOX, A)], blink_every=20_000)
    assert h.events == []


def test_time_with_the_eyes_unmeasured_does_not_count_towards_spoof():
    # e.g. the agent is reading the keyboard: the eyes are paused, so no blinks can be seen
    h = Harness().run(300_000, [(AGENT_BOX, A)], blink_every=None, eyes_active=False)
    assert h.events == []


def test_repeated_passive_liveness_failures_ask_for_a_live_action_instead_of_closing_the_lock():
    h = Harness(liveness=lambda i: 0.1).run(30_000, [(AGENT_BOX, A)])
    assert len(h.challenges) == 1 and h.challenges[0] <= 8_000  # 3 failed checks, 2 s apart, asked once
    assert EventType.SPOOF_SUSPECTED not in h.kinds()


def test_a_live_action_that_is_not_done_closes_the_lock_as_a_spoof():
    h = Harness(liveness=lambda i: 0.1).run(9_000, [(AGENT_BOX, A)])
    assert h.challenges
    h.lock.liveness_failed(h.t)
    h.run(100, [(AGENT_BOX, A)])
    assert h.kinds() == [EventType.SPOOF_SUSPECTED]


def test_a_live_action_that_is_done_clears_the_doubt_for_the_cooldown():
    h = Harness(liveness=lambda i: 0.1).run(9_000, [(AGENT_BOX, A)])
    assert len(h.challenges) == 1
    h.lock.liveness_passed(h.t)
    h.run(200_000, [(AGENT_BOX, A)])  # the model still says "fake", but the agent just proved otherwise
    assert len(h.challenges) == 1 and EventType.SPOOF_SUSPECTED not in h.kinds()
    assert h.last.status is LockStatus.VERIFIED
    h.run(120_000, [(AGENT_BOX, A)])  # the cooldown (5 minutes) is over: the doubt can be raised again
    assert len(h.challenges) == 2

def test_an_occasional_failed_liveness_check_among_good_ones_is_tolerated():
    scores = iter([0.9, 0.9, 0.9, 0.9, 0.1] * 40)  # a real face with one bad frame in five
    h = Harness(liveness=lambda i: next(scores)).run(120_000, [(AGENT_BOX, A)])
    assert EventType.SPOOF_SUSPECTED not in h.kinds()


def test_a_face_that_keeps_flickering_between_real_and_fake_is_questioned():
    # a screen replay near the threshold: the old "3 in a row" rule kept resetting on the passes
    scores = iter([0.9, 0.1] * 40)
    h = Harness(liveness=lambda i: next(scores)).run(30_000, [(AGENT_BOX, A)])
    assert len(h.challenges) == 1 and h.challenges[0] <= 12_000  # third failed check, about 10 s in

class Switch:
    """A passive-liveness score the test can change while the lock runs."""

    def __init__(self, value):
        self.value = value

    def __call__(self, i):
        return self.value


def test_a_head_turned_away_or_looking_down_is_not_judged_by_the_passive_model():
    # typing or reading notes: the model is trained on faces looking at the camera and scores them badly
    live = Switch(0.95)
    h = Harness(liveness=live).run(3_000, [(AGENT_BOX, A)])
    live.value = 0.05
    h.run(120_000, [(AGENT_BOX, A)], frontal=False)
    assert EventType.SPOOF_SUSPECTED not in h.kinds() and h.last.status is LockStatus.VERIFIED


def test_losing_track_while_looking_down_does_not_burn_through_the_spoof_counter():
    # the track is lost (the face moves a lot) and the search runs every 0.5 s: three bad scores would be 1.5 s
    live = Switch(0.95)
    h = Harness(liveness=live).run(3_000, [(AGENT_BOX, A)])
    live.value = 0.05
    h.run(500, [])  # face out of view for a moment
    h.run(20_000, [(FAR_BOX, A)], frontal=False)  # back in a different place, head down
    assert EventType.SPOOF_SUSPECTED not in h.kinds()
    assert h.last.status is LockStatus.VERIFIED  # taken back: it passed the passive check a few seconds ago


def test_a_face_that_never_passed_is_not_accepted_while_turned_but_not_called_a_spoof_either():
    h = Harness(liveness=Switch(0.05)).run(20_000, [(AGENT_BOX, A)], frontal=False)
    assert h.kinds() == [] and h.last.status is LockStatus.SEARCHING


def test_a_screen_replay_facing_the_camera_is_still_questioned_and_then_caught():
    h = Harness(liveness=lambda i: 0.05).run(30_000, [(AGENT_BOX, A)], frontal=True)
    assert len(h.challenges) == 1
    h.lock.liveness_failed(h.t)  # a photo or a recording does not blink or turn on command
    h.run(100, [(AGENT_BOX, A)], frontal=True)
    assert h.kinds() == [EventType.SPOOF_SUSPECTED]

def test_the_grace_for_a_turned_head_runs_out():
    live = Switch(0.95)
    h = Harness(liveness=live).run(3_000, [(AGENT_BOX, A)])
    live.value = 0.05
    h.run(35_000, [])  # away for 35 s: the last passed check is now older than the 30 s grace
    h.run(5_000, [(FAR_BOX, A)], frontal=False)
    assert h.last.status is LockStatus.SEARCHING and EventType.SPOOF_SUSPECTED not in h.kinds()
    h.run(2_000, [(FAR_BOX, A)], frontal=True)  # it must face the camera and pass; a fake is questioned
    assert h.challenges or h.last.status is not LockStatus.VERIFIED

def test_the_latest_liveness_score_is_reported():
    h = Harness(liveness=lambda i: 0.77).run(5000, [(AGENT_BOX, A)])
    assert h.last.liveness == pytest.approx(0.77)


# --- threshold -----------------------------------------------------------------------
@pytest.mark.parametrize("cosine, verified", [(0.50, True), (0.46, True), (0.44, False), (0.10, False)])
def test_tau_decides_who_is_the_agent(cosine, verified):
    h = Harness().run(2000, [(AGENT_BOX, with_cosine(cosine))])
    assert (h.last.status is LockStatus.VERIFIED) == verified


# --- liveness gate -------------------------------------------------------------------
def test_a_replay_that_fails_liveness_is_never_measured_as_the_agent():
    # looks exactly like the agent (cosine 1.0), but the passive check says "screen"
    h = Harness(liveness=lambda i: 0.01)
    indexes = []
    for _ in range(150):
        h.run(STEP, [(AGENT_BOX, A)])
        indexes.append(h.last.agent_index)
    assert all(i is None for i in indexes)  # not even for one frame
    assert len(h.challenges) == 1 and h.challenges[0] <= 2_000  # three failed checks, 0.5 s apart: a live action is asked for
    h.lock.liveness_failed(h.t)  # a screen cannot do it
    h.run(STEP, [(AGENT_BOX, A)])
    assert h.kinds() == [EventType.SPOOF_SUSPECTED]
    assert EventType.IDENTITY_MISMATCH not in h.kinds()  # it is a spoof, not a stranger


def test_a_real_face_passes_the_gate_immediately():
    h = Harness(liveness=lambda i: 0.99).run(500, [(AGENT_BOX, A)])
    assert h.last.status is LockStatus.VERIFIED and h.last.agent_index == 0


def test_one_bad_first_frame_delays_but_does_not_block_a_real_face():
    scores = iter([0.05] + [0.99] * 500)
    h = Harness(liveness=lambda i: next(scores)).run(3000, [(AGENT_BOX, A)])
    assert h.last.status is LockStatus.VERIFIED
    assert EventType.SPOOF_SUSPECTED not in h.kinds()


def test_a_stranger_is_still_a_mismatch_not_a_spoof_when_liveness_is_fine():
    h = Harness(liveness=lambda i: 0.99).run(1000, []).run(7000, [(AGENT_BOX, B)])
    assert h.kinds() == [EventType.IDENTITY_MISMATCH]
