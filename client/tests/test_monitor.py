"""M6 core: the offline queue, the uploader, the monitor core (events and minute metrics, heartbeat state),
the camera gate and the sign-in reference. No hardware: a fake pipeline and a fake camera."""
import time
from types import SimpleNamespace

import numpy as np
import pytest

from auth.supabase_client import ApiError
from monitor.core import MinuteAggregator, MonitorCore
from monitor.reference import SessionReference
from monitor.runner import CAMERA_STATUSES, CameraGate, MonitorRunner, camera_wanted
from storage.queue import OfflineQueue
from sync.uploader import MAX_ATTEMPTS, Uploader
from vision.drowsiness import Event, EventType, FatigueState
from vision.pipeline import Measurement


# --- queue ---
def test_queue_keeps_order_and_groups_runs_of_one_kind():
    q = OfflineQueue(":memory:")
    q.put("session_open", {"id": "s"})
    q.put("event", {"n": 1})
    q.put("event", {"n": 2})
    q.put("metric", {"m": 1})
    first = q.next_run()
    assert [r.kind for r in first] == ["session_open"]
    q.done([r.id for r in first])
    second = q.next_run()
    assert [r.payload["n"] for r in second] == [1, 2]
    q.done([r.id for r in second])
    assert q.next_run()[0].kind == "metric" and q.count() == 1


def test_queue_survives_reopening(tmp_path):
    path = tmp_path / "q.sqlite"
    q = OfflineQueue(path)
    q.put("event", {"n": 1})
    q.close()
    assert OfflineQueue(path).count() == 1


def test_queue_rejects_unknown_kinds():
    with pytest.raises(ValueError):
        OfflineQueue(":memory:").put("nope", {})


# --- uploader ---
class FakeServer:
    def __init__(self):
        self.calls, self.errors = [], []

    def _go(self, name, *a, **kw):
        self.calls.append((name, a, kw))
        if self.errors:
            e = self.errors.pop(0)
            if e:
                raise e
        return []

    def insert(self, table, rows, **kw):
        return self._go("insert", table, rows, **kw)

    def patch(self, table, match, values):
        return self._go("patch", table, match, values)


def test_uploader_sends_in_order_and_empties_the_queue():
    q, api = OfflineQueue(":memory:"), FakeServer()
    q.put("session_open", {"id": "s1"})
    q.put("event", {"session_id": "s1", "type": "YAWN"})
    q.put("metric", {"session_id": "s1"})
    q.put("session_close", {"id": "s1", "ended_at": "t"})
    assert Uploader(api, q).flush() == "ok"
    assert [(c[0], c[1][0]) for c in api.calls] == [
        ("insert", "sessions"), ("insert", "events"), ("insert", "metrics_minute"), ("patch", "sessions")]
    assert all(c[2].get("ignore_duplicates") for c in api.calls if c[0] == "insert")
    assert q.count() == 0 and Uploader(api, q).flush() == "empty"


def test_uploader_keeps_everything_while_offline():
    q, api = OfflineQueue(":memory:"), FakeServer()
    q.put("event", {"a": 1})
    api.errors = [ApiError(0, "network_error", "down")]
    up = Uploader(api, q)
    assert up.flush() == "offline" and q.count() == 1
    assert up.flush() == "ok" and q.count() == 0


@pytest.mark.parametrize("status,message,outcome", [
    (403, "new row violates row-level security policy for table events", "blocked"),
    (403, "face check required", "blocked"),
    (401, "JWT expired", "blocked"),
    (503, "x", "offline"),
    (429, "x", "offline"),
    (0, "no route to host", "offline"),
])
def test_uploader_status_codes(status, message, outcome):
    q, api = OfflineQueue(":memory:"), FakeServer()
    q.put("event", {"a": 1})
    api.errors = [ApiError(status, "x", message)]
    assert Uploader(api, q).flush() == outcome and q.count() == 1


def test_a_database_refusal_of_one_row_is_not_mistaken_for_a_lost_login():
    q, lines = OfflineQueue(":memory:"), []

    class Refuses(FakeServer):
        def insert(self, table, rows, **kw):
            if any(r.get("closed") for r in rows):
                raise ApiError(403, "42501", "unknown or closed session")
            return []

    q.put("event", {"closed": True})
    q.put("event", {"fine": True})
    up = Uploader(Refuses(), q, log=lines.append)
    for _ in range(6):
        up.flush()
    assert q.count() == 0 and any("dropping" in line for line in lines)


