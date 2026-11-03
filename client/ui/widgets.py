"""Small building blocks in the dashboard's language: micro labels, chips, hairlines, stat grids, meters."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from . import theme


def _spaced(widget: QLabel) -> None:
    """Wide tracking for the mono micro-labels (a style sheet cannot set letter spacing)."""
    f = widget.font()
    if f.letterSpacing() != 1.0:
        f.setLetterSpacing(QFont.AbsoluteSpacing, 1.0)
        widget.setFont(f)


class MicroLabel(QLabel):
    """tiny, mono, UPPERCASE, wide tracking: the signature register of the design."""

    def __init__(self, text: str = "") -> None:
        super().__init__(text.upper())
        self.setObjectName("label")

    def setText(self, text: str) -> None:  # noqa: N802 (Qt name)
        super().setText(text.upper())

    def changeEvent(self, e: QEvent) -> None:  # noqa: N802 (Qt name)
        super().changeEvent(e)
        if e.type() in (QEvent.StyleChange, QEvent.Polish):
            _spaced(self)

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        _spaced(self)


def label(text: str) -> MicroLabel:
    return MicroLabel(text)


def section(n: int, title: str) -> QLabel:
    """"01 — now": a numbered, em-dashed section header in the pixel font."""
    w = QLabel(f"{n:02d} — {title}")
    w.setObjectName("section")
    return w


def card(alarm: bool = False) -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("cardAlarm" if alarm else "card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(22, 18, 22, 18)
    lay.setSpacing(10)
    return f, lay


def hair(vertical: bool = False) -> QFrame:
    f = QFrame()
    f.setObjectName("hairV" if vertical else "hair")
    f.setFrameShape(QFrame.NoFrame)
    return f


class Chip(QLabel):
    """A small pill. tone: plain | ink | strong | alarm | live. The red `alarm` is only for microsleep / identity mismatch."""

    def __init__(self, text: str = "", tone: str = "plain") -> None:
        super().__init__()
        self.setProperty("chip", True)
        self.setFixedHeight(22)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.setAlignment(Qt.AlignCenter)
        self.set(text, tone)

    def set(self, text: str, tone: str = "plain") -> None:
        self.setText(text.upper())
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)
        _spaced(self)

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        _spaced(self)


def retone(widget: QWidget, tone: str | None) -> None:
    """Change a widget's `tone` property and let the style sheet pick it up."""
    widget.setProperty("tone", tone or "")
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class Meter(QWidget):
    """A thin horizontal bar, 0..1, in ink (or the one red)."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(4)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._ratio = 0.0
        self._alarm = False

    def set(self, ratio: float | None, alarm: bool = False) -> None:
        r = 0.0 if ratio is None else min(max(float(ratio), 0.0), 1.0)
        if r != self._ratio or alarm != self._alarm:
            self._ratio, self._alarm = r, alarm
            self.update()

    @property
    def ratio(self) -> float:
        return self._ratio

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        w, h = self.width(), self.height()
        p.setBrush(QColor(theme.token("g200")))
        p.drawRoundedRect(QRectF(0, 0, w, h), 2, 2)
        if self._ratio > 0:
            p.setBrush(QColor(theme.token("alarm" if self._alarm else "ink")))
            p.drawRoundedRect(QRectF(0, 0, max(h, w * self._ratio), h), 2, 2)


class StatCell(QWidget):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setMinimumHeight(118)  # wrapped labels report a small minimum: without this the cells get squeezed instead of the page scrolling
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(6)
        self.title = MicroLabel(title)
        self.title.setWordWrap(True)
        self.value = QLabel("–")
        self.value.setObjectName("value")
        self.hint = QLabel("")
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        lay.addWidget(self.title)
        lay.addWidget(self.value)
        lay.addWidget(self.hint)
        lay.addStretch()

    def set(self, value: str, hint: str = "", tone: str | None = None) -> None:
        self.value.setText(value)
        self.hint.setText(hint)
        retone(self.value, tone)


class StatGrid(QFrame):
    """Stat cells separated by hairlines (both ways) instead of gaps: the dividers are the design."""

    def __init__(self, titles: list[str], cols: int = 4) -> None:
        super().__init__()
        self.setObjectName("card")
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        self.cells: list[StatCell] = []
        rows = (len(titles) + cols - 1) // cols
        for i, title in enumerate(titles):
            r, c = divmod(i, cols)
            cell = StatCell(title)
            self.cells.append(cell)
            grid.addWidget(cell, r * 2, c * 2)
            if c < cols - 1:
                grid.addWidget(hair(vertical=True), r * 2, c * 2 + 1)
            grid.setColumnStretch(c * 2, 1)
        for r in range(rows - 1):
            grid.addWidget(hair(), r * 2 + 1, 0, 1, cols * 2 - 1)

    def set(self, i: int, value: str, hint: str = "", tone: str | None = None) -> None:
        self.cells[i].set(value, hint, tone)


class ThemeSwitch(QFrame):
    """light | dark | system, like the dashboard's switch."""

    changed = Signal(str)

    def __init__(self, current: str) -> None:
        super().__init__()
        self.setObjectName("segframe")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for name in theme.CHOICES:
            b = QPushButton(name.upper())
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setChecked(name == current)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, n=name: self.changed.emit(n))
            self.group.addButton(b)
            lay.addWidget(b)
            self.buttons[name] = b

    def select(self, name: str) -> None:
        if name in self.buttons:
            self.buttons[name].setChecked(True)


class Halftone(QWidget):
    """The signature accent: round dots on a 9px grid that fade out from the top right corner. Once per screen, at most."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)

    def paintEvent(self, _e) -> None:  # noqa: N802
        w, h = self.width(), self.height()
        if w < 10 or h < 10:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        ink = QColor(theme.token("ink"))
        base = 0.42 if theme.token("bg") != "#ffffff" else 0.9
        reach = 0.7 * max(w, h)
        step = 9
        for x in range(step // 2, w, step):
            for y in range(step // 2, h, step):
                d = ((w - x) ** 2 + (y * w / max(h, 1)) ** 2) ** 0.5  # an ellipse from the top right corner
                a = max(0.0, 1.0 - d / (reach * 1.6)) ** 1.2
                if a < 0.03:
                    continue
                ink.setAlphaF(min(1.0, a * base))
                p.setBrush(ink)
                p.drawEllipse(QPointF(x, y), 1.0, 1.0)
