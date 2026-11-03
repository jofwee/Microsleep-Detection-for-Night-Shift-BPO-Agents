"""Full-screen "are you awake?" alert with a repeating sound. Shown on MICROSLEEP; closes when the agent confirms."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from .sound import play_ping
from .widgets import MicroLabel


class AlertWindow(QWidget):
    confirmed = Signal()

    def __init__(self, beep=None, interval_ms: int = 900) -> None:
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setObjectName("alertRoot")
        self._beep = beep or play_ping  # a real sound, not the Windows default beep (which can be silent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        small = MicroLabel("microsleep detected")
        small.setObjectName("alertSmall")
        small.setAlignment(Qt.AlignCenter)
        big = QLabel("Are you awake?")
        big.setObjectName("alertBig")
        big.setAlignment(Qt.AlignCenter)
        self.detail = QLabel("Your eyes were closed for a moment. Confirm to continue.")
        self.detail.setAlignment(Qt.AlignCenter)
        self.button = QPushButton("I am awake")
        self.button.setObjectName("danger")
        self.button.setMinimumHeight(52)
        self.button.setMinimumWidth(240)
        self.button.clicked.connect(self._confirm)
        for w in (small, big, self.detail):
            lay.addWidget(w)
        lay.addSpacing(24)
        lay.addWidget(self.button, alignment=Qt.AlignCenter)
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._beep)

    @property
    def active(self) -> bool:
        return self.isVisible()

    def raise_alert(self, detail: str | None = None) -> None:
        if detail:
            self.detail.setText(detail)
        if not self.isVisible():
            self.showFullScreen()
            self._beep()
            self._timer.start()
        self.raise_()

    def _confirm(self) -> None:
        self.dismiss()
        self.confirmed.emit()

    def dismiss(self) -> None:
        self._timer.stop()
        self.hide()