def test_uploader_drops_a_row_the_server_keeps_refusing_but_sends_the_rest():
    q, lines = OfflineQueue(":memory:"), []

    class Picky(FakeServer):
        def insert(self, table, rows, **kw):
            self.calls.append(("insert", table, rows))
            if any(r.get("bad") for r in rows):
                raise ApiError(400, "invalid", "bad row")
            return []

    api = Picky()
    q.put("event", {"ok": 1})
    q.put("event", {"bad": True})
    q.put("event", {"ok": 2})
    assert Uploader(api, q, log=lines.append).flush() == "ok"
    assert q.count() == 0
    sent = [r for c in api.calls for r in c[2] if not r.get("bad")]
    assert {"ok": 1} in sent and {"ok": 2} in sent
    assert any("dropping" in line for line in lines)
    assert MAX_ATTEMPTS >= 2


# --- monitor core ---
class FakePipeline:
    def __init__(self):
        self.engine = SimpleNamespace(state=FatigueState.ALERT, metrics=lambda: {"blink_rate": 15.0, "perclos": 0.1})
        self.script = []

    def process(self, frame, ts, recent):
        events, kw = self.script.pop(0) if self.script else ([], {})
        base = dict(ts_ms=ts, face_count=1, ear_smooth=0.3, lock_status="VERIFIED", lock_liveness=1.0)
        base.update(kw)
        return Measurement(**base), events


def make_core(**kw):
    clock = {"t": 1_800_000_000.0}
    q = OfflineQueue(":memory:")
    p = FakePipeline()
    core = MonitorCore(p, q, session_id=lambda: "sess", wall=lambda: clock["t"], **kw)
    return core, q, p, clock


FRAME = np.zeros((4, 4, 3), np.uint8)


def test_core_queues_events_but_not_blinks_with_wall_times_and_call_id():
    core, q, p, _ = make_core(call_id=lambda: "call-1")
    p.script = [([Event(EventType.MICROSLEEP, 1000, 700, 0.1, "critical"), Event(EventType.BLINK, 1000, 120)], {})]
    core.process(FRAME, 1000)
    rows = q.next_run()
    assert len(rows) == 1 and rows[0].kind == "event"
    r = rows[0].payload
    assert (r["type"], r["duration_ms"], r["severity"], r["call_id"], r["session_id"]) == ("MICROSLEEP", 700, "critical", "call-1", "sess")
    assert r["ts"].startswith("2027-") or r["ts"].startswith("2026-") or r["ts"].startswith("2027")


def test_core_without_a_session_does_not_queue_but_still_reports():
    seen = []
    core, q, p, _ = make_core(on_event=seen.append)
    core.session_id = lambda: None
    p.script = [([Event(EventType.YAWN, 10, 2500)], {})]
    core.process(FRAME, 10)
    assert q.count() == 0 and len(seen) == 1


def test_minute_aggregator_emits_one_row_per_full_minute_and_skips_short_ones():
    agg = MinuteAggregator(min_seconds=10)
    t0 = 60 * 1000.0
    out = []
    m = Measurement(ts_ms=0, face_count=1, ear_smooth=0.3)
    for i in range(0, 600):  # 60 s of frames in minute 1000
        out.append(agg.add(t0 + i * 0.1, m, [Event(EventType.YAWN, 0)] if i == 5 else [], {"blink_rate": 14, "perclos": 0.05}, "ON_CALL"))
    out.append(agg.add(t0 + 61, m, [], {"blink_rate": 14, "perclos": 0.05}, "ON_CALL"))  # next minute: flushes
    rows = [r for r in out if r]
    assert len(rows) == 1
    assert rows[0]["yawns"] == 1 and rows[0]["status"] == "ON_CALL" and rows[0]["mean_ear"] == 0.3
    short = MinuteAggregator(min_seconds=10)
    for i in range(20):  # 2 s only
        short.add(t0 + i * 0.1, m, [], {}, None)
    assert short.flush() is None


