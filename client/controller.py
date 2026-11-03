"""Connects the agent's desk window to everything behind it: the status RPCs, the camera, the offline queue,
the alert and the voice call. The window only shows things and emits signals; every decision is here.

Rules it carries out:
  - the camera opens only when the agent goes Available (after a short "is it really you" re-check) and
    closes again in Not ready, on a break and when signed out
  - Available is only requested after the monitor reports LIVE and the identity confirmed
  - a microsleep raises the full-screen alert; the server then puts the agent On hold until they confirm
  - a call keeps going when something goes wrong; the hold comes after wrap-up (the database does that)
"""
from __future__ import annotations

import threading
import time
from collections import Counter, deque
from datetime import datetime, timezone
from typing import Callable, Protocol

from PySide6.QtCore import QObject, QTimer, Signal

from auth.supabase_client import ApiError
from bpo import customer_panel
from bpo.shift import shift_stats
from bpo.call_session import CallOutcome
from monitor.runner import CameraGate, camera_wanted
from ui.desktop_window import AgentView, DesktopWindow
from ui.sound import play_ping
from vision.drowsiness import Event, EventType

POLL_MS = 500
PREVIEW_MS = 100
RECHECK_S = 25.0  # a quick check in progress is waited for, up to MAX_CHECK_S in the runner

# (call_id, on_turn, end_requested) -> outcome
CallRunner = Callable[[str, Callable[[str, str], None], Callable[[], bool]], CallOutcome]


class DeskLike(Protocol):
    last: dict

    def heartbeat(self, monitor: str, identity_ok: bool, fatigue: str) -> dict: ...
    def go_available(self) -> None: ...
    def not_ready(self, reason: str = ...) -> None: ...
    def accept(self, call_id: str) -> None: ...
    def decline(self, call_id: str) -> None: ...
    def end_call(self, call_id: str) -> None: ...
    def finish_wrap_up(self, call_id: str, disposition: str, notes: str | None = ...) -> None: ...
    def my_calls(self, since_iso: str) -> list[dict]: ...
    def lookup_customer(self, account: str) -> dict | None: ...


def say(exc: Exception) -> str:
    return (exc.message or exc.code) if isinstance(exc, ApiError) else f"{exc.__class__.__name__}: {exc}"


