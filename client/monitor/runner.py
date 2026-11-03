"""The camera thread, and the rule for when the camera may be on.

The camera runs only while the agent is working (CAMERA_STATUSES). On break, in Not ready, and when
signed out it is closed, and those minutes are neither monitored nor counted as sleep.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, ContextManager, Protocol

import numpy as np

from vision.input_idle import recent_input

from .core import MonitorCore

CAMERA_STATUSES = frozenset({"AVAILABLE", "RINGING", "ON_CALL", "WRAP_UP", "SYSTEM_HOLD"})


def camera_wanted(status: str | None) -> bool:
    return status in CAMERA_STATUSES


class CameraLike(Protocol):
    def read(self) -> tuple[int, np.ndarray] | None: ...


class MonitorRunner:
    def __init__(self, make_core: Callable[[], MonitorCore], open_camera: Callable[[], ContextManager[CameraLike]],
                 idle: Callable[[], bool] = recent_input, log: Callable[[str], None] = print) -> None:
        self._make_core, self._open_camera, self._idle, self.log = make_core, open_camera, idle, log
        self.core: MonitorCore | None = None
        self.error: str | None = None
        self.fps = 0.0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._frame: np.ndarray | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, wait_s: float = 8.0) -> bool:
        """Open the camera and start measuring. Returns False (with .error set) if the camera does not open."""
        if self.running:
            return True
        self.error, self._frame = None, None
        self._stop.clear()
        self._ready.clear()
        self.core = self._make_core()
        self._thread = threading.Thread(target=self._loop, name="monitor", daemon=True)
        self._thread.start()
        self._ready.wait(wait_s)
        return self.running and self.error is None

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None

    def latest_frame(self) -> np.ndarray | None:
        return self._frame

    MAX_CHECK_S = 45.0  # however long a quick check takes, the re-check never runs longer than this

    def wait_verified(self, timeout_s: float = 10.0) -> bool:
        """The short re-check when the agent goes Available: the face at the desk matches the sign-in face and is
        live (the passive check, or the quick check the agent was asked to do). A quick check in progress is waited for."""
        start = time.monotonic()
        while True:
            now = time.monotonic()
            core = self.core
            if core is not None and core.verified():
                return True
            if not self.running or now - start >= self.MAX_CHECK_S:
                return False
            asked = core is not None and core.snapshot.prompt is not None
            if now - start >= timeout_s and not asked:
                return False
            time.sleep(0.05)

    def _loop(self) -> None:
        core = self.core
        try:
            with self._open_camera() as cam:
                self._ready.set()
                last = time.perf_counter()
                while not self._stop.is_set():
                    item = cam.read()
                    if item is None:
                        time.sleep(0.002)
                        continue
                    ts, frame = item
                    core.process(frame, ts, self._idle())
                    self._frame = frame
                    now = time.perf_counter()
                    self.fps = 0.9 * self.fps + 0.1 / max(now - last, 1e-6)
                    last = now
        except Exception as exc:  # noqa: BLE001 - reported to the desk, which holds the agent
            self.error = f"{exc.__class__.__name__}: {exc}"
            self.log(f"  (camera problem: {self.error})")
        finally:
            self._ready.set()
            if core is not None:
                core.finish()


class CameraGate:
    """Follows the agent's status: opens the camera when work starts, closes it when work stops."""

    def __init__(self, runner: MonitorRunner) -> None:
        self.runner = runner

    def sync(self, status: str | None) -> None:
        if camera_wanted(status):
            if not self.runner.running:
                self.runner.start()
        elif self.runner.running:
            self.runner.stop()
