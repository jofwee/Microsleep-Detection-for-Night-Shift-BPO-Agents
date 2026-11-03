"""Find where the agent's sentences start and end in a stream of microphone frames.

A simple energy detector with an adaptive noise floor and hysteresis, which works well with a headset
in a quiet room (the Spike S1 setting). The plan names Silero VAD (a small neural model) for this; it
stays the upgrade if background noise turns out to break this detector.
"""
from __future__ import annotations

from collections import deque

import numpy as np

RATE = 16_000
FRAME = 512  # samples per frame: 32 ms
FRAME_MS = FRAME * 1000 // RATE


def rms(frame: np.ndarray) -> float:
    """Loudness of an int16 frame on a 0..1 scale."""
    x = frame.astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(x * x))) if len(x) else 0.0


class UtteranceDetector:
    """feed() one frame at a time; it returns the finished utterance (int16 samples) or None.

    An utterance starts after a few consecutive loud frames and ends after `end_silence_ms` of quiet.
    Very short sounds (a click, a cough) are discarded, and very long ones are cut at `max_utterance_ms`.
    """

    def __init__(
        self,
        end_silence_ms: int = 700,
        min_speech_ms: int = 250,
        max_utterance_ms: int = 25_000,
        preroll_ms: int = 320,
        tail_ms: int = 160,
        start_frames: int = 3,
        floor_init_ms: int = 500,
        margin: float = 3.0,
        abs_min: float = 0.006,
    ) -> None:
        self.end_silence_frames = max(1, end_silence_ms // FRAME_MS)
        self.min_speech_frames = max(1, min_speech_ms // FRAME_MS)
        self.max_frames = max(1, max_utterance_ms // FRAME_MS)
        self.tail_frames = max(0, tail_ms // FRAME_MS)
        self.start_frames = start_frames
        self.floor_init_frames = max(1, floor_init_ms // FRAME_MS)
        self.margin = margin
        self.abs_min = abs_min
        self._preroll: deque[np.ndarray] = deque(maxlen=max(1, preroll_ms // FRAME_MS))
        self.reset()

    def reset(self) -> None:
        """Forget the current sound (but keep what was learned about the room's noise)."""
        self._loud_run = 0
        self._in_speech = False
        self._frames: list[np.ndarray] = []
        self._speech_frames = 0
        self._quiet_run = 0
        self._preroll.clear()
        if not hasattr(self, "_floor"):
            self._floor = 0.0
            self._init_levels: list[float] = []

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    @property
    def noise_floor(self) -> float:
        return self._floor

    @property
    def threshold(self) -> float:
        return max(self.abs_min, self._floor * self.margin)

    def feed(self, frame: np.ndarray) -> np.ndarray | None:
        level = rms(frame)
        if len(self._init_levels) < self.floor_init_frames and self._floor == 0.0:
            self._init_levels.append(level)  # learn the room's noise from the first half second
            if len(self._init_levels) == self.floor_init_frames:
                self._floor = float(np.median(self._init_levels))
            return None

        hi, lo = self.threshold, self.threshold * 0.6
        if not self._in_speech:
            if level < hi:  # the room's noise drifts slowly; loud frames never raise the floor
                self._floor = 0.98 * self._floor + 0.02 * level
            self._loud_run = self._loud_run + 1 if level >= hi else 0
            if self._loud_run >= self.start_frames:
                self._in_speech = True
                self._frames = list(self._preroll) + [frame]
                self._speech_frames = self._loud_run
                self._quiet_run = 0
            else:
                self._preroll.append(frame)
            return None

        self._frames.append(frame)
        if level >= lo:
            self._speech_frames += 1
            self._quiet_run = 0
        else:
            self._quiet_run += 1
        if self._quiet_run >= self.end_silence_frames or len(self._frames) >= self.max_frames:
            return self._finish()
        return None

    def _finish(self) -> np.ndarray | None:
        frames, speech = self._frames, self._speech_frames
        extra_quiet = max(self._quiet_run - self.tail_frames, 0)  # keep a little tail, drop the long silence
        if extra_quiet:
            frames = frames[:-extra_quiet]
        self._in_speech = False
        self._frames = []
        self._speech_frames = 0
        self._quiet_run = 0
        self._loud_run = 0
        self._preroll.clear()
        if speech < self.min_speech_frames:
            return None
        return np.concatenate(frames)