def test_minute_aggregator_ignores_frames_while_looking_down():
    agg = MinuteAggregator(min_seconds=1)
    down = Measurement(ts_ms=0, face_count=1, ear_smooth=0.1, looking_down=True)
    for i in range(50):
        agg.add(100.0 + i * 0.1, down, [], {}, None)
    assert agg.flush() is None


def test_core_heartbeat_state_follows_the_lock_and_the_fatigue_state():
    core, _, p, clock = make_core()
    assert core.heartbeat_state() == ("OFF", False, "ALERT")  # nothing processed yet
    core.process(FRAME, 0)
    assert core.heartbeat_state() == ("LIVE", True, "ALERT") and core.verified()
    p.script = [([], {"lock_status": "SEARCHING"})]
    core.process(FRAME, 33)
    assert core.heartbeat_state()[1] is True and not core.verified()  # a glance away is not a problem...
    for status in ("ABSENT", "IDENTITY_MISMATCH", "SPOOF_SUSPECTED"):  # ...these are
        p.script = [([], {"lock_status": status})]
        core.process(FRAME, 66)
        assert core.heartbeat_state()[1] is False
    p.engine.state = FatigueState.MICROSLEEP
    core.process(FRAME, 99)
    assert core.heartbeat_state()[2] == "MICROSLEEP"
    clock["t"] += 10  # the frames stop
    assert core.heartbeat_state()[0] == "OFF"


def test_core_verified_needs_passive_liveness():
    core, _, p, _ = make_core()
    p.script = [([], {"lock_liveness": 0.1})]
    core.process(FRAME, 0)
    assert not core.verified()


def test_core_finish_writes_the_minute_in_progress():
    core, q, p, clock = make_core()
    for i in range(300):
        clock["t"] += 0.1
        core.process(FRAME, i * 100)
    core.finish()
    assert any(r.kind == "metric" for r in q.next_run(500)) or q.count("metric") >= 1
    assert core.snapshot.last_frame_at == 0.0


# --- the camera gate ---
class FakeCamera:
    opened = 0
    closed = 0

    def __enter__(self):
        FakeCamera.opened += 1
        return self

    def __exit__(self, *exc):
        FakeCamera.closed += 1

    def read(self):
        time.sleep(0.005)
        return int(time.perf_counter() * 1000), FRAME


def make_runner(raises=False):
    FakeCamera.opened = FakeCamera.closed = 0
    core, q, p, _ = make_core()

    def open_camera():
        if raises:
            raise RuntimeError("no camera")
        return FakeCamera()

    return MonitorRunner(lambda: core, open_camera, idle=lambda: False, log=lambda s: None), core


def test_camera_is_wanted_only_while_working():
    assert CAMERA_STATUSES == {"AVAILABLE", "RINGING", "ON_CALL", "WRAP_UP", "SYSTEM_HOLD"}
    for s in ("OFFLINE", "NOT_READY", None):
        assert not camera_wanted(s)
    for s in CAMERA_STATUSES:
        assert camera_wanted(s)


def test_gate_opens_and_closes_the_camera_with_the_status():
    runner, core = make_runner()
    gate = CameraGate(runner)
    gate.sync("NOT_READY")
    assert not runner.running and FakeCamera.opened == 0  # the camera is off in Not ready
    gate.sync("AVAILABLE")
    assert runner.running and FakeCamera.opened == 1
    assert runner.wait_verified(3)
    gate.sync("ON_CALL")
    gate.sync("WRAP_UP")
    assert FakeCamera.opened == 1  # still the same camera session
    gate.sync("NOT_READY")  # break: camera off
    assert not runner.running and FakeCamera.closed == 1
    gate.sync("OFFLINE")
    assert FakeCamera.opened == 1


def test_runner_reports_a_camera_that_will_not_open():
    runner, _ = make_runner(raises=True)
    assert runner.start(wait_s=2) is False
    assert "no camera" in runner.error


def test_recheck_fails_when_the_face_is_not_the_agent():
    runner, core = make_runner()
    core.pipeline.script = [([], {"lock_status": "SEARCHING"})] * 100000
    runner.start()
    assert runner.wait_verified(0.3) is False
    runner.stop()


