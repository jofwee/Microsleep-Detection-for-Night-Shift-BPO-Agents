"""Webcam capture on its own thread, so slow processing never blocks the camera.

read() always returns the newest frame. A slow consumer skips frames instead of
falling behind, which keeps the on-screen state in step with the real world.
"""
from __future__ import annotations

import sys
import threading
import time

import cv2
import numpy as np

from .camera_guard import find_physical, list_cameras


class Camera:
    """index=None picks the physical webcam by name and ignores virtual cameras.

    An explicit index that points at a virtual camera is refused unless allow_virtual=True
    (a developer escape hatch; the real app never sets it).
    """

    def __init__(
        self,
        index: int | None = None,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        allow_virtual: bool = False,
    ) -> None:
        cams = list_cameras()
        if index is None:
            found = find_physical()
            if found is None:
                names = ", ".join(c.name for c in cams) or "none"
                raise RuntimeError(f"no physical webcam found (cameras seen: {names}); virtual cameras are not allowed")
            index = found.index
        chosen = next((c for c in cams if c.index == index), None)
        if chosen is not None and chosen.virtual and not allow_virtual:
            raise RuntimeError(f"camera {index} ({chosen.name}) is a virtual camera and is not allowed")
        self.name = chosen.name if chosen else f"camera {index}"
        self._index = index
        self._size = (width, height)
        self._fps = fps
        self._cap: cv2.VideoCapture | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._ts_ms = 0
        self._seq = 0
        self._seen = 0
        self._running = False

    def start(self) -> None:
        # Media Foundation, not DirectShow: on the test webcam (USB2.0 VGA UVC) DirectShow
        # delivered only 8 FPS, Media Foundation 29. Camera indexes differ between the two
        # backends, and virtual cameras (OBS, DroidCam) take indexes too; picking the
        # physical webcam by name comes with the camera guard in milestone 3.
        backend = cv2.CAP_MSMF if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(self._index, backend)
        if not cap.isOpened():
            raise RuntimeError(f"cannot open camera {self._index}")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._size[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._size[1])
        cap.set(cv2.CAP_PROP_FPS, self._fps)
        self._cap = cap
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="camera", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        assert self._cap is not None
        while self._running:
            ok, frame = self._cap.read()
            if not ok:
                time.sleep(0.01)
                continue
            ts = int(time.monotonic() * 1000)
            with self._lock:
                self._frame, self._ts_ms = frame, ts
                self._seq += 1

    def read(self) -> tuple[int, np.ndarray] | None:
        """(timestamp_ms, frame) of the newest frame not yet returned, else None."""
        with self._lock:
            if self._frame is None or self._seq == self._seen:
                return None
            self._seen = self._seq
            return self._ts_ms, self._frame.copy()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._cap is not None:
            self._cap.release()
        self._cap = None

    def __enter__(self) -> "Camera":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
