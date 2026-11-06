"""The agent desktop app (milestone 6):  python app.py   (or the packaged call-desk.exe)

Pair this PC (first time), sign in with email + password, pass the face check, then work the desk. The
camera is on only while Available / on a call / in wrap-up / on hold. Events and metrics go to an offline
queue first and are uploaded whenever the network and the login allow.
"""
from __future__ import annotations

import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from auth import face_mfa, login, workstation
from auth.supabase_client import Api, ApiError
from bpo.audio import devices as audio_devices
from bpo.audio.io import Microphone, Speaker
from bpo.audio.stt import Transcriber
from bpo.audio.tts import EdgeTtsSynthesizer
from bpo.call_session import CallSession
from bpo.status import AgentDesk, HeartbeatThread
from capture.camera import Camera
from config import ConfigError, load_config
from controller import Controller
from identity.embedder import ArcFaceOnnx
from identity.enroll import EnrollmentError
from identity.liveness import PassiveLiveness
from identity.lock import IdentityLock, LockConfig
from monitor.core import MonitorCore
from monitor.reference import SessionReference
from monitor.runner import MonitorRunner
from monitor.trace import FileTrace
from storage.queue import OfflineQueue
from sync.uploader import Uploader, UploaderThread
from ui import theme
from ui.alert_window import AlertWindow
from ui.desktop_window import DesktopWindow
from ui.face_window import FaceWindow
from ui.login_window import LoginWindow
from ui.tray import Tray
from ui.window import show_standard
from vision.calibration import Calibration, CalibrationError
from vision.drowsiness import DrowsinessEngine
from vision.landmarks import FaceLandmarker
from vision.pipeline import FramePipeline

DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "thesis-microsleep"

