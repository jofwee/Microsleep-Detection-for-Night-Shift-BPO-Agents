"""The look of the agent app: the same design tokens as the team-leader dashboard (bryl-minimal).

A background, an ink and a gray ramp; dark mode swaps the same roles. Geist for text, Geist Mono for the tiny
uppercase labels, Geist Pixel for titles. One muted red, only for MICROSLEEP and IDENTITY_MISMATCH.
`apply(app, "light" | "dark" | "system")` installs the fonts and the style sheet and remembers the choice.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

LIGHT = {
    "bg": "#ffffff", "ink": "#0a0a0a", "g50": "#fafafa", "g100": "#f5f5f5", "g200": "#e9e9e9", "g300": "#d4d4d4",
    "g400": "#a3a3a3", "g500": "#737373", "g600": "#525252", "g800": "#262626",
    "alarm": "#b3402f", "alarm_soft": "#f9ece9", "live": "#3d7a4f",
}
DARK = {
    "bg": "#0c0c0f", "ink": "#f4f4f5", "g50": "#18181b", "g100": "#1e1e22", "g200": "#2a2a30", "g300": "#3a3a42",
    "g400": "#8a8a92", "g500": "#a0a0a8", "g600": "#b4b4bb", "g800": "#dcdce0",
    "alarm": "#e0705f", "alarm_soft": "#2a1714", "live": "#6fbf8a",
}

CHOICES = ("light", "dark", "system")
_current: dict[str, str] = dict(LIGHT)
_choice = "system"

FONT_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "assets" / "fonts"
FONT_FILES = ("Geist-Regular.ttf", "Geist-Medium.ttf", "Geist-SemiBold.ttf", "GeistMono-Regular.ttf", "GeistMono-Medium.ttf", "GeistPixel-Square.ttf")

STATUS_TEXT = {
    "OFFLINE": "offline",
    "NOT_READY": "not ready",
    "AVAILABLE": "available",
    "RINGING": "incoming call",
    "ON_CALL": "on call",
    "WRAP_UP": "wrap-up",
    "SYSTEM_HOLD": "on hold",
}


def token(name: str) -> str:
    """A colour of the theme that is active right now (for painting and icons)."""
    return _current[name]


_families: list[str] | None = None


def load_fonts() -> list[str]:
    """Register the bundled fonts with Qt (once). Returns the family names that loaded."""
    global _families
    if _families is None:
        _families = []
        for name in FONT_FILES:
            fid = QFontDatabase.addApplicationFont(str(FONT_DIR / name))
            if fid >= 0:
                _families += [f for f in QFontDatabase.applicationFontFamilies(fid) if f not in _families]
    return list(_families)


def qss(t: dict[str, str]) -> str:
    return f"""
* {{ font-family: "Geist", "Segoe UI", sans-serif; color: {t['ink']}; font-size: 14px; selection-background-color: {t['ink']}; selection-color: {t['bg']}; }}
QMainWindow, QDialog, QWidget#root {{ background: {t['bg']}; }}
QLabel {{ background: transparent; }}
QToolTip {{ background: {t['bg']}; color: {t['ink']}; border: 1px solid {t['g300']}; padding: 4px 8px; }}

QLabel#label {{ font-family: "Geist Mono"; font-size: 10px; color: {t['g400']}; }}
QLabel#title {{ font-family: "Geist Pixel"; font-size: 46px; }}
QLabel#title[tone="alarm"] {{ color: {t['alarm']}; }}
QLabel#alertBig {{ font-family: "Geist Pixel"; font-size: 84px; color: {t['alarm']}; }}
QWidget#alertRoot {{ background: {t['alarm_soft']}; }}
QLabel#alertSmall {{ font-family: "Geist Mono"; font-size: 12px; color: {t['alarm']}; }}
QLabel#heading {{ font-family: "Geist Pixel"; font-size: 28px; }}
QLabel#section {{ font-family: "Geist Pixel"; font-size: 13px; color: {t['g400']}; }}
QLabel#value {{ font-family: "Geist Pixel"; font-size: 30px; }}
QLabel#value[tone="alarm"] {{ color: {t['alarm']}; }}
QLabel#muted {{ color: {t['g500']}; font-size: 12px; }}
QLabel#mono {{ font-family: "Geist Mono"; font-size: 12px; }}
QLabel#monoValue {{ font-family: "Geist Mono"; font-size: 13px; font-weight: 500; }}
QLabel#monoValue[tone="alarm"] {{ color: {t['alarm']}; }}
QLabel#redText {{ color: {t['alarm']}; font-weight: 600; }}
QLabel#preview {{ background: #0c0c0f; color: #a3a3a3; border-radius: 12px; font-family: "Geist Mono"; font-size: 12px; }}
QLabel#banner {{ background: {t['ink']}; color: {t['bg']}; border-radius: 14px; padding: 14px 18px; font-size: 20px; font-weight: 600; }}

