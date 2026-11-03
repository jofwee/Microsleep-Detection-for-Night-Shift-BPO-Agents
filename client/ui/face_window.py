"""Camera preview with prompts, used for the sign-in face check (the challenge: blink twice, turn left...).

The face check runs in the calling thread and hands every frame to show_frame(); the window repaints and
keeps the rest of the app responsive by processing Qt events itself. Closing the window or pressing Cancel
makes show_frame() return False, which stops the check.
"""
from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from .widgets import label
from .window import show_standard, standard_window


class FaceWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("Call desk · face check")
        standard_window(self)
        self.cancelled = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.addWidget(label("Face check"))
        self.prompt = QLabel("Look at the camera")
        self.prompt.setObjectName("title")
        self.prompt.setWordWrap(True)
        lay.addWidget(self.prompt)
        self.preview = QLabel()
        self.preview.setMinimumSize(640, 400)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setObjectName("preview")
        lay.addWidget(self.preview, 1)
        self.detail = QLabel("")
        self.detail.setObjectName("mono")
        lay.addWidget(self.detail)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self._cancel)
        lay.addWidget(cancel, alignment=Qt.AlignLeft)

    def _cancel(self) -> None:
        self.cancelled = True

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt name)
        self.cancelled = True
        super().closeEvent(event)

    def begin(self) -> None:
        self.cancelled = False
        show_standard(self)

    def show_frame(self, frame_bgr: np.ndarray, prompt: str, detail: str = "", warn: bool = False) -> bool:
        """Show one frame (mirrored, like a mirror). Returns False when the person cancelled."""
        view = cv2.cvtColor(cv2.flip(frame_bgr, 1), cv2.COLOR_BGR2RGB)
        h, w, _ = view.shape
        img = QImage(view.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        self.preview.setPixmap(QPixmap.fromImage(img).scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.prompt.setText(prompt)
        self.prompt.setProperty("tone", "alarm" if warn else "")
        self.prompt.style().unpolish(self.prompt)
        self.prompt.style().polish(self.prompt)
        self.detail.setText(detail)
        QApplication.processEvents()
        return not self.cancelled