CONSENT = (
    "To check that it is really you, this system keeps a numeric template of your face (not photos) on a server, "
    "encrypted. It is used only to confirm your identity while you work and to monitor your alertness. Your team leader "
    "can see whether your face checks passed. You can ask your team leader to delete your data at any time.\n\n"
    "(Draft wording, to be reviewed before real use.)\n\nDo you agree to enroll your face?"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_my_calibration(api: Api) -> Calibration | None:
    rows = api.select("calibration_profiles", {"agent_id": f"eq.{api.user_id}"})
    if not rows:
        return None
    keep = {k: rows[0][k] for k in Calibration.__dataclass_fields__ if k in rows[0] and rows[0][k] is not None}
    return Calibration(**keep)


class App(QObject):
    def __init__(self, qapp: QApplication, api: Api) -> None:
        super().__init__()
        self.qapp, self.api = qapp, api
        self.login = LoginWindow()
        self.face_win = FaceWindow()
        self.models: tuple | None = None
        self.desk_parts: dict = {}
        self.login.pair.connect(self.on_pair)
        self.login.sign_in.connect(self.on_sign_in)
        self.login.new_password.connect(self.on_new_password)
        self.ws = workstation.load()
        if self.ws:
            api.workstation_token = self.ws.token

    def run(self) -> None:
        self.login.show_page(LoginWindow.SIGN_IN if self.ws else LoginWindow.PAIR)
        show_standard(self.login)

    # --- pairing and sign-in --------------------------------------------------------------------
    def on_pair(self, code: str) -> None:
        try:
            self.ws = workstation.pair(self.api, code)
        except ApiError as exc:
            self.login.show_error(exc.message or exc.code)
            return
        self.login.show_page(LoginWindow.SIGN_IN)

    def on_sign_in(self, email: str, password: str) -> None:
        self.login.show_busy("Signing in…")
        self.qapp.processEvents()
        try:
            self.account = login.sign_in(self.api, email, password)
        except (ApiError, login.NotAnAgent) as exc:
            self.login.show_page(LoginWindow.SIGN_IN)
            self.login.show_error(getattr(exc, "message", "") or getattr(exc, "code", "") or str(exc))
            return
        self.login.clear_secrets()
        if self.account.must_change_password:
            self.login.show_page(LoginWindow.NEW_PASSWORD)
        else:
            self.face_stage()

    def on_new_password(self, new: str) -> None:
        problems = login.password_problems(new)
        if problems:
            self.login.show_error(", ".join(problems))
            return
        try:
            login.change_password(self.api, new)
        except ApiError as exc:
            self.login.show_error(exc.message or exc.code)
            return
        self.login.clear_secrets()
        self.face_stage()

    # --- the face check -----------------------------------------------------------------------------
    def load_models(self) -> tuple:
        if self.models is None:
            self.models = (ArcFaceOnnx(), PassiveLiveness())
        return self.models

    def face_stage(self) -> None:
        from tools.identity_demo import UserQuit, capture_profile, frames  # the guided enrollment capture is shared

        self.login.show_busy("Getting ready…", "Loading the face models.")
        self.qapp.processEvents()
        arc, live = self.load_models()
        holder: dict = {}
        verified = False
        try:
            with Camera(None) as cam, FaceLandmarker() as lm:
                self.login.hide()
                self.face_win.begin()
                for attempt in range(1, 4):
                    calibration = load_my_calibration(self.api)

                    def on_frame(frame, challenge, status, n_faces, live_score) -> bool:
                        prompt = challenge.prompt if n_faces == 1 else ("Only you should be in view" if n_faces else "I cannot see your face")
                        detail = "" if live_score is None else f"liveness {live_score:.2f}"
                        return self.face_win.show_frame(frame, prompt, detail, warn=n_faces != 1)

                    def evidence(actions, calibration=calibration):
                        holder["ev"] = face_mfa.collect_evidence(frames(cam), lm, arc, live, actions, calibration, on_frame)
                        return holder["ev"]

                    try:
                        result = face_mfa.run_face_mfa(self.api, evidence)
                    except ApiError as exc:
                        if exc.code == "not_enrolled":
                            self.face_win.hide()
                            if not self.enroll(cam, lm, arc, capture_profile):
                                break
                            self.face_win.begin()
                            continue
                        if exc.code == "face_locked":
                            wait = int(exc.extra.get("retry_after_seconds", 0))
                            self.fail(f"Face sign-in is locked after too many failed checks. Try again in {wait // 60 + 1} minutes.")
                            return
                        raise
                    if self.face_win.cancelled:
                        break
                    if result.verified:
                        verified = True
                        break
                    self.face_win.prompt.setText(f"Face check failed ({result.reason}). Attempt {attempt} of 3.")
                    self.qapp.processEvents()
        except UserQuit:
            self.fail("Face check cancelled.")
            return
        except (ApiError, EnrollmentError, CalibrationError, ValueError, RuntimeError) as exc:
            self.fail(getattr(exc, "message", "") or str(exc))
            return
        self.face_win.hide()
        if not verified:
            self.fail("The face check did not pass.")
            return
        self.start_desk(SessionReference(holder["ev"].embeddings), load_my_calibration(self.api))

    def enroll(self, cam, lm, arc, capture_profile) -> bool:
        if QMessageBox.question(None, "Enroll your face", CONSENT) != QMessageBox.Yes:
            self.fail("Enrollment needs your consent. Nothing was stored.")
            return False
        code, ok = QInputDialog.getText(None, "Enrollment code", "Enrollment code from your team leader:")
        if not ok or not code.strip():
            return False
        profile = capture_profile(cam, lm, arc, False)
        face_mfa.upload_enrollment(self.api, profile, code.strip(), consent=True)
        return True

    def fail(self, text: str) -> None:
        self.face_win.hide()
        self.login.show_page(LoginWindow.SIGN_IN)
        self.login.show_error(text)
        show_standard(self.login)
        self.api.sign_out()

    # --- the desk -----------------------------------------------------------------------------------
    def start_desk(self, reference: SessionReference, calibration: Calibration | None) -> None:
        arc, live = self.load_models()
        queue = OfflineQueue(DATA_DIR / "queue.sqlite")
        session_id = str(uuid.uuid4())
        queue.put("session_open", {"id": session_id, "workstation_id": self.ws.id})
        uploader = UploaderThread(Uploader(self.api, queue))
        uploader.start()
        desk = AgentDesk(self.api)
        desk.sign_in_ready()

        window, alert, tray = DesktopWindow(), AlertWindow(), Tray()
        holder: dict = {}
        landmarkers: list[FaceLandmarker] = []

        def make_core() -> MonitorCore:
            for old in landmarkers:  # a new camera session restarts the frame clock: use a fresh landmarker
                old.close()
            landmarkers.clear()
            lm = FaceLandmarker()
            landmarkers.append(lm)
            lock = IdentityLock(reference.score, LockConfig())
            pipeline = FramePipeline(lm, DrowsinessEngine(calibration or Calibration()), lock=lock, embedder=arc, liveness=live)
            return MonitorCore(pipeline, queue, session_id=lambda: session_id, call_id=lambda: holder["c"].call_id,
                               status=lambda: desk.last.get("status"), on_event=lambda e: holder["c"].on_monitor_event(e),
                               trace=trace)

        trace = FileTrace(DATA_DIR / "monitor.log")
        runner = MonitorRunner(make_core, lambda: Camera(None))

        def run_call(call_id, on_turn, end_requested):
            number = audio_devices.device_number(audio_devices.saved_name())  # None = the system default
            try:
                mic = Microphone(number).__enter__()
            except Exception:  # noqa: BLE001 - the chosen microphone vanished or refused: fall back to the default
                mic = Microphone().__enter__()
            try:
                speaker = Speaker(mic)
                session = CallSession(self.api, call_id, mic, Transcriber(self.api), EdgeTtsSynthesizer(),
                                      lambda samples, rate: speaker.play(samples, rate, cancel=end_requested),
                                      end_requested=end_requested, on_turn=on_turn,
                                      on_state=lambda s: holder["c"].set_call_state(s))
                return session.run()
            finally:
                mic.__exit__(None, None, None)

        def fill_microphones() -> None:
            window.set_microphones([n for _, n in audio_devices.input_devices()], audio_devices.saved_name(),
                                   audio_devices.default_input_name())

        fill_microphones()
        window.mic_refresh.connect(fill_microphones)
        window.mic_changed.connect(lambda name: audio_devices.save_name(name or None))

        controller = holder["c"] = Controller(desk, runner, window, tray, alert, queue, uploader, run_call, self.account.full_name)
        heartbeat = HeartbeatThread(
            desk, monitor=lambda: runner.core.heartbeat_state() if runner.running and runner.core else ("OFF", False, "ALERT"),
            interval_s=1.0, on_denied=lambda why: controller.lose_session("Your face check is no longer valid. Please sign in again."))
        heartbeat.start()
        window.to_tray = True
        tray.open_desk.connect(window.show)
        tray.go_available.connect(controller.go_available)
        tray.take_break.connect(controller.take_break)
        tray.sign_out.connect(controller.sign_out_requested)
        tray.quit.connect(controller.sign_out_requested)
        controller.session_lost.connect(lambda why: self.lose_shift(why, controller, runner, heartbeat, uploader, queue, session_id, window, alert, tray))
        controller.sign_out_requested.connect(lambda: self.end_shift(controller, runner, desk, heartbeat, uploader, queue, session_id, window, alert, tray))
        tray.show()
        show_standard(window)
        controller.start()
        threading.Thread(target=desk.warm_up, daemon=True).start()

    def lose_shift(self, why, controller, runner, heartbeat, uploader, queue, session_id, window, alert, tray) -> None:
        """The server withdrew the face check (or the login). Close everything, keep what could not be uploaded
        in the offline queue (it goes out after the next sign-in), and return to the sign-in window."""
        controller.stop()
        runner.stop()
        heartbeat.stop()
        queue.put("session_close", {"id": session_id, "ended_at": now_iso()})
        uploader.stop(final_flush=False)
        self.api.sign_out()
        alert.dismiss()
        window.to_tray = False
        window.close()
        tray.icon.hide()
        queue.close()
        self.login.show_page(LoginWindow.SIGN_IN)
        self.login.show_error(why)
        show_standard(self.login)

    def end_shift(self, controller, runner, desk, heartbeat, uploader, queue, session_id, window, alert, tray) -> None:
        controller.stop()
        runner.stop()
        heartbeat.stop()
        try:
            desk.sign_out()
        except ApiError:
            pass
        queue.put("session_close", {"id": session_id, "ended_at": now_iso()})
        uploader.stop(final_flush=True)
        self.api.sign_out()
        alert.dismiss()
        window.to_tray = False
        window.close()
        tray.icon.hide()
        queue.close()
        self.qapp.quit()


def main() -> int:
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(exc)
        return 1
    qapp = QApplication(sys.argv)
    theme.apply(qapp)  # fonts, colours and light/dark: the same design as the team-leader dashboard
    theme.follow_system(qapp)
    qapp.setQuitOnLastWindowClosed(False)
    try:
        app = App(qapp, Api(cfg.supabase_url, cfg.publishable_key))
        app.run()
        return qapp.exec()
    except Exception:  # noqa: BLE001 - a windowed exe has no console: leave a trace the owner can read
        import traceback

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "crash.log").write_text(traceback.format_exc(), encoding="utf-8")
        QMessageBox.critical(None, "Call desk", f"The app hit a problem and has to close. Details: {DATA_DIR / 'crash.log'}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