QLabel[chip="true"] {{ font-family: "Geist Mono"; font-size: 9px; color: {t['g500']}; border: 1px solid {t['g300']}; border-radius: 9px; padding: 2px 8px; background: transparent; }}
QLabel[chip="true"][tone="ink"] {{ background: {t['ink']}; color: {t['bg']}; border-color: {t['ink']}; }}
QLabel[chip="true"][tone="strong"] {{ color: {t['ink']}; border-color: {t['ink']}; }}
QLabel[chip="true"][tone="alarm"] {{ color: {t['alarm']}; border-color: {t['alarm']}; background: {t['alarm_soft']}; }}
QLabel[chip="true"][tone="live"] {{ color: {t['live']}; border-color: {t['live']}; }}

QFrame#card {{ border: 1px solid {t['g200']}; border-radius: 16px; background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {t['g50']}, stop:1 {t['bg']}); }}
QFrame#cardAlarm {{ border: 1px solid {t['alarm']}; border-radius: 16px; background: {t['alarm_soft']}; }}
QFrame#hair {{ background: {t['g200']}; border: none; max-height: 1px; min-height: 1px; }}
QFrame#hairV {{ background: {t['g200']}; border: none; max-width: 1px; min-width: 1px; }}
QWidget#content {{ background: transparent; }}
QScrollArea {{ background: transparent; border: none; }}
QAbstractScrollArea > QWidget > QWidget {{ background: transparent; }}

QPushButton {{ background: {t['bg']}; border: 1px solid {t['g300']}; border-radius: 8px; padding: 8px 16px; font-size: 12px; }}
QPushButton:hover {{ background: {t['g100']}; }}
QPushButton:disabled {{ color: {t['g400']}; background: {t['g50']}; border-color: {t['g200']}; }}
QPushButton:checked {{ background: {t['ink']}; color: {t['bg']}; border-color: {t['ink']}; font-weight: 600; }}
QPushButton:checked:hover {{ background: {t['g800']}; }}
QPushButton#primary {{ background: {t['ink']}; color: {t['bg']}; border-color: {t['ink']}; }}
QPushButton#primary:hover {{ background: {t['g800']}; }}
QPushButton#primary:disabled {{ background: {t['g200']}; color: {t['g400']}; border-color: {t['g200']}; }}
QPushButton#danger {{ background: {t['alarm']}; color: #ffffff; border-color: {t['alarm']}; }}
QFrame#segframe {{ border: 1px solid {t['g200']}; border-radius: 7px; background: transparent; }}
QPushButton#seg {{ font-family: "Geist Mono"; font-size: 9px; padding: 5px 10px; border: none; border-radius: 5px; background: transparent; color: {t['g500']}; font-weight: 400; }}
QPushButton#seg:hover:!checked {{ background: {t['g100']}; }}
QPushButton#seg:checked {{ background: {t['ink']}; color: {t['bg']}; }}

QLineEdit, QPlainTextEdit, QTextEdit {{ background: {t['g50']}; border: 1px solid {t['g200']}; border-radius: 6px; padding: 8px 10px; font-family: "Geist Mono"; font-size: 13px; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {t['ink']}; }}
QListWidget {{ background: transparent; border: 1px solid {t['g200']}; border-radius: 12px; padding: 6px; outline: none; }}
QListWidget::item {{ padding: 6px; border-radius: 8px; }}
QScrollBar:vertical {{ width: 6px; background: transparent; margin: 0; }}
QScrollBar::handle:vertical {{ background: {t['g300']}; border-radius: 3px; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ height: 6px; background: transparent; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {t['g300']}; border-radius: 3px; min-width: 24px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
"""


def resolved(choice: str, system_dark: bool) -> dict[str, str]:
    return DARK if choice == "dark" or (choice == "system" and system_dark) else LIGHT


def system_is_dark(app: QApplication) -> bool:
    try:
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:  # noqa: BLE001 - older Qt: treat the system as light
        return False


def saved_choice() -> str:
    value = str(QSettings("thesis-microsleep", "call-desk").value("theme", "system"))
    return value if value in CHOICES else "system"


def apply(app: QApplication, choice: str | None = None, remember: bool = True) -> str:
    """Install fonts and the style sheet for light / dark / system. Returns the choice in force."""
    global _current, _choice
    load_fonts()
    choice = choice if choice in CHOICES else saved_choice()
    _choice = choice
    _current = dict(resolved(choice, system_is_dark(app)))
    font = QFont("Geist")
    font.setPixelSize(14)
    app.setFont(font)
    app.setStyleSheet(qss(_current))
    if remember:
        QSettings("thesis-microsleep", "call-desk").setValue("theme", choice)
    return choice


def current_choice() -> str:
    return _choice


def follow_system(app: QApplication) -> None:
    """When the choice is "system", repaint if the operating system switches between light and dark."""
    try:
        app.styleHints().colorSchemeChanged.connect(lambda *_: apply(app, _choice, remember=False) if _choice == "system" else None)
    except Exception:  # noqa: BLE001
        pass