class Controller(QObject):
    alert_requested = Signal(str)
    refresh_requested = Signal()
    sign_out_requested = Signal()
    session_lost = Signal(str)  # the server withdrew the face check / login: back to sign-in with this message

    def __init__(self, desk: DeskLike, runner, window: DesktopWindow, tray, alert, queue, uploader,
                 run_call: CallRunner, name: str, run_async: Callable[[Callable[[], None]], None] | None = None) -> None:
        super().__init__()
        self.desk, self.runner, self.window, self.tray, self.alert = desk, runner, window, tray, alert
        self.queue, self.uploader, self.run_call, self.name = queue, uploader, run_call, name
        self.gate = CameraGate(runner)
        self._async = run_async or self._thread
        self.call_id: str | None = None
        self.transcript: list[tuple[str, str]] = []
        self.message: str | None = None
        self.customer_text: str | None = None
        self._checking = False  # the Go-available re-check is running: the camera is on while still Not ready
        self._in_call = False
        self._end_flag = False
        self._lost = False
        self._prompting = False
        self.started_at = time.time()  # the shift starts when the desk opens
        self.event_counts: Counter[str] = Counter()  # fatigue events this shift, by type
        self.recent: deque[tuple[str, str, int]] = deque(maxlen=8)  # (time, type, duration_ms), newest first
        self.shift_rows: list[dict] = []  # this agent's calls since the shift began
        self._tick = 0
        self._shift_timer = QTimer(self)
        self._shift_timer.timeout.connect(self.refresh_shift)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll)
        self._preview_timer = QTimer(self)  # the camera picture, about ten times a second
        self._preview_timer.timeout.connect(self.preview_tick)

        window.go_available.connect(self.go_available)
        window.take_break.connect(self.take_break)
        window.sign_out.connect(self.sign_out_requested)
        window.accept.connect(self.accept)
        window.decline.connect(self.decline)
        window.end_call.connect(self.end_call)
        window.acknowledge.connect(self.acknowledge)
        window.lookup.connect(self.lookup)
        window.finish.connect(self.finish)
        alert.confirmed.connect(self.acknowledge)  # one click on the alert is enough: it also returns the agent to work
        self.alert_requested.connect(self.alert.raise_alert)
        self.refresh_requested.connect(self.poll)

    # --- plumbing ------------------------------------------------------------------------------
    @staticmethod
    def _thread(fn: Callable[[], None]) -> None:
        threading.Thread(target=fn, daemon=True).start()

    def start(self) -> None:
        self._timer.start(POLL_MS)
        self._preview_timer.start(PREVIEW_MS)
        self._shift_timer.start(30_000)
        self.refresh_shift()
        self.poll()

    def stop(self) -> None:
        self._timer.stop()
        self._preview_timer.stop()
        self._shift_timer.stop()

    def preview_tick(self) -> None:
        """The picture is shown whenever the camera is on (also during the "is it you" check), and a plain note when it is off."""
        frame = self.runner.latest_frame() if self.runner.running else None
        if frame is not None:
            self.window.show_frame(frame)
        elif not self.runner.running:
            self.window.clear_frame()
        else:
            self.window.clear_frame("Starting the camera…")
        self._tick += 1
        if self._tick % 2 == 0:  # the numbers about five times a second: fast enough to follow, calm enough to read
            core = self.runner.core
            readings = core.snapshot.readings if self.runner.running and core is not None else None
            self.window.show_readings(readings, getattr(self.runner, "fps", 0.0))

    def refresh_shift(self) -> None:
        """Read this agent's own calls for the shift stats. Quietly skipped when the network is away."""
        since = datetime.fromtimestamp(self.started_at, timezone.utc).isoformat()

        def work() -> None:
            try:
                self.shift_rows = self.desk.my_calls(since)
            except Exception:  # noqa: BLE001 - statistics are a nicety: never an error on screen
                return
            self.refresh_requested.emit()

        self._async(work)

    def lose_session(self, why: str) -> None:
        if not self._lost:
            self._lost = True
            self.session_lost.emit(why)

    def _safely(self, fn: Callable[[], None]) -> Callable[[], None]:
        def wrapped() -> None:
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - shown to the agent instead of killing the thread
                self.message = say(exc)
                if isinstance(exc, ApiError) and exc.status in (401, 403):
                    self.lose_session("Your face check is no longer valid. Please sign in again.")
            self.refresh_requested.emit()
        return wrapped

    # --- what the monitor thread tells us ------------------------------------------------------
    def on_monitor_event(self, event: Event) -> None:
        self.event_counts[event.type.value] += 1
        self.recent.appendleft((time.strftime("%H:%M"), event.type.value, int(event.duration_ms)))
        if event.type is EventType.MICROSLEEP:
            self.alert_requested.emit("Your eyes were closed for a moment. Confirm to continue.")
        elif event.type is EventType.IDENTITY_MISMATCH:
            self.message = "The monitor does not recognise the person at the desk."

    # --- the view ------------------------------------------------------------------------------
    def build_view(self) -> AgentView:
        last = self.desk.last or {}
        status = last.get("status") or "OFFLINE"
        snap = self.runner.core.snapshot if self.runner.core is not None and self.runner.running else None
        seconds = None
        if last.get("offer_expires_at"):
            try:
                end = datetime.fromisoformat(str(last["offer_expires_at"]).replace("Z", "+00:00"))
                seconds = int((end - datetime.now(timezone.utc)).total_seconds())
            except ValueError:
                pass
        prompt = snap.prompt if snap and snap.prompt else ("Look at the camera. Checking that it is you…" if self._checking and self.runner.running else None)
        return AgentView(
            prompt=prompt,
            status=status, reason=last.get("reason"), name=self.name,
            camera_on=self.runner.running, fatigue=snap.fatigue if snap else "ALERT",
            lock_status=snap.lock_status if snap else None,
            queued=self.queue.count(), sync=getattr(self.uploader, "state", "empty"),
            offer_seconds=seconds, message=self.message, transcript=list(self.transcript),
            customer_text=self.customer_text,
            shift_seconds=int(time.time() - self.started_at), shift=shift_stats(self.shift_rows),
            counts=dict(self.event_counts), events=list(self.recent),
        )

    LATCHED = {"SPOOF_SUSPECTED": "The monitor suspected a photo, screen or video instead of a live face.",
               "IDENTITY_MISMATCH": "The monitor saw someone other than the signed-in agent."}

    def poll(self) -> None:
        snap = self.runner.core.snapshot if self.runner.core is not None and self.runner.running else None
        if snap is not None and snap.lock_status in self.LATCHED:
            # the server revokes the face check for these, so there is nothing more to do here: sign in again
            self.lose_session(f"{self.LATCHED[snap.lock_status]} You were signed out. Sign in again to continue.")
            return
        status = (self.desk.last or {}).get("status")
        if status and not self._checking and not camera_wanted(status):
            self.gate.sync(status)  # break, not ready, signed out: the camera closes
        view = self.build_view()
        asked = bool(snap is not None and snap.prompt)  # a real quick check (not just the plain "checking" banner)
        if asked and not self._prompting:
            play_ping()  # once, when the quick check starts
        self._prompting = asked
        self.window.show_view(view)
        self.tray.set_state(view.status, view.camera_on, view.fatigue == "MICROSLEEP" or view.status == "SYSTEM_HOLD")

    # --- actions from the window ---------------------------------------------------------------
    def go_available(self) -> None:
        if self._checking:
            return
        self._checking = True
        self.message = "Checking that it is you…"
        self.poll()

        def work() -> None:
            try:
                if not self.runner.start():
                    self.message = f"The camera did not open ({self.runner.error or 'unknown problem'})."
                    return
                if not self.runner.wait_verified(RECHECK_S):
                    self.runner.stop()
                    self.message = "I could not confirm that it is you. Face the camera and try again."
                    return
                self.desk.heartbeat(*self.runner.core.heartbeat_state())
                self.desk.go_available()
                self.desk.heartbeat(*self.runner.core.heartbeat_state())  # learn at once if a call is already ringing
                self.message = None
            except Exception as exc:  # noqa: BLE001
                self.runner.stop()
                self.message = say(exc)
            finally:
                self._checking = False

        self._async(self._safely(work))

    def take_break(self) -> None:
        def work() -> None:
            self.desk.not_ready("break")
            self.message = None
        self._async(self._safely(work))

    ACK_WAIT_S = 8.0  # how long to wait for the "microsleep" state to clear after the eyes reopen (it holds for 5 s)

    def acknowledge(self) -> None:
        """"I am awake": close the alert and go straight back to work with the camera still on. Only if the monitor cannot
        confirm the agent is alert and identified does the agent end up Not ready (camera off), as a safe fallback."""
        self.alert.dismiss()
        status = (self.desk.last or {}).get("status")
        if status not in ("SYSTEM_HOLD", "AVAILABLE"):
            return  # in a call the alert is just closed (the call goes on); on a break there is nothing to return to

        def work() -> None:
            self.message = "Checking that you are alert…"
            if self._back_to_available():
                self.message = None
                return
            self.desk.not_ready("acknowledged")
            self.message = "The monitor could not confirm that you are ready. Face the camera and press Go available."

        self._async(self._safely(work))

    def _back_to_available(self) -> bool:
        core = self.runner.core
        if not self.runner.running or core is None:
            return False
        end = time.monotonic() + self.ACK_WAIT_S
        while True:
            monitor, identity_ok, fatigue = core.heartbeat_state()
            if monitor == "LIVE" and identity_ok and fatigue != "MICROSLEEP":
                self.desk.heartbeat(monitor, identity_ok, fatigue)
                self.desk.go_available()
                self.desk.heartbeat(monitor, identity_ok, fatigue)
                return True
            if time.monotonic() >= end:
                return False
            time.sleep(0.2)

    def accept(self) -> None:
        call_id = (self.desk.last or {}).get("call_id")
        if not call_id or self._in_call:
            return
        self._in_call, self._end_flag = True, False
        self.call_id, self.transcript, self.customer_text = call_id, [], None

        def on_turn(speaker: str, text: str) -> None:
            self.transcript.append((speaker, text))
            self.refresh_requested.emit()

        def work() -> None:
            try:
                self.desk.accept(call_id)
                outcome = self.run_call(call_id, on_turn, lambda: self._end_flag)
                if outcome.ended_by == "agent":
                    self.desk.end_call(call_id)
            finally:
                self._in_call = False

        self._async(self._safely(work))

    def decline(self) -> None:
        call_id = (self.desk.last or {}).get("call_id")
        if call_id:
            self._async(self._safely(lambda: self.desk.decline(call_id)))

    def end_call(self) -> None:
        self._end_flag = True

    def finish(self, disposition: str, notes: str) -> None:
        call_id = self.call_id
        if not call_id:
            return

        def work() -> None:
            self.desk.finish_wrap_up(call_id, disposition, notes or None)
            self.call_id, self.transcript, self.customer_text, self.message = None, [], None, None
            self.refresh_shift()
        self._async(self._safely(work))

    def lookup(self, account: str) -> None:
        if not account:
            return

        def work() -> None:
            self.customer_text = customer_panel.format_customer(self.desk.lookup_customer(account))
        self._async(self._safely(work))