# --- the sign-in reference ---
def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_reference_scores_the_same_face_high_and_another_low():
    rng = np.random.default_rng(3)
    base = rng.normal(size=512)
    embs = [unit(base + rng.normal(scale=0.1, size=512)) for _ in range(5)]
    ref = SessionReference(embs)
    assert ref.score(unit(base + rng.normal(scale=0.1, size=512))) > 0.9
    assert ref.score(unit(rng.normal(size=512))) < 0.3


def test_reference_needs_three_embeddings():
    with pytest.raises(ValueError):
        SessionReference([unit(np.ones(8))] * 2)


def test_the_trace_notes_status_changes_and_low_liveness_scores_only(tmp_path):
    from monitor.trace import FileTrace

    lines = []
    core, _, p, _ = make_core(trace=lines.append)
    for status, live in (("VERIFIED", 1.0), ("VERIFIED", 1.0), ("VERIFIED", 0.2), ("VERIFIED", 0.2), ("SEARCHING", None)):
        p.script = [([], {"lock_status": status, "lock_liveness": live})]
        core.process(FRAME, 0)
    assert len(lines) == 3 and "liveness=0.20" in lines[1] and "lock=SEARCHING" in lines[2]
    f = FileTrace(tmp_path / "m.log")
    f("hello")
    assert "hello" in (tmp_path / "m.log").read_text(encoding="utf-8")


class FakeLock:
    def __init__(self):
        self.passed, self.failed = [], []

    def liveness_passed(self, ts):
        self.passed.append(ts)

    def liveness_failed(self, ts):
        self.failed.append(ts)


class FakeChallenge:
    """Answers PENDING for `frames` updates, then `then`."""

    def __init__(self, frames, then):
        from identity.liveness import ChallengeStatus

        self.left, self.then, self.status = frames, then, ChallengeStatus.PENDING
        self.prompt = "Please blink twice"

    def update(self, ts, ear, turn):
        from identity.liveness import ChallengeStatus

        self.left -= 1
        return ChallengeStatus.PENDING if self.left > 0 else self.then


def stepup_core(then):
    from identity.liveness import ChallengeStatus

    core, q, p, clock = make_core(make_challenge=lambda: FakeChallenge(3, getattr(ChallengeStatus, then)))
    p.lock = FakeLock()
    return core, p


def test_the_monitor_asks_for_a_live_action_when_the_lock_says_the_photo_check_keeps_failing():
    core, p = stepup_core("PASSED")
    p.script = [([], {"challenge_needed": True})]
    core.process(FRAME, 0)
    assert core.snapshot.prompt == "Please blink twice"  # shown to the agent
    core.process(FRAME, 33)
    assert core.snapshot.prompt == "Please blink twice" and p.lock.passed == []


def test_doing_the_live_action_clears_the_doubt():
    core, p = stepup_core("PASSED")
    p.script = [([], {"challenge_needed": True})]
    for i in range(6):
        core.process(FRAME, i * 33)
    assert len(p.lock.passed) == 1 and p.lock.failed == [] and core.snapshot.prompt is None


def test_not_doing_the_live_action_tells_the_lock_to_close():
    core, p = stepup_core("FAILED")
    p.script = [([], {"challenge_needed": True})]
    for i in range(6):
        core.process(FRAME, i * 33)
    assert len(p.lock.failed) == 1 and p.lock.passed == [] and core.snapshot.prompt is None


def test_no_challenge_without_a_request_and_only_one_at_a_time():
    core, p = stepup_core("PASSED")
    for i in range(5):
        core.process(FRAME, i * 33)
    assert core.snapshot.prompt is None
    p.script = [([], {"challenge_needed": True}), ([], {"challenge_needed": True})]
    core.process(FRAME, 200)
    first = core._challenge
    core.process(FRAME, 233)
    assert core._challenge is first


