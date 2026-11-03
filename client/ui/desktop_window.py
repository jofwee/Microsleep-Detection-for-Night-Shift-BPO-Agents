"""The agent desktop, in the same design as the team-leader dashboard.

Pure view: it shows an AgentView (status, shift, events) and Readings (the numbers from the camera) and emits
signals; controller.py decides what they do. The camera is always visible while it is on, with the values the
monitor is working from, so the agent can see what is being measured.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPlainTextEdit, QPushButton, QScrollArea, QStackedWidget,
    QVBoxLayout, QWidget,
)

from bpo.shift import ShiftStats
from monitor.core import Readings

from . import theme
from .widgets import Chip, Meter, MicroLabel, StatGrid, ThemeSwitch, card, hair, label, retone, section
from .window import standard_window

ALARM_EVENTS = ("MICROSLEEP", "IDENTITY_MISMATCH")  # the only things that are red
DIM, GOOD = 75, 105  # face brightness out of 255: below DIM the real-face check gets unreliable


@dataclass
class AgentView:
    status: str = "OFFLINE"
    reason: str | None = None
    name: str = ""
    camera_on: bool = False
    fatigue: str = "ALERT"
    lock_status: str | None = None
    queued: int = 0  # rows waiting in the offline notebook
    sync: str = "empty"  # empty | ok | offline | blocked
    offer_seconds: int | None = None
    message: str | None = None  # a short notice (for example why Available was refused)
    transcript: list[tuple[str, str]] = field(default_factory=list)  # (speaker, text)
    customer_text: str | None = None
    prompt: str | None = None  # a quick live check the agent is asked to do right now
    shift_seconds: int = 0
    shift: ShiftStats = field(default_factory=ShiftStats)
    counts: dict[str, int] = field(default_factory=dict)  # fatigue events this shift, by type
    events: list[tuple[str, str, int]] = field(default_factory=list)  # (time, type, duration_ms), newest first


def clock(total: float | None) -> str:
    if total is None:
        return "–"
    s = max(0, int(round(total)))
    h, m, r = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{r:02d}" if h else f"{m}:{r:02d}"


def signed(v: float | None, unit: str = "°") -> str:
    return "–" if v is None else f"{v:+.0f}{unit}"


class MetricRow(QWidget):
    """One measured value of the camera: a tiny label, the number, and (for ratios) a thin bar."""

    def __init__(self, title: str, bar: bool = True) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        top = QHBoxLayout()
        top.addWidget(MicroLabel(title))
        top.addStretch()
        self.value = QLabel("–")
        self.value.setObjectName("monoValue")
        top.addWidget(self.value)
        lay.addLayout(top)
        self.meter = Meter() if bar else None
        if self.meter:
            lay.addWidget(self.meter)

    def set(self, text: str, ratio: float | None = None, tone: str | None = None) -> None:
        self.value.setText(text)
        retone(self.value, tone)
        if self.meter:
            self.meter.set(ratio)


class DesktopWindow(QWidget):
    go_available = Signal()
    take_break = Signal()
    sign_out = Signal()
    accept = Signal()
    decline = Signal()
    end_call = Signal()
    acknowledge = Signal()
    lookup = Signal(str)
    finish = Signal(str, str)  # disposition, notes
    theme_changed = Signal(str)

    PAGES = ("idle", "ringing", "call", "wrap", "hold")
    PREVIEW_SIZE = (340, 255)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("Call desk")
        standard_window(self)
        self._disposition = "resolved"
        self._page = 0
        self._events_shown: list | None = None
        self.to_tray = False  # closing the window hides it to the tray while the shift is on

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 0, 32, 10)
        outer.setSpacing(0)
        outer.addLayout(self._build_topbar())
        outer.addWidget(hair())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("content")
        body = QHBoxLayout(content)
        body.setContentsMargins(0, 36, 6, 40)
        body.setSpacing(40)
        body.addLayout(self._build_main(), 1)
        body.addLayout(self._build_side(), 0)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        self.footer = MicroLabel("")
        outer.addWidget(self.footer)

    # --- the frame: top bar ---------------------------------------------------------------------------
    def _build_topbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 14, 0, 14)
        bar.addWidget(MicroLabel("call desk"))
        bar.addStretch()
        self.theme_switch = ThemeSwitch(theme.current_choice())
        self.theme_switch.changed.connect(self._on_theme)
        bar.addWidget(self.theme_switch)
        self.name = MicroLabel("")
        bar.addSpacing(16)
        bar.addWidget(self.name)
        return bar

    def _on_theme(self, choice: str) -> None:
        theme.apply(QApplication.instance(), choice)
        self.theme_changed.emit(choice)
        for w in self.findChildren(QWidget):
            w.update()

    # --- the work area ------------------------------------------------------------------------------------
    def _build_main(self) -> QVBoxLayout:
        main = QVBoxLayout()
        main.setSpacing(0)

        self.kicker = MicroLabel("")
        main.addWidget(self.kicker)
        main.addSpacing(10)
        head = QHBoxLayout()
        self.status = QLabel("offline")
        self.status.setObjectName("title")
        head.addWidget(self.status)
        head.addStretch()
        chips = QHBoxLayout()
        chips.setSpacing(8)
        self.status_chip = Chip("offline")
        self.camera = Chip("camera off")
        self.fatigue = Chip("")
        for c in (self.status_chip, self.camera, self.fatigue):
            chips.addWidget(c)
        head.addLayout(chips)
        head.setAlignment(chips, Qt.AlignVCenter)
        main.addLayout(head)
        main.addSpacing(14)

        self.banner = QLabel("")
        self.banner.setObjectName("banner")
        self.banner.setAlignment(Qt.AlignCenter)
        self.banner.hide()
        main.addWidget(self.banner)
        self.notice = QLabel("")
        self.notice.setObjectName("muted")
        self.notice.setWordWrap(True)
        main.addWidget(self.notice)
        main.addSpacing(22)

        main.addWidget(section(1, "now"))
        main.addSpacing(14)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_idle())
        self.pages.addWidget(self._build_ringing())
        self.pages.addWidget(self._build_call())
        self.pages.addWidget(self._build_wrap())
        self.pages.addWidget(self._build_hold())
        main.addWidget(self.pages)
        main.addSpacing(44)

        main.addWidget(section(2, "this shift"))
        main.addSpacing(14)
        self.shift_grid = StatGrid(
            ["calls handled", "average talk time", "resolved", "time on shift", "microsleeps", "yawns", "head nods and tilts", "long blinks"], cols=4)
        main.addWidget(self.shift_grid)
        main.addSpacing(44)

        main.addWidget(section(3, "what the monitor noticed"))
        main.addSpacing(14)
        self.events_card, self.events_lay = card()
        self.events_lay.setSpacing(0)
        main.addWidget(self.events_card)
        main.addStretch(1)
        return main

    # --- the side: the camera and its numbers ---------------------------------------------------------------
    def _build_side(self) -> QVBoxLayout:
        side = QVBoxLayout()
        side.setSpacing(0)
        holder = QWidget()
        holder.setFixedWidth(self.PREVIEW_SIZE[0] + 44)  # plus the card padding
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        lay.addWidget(section(1, "your camera"))
        lay.addSpacing(14)
        c, cl = card()
        self.preview = QLabel()
        self.preview.setObjectName("preview")
        self.preview.setFixedSize(*self.PREVIEW_SIZE)
        self.preview.setAlignment(Qt.AlignCenter)
        cl.addWidget(self.preview)
        self.clear_frame()
        self.preview_caption = MicroLabel("")
        cl.addWidget(self.preview_caption)
        privacy = QLabel("This picture stays on this PC. Only events and per-minute averages are sent.")
        privacy.setObjectName("muted")
        privacy.setWordWrap(True)
        cl.addWidget(privacy)
        lay.addWidget(c)
        lay.addSpacing(36)

        lay.addWidget(section(2, "what the camera sees"))
        lay.addSpacing(14)
        c2, cl2 = card()
        cl2.setSpacing(14)
        self.m_light = MetricRow("light on your face")
        self.m_eyes = MetricRow("eyes (openness)")
        self.m_closure = MetricRow("eye closure, last minute")
        self.m_blinks = MetricRow("blinks", bar=False)
        self.m_mouth = MetricRow("mouth (opening)")
        self.m_head = MetricRow("head angle", bar=False)
        self.m_match = MetricRow("face match")
        self.m_live = MetricRow("real-face check")
        self.m_fps = MetricRow("frame rate", bar=False)
        for row in (self.m_light, self.m_eyes, self.m_closure, self.m_blinks, self.m_mouth, self.m_head, hair(), self.m_match, self.m_live, self.m_fps):
            cl2.addWidget(row)
        self.metrics_note = QLabel("Go available to start the camera.")
        self.metrics_note.setObjectName("muted")
        self.metrics_note.setWordWrap(True)
        cl2.addWidget(self.metrics_note)
        lay.addWidget(c2)
        lay.addStretch(1)
        side.addWidget(holder)
        side.addStretch(1)
        return side

    # --- pages -------------------------------------------------------------------------------------------------
    def _build_idle(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        c, cl = card()
        self.idle_big = QLabel("")
        self.idle_big.setObjectName("heading")
        self.idle_hint = QLabel("")
        self.idle_hint.setObjectName("muted")
        self.idle_hint.setWordWrap(True)
        cl.addWidget(self.idle_big)
        cl.addWidget(self.idle_hint)
        row = QHBoxLayout()
        self.btn_available = QPushButton("Go available")
        self.btn_available.setObjectName("primary")
        self.btn_available.clicked.connect(self.go_available)
        self.btn_break = QPushButton("Take a break")
        self.btn_break.clicked.connect(self.take_break)
        self.btn_signout = QPushButton("Sign out")
        self.btn_signout.clicked.connect(self.sign_out)
        for b in (self.btn_available, self.btn_break, self.btn_signout):
            row.addWidget(b)
        row.addStretch()
        cl.addSpacing(6)
        cl.addLayout(row)
        lay.addWidget(c)
        return w

    def _build_ringing(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        c, cl = card()
        cl.addWidget(label("Incoming call"))
        self.ring_big = QLabel("A caller is waiting")
        self.ring_big.setObjectName("heading")
        self.ring_time = QLabel("")
        self.ring_time.setObjectName("muted")
        cl.addWidget(self.ring_big)
        cl.addWidget(self.ring_time)
        row = QHBoxLayout()
        accept = QPushButton("Accept")
        accept.setObjectName("primary")
        accept.clicked.connect(self.accept)
        decline = QPushButton("Decline")
        decline.clicked.connect(self.decline)
        row.addWidget(accept)
        row.addWidget(decline)
        row.addStretch()
        cl.addSpacing(6)
        cl.addLayout(row)
        lay.addWidget(c)
        return w

    def _customer_box(self) -> tuple[QWidget, QLineEdit, QLabel]:
        c, cl = card()
        cl.addWidget(label("Customer"))
        row = QHBoxLayout()
        edit = QLineEdit()
        edit.setPlaceholderText("Account number")
        find = QPushButton("Look up")
        row.addWidget(edit, 1)
        row.addWidget(find)
        cl.addLayout(row)
        out = QLabel("")
        out.setObjectName("mono")
        out.setWordWrap(True)
        cl.addWidget(out)
        find.clicked.connect(lambda: self.lookup.emit(edit.text().strip()))
        edit.returnPressed.connect(lambda: self.lookup.emit(edit.text().strip()))
        return c, edit, out

    def _build_call(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(20)
        c, cl = card()
        top = QHBoxLayout()
        top.addWidget(label("Conversation"))
        top.addStretch()
        end = QPushButton("End call")
        end.setObjectName("danger")
        end.clicked.connect(self.end_call)
        top.addWidget(end)
        cl.addLayout(top)
        self.transcript = QListWidget()
        self.transcript.setWordWrap(True)
        self.transcript.setMinimumHeight(260)
        self.transcript.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        cl.addWidget(self.transcript, 1)
        hint = QLabel("Speak to the caller. Use a headset: the microphone pauses while they talk.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        cl.addWidget(hint)
        lay.addWidget(c, 3)
        box, self.call_account, self.call_customer = self._customer_box()
        side = QVBoxLayout()
        side.addWidget(box)
        side.addStretch()
        lay.addLayout(side, 2)
        return w

    def _build_wrap(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(20)
        c, cl = card()
        cl.addWidget(label("Wrap-up"))
        title = QLabel("How did the call end?")
        title.setObjectName("heading")
        cl.addWidget(title)
        row = QHBoxLayout()
        self.dispo_buttons = {}
        for key, text in (("resolved", "Resolved"), ("unresolved", "Unresolved"), ("escalated", "Escalated")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self._pick(k))
            self.dispo_buttons[key] = b
            row.addWidget(b)
        row.addStretch()
        cl.addLayout(row)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Notes (optional)")
        self.notes.setFixedHeight(110)
        cl.addWidget(self.notes)
        done = self.btn_finish = QPushButton("Finish wrap-up")
        done.setObjectName("primary")
        done.clicked.connect(lambda: self.finish.emit(self._disposition, self.notes.toPlainText().strip()))
        cl.addWidget(done, alignment=Qt.AlignLeft)
        lay.addWidget(c, 3)
        box, self.wrap_account, self.wrap_customer = self._customer_box()
        side = QVBoxLayout()
        side.addWidget(box)
        side.addStretch()
        lay.addLayout(side, 2)
        self._pick("resolved")
        return w

    def _build_hold(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        c, cl = card(alarm=True)
        cl.addWidget(label("You are on hold"))
        self.hold_big = QLabel("Are you awake?")
        self.hold_big.setObjectName("heading")
        self.hold_why = QLabel("")
        self.hold_why.setObjectName("redText")
        self.hold_why.setWordWrap(True)
        ack = QPushButton("I am awake and ready")
        ack.setObjectName("danger")
        ack.clicked.connect(self.acknowledge)
        cl.addWidget(self.hold_big)
        cl.addWidget(self.hold_why)
        cl.addSpacing(6)
        cl.addWidget(ack, alignment=Qt.AlignLeft)
        lay.addWidget(c)
        return w

    def _pick(self, key: str) -> None:
        self._disposition = key
        for k, b in self.dispo_buttons.items():
            b.setChecked(k == key)

    # --- the camera picture -----------------------------------------------------------------------------------
    def show_frame(self, frame_bgr: np.ndarray) -> None:
        """Show the latest camera frame, mirrored like a mirror (what the agent expects to see)."""
        h, w = frame_bgr.shape[:2]
        small = cv2.resize(frame_bgr, (480, max(1, round(h * 480 / w))), interpolation=cv2.INTER_AREA) if w > 480 else frame_bgr
        rgb = cv2.cvtColor(cv2.flip(small, 1), cv2.COLOR_BGR2RGB)
        sh, sw = rgb.shape[:2]
        img = QImage(rgb.data, sw, sh, 3 * sw, QImage.Format_RGB888).copy()
        self.preview.setPixmap(QPixmap.fromImage(img).scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def clear_frame(self, text: str = "Camera is off") -> None:
        self.preview.setPixmap(QPixmap())
        self.preview.setText(text)

    def show_readings(self, r: Readings | None, fps: float = 0.0) -> None:
        """The numbers behind the picture. None means the camera is off."""
        if r is None:
            for row in (self.m_light, self.m_eyes, self.m_closure, self.m_blinks, self.m_mouth, self.m_head, self.m_match, self.m_live, self.m_fps):
                row.set("–", 0.0)
            self.metrics_note.setText("Go available to start the camera.")
            return
        if r.faces == 0:
            note = "No face in view."
        elif r.faces > 1:
            note = f"{r.faces} faces in view. Only yours is measured."
        elif r.brightness is not None and r.brightness < DIM:
            note = "Your face is dim. More light in front of you (a lamp or a window) makes every check more reliable."
        elif r.looking_down:
            note = "Looking down: eyes are not counted while you read or type."
        else:
            note = "Measured live. Nothing here is sent except the events listed on the left."
        self.metrics_note.setText(note)

        if r.brightness is None:
            self.m_light.set("–", 0.0)
        else:
            word = "too dark" if r.brightness < DIM else ("dim" if r.brightness < GOOD else "good")
            self.m_light.set(f"{r.brightness:.0f} · {word}", r.brightness / 255)
        span = max(r.ear_open - r.ear_closed, 1e-6)
        eye_state = "closed" if r.eyes_closed else "open"
        self.m_eyes.set("–" if r.ear is None else f"{r.ear:.2f} · {eye_state}", None if r.ear is None else (r.ear - r.ear_closed) / span)
        self.m_closure.set("–" if r.perclos is None else f"{r.perclos * 100:.1f}%", None if r.perclos is None else r.perclos / 0.30)
        self.m_blinks.set("–" if r.blink_rate is None else f"{r.blink_rate:.0f} per minute")
        yawning = r.mar is not None and r.mar >= r.mar_yawn
        self.m_mouth.set("–" if r.mar is None else f"{r.mar:.2f} · {'wide open' if yawning else 'closed'}", None if r.mar is None else r.mar / max(r.mar_yawn, 1e-6))
        self.m_head.set("–" if r.pitch is None else f"{signed(r.pitch)} up/down  {signed(r.yaw)} turn  {signed(r.roll)} tilt")
        self.m_match.set("–" if r.match is None else f"{r.match:.2f}", r.match)
        self.m_live.set("–" if r.liveness is None else f"{r.liveness:.2f}", r.liveness)
        self.m_fps.set(f"{fps:.0f} per second" if fps else "–")

    # --- showing a view -----------------------------------------------------------------------------------------
    def show_view(self, v: AgentView) -> None:
        self.name.setText(v.name)
        self.kicker.setText(f"agent · {v.name} · on shift {clock(v.shift_seconds)}" if v.name else "agent")
        self.status.setText(theme.STATUS_TEXT.get(v.status, v.status.lower()))
        self.status_chip.set(theme.STATUS_TEXT.get(v.status, v.status), "ink" if v.status in ("ON_CALL", "RINGING", "SYSTEM_HOLD") else "plain")
        self.camera.set("camera on" if v.camera_on else "camera off", "live" if v.camera_on else "plain")
        bad = v.fatigue == "MICROSLEEP" or v.lock_status == "IDENTITY_MISMATCH"
        text = ""
        if v.camera_on:
            text = v.fatigue + (f" · {v.lock_status}" if v.lock_status else "")
        self.fatigue.set(text, "alarm" if bad else ("strong" if v.fatigue == "DROWSY" else "plain"))
        self.fatigue.setVisible(bool(text))
        self.notice.setText(v.message or "")
        self.banner.setVisible(bool(v.prompt))
        self.banner.setText(f"Quick check:  {v.prompt}" if v.prompt else "")
        self.preview_caption.setText("" if not v.camera_on else f"{v.fatigue.lower()}" + (f" · {v.lock_status.lower()}" if v.lock_status else ""))

        previous, self._page = self._page, {"RINGING": 1, "ON_CALL": 2, "WRAP_UP": 3, "SYSTEM_HOLD": 4}.get(v.status, 0)
        page = self._page
        self.pages.setCurrentIndex(page)
        self.pages.setMaximumHeight(16_777_215 if page in (2, 3) else self.pages.widget(page).sizeHint().height())

        if page == 0:
            self.idle_big.setText({"OFFLINE": "Signed out", "NOT_READY": "Not taking calls", "AVAILABLE": "Waiting for a call"}.get(v.status, v.status))
            self.idle_hint.setText({
                "NOT_READY": "The camera is off. Press Go available when you are ready; it checks that it is you first.",
                "AVAILABLE": "The camera is on and monitoring. A caller will ring you.",
                "OFFLINE": "Sign in to start.",
            }.get(v.status, ""))
            self.btn_available.setEnabled(v.status == "NOT_READY")
            self.btn_break.setEnabled(v.status == "AVAILABLE")
            self.btn_signout.setEnabled(v.status in ("NOT_READY", "AVAILABLE"))
        elif page == 1:
            self.ring_time.setText("" if v.offer_seconds is None else f"{max(v.offer_seconds, 0)} s to answer")
        elif page == 2:
            self.set_transcript(v.transcript)
            self.call_customer.setText(v.customer_text or "")
        elif page == 3:
            self.wrap_customer.setText(v.customer_text or "")
            if previous != 3:  # a new wrap-up starts with nothing written
                self._pick("resolved")
                self.notes.clear()
        elif page == 4:
            self.hold_why.setText(v.reason or "The monitor stopped you for a moment.")

        s = v.shift
        self.shift_grid.set(0, str(s.calls), "finished this shift")
        self.shift_grid.set(1, clock(s.avg_talk_s), "per call")
        self.shift_grid.set(2, "–" if s.resolved_share is None else f"{s.resolved_share * 100:.0f}%", f"{s.resolved} of {s.calls}" if s.calls else "no calls yet")
        self.shift_grid.set(3, clock(v.shift_seconds), "since you signed in")
        c = v.counts
        self.shift_grid.set(4, str(c.get("MICROSLEEP", 0)), "eyes closed over half a second", "alarm" if c.get("MICROSLEEP", 0) else None)
        self.shift_grid.set(5, str(c.get("YAWN", 0)), "mouth open over two seconds")
        self.shift_grid.set(6, str(c.get("HEAD_NOD", 0) + c.get("HEAD_TILT", 0)))
        self.shift_grid.set(7, str(c.get("LONG_BLINK", 0)), "slow blinks")
        self.set_events(v.events)

        sync = {"empty": "all saved", "ok": "all saved", "offline": "offline, will send later", "blocked": "waiting for sign-in"}.get(v.sync, v.sync)
        self.footer.setText(f"{v.queued} waiting to send · {sync}" if v.queued else f"data {sync}")

    def set_events(self, events: list[tuple[str, str, int]]) -> None:
        if events == self._events_shown:
            return
        self._events_shown = list(events)
        while self.events_lay.count():
            item = self.events_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not events:
            empty = QLabel("Nothing unusual yet. Events appear here as the monitor notices them.")
            empty.setObjectName("muted")
            self.events_lay.addWidget(empty)
            return
        for i, (when, kind, ms) in enumerate(events):
            if i:
                self.events_lay.addWidget(hair())
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 10, 0, 10)
            h.addWidget(Chip(kind.replace("_", " "), "alarm" if kind in ALARM_EVENTS else "plain"))
            if ms:
                dur = QLabel(f"{ms / 1000:.1f} s")
                dur.setObjectName("mono")
                h.addWidget(dur)
            h.addStretch()
            h.addWidget(MicroLabel(when))
            self.events_lay.addWidget(row)

    def set_transcript(self, lines: list[tuple[str, str]]) -> None:
        if self.transcript.count() == len(lines):
            return
        self.transcript.clear()
        for speaker, text in lines:
            self.transcript.addItem(("Caller: " if speaker == "caller" else "You: ") + text)
        self.transcript.scrollToBottom()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt name)
        if self.to_tray:
            event.ignore()
            self.hide()
        else:
            super().closeEvent(event)
