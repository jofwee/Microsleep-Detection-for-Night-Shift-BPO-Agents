"""Setup and sign-in: pair this PC, email + password, choose a new password. The face step has its own window."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from .widgets import Halftone, card, label
from .window import standard_window


class LoginWindow(QWidget):
    pair = Signal(str)
    sign_in = Signal(str, str)
    new_password = Signal(str)

    PAIR, SIGN_IN, NEW_PASSWORD, BUSY = range(4)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("Call desk · sign in")
        standard_window(self)
        self.halftone = Halftone(self)  # the one halftone accent of this screen
        self.halftone.lower()
        outer = QHBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        column = QWidget()
        column.setFixedWidth(500)
        root = QVBoxLayout(column)
        root.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)
        outer.addWidget(column)
        outer.addStretch(1)
        self.error = QLabel("")
        self.error.setObjectName("redText")
        self.error.setWordWrap(True)
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)  # keep the card its own height, centred
        root.addStretch(1)
        root.addWidget(self.stack)
        root.addWidget(self.error)
        root.addStretch(1)
        self.stack.addWidget(self._pair_page())
        self.stack.addWidget(self._signin_page())
        self.stack.addWidget(self._newpass_page())
        self.stack.addWidget(self._busy_page())

    def resizeEvent(self, e) -> None:  # noqa: N802 (Qt name)
        super().resizeEvent(e)
        w, h = min(760, self.width()), min(520, self.height())
        self.halftone.setGeometry(self.width() - w, 0, w, h)

    def _page(self, kicker: str, title: str, hint: str):
        w = QWidget()
        lay = QVBoxLayout(w)
        c, cl = card()
        cl.addWidget(label(kicker))
        t = QLabel(title)
        t.setObjectName("heading")
        cl.addWidget(t)
        h = QLabel(hint)
        h.setObjectName("muted")
        h.setWordWrap(True)
        cl.addWidget(h)
        lay.addWidget(c)
        lay.addStretch()
        return w, cl

    def _pair_page(self) -> QWidget:
        w, cl = self._page("First time on this PC", "Pair this computer", "Your team leader gives you a pairing code. This links the PC to its real webcam.")
        self.pair_code = QLineEdit()
        self.pair_code.setPlaceholderText("Pairing code")
        btn = QPushButton("Pair")
        btn.setObjectName("primary")
        btn.clicked.connect(lambda: self.pair.emit(self.pair_code.text().strip()))
        self.pair_code.returnPressed.connect(btn.click)
        cl.addWidget(self.pair_code)
        cl.addWidget(btn, alignment=Qt.AlignLeft)
        return w

    def _signin_page(self) -> QWidget:
        w, cl = self._page("Sign in", "Welcome back", "Email and password first. Then a quick face check.")
        self.email = QLineEdit()
        self.email.setPlaceholderText("Email")
        self.password = QLineEdit()
        self.password.setPlaceholderText("Password")
        self.password.setEchoMode(QLineEdit.Password)
        btn = QPushButton("Continue")
        btn.setObjectName("primary")
        btn.clicked.connect(lambda: self.sign_in.emit(self.email.text().strip(), self.password.text()))
        self.password.returnPressed.connect(btn.click)
        for x in (self.email, self.password):
            cl.addWidget(x)
        cl.addWidget(btn, alignment=Qt.AlignLeft)
        return w

    def _newpass_page(self) -> QWidget:
        w, cl = self._page("First sign-in", "Choose a new password", "12 or more characters, with upper case, lower case and a digit.")
        self.new1 = QLineEdit()
        self.new1.setPlaceholderText("New password")
        self.new1.setEchoMode(QLineEdit.Password)
        self.new2 = QLineEdit()
        self.new2.setPlaceholderText("Repeat it")
        self.new2.setEchoMode(QLineEdit.Password)
        btn = QPushButton("Save password")
        btn.setObjectName("primary")
        btn.clicked.connect(self._submit_new)
        self.new2.returnPressed.connect(btn.click)
        for x in (self.new1, self.new2):
            cl.addWidget(x)
        cl.addWidget(btn, alignment=Qt.AlignLeft)
        return w

    def _busy_page(self) -> QWidget:
        w, cl = self._page("One moment", "Working…", "")
        self.busy_title = w.findChildren(QLabel, "heading")[0]
        self.busy_hint = w.findChildren(QLabel, "muted")[0]
        return w

    def _submit_new(self) -> None:
        if self.new1.text() != self.new2.text():
            self.show_error("The two passwords do not match.")
            return
        self.new_password.emit(self.new1.text())

    def show_page(self, page: int) -> None:
        self.stack.setCurrentIndex(page)
        self.error.setText("")

    def show_busy(self, title: str, hint: str = "") -> None:
        self.busy_title.setText(title)
        self.busy_hint.setText(hint)
        self.show_page(self.BUSY)

    def show_error(self, text: str) -> None:
        self.error.setText(text)

    def clear_secrets(self) -> None:
        for x in (self.password, self.new1, self.new2):
            x.clear()