# --- the "is it you" check when going Available ---------------------------------------------------------------------
class GatePipeline(FakePipeline):
    """The lock stays SEARCHING (the photo check is unsure) and asks for a quick check once; it turns VERIFIED when that is passed."""

    def __init__(self):
        super().__init__()
        self.lock = FakeLock()
        self._asked = False

    def process(self, frame, ts, recent):
        passed = bool(self.lock.passed)
        ask = not passed and not self._asked
        self._asked = self._asked or ask
        return Measurement(ts_ms=ts, face_count=1, ear_smooth=0.3, lock_status="VERIFIED" if passed else "SEARCHING", lock_liveness=0.1, challenge_needed=ask), []


def gate_runner(challenge_frames):
    from identity.liveness import ChallengeStatus

    pipeline = GatePipeline()
    q = OfflineQueue(":memory:")
    core = MonitorCore(pipeline, q, session_id=lambda: "s", make_challenge=lambda: FakeChallenge(challenge_frames, ChallengeStatus.PASSED))
    FakeCamera.opened = FakeCamera.closed = 0
    return MonitorRunner(lambda: core, lambda: FakeCamera(), idle=lambda: False, log=lambda s: None)


def test_the_recheck_waits_while_a_quick_check_is_in_progress():
    runner = gate_runner(challenge_frames=60)  # takes about 0.4 s: longer than the plain timeout below
    runner.start()
    assert runner.wait_verified(timeout_s=0.15) is True  # the agent does the action, the lock verifies, the check passes
    runner.stop()


def test_the_recheck_gives_up_at_its_timeout_when_no_quick_check_was_asked():
    runner = gate_runner(challenge_frames=60)
    runner.core_made = None
    runner.start()
    runner.core.pipeline._asked = True  # nothing will be asked
    runner.core._challenge = None
    runner.core.snapshot.prompt = None
    import time as _t

    t0 = _t.monotonic()
    assert runner.wait_verified(timeout_s=0.2) is False
    assert 0.15 <= _t.monotonic() - t0 < 1.0
    runner.stop()


def test_the_recheck_never_waits_longer_than_its_cap_even_for_a_quick_check_that_never_ends():
    runner = gate_runner(challenge_frames=10**7)
    runner.MAX_CHECK_S = 0.4
    runner.start()
    import time as _t

    t0 = _t.monotonic()
    assert runner.wait_verified(timeout_s=0.1) is False
    assert _t.monotonic() - t0 < 1.5
    runner.stop()


def test_a_dim_but_real_face_passes_while_a_phone_picture_does_not():
    from identity.lock import LockConfig

    assert LockConfig().liveness_min == 0.35
    core, _, p, _ = make_core()
    for score, expected in ((0.46, True), (0.61, True), (0.93, True), (0.18, False), (0.01, False)):  # measured: dim real face, phone picture
        p.script = [([], {"lock_liveness": score})]
        core.process(FRAME, 0)
        assert core.verified() is expected, score


def test_the_monitor_measures_how_bright_the_face_is():
    import numpy as np

    from monitor.core import MonitorCore

    px = np.array([[100, 80], [200, 80], [150, 200], [150, 120]], dtype=float)  # a few landmarks spanning the face
    frame = np.zeros((300, 400, 3), np.uint8)
    frame[80:201, 100:201] = 60  # a dim face on a black wall
    m = Measurement(ts_ms=0, face_count=1, px=px)
    assert abs(MonitorCore._brightness(m, frame) - 60) < 1
    frame[80:201, 100:201] = 160
    assert abs(MonitorCore._brightness(m, frame) - 160) < 1
    assert MonitorCore._brightness(Measurement(ts_ms=0, face_count=0), frame) is None  # no face, no reading


def test_passing_the_quick_check_counts_even_if_the_last_passive_score_was_low():
    core, p = stepup_core("PASSED")
    p.script = [([], {"challenge_needed": True, "lock_liveness": 0.1, "lock_status": "SEARCHING"})]
    core.process(FRAME, 0)
    for i in range(1, 6):
        p.script = [([], {"lock_liveness": 0.1, "lock_status": "SEARCHING"})]
        core.process(FRAME, i * 33)
    assert p.lock.passed and not core.verified()  # not verified yet: the lock has not accepted the face
    p.script = [([], {"lock_liveness": 0.1, "lock_status": "VERIFIED"})]  # the lock now accepts it, the passive score is stale
    core.process(FRAME, 300)
    assert core.verified()
