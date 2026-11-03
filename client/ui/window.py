"""One window policy for every screen of the app, so the resolution is the same everywhere.

Each window has its normal border and title bar, is resizable down to MIN_SIZE, and opens maximized.
F11 switches true full screen on and off.
"""
from __future__ import annotations

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QWidget

MIN_SIZE = (1180, 680)
START_SIZE = (1280, 800)  # the size it returns to when un-maximized


def standard_window(w: QWidget) -> None:
    w.setMinimumSize(*MIN_SIZE)
    w.resize(*START_SIZE)
    QShortcut(QKeySequence("F11"), w, activated=lambda: toggle_full_screen(w))


def show_standard(w: QWidget) -> None:
    w.showMaximized()


def toggle_full_screen(w: QWidget) -> None:
    if w.isFullScreen():
        w.showMaximized()
    else:
        w.showFullScreen()
