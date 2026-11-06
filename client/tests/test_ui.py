"""The agent desktop: windows show the right things, and the controller follows the rules (camera only while
working, Available only after the re-check, alert on microsleep, hold acknowledged, wrap-up). Qt runs
offscreen; background work runs inline so every step is deterministic."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from auth.supabase_client import ApiError
from bpo.call_session import CallOutcome
from controller import Controller
from storage.queue import OfflineQueue
from ui import theme
from ui.alert_window import AlertWindow
from ui.desktop_window import AgentView, DesktopWindow
from ui.login_window import LoginWindow
from ui.tray import Tray
from vision.drowsiness import Event, EventType


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    theme.apply(app, "light", remember=False)
    return app


# --- the windows ---
def test_desktop_shows_a_page_for_each_status(qapp):
    w = DesktopWindow()
    pages = {"OFFLINE": 0, "NOT_READY": 0, "AVAILABLE": 0, "RINGING": 1, "ON_CALL": 2, "WRAP_UP": 3, "SYSTEM_HOLD": 4}
    for status, page in pages.items():
        w.show_view(AgentView(status=status))
        assert w.pages.currentIndex() == page, status


def test_buttons_follow_the_status(qapp):
    w = DesktopWindow()
    w.show_view(AgentView(status="NOT_READY"))
    assert w.btn_available.isEnabled() and not w.btn_break.isEnabled()
    w.show_view(AgentView(status="AVAILABLE", camera_on=True))
    assert not w.btn_available.isEnabled() and w.btn_break.isEnabled()


def test_camera_indicator_is_always_visible_and_honest(qapp):
    w = DesktopWindow()
    w.show_view(AgentView(status="NOT_READY", camera_on=False))
    assert "OFF" in w.camera.text()
    w.show_view(AgentView(status="AVAILABLE", camera_on=True))
    assert "ON" in w.camera.text() and "OFF" not in w.camera.text()


def test_red_is_used_for_microsleep_and_mismatch_only(qapp):
    w = DesktopWindow()
    w.show_view(AgentView(status="AVAILABLE", camera_on=True, fatigue="DROWSY", lock_status="VERIFIED"))
    assert w.fatigue.property("tone") != "alarm"
    w.show_view(AgentView(status="AVAILABLE", camera_on=True, fatigue="MICROSLEEP"))
    assert w.fatigue.property("tone") == "alarm"
    w.show_view(AgentView(status="AVAILABLE", camera_on=True, lock_status="IDENTITY_MISMATCH"))
    assert w.fatigue.property("tone") == "alarm"
    for status in ("OFFLINE", "NOT_READY", "AVAILABLE", "RINGING", "ON_CALL", "WRAP_UP", "SYSTEM_HOLD"):
        w.show_view(AgentView(status=status, camera_on=True))
        assert w.status_chip.property("tone") != "alarm", status


def test_transcript_and_wrap_up_signals(qapp):
    w = DesktopWindow()
    w.show_view(AgentView(status="ON_CALL", transcript=[("caller", "Hello"), ("agent", "Hi")]))
    assert [w.transcript.item(i).text() for i in range(2)] == ["Caller: Hello", "You: Hi"]
    got = []
    w.finish.connect(lambda d, n: got.append((d, n)))
    w.dispo_buttons["escalated"].click()
    w.notes.setPlainText("  asked for a supervisor ")
    w.btn_finish.click()
    assert got == [("escalated", "asked for a supervisor")]


def test_sync_footer_tells_the_truth(qapp):
    w = DesktopWindow()
    w.show_view(AgentView(status="AVAILABLE", queued=4, sync="offline"))
    assert "4 waiting" in w.footer.text().lower() and "offline" in w.footer.text().lower()


def test_login_flow_pages_and_password_mismatch(qapp):
    w = LoginWindow()
    got = []
    w.new_password.connect(got.append)
    w.show_page(LoginWindow.NEW_PASSWORD)
    w.new1.setText("Abcdefgh1234")
    w.new2.setText("different")
    w._submit_new()
    assert got == [] and "do not match" in w.error.text()
    w.new2.setText("Abcdefgh1234")
    w._submit_new()
    assert got == ["Abcdefgh1234"]


def test_alert_window_beeps_until_confirmed(qapp):
    beeps, confirmed = [], []
    a = AlertWindow(beep=lambda: beeps.append(1))
    a.confirmed.connect(lambda: confirmed.append(1))
    a.raise_alert("test")
    assert a.active and len(beeps) == 1 and a._timer.isActive()
    a.button.click()
    assert not a.active and not a._timer.isActive() and confirmed == [1]


def test_tray_icon_tells_camera_state(qapp):
    t = Tray()
    t.set_state("AVAILABLE", True, False)
    assert "ON" in t.camera_action.text() and "ON" in t.icon.toolTip()
    t.set_state("NOT_READY", False, False)
    assert "OFF" in t.camera_action.text()


# --- the controller ---
class FakeDesk:
    def __init__(self):
        self.last = {"status": "NOT_READY"}
        self.calls, self.refuse = [], None
        self.rows = []

    def _rec(self, *a):
        self.calls.append(a)

    def heartbeat(self, monitor, identity_ok, fatigue):
        self._rec("heartbeat", monitor, identity_ok, fatigue)
        return self.last

    def go_available(self):
        self._rec("go_available")
        if self.refuse:
            raise self.refuse
        self.last = {"status": "AVAILABLE"}

    def not_ready(self, reason="break"):
        self._rec("not_ready", reason)
        self.last = {"status": "NOT_READY"}

    def accept(self, call_id):
        self._rec("accept", call_id)

    def decline(self, call_id):
        self._rec("decline", call_id)

    def end_call(self, call_id):
        self._rec("end_call", call_id)

    def finish_wrap_up(self, call_id, disposition, notes=None):
        self._rec("finish", call_id, disposition, notes)
        self.last = {"status": "AVAILABLE"}

    def my_calls(self, since_iso):
        self._rec("my_calls", since_iso)
        return self.rows

    def lookup_customer(self, account):
        self._rec("lookup", account)
        return {"full_name": "Ana Reyes", "account_no": account, "status": "active", "plan": "Fiber", "balance": 10, "profile": {}}


class FakeCore:
    def __init__(self, verified=True):
        self._verified = verified
        self.snapshot = type("S", (), {"fatigue": "ALERT", "lock_status": "VERIFIED", "prompt": None, "readings": None})()

    def heartbeat_state(self):
        return "LIVE", True, "ALERT"


class FakeRunner:
    def __init__(self, verified=True, opens=True):
        self.running, self.error, self.core = False, None, None
        self._verified, self._opens = verified, opens
        self.starts = self.stops = 0
        self.frame = None
        self.fps = 0.0

    def start(self, wait_s=8.0):
        self.starts += 1
        if not self._opens:
            self.error = "no camera"
            return False
        self.running, self.core = True, FakeCore()
        return True

    def stop(self):
        self.stops += 1
        self.running = False

    def latest_frame(self):
        return self.frame

    def wait_verified(self, timeout_s=10.0):
        return self._verified


class StubUploader:
    state = "empty"


def make(verified=True, opens=True, outcome=None):
    desk, runner = FakeDesk(), FakeRunner(verified, opens)
    ran = []

    def run_call(call_id, on_turn, end_requested):
        ran.append(call_id)
        on_turn("caller", "Hello po")
        on_turn("agent", "Good morning")
        return outcome or CallOutcome("caller", "satisfied")

    win, alert = DesktopWindow(), AlertWindow(beep=lambda: None)
    c = Controller(desk, runner, win, Tray(), alert, OfflineQueue(":memory:"), StubUploader(), run_call, "Ana",
                   run_async=lambda fn: fn())
    return c, desk, runner, win, alert, ran


def test_the_camera_stays_off_until_the_agent_goes_available(qapp):
    c, desk, runner, *_ = make()
    c.poll()
    assert runner.starts == 0 and not runner.running


def test_going_available_runs_the_recheck_then_the_heartbeat_then_the_status(qapp):
    c, desk, runner, *_ = make()
    c.go_available()
    assert [x[0] for x in desk.calls] == ["heartbeat", "go_available", "heartbeat"]  # the last one learns at once if a call is ringing
    assert desk.calls[0][1:] == ("LIVE", True, "ALERT")  # the server checks this before it allows Available
    assert runner.running and c.message is None


def test_a_failed_recheck_keeps_the_agent_not_ready_and_closes_the_camera(qapp):
    c, desk, runner, *_ = make(verified=False)
    c.go_available()
    assert desk.calls == [] and not runner.running and "confirm" in c.message


def test_a_camera_that_will_not_open_is_explained(qapp):
    c, desk, runner, *_ = make(opens=False)
    c.go_available()
    assert desk.calls == [] and "camera did not open" in c.message and "no camera" in c.message


def test_a_refused_available_closes_the_camera_and_shows_why(qapp):
    c, desk, runner, *_ = make()
    desk.refuse = ApiError(400, "x", "not while a microsleep is flagged")
    c.go_available()
    assert not runner.running and "microsleep" in c.message


def test_break_and_sign_out_states_close_the_camera(qapp):
    c, desk, runner, *_ = make()
    c.go_available()
    assert runner.running
    c.take_break()
    assert desk.calls[-1] == ("not_ready", "break") and not runner.running
    desk.last = {"status": "OFFLINE"}
    runner.running = True
    c.poll()
    assert not runner.running


def test_the_camera_stays_on_through_a_call_and_wrap_up(qapp):
    c, desk, runner, *_ = make()
    c.go_available()
    for status in ("RINGING", "ON_CALL", "WRAP_UP", "SYSTEM_HOLD"):
        desk.last = {"status": status}
        c.poll()
        assert runner.running, status


def test_a_microsleep_raises_the_alert_and_confirming_it_returns_to_work_with_the_camera_on(qapp):
    c, desk, runner, win, alert, _ = make()
    c.go_available()
    c.on_monitor_event(Event(EventType.MICROSLEEP, 1, 700, 0.1, "critical"))
    qapp.processEvents()
    assert alert.active
    desk.last = {"status": "SYSTEM_HOLD", "reason": "microsleep"}
    c.poll()
    assert win.pages.currentIndex() == 4 and "microsleep" in win.hold_why.text()
    desk.calls.clear()
    alert.button.click()  # one click on the full-screen alert
    assert not alert.active
    assert [x[0] for x in desk.calls] == ["heartbeat", "go_available", "heartbeat"]  # straight back to Available...
    assert desk.last["status"] == "AVAILABLE" and runner.running and runner.stops == 0  # ...with the camera still on
    assert c.message is None


def test_the_hold_screen_button_does_the_same_as_the_alert(qapp):
    c, desk, runner, win, alert, _ = make()
    c.go_available()
    desk.last = {"status": "SYSTEM_HOLD", "reason": "microsleep"}
    c.poll()
    desk.calls.clear()
    win.acknowledge.emit()
    assert desk.last["status"] == "AVAILABLE" and runner.running


def test_it_waits_for_the_microsleep_state_to_clear_before_asking_for_available(qapp, monkeypatch):
    c, desk, runner, *_ = make()
    c.go_available()
    states = iter([("LIVE", True, "MICROSLEEP"), ("LIVE", True, "MICROSLEEP"), ("LIVE", True, "ALERT")] + [("LIVE", True, "ALERT")] * 5)
    runner.core.heartbeat_state = lambda: next(states)
    monkeypatch.setattr("controller.time.sleep", lambda s: None)
    desk.last = {"status": "SYSTEM_HOLD"}
    desk.calls.clear()
    c.acknowledge()
    assert desk.calls[0] == ("heartbeat", "LIVE", True, "ALERT")  # nothing was sent while the state was still MICROSLEEP
    assert desk.last["status"] == "AVAILABLE"


def test_when_the_monitor_cannot_confirm_the_agent_falls_back_to_not_ready(qapp, monkeypatch):
    c, desk, runner, *_ = make()
    c.go_available()
    runner.core.heartbeat_state = lambda: ("LIVE", False, "ALERT")  # identity not confirmed
    c.ACK_WAIT_S = 0.0
    desk.last = {"status": "SYSTEM_HOLD"}
    desk.calls.clear()
    c.acknowledge()
    assert desk.calls[-1] == ("not_ready", "acknowledged") and "press Go available" in c.message
    c.poll()
    assert not runner.running  # the camera closes in Not ready


def test_confirming_the_alert_during_a_call_only_closes_it(qapp):
    c, desk, runner, win, alert, _ = make()
    c.go_available()
    desk.last = {"status": "ON_CALL", "call_id": "c1"}
    desk.calls.clear()
    alert.raise_alert("x")
    alert.button.click()
    assert desk.calls == [] and runner.running and not alert.active  # the call goes on; no status change


def test_confirming_with_the_camera_off_does_not_start_anything(qapp):
    c, desk, runner, *_ = make()
    desk.last = {"status": "NOT_READY"}
    c.acknowledge()
    assert desk.calls == [] and runner.starts == 0


def test_an_accepted_call_shows_the_conversation_and_wraps_up(qapp):
    c, desk, runner, win, alert, ran = make()
    c.go_available()
    desk.last = {"status": "RINGING", "call_id": "call-1"}
    c.accept()
    assert ran == ["call-1"] and ("accept", "call-1") in desk.calls
    assert c.transcript == [("caller", "Hello po"), ("agent", "Good morning")]
    desk.last = {"status": "WRAP_UP", "call_id": "call-1"}
    c.lookup("AC-1")
    assert "Ana Reyes" in c.customer_text
    c.poll()
    assert "Ana Reyes" in win.wrap_customer.text()
    c.finish("resolved", "all good")
    assert ("finish", "call-1", "resolved", "all good") in desk.calls
    assert desk.calls[-1][0] == "my_calls"  # and the shift numbers are refreshed right after
    assert c.call_id is None and c.transcript == [] and c.customer_text is None


def test_when_the_agent_ends_the_call_the_server_is_told(qapp):
    c, desk, runner, *_ = make(outcome=CallOutcome("agent"))
    c.go_available()
    desk.last = {"status": "RINGING", "call_id": "call-2"}
    c.accept()
    assert ("end_call", "call-2") in desk.calls


def test_decline_and_an_offer_that_vanished(qapp):
    c, desk, *_ = make()
    desk.last = {"status": "RINGING", "call_id": "call-3"}
    c.decline()
    assert ("decline", "call-3") in desk.calls
    desk.last = {"status": "AVAILABLE"}
    c.accept()  # nothing is ringing: nothing happens
    assert not any(x[0] == "accept" for x in desk.calls)


def test_the_view_counts_the_offline_queue(qapp):
    c, desk, runner, win, *_ = make()
    c.queue.put("event", {"a": 1})
    c.queue.put("event", {"a": 2})
    desk.last = {"status": "AVAILABLE"}
    c.poll()
    assert "2 waiting" in win.footer.text().lower()


def test_the_app_module_imports_and_has_an_entry_point():
    import app

    assert callable(app.main) and app.DATA_DIR.name == "thesis-microsleep"


class LaggyDesk(FakeDesk):
    """Like the real desk: a status change shows up in .last only after the next heartbeat."""

    def go_available(self):
        self._rec("go_available")

    def heartbeat(self, monitor, identity_ok, fatigue):
        self._rec("heartbeat", monitor, identity_ok, fatigue)
        if any(c[0] == "go_available" for c in self.calls):
            self.last = {"status": "AVAILABLE"}
        return self.last


def test_the_camera_is_not_closed_in_the_gap_before_the_status_catches_up(qapp):
    c, _, runner, *_ = make()
    desk = c.desk = LaggyDesk()
    c.go_available()
    assert runner.running and desk.last["status"] == "AVAILABLE"
    c.poll()
    assert runner.running  # the bug: a stale Not ready used to close the camera here, and the server put the agent on hold
    assert runner.stops == 0


def test_the_real_desk_shows_a_status_change_immediately(qapp):
    from bpo.status import AgentDesk
    from test_bpo import FakeApi

    api = FakeApi()
    desk = AgentDesk(api)
    desk.go_available()
    assert desk.last["status"] == "AVAILABLE"
    desk.not_ready("break")
    assert desk.last["status"] == "NOT_READY" and desk.last["reason"] == "break"
    desk.sign_out()
    assert desk.last["status"] == "OFFLINE"


def test_the_chosen_result_is_visibly_selected_and_resets_for_the_next_call(qapp):
    assert "QPushButton:checked" in theme.qss(theme.LIGHT)  # without this the choice looked like it did nothing
    w = DesktopWindow()
    w.show_view(AgentView(status="WRAP_UP"))
    assert w.dispo_buttons["resolved"].isChecked()
    w.dispo_buttons["unresolved"].click()
    assert w.dispo_buttons["unresolved"].isChecked() and not w.dispo_buttons["resolved"].isChecked()
    w.dispo_buttons["unresolved"].click()  # clicking the chosen one again keeps it chosen
    assert w.dispo_buttons["unresolved"].isChecked()
    w.notes.setPlainText("x")
    w.show_view(AgentView(status="WRAP_UP"))  # a refresh during the same wrap-up keeps what was typed
    assert w.dispo_buttons["unresolved"].isChecked() and w.notes.toPlainText() == "x"
    w.show_view(AgentView(status="AVAILABLE"))
    w.show_view(AgentView(status="WRAP_UP"))  # the next call starts clean
    assert w.dispo_buttons["resolved"].isChecked() and w.notes.toPlainText() == ""


def test_a_latched_lock_status_signs_the_agent_out_once_with_a_clear_message(qapp):
    c, desk, runner, *_ = make()
    c.go_available()
    got = []
    c.session_lost.connect(got.append)
    runner.core.snapshot.lock_status = "SPOOF_SUSPECTED"
    c.poll()
    c.poll()
    assert len(got) == 1 and "photo, screen or video" in got[0] and "Sign in again" in got[0]
    runner.core.snapshot.lock_status = "IDENTITY_MISMATCH"
    c2, _, r2, *_ = make()
    c2.go_available()
    seen = []
    c2.session_lost.connect(seen.append)
    r2.core.snapshot.lock_status = "IDENTITY_MISMATCH"
    c2.poll()
    assert seen and "someone other than" in seen[0]


def test_a_refused_request_means_the_face_check_is_gone(qapp):
    c, desk, *_ = make()
    got = []
    c.session_lost.connect(got.append)
    desk.last = {"status": "WRAP_UP", "call_id": "c1"}
    c.call_id = "c1"

    def refuse(*a, **k):
        raise ApiError(403, "42501", "not allowed")

    desk.finish_wrap_up = refuse
    c.finish("resolved", "")
    assert len(got) == 1 and "sign in again" in got[0].lower()
    assert c.message == "not allowed"


def test_an_ordinary_error_does_not_sign_the_agent_out(qapp):
    c, desk, *_ = make()
    got = []
    c.session_lost.connect(got.append)
    desk.last = {"status": "WRAP_UP"}
    c.call_id = "c1"

    def boom(*a, **k):
        raise ApiError(500, "x", "the server is busy")

    desk.finish_wrap_up = boom
    c.finish("resolved", "")
    assert got == [] and "busy" in c.message


def test_a_quick_check_is_shown_as_a_banner_and_beeps_once(qapp, monkeypatch):
    beeps = []
    monkeypatch.setattr("controller.play_ping", lambda: beeps.append(1))
    c, desk, runner, win, *_ = make()
    c.go_available()
    runner.core.snapshot.prompt = "Please blink twice"
    c.poll()
    c.poll()
    assert "Please blink twice" in win.banner.text() and not win.banner.isHidden() and len(beeps) == 1
    runner.core.snapshot.prompt = None
    c.poll()
    assert win.banner.isHidden()


# --- the ping, the window policy and the camera panel -------------------------------------------------------------
def test_the_ping_is_a_real_audible_sound_not_the_system_beep(tmp_path):
    import wave

    from ui.sound import RATE, ping_samples, write_ping

    path = write_ping(tmp_path / "ping.wav")
    with wave.open(str(path)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, RATE)
        seconds = w.getnframes() / w.getframerate()
    assert 0.4 <= seconds <= 0.8
    loud = max(abs(v) for v in ping_samples())
    assert loud > 0.5 * 32767  # clearly audible, not a whisper
    assert abs(ping_samples()[0]) < 1000 and abs(ping_samples()[-1]) < 1000  # faded in and out: no clicks


def test_playing_the_ping_goes_through_the_windows_mixer_and_never_raises(tmp_path, monkeypatch):
    import sys
    import types

    from ui import sound

    if sys.platform != "win32":
        assert sound.play_ping(tmp_path / "p.wav") is False
        return
    calls = []
    fake = types.SimpleNamespace(SND_FILENAME=1, SND_ASYNC=2, PlaySound=lambda target, flags: calls.append((target, flags)))
    monkeypatch.setitem(sys.modules, "winsound", fake)
    assert sound.play_ping(tmp_path / "p.wav") is True
    assert calls[0][0].endswith("p.wav") and calls[0][1] == 3  # a file, asynchronous: it does not freeze the window or cut the caller off

    def boom(*a):
        raise RuntimeError("no sound card")

    monkeypatch.setattr(fake, "PlaySound", boom)
    assert sound.play_ping(tmp_path / "p.wav") is False


def test_the_alert_uses_the_ping_by_default_and_repeats_it(qapp):
    from ui.sound import play_ping

    assert AlertWindow()._beep is play_ping
    beeps = []
    a = AlertWindow(beep=lambda: beeps.append(1), interval_ms=10)
    a.raise_alert()
    assert len(beeps) == 1 and a._timer.isActive()
    a.dismiss()
    assert not a._timer.isActive()


def test_every_window_has_the_same_minimum_size_and_is_resizable(qapp):
    from ui.face_window import FaceWindow
    from ui.window import MIN_SIZE, START_SIZE

    for w in (DesktopWindow(), LoginWindow(), FaceWindow()):
        assert (w.minimumWidth(), w.minimumHeight()) == MIN_SIZE
        assert (w.width(), w.height()) == START_SIZE
        assert w.maximumWidth() > 10_000  # no fixed size: it can be resized and maximized


def test_the_windows_open_maximized_and_f11_toggles_full_screen(qapp):
    from ui.window import show_standard, toggle_full_screen

    w = DesktopWindow()
    calls = []
    w.showMaximized = lambda: calls.append("max")
    w.showFullScreen = lambda: calls.append("full")
    show_standard(w)
    toggle_full_screen(w)
    assert calls == ["max", "full"]


def test_the_camera_panel_shows_the_picture_while_the_camera_is_on(qapp):
    import numpy as np

    w = DesktopWindow()
    assert w.preview.text() == "Camera is off"  # nothing to show yet
    frame = np.full((480, 640, 3), 200, np.uint8)
    w.show_frame(frame)
    assert not w.preview.pixmap().isNull() and w.preview.pixmap().width() <= w.preview.width()
    w.clear_frame()
    assert w.preview.pixmap().isNull() and w.preview.text() == "Camera is off"


def test_the_controller_keeps_the_picture_in_step_with_the_camera(qapp):
    import numpy as np

    c, desk, runner, win, *_ = make()
    c.preview_tick()
    assert win.preview.text() == "Camera is off"
    c.go_available()
    c.preview_tick()
    assert win.preview.text() == "Starting the camera…"  # on, but no frame yet
    runner.frame = np.full((480, 640, 3), 90, np.uint8)
    c.preview_tick()
    assert not win.preview.pixmap().isNull()
    runner.stop()
    c.preview_tick()
    assert win.preview.pixmap().isNull() and win.preview.text() == "Camera is off"


def test_the_caption_under_the_picture_says_what_the_monitor_sees(qapp):
    w = DesktopWindow()
    w.show_view(AgentView(status="AVAILABLE", camera_on=True, fatigue="ALERT", lock_status="VERIFIED"))
    assert w.preview_caption.text().lower() == "alert · verified"
    w.show_view(AgentView(status="NOT_READY", camera_on=False))
    assert w.preview_caption.text() == ""


# --- the same design as the dashboard -----------------------------------------------------------------------------
def test_the_fonts_of_the_dashboard_are_bundled_and_load(qapp):
    families = theme.load_fonts()
    assert {"Geist", "Geist Mono", "Geist Pixel"} <= set(families)


def test_light_and_dark_use_the_dashboards_tokens(qapp):
    assert theme.LIGHT["bg"] == "#ffffff" and theme.LIGHT["ink"] == "#0a0a0a" and theme.LIGHT["g200"] == "#e9e9e9"
    assert theme.DARK["bg"] == "#0c0c0f" and theme.DARK["ink"] == "#f4f4f5" and theme.DARK["g200"] == "#2a2a30"
    assert theme.LIGHT["alarm"] == "#b3402f"
    light, dark = theme.qss(theme.LIGHT), theme.qss(theme.DARK)
    assert theme.LIGHT["bg"] in light and theme.DARK["bg"] in dark and theme.DARK["g50"] not in light and theme.LIGHT["g50"] not in dark
    assert 'font-family: "Geist Pixel"' in light  # titles in the pixel font, like the dashboard


def test_applying_a_theme_installs_it_and_system_follows_the_os(qapp):
    assert theme.apply(qapp, "dark", remember=False) == "dark"
    assert theme.token("bg") == theme.DARK["bg"] and theme.DARK["bg"] in qapp.styleSheet()
    theme.apply(qapp, "light", remember=False)
    assert theme.token("bg") == theme.LIGHT["bg"]
    assert theme.resolved("system", True) is theme.DARK and theme.resolved("system", False) is theme.LIGHT
    assert theme.apply(qapp, "nonsense", remember=False) in theme.CHOICES
    theme.apply(qapp, "light", remember=False)


def test_the_theme_switch_changes_the_whole_app(qapp):
    w = DesktopWindow()
    got = []
    w.theme_changed.connect(got.append)
    w.theme_switch.buttons["dark"].click()
    assert got == ["dark"] and theme.token("bg") == theme.DARK["bg"]
    w.theme_switch.buttons["light"].click()
    assert got == ["dark", "light"] and theme.token("bg") == theme.LIGHT["bg"]


def test_micro_labels_are_uppercase_and_chips_carry_their_tone(qapp):
    from ui.widgets import Chip, MicroLabel

    assert MicroLabel("on shift").text() == "ON SHIFT"
    c = Chip("camera on", "live")
    assert c.text() == "CAMERA ON" and c.property("tone") == "live" and c.property("chip") is True
    c.set("microsleep", "alarm")
    assert c.property("tone") == "alarm"


def test_meters_and_stat_cells(qapp):
    from ui.widgets import Meter, StatGrid

    m = Meter()
    m.set(1.7)
    assert m.ratio == 1.0
    m.set(-3)
    assert m.ratio == 0.0
    m.set(None)
    assert m.ratio == 0.0
    g = StatGrid(["a", "b", "c", "d", "e"], cols=4)
    g.set(4, "9", "hint", "alarm")
    assert g.cells[4].value.text() == "9" and g.cells[4].value.property("tone") == "alarm"
    assert len(g.cells) == 5


# --- the numbers from the camera -----------------------------------------------------------------------------------
def readings(**kw):
    from monitor.core import Readings

    base = dict(ear=0.29, ear_open=0.31, ear_closed=0.12, perclos=0.065, blink_rate=16.0, mar=0.05, mar_yawn=0.6,
                pitch=4.0, yaw=-3.0, roll=1.0, match=0.88, liveness=0.91, faces=1)
    base.update(kw)
    return Readings(**base)


def test_the_desk_shows_what_the_camera_measures(qapp):
    w = DesktopWindow()
    w.show_readings(readings(), fps=29.4)
    assert w.m_eyes.value.text() == "0.29 · open" and 0.85 < w.m_eyes.meter.ratio < 0.95  # between this agent's closed and open
    assert w.m_closure.value.text() == "6.5%"
    assert w.m_blinks.value.text() == "16 per minute"
    assert w.m_mouth.value.text() == "0.05 · closed"
    assert "+4°" in w.m_head.value.text() and "-3°" in w.m_head.value.text() and "+1°" in w.m_head.value.text()
    assert w.m_match.value.text() == "0.88" and w.m_live.value.text() == "0.91"
    assert w.m_fps.value.text() == "29 per second"


def test_the_readings_say_so_when_the_eyes_are_closed_or_the_mouth_is_open(qapp):
    w = DesktopWindow()
    w.show_readings(readings(ear=0.11, eyes_closed=True, mar=0.8))
    assert w.m_eyes.value.text().endswith("closed") and w.m_eyes.meter.ratio == 0.0
    assert w.m_mouth.value.text().endswith("wide open") and w.m_mouth.meter.ratio == 1.0


def test_the_readings_explain_missing_data_instead_of_showing_zeros(qapp):
    w = DesktopWindow()
    w.show_readings(readings(ear=None, perclos=None, blink_rate=None, mar=None, pitch=None, match=None, liveness=None, faces=0))
    assert w.m_eyes.value.text() == "–" and w.m_head.value.text() == "–" and w.m_match.value.text() == "–"
    assert "No face" in w.metrics_note.text()
    w.show_readings(readings(faces=2))
    assert "2 faces" in w.metrics_note.text()
    w.show_readings(readings(looking_down=True))
    assert "Looking down" in w.metrics_note.text()
    w.show_readings(None)
    assert w.m_eyes.value.text() == "–" and "Go available" in w.metrics_note.text()


def test_the_monitor_reports_head_angles_relative_to_the_agents_calibrated_pose(qapp):
    from types import SimpleNamespace

    from monitor.core import MonitorCore
    from storage.queue import OfflineQueue
    from vision.calibration import Calibration
    from vision.drowsiness import FatigueState
    from vision.pipeline import Measurement

    cal = Calibration(ear_open=0.32, ear_closed=0.10, neutral_pitch=10.0, neutral_yaw=-4.0, neutral_roll=2.0)

    class P:
        engine = SimpleNamespace(state=FatigueState.ALERT, cal=cal, metrics=lambda: {"blink_rate": 14.0, "perclos": 0.08, "eyes_closed": False})

        def process(self, frame, ts, recent):
            return Measurement(ts_ms=ts, face_count=1, ear_smooth=0.3, mar=0.04, pitch=15.0, yaw=-1.0, roll=2.0, lock_status="VERIFIED", lock_score=0.9, lock_liveness=0.95), []

    import numpy as np

    core = MonitorCore(P(), OfflineQueue(":memory:"), session_id=lambda: "s")
    core.process(np.zeros((4, 4, 3), np.uint8), 0)
    r = core.snapshot.readings
    assert (r.pitch, r.yaw, r.roll) == (5.0, 3.0, 0.0)
    assert (r.ear_open, r.ear_closed, r.blink_rate, r.perclos, r.match, r.liveness) == (0.32, 0.10, 14.0, 0.08, 0.9, 0.95)


# --- this shift ----------------------------------------------------------------------------------------------------
def test_shift_stats_from_the_agents_own_calls():
    from bpo.shift import ShiftStats, shift_stats

    rows = [
        {"status": "COMPLETED", "answered_at": "2026-11-04T10:00:00+00:00", "ended_at": "2026-11-04T10:05:00+00:00", "disposition": "resolved"},
        {"status": "COMPLETED", "answered_at": "2026-11-04T10:10:00Z", "ended_at": "2026-11-04T10:17:00Z", "disposition": "escalated"},
        {"status": "ABANDONED", "answered_at": None, "ended_at": None, "disposition": None},
        {"status": "ACTIVE", "answered_at": "2026-11-04T10:20:00Z", "ended_at": None, "disposition": None},
    ]
    s = shift_stats(rows)
    assert (s.calls, s.resolved, s.escalated, s.unresolved) == (2, 1, 1, 0)
    assert s.avg_talk_s == 360 and s.resolved_share == 0.5
    assert shift_stats([]) == ShiftStats() and shift_stats([]).resolved_share is None


def test_the_desk_shows_the_shift_and_the_recent_events(qapp):
    from bpo.shift import ShiftStats

    w = DesktopWindow()
    w.show_view(AgentView(
        status="AVAILABLE", name="Ana", shift_seconds=3725, shift=ShiftStats(calls=4, avg_talk_s=300, resolved=3, unresolved=1),
        counts={"MICROSLEEP": 2, "YAWN": 3, "HEAD_NOD": 1, "HEAD_TILT": 2, "LONG_BLINK": 5},
        events=[("14:02", "MICROSLEEP", 700), ("13:40", "YAWN", 2400)],
    ))
    g = w.shift_grid
    assert [c.value.text() for c in g.cells] == ["4", "5:00", "75%", "1:02:05", "2", "3", "3", "5"]
    assert g.cells[4].value.property("tone") == "alarm" and g.cells[5].value.property("tone") != "alarm"  # only microsleeps are red
    assert w.events_lay.count() == 3  # two rows and one hairline between them
    w.show_view(AgentView(status="AVAILABLE", name="Ana"))
    assert w.events_lay.count() == 1 and "Nothing unusual" in w.events_lay.itemAt(0).widget().text()
    assert w.shift_grid.cells[2].value.text() == "–"


def test_the_controller_counts_events_for_the_shift_and_lists_the_latest(qapp):
    c, desk, runner, win, *_ = make()
    for kind in ("YAWN", "YAWN", "MICROSLEEP"):
        c.on_monitor_event(Event(EventType[kind], 1, 700 if kind == "MICROSLEEP" else 2400))
    v = c.build_view()
    assert v.counts == {"YAWN": 2, "MICROSLEEP": 1}
    assert [e[1] for e in v.events] == ["MICROSLEEP", "YAWN", "YAWN"]  # newest first
    for _ in range(12):
        c.on_monitor_event(Event(EventType.HEAD_NOD, 1, 0))
    assert len(c.build_view().events) == 8  # only the latest few


def test_the_controller_reads_the_agents_calls_for_the_shift_stats(qapp):
    c, desk, *_ = make()
    desk.rows = [{"status": "COMPLETED", "answered_at": "2026-11-04T10:00:00Z", "ended_at": "2026-11-04T10:04:00Z", "disposition": "resolved"}]
    c.refresh_shift()
    assert desk.calls[-1][0] == "my_calls" and "T" in desk.calls[-1][1]
    assert c.build_view().shift.calls == 1 and c.build_view().shift.avg_talk_s == 240

    def down(_since):
        raise ApiError(0, "network_error", "offline")

    desk.my_calls = down
    c.refresh_shift()  # a missing network is not an error on screen
    assert c.message is None and c.build_view().shift.calls == 1


def test_the_controller_feeds_the_camera_numbers_to_the_desk(qapp):
    c, desk, runner, win, *_ = make()
    c.preview_tick()
    c.preview_tick()
    assert win.m_eyes.value.text() == "–"  # camera off
    c.go_available()
    runner.fps = 28.0
    runner.core.snapshot.readings = readings()
    c.preview_tick()
    c.preview_tick()
    assert win.m_eyes.value.text() == "0.29 · open" and win.m_fps.value.text() == "28 per second"


def test_the_real_desk_asks_for_its_own_calls_only(qapp):
    from bpo.status import AgentDesk
    from test_bpo import FakeApi

    class Selecting(FakeApi):
        def select(self, table, params):
            self.sent.append((table, params))
            return [{"status": "COMPLETED"}]

    api = Selecting()
    rows = AgentDesk(api).my_calls("2026-11-04T10:00:00+00:00")
    table, params = api.sent[0]
    assert table == "calls" and params["queued_at"] == "gte.2026-11-04T10:00:00+00:00" and "agent_id" not in params  # row level security already limits it to their own
    assert rows == [{"status": "COMPLETED"}]


def test_the_desk_shows_the_light_on_the_face_and_warns_when_it_is_dim(qapp):
    w = DesktopWindow()
    w.show_readings(readings(brightness=140.0))
    assert w.m_light.value.text() == "140 · good" and "dim" not in w.metrics_note.text()
    w.show_readings(readings(brightness=90.0))
    assert w.m_light.value.text() == "90 · dim"
    w.show_readings(readings(brightness=60.0))
    assert w.m_light.value.text() == "60 · too dark" and "More light" in w.metrics_note.text()
    w.show_readings(readings(brightness=None))
    assert w.m_light.value.text() == "–"
    w.show_readings(None)
    assert w.m_light.value.text() == "–"


def test_going_available_tells_the_agent_what_is_happening_without_a_ping(qapp, monkeypatch):
    pings = []
    monkeypatch.setattr("controller.play_ping", lambda: pings.append(1))
    c, desk, runner, win, *_ = make()
    c._checking = True
    runner.start()
    v = c.build_view()
    assert v.prompt and "Checking that it is you" in v.prompt  # a plain banner while the check runs
    c.poll()
    assert pings == []  # no sound for the plain check
    runner.core.snapshot.prompt = "Please blink twice"
    assert c.build_view().prompt == "Please blink twice"  # a real quick check replaces it...
    c.poll()
    assert pings == [1]  # ...and that one does ping, once
    c.poll()
    assert pings == [1]


def test_the_recheck_allows_time_for_a_quick_check(qapp):
    import controller

    assert controller.RECHECK_S >= 20


# --- who may speak, and the microphone choice ---
def test_desktop_shows_whose_turn_it_is_quietly(qapp):
    w = DesktopWindow()
    expect = {"caller": ("wait", "CALLER SPEAKING"), "listening": ("ready", "YOUR TURN"),
              "hearing": ("speaking", "YOU"), "thinking": ("wait", "CALLER REPLYING")}
    for state, (mode, word) in expect.items():
        w.show_view(AgentView(status="ON_CALL", call_state=state))
        assert w.voice_dot.mode == mode and w.turn_label.text() == word, state
    assert not hasattr(w, "turn_text")  # no big banner any more


def test_microphone_list_remembers_the_choice_and_locks_during_calls(qapp):
    w = DesktopWindow()
    w.set_microphones(["Headset (USB)", "Laptop mic"], "Laptop mic", "Laptop mic")
    assert w.mic_box.currentData() == "Laptop mic" and w.mic_box.count() == 3
    picked = []
    w.mic_changed.connect(picked.append)
    w.mic_box.setCurrentIndex(1)
    w._on_mic_picked(1)
    assert picked == ["Headset (USB)"]
    w.show_view(AgentView(status="ON_CALL"))
    assert not w.mic_box.isEnabled()
    w.show_view(AgentView(status="AVAILABLE"))
    assert w.mic_box.isEnabled()
    w.set_microphones(["Laptop mic"], "Unplugged headset")  # a saved choice that is gone falls back to the default
    assert w.mic_box.currentData() == ""
