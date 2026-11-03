"""The tray icon: where the agent can always see whether the camera is on, and reach the desk."""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import theme


def dot_icon(color: str) -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    p.drawEllipse(8, 8, 48, 48)
    p.end()
    return QIcon(pm)


class Tray(QObject):
    open_desk = Signal()
    go_available = Signal()
    take_break = Signal()
    sign_out = Signal()
    quit = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.icon = QSystemTrayIcon(dot_icon(theme.token("g500")))
        menu = QMenu()
        self.status_action = QAction("Offline")
        self.status_action.setEnabled(False)
        self.camera_action = QAction("Camera monitoring: OFF")
        self.camera_action.setEnabled(False)
        menu.addAction(self.status_action)
        menu.addAction(self.camera_action)
        menu.addSeparator()
        for text, sig in (("Open desk", self.open_desk), ("Go available", self.go_available),
                          ("Take a break", self.take_break), ("Sign out", self.sign_out)):
            a = QAction(text)
            a.triggered.connect(sig)
            menu.addAction(a)
        menu.addSeparator()
        q = QAction("Quit")
        q.triggered.connect(self.quit)
        menu.addAction(q)
        self._menu = menu  # keep a reference: the tray does not own it
        self.icon.setContextMenu(menu)
        self.icon.activated.connect(lambda reason: self.open_desk.emit() if reason == QSystemTrayIcon.Trigger else None)
        self.set_state("OFFLINE", False, False)

    def show(self) -> None:
        self.icon.show()

    def set_state(self, status: str, camera_on: bool, alert: bool) -> None:
        text = theme.STATUS_TEXT.get(status, status)
        cam = "ON" if camera_on else "OFF"
        self.status_action.setText(text)
        self.camera_action.setText(f"Camera monitoring: {cam}")
        self.icon.setToolTip(f"{text} · camera monitoring {cam}")
        self.icon.setIcon(dot_icon(theme.token("alarm") if alert else (theme.token("live") if camera_on else theme.token("g500"))))
