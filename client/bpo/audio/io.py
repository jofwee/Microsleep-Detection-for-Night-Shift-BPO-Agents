"""Microphone and speaker for the call, half-duplex: the microphone is muted while the caller speaks.

A headset is expected. With open speakers the caller's voice would feed back into the microphone,
so the mic stays muted during playback (plus a short tail) and anything it hears then is dropped.
"""
from __future__ import annotations

import queue
import threading
import time

import numpy as np

from .vad import FRAME, RATE

try:  # sounddevice needs the PortAudio library; tests run without audio hardware
    import sounddevice as sd
except Exception:  # noqa: BLE001
    sd = None


class Microphone:
    def __init__(self, device: int | None = None) -> None:
        self.device = device
        self._q: queue.Queue[np.ndarray] = queue.Queue(maxsize=400)
        self._muted_until = 0.0
        self._stream = None

    def __enter__(self) -> "Microphone":
        if sd is None:
            raise RuntimeError("sounddevice is not available")
        self._stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME,
                                      device=self.device, callback=self._callback)
        self._stream.start()
        return self

    def __exit__(self, *exc) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()

    def _callback(self, indata, frames, time_info, status) -> None:
        if time.monotonic() < self._muted_until:
            return  # half-duplex: the caller is speaking, ignore the microphone
        try:
            self._q.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass  # the loop is behind; dropping a frame beats growing without bound

    def mute_for(self, seconds: float) -> None:
        self._muted_until = max(self._muted_until, time.monotonic() + seconds)
        self.flush()

    def flush(self) -> None:
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                return

    def read(self, timeout: float = 0.1) -> np.ndarray | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None


class Speaker:
    """Plays audio and keeps the microphone muted while it does."""

    TAIL_S = 0.35  # room echo and the end of the sound card buffer

    def __init__(self, mic: Microphone | None = None, device: int | None = None) -> None:
        self.mic = mic
        self.device = device
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()
        if sd is not None:
            sd.stop()

    def play(self, samples: np.ndarray, rate: int, cancel=None) -> None:
        """Blocking. samples: float32 in -1..1, mono. `cancel` is polled while it plays: when it turns true the sound stops at once."""
        if sd is None:
            raise RuntimeError("sounddevice is not available")
        self._stop.clear()
        duration = len(samples) / rate
        if self.mic is not None:
            self.mic.mute_for(duration + self.TAIL_S)
        sd.play(samples, rate, device=self.device)
        deadline = time.monotonic() + duration + 0.5
        while time.monotonic() < deadline and not self._stop.is_set():
            if cancel is not None and cancel():
                sd.stop()  # cut the caller off in the middle of a word
                break
            time.sleep(0.02)
        sd.wait()
        if self.mic is not None:
            self.mic.mute_for(self.TAIL_S)
