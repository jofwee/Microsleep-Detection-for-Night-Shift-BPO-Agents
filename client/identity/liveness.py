"""Liveness: a passive photo/screen check (MiniFASNet) plus an active challenge.

The passive model looks at one frame and says "real face" or "photo / screen replay".
The active challenge ("blink twice", "turn your head to your left") makes a replayed
photo or pre-recorded video fail, because it cannot know which actions will be asked.
The server picks the random actions in the Supabase milestone; here they come from
random_actions().
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np
import onnxruntime as ort

from vision.calibration import Calibration
from vision.drowsiness import EventType, EyeTracker, Thresholds
from vision.landmarks import FaceResult

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"


class PassiveLiveness:
    """MiniFASNetV2 (Minivision Silent-Face-Anti-Spoofing, ONNX export, Apache-2.0).

    Preprocessing follows the model's reference code: crop the face box enlarged 2.7x,
    resize to the model input (80x80), feed BGR float values 0-255 without normalisation,
    softmax the 3 outputs. Class 1 is "real"; classes 0 and 2 are two kinds of attack.
    """

    def __init__(self, model_path: str | Path = MODELS_DIR / "MiniFASNetV2.onnx", scale: float = 2.7) -> None:
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(f"{path} not found. Run: python scripts/download_models.py")
        self._session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        inp = self._session.get_inputs()[0]
        self._input = inp.name
        self._size = (int(inp.shape[3]), int(inp.shape[2]))  # (w, h)
        self.scale = scale

    def _crop(self, frame: np.ndarray, face: FaceResult) -> np.ndarray:
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = face.box()
        x, y, bw, bh = x1 * w, y1 * h, max((x2 - x1) * w, 1.0), max((y2 - y1) * h, 1.0)
        scale = min((h - 1) / bh, (w - 1) / bw, self.scale)
        nw, nh = bw * scale, bh * scale
        cx, cy = x + bw / 2, y + bh / 2
        l, t = max(0, int(cx - nw / 2)), max(0, int(cy - nh / 2))
        r, b = min(w - 1, int(cx + nw / 2)), min(h - 1, int(cy + nh / 2))
        return cv2.resize(frame[t:b + 1, l:r + 1], self._size)

    def score(self, frame_bgr: np.ndarray, face: FaceResult) -> float:
        """Probability that the face is a real, live one (0..1)."""
        blob = self._crop(frame_bgr, face).astype(np.float32).transpose(2, 0, 1)[None]
        logits = self._session.run(None, {self._input: blob})[0]
        e = np.exp(logits - logits.max(axis=1, keepdims=True))
        return float((e / e.sum(axis=1, keepdims=True))[0, 1])


class Action(str, Enum):
    BLINK_TWICE = "blink twice"
    TURN_LEFT = "turn your head to your LEFT"
    TURN_RIGHT = "turn your head to your RIGHT"


def random_actions(n: int = 2, rng: random.Random | None = None) -> list[Action]:
    """n different random actions. SystemRandom by default so the order is unpredictable."""
    return (rng or random.SystemRandom()).sample(list(Action), n)


class ChallengeStatus(str, Enum):
    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"


@dataclass
class ChallengeConfig:
    get_ready_ms: int = 1000  # look straight at the camera; fixes the "facing front" reference
    per_action_ms: int = 8000
    blinks_needed: int = 2
    turn_delta: float = 0.15  # head_turn() change from the front reference (about 15 degrees)
    hold_ms: int = 300  # the turn must be held this long
    recenter_delta: float = 0.07  # head must be back within this of the reference to start an action


class ActiveChallenge:
    """Feed one (ear, turn) pair per frame; it reports PENDING, PASSED or FAILED.

    ear:  mean eye aspect ratio, or None if no face.
    turn: metrics.head_turn() value, or None if no face.
    """

    def __init__(
        self,
        actions: Sequence[Action],
        calibration: Calibration | None = None,
        cfg: ChallengeConfig | None = None,
    ) -> None:
        self.actions = list(actions)
        self.cfg = cfg or ChallengeConfig()
        self._eyes = EyeTracker(calibration or Calibration(), Thresholds())
        self.index = 0
        self.status = ChallengeStatus.PENDING
        self._t0: int | None = None  # challenge start
        self._turns: list[float] = []
        self._reference: float | None = None
        self._action_start: int | None = None
        self._blinks = 0
        self._hold_since: int | None = None

    @property
    def current(self) -> Action | None:
        return self.actions[self.index] if self.status is ChallengeStatus.PENDING and self.index < len(self.actions) else None

    @property
    def prompt(self) -> str:
        if self.status is ChallengeStatus.PASSED:
            return "Done"
        if self.status is ChallengeStatus.FAILED:
            return "Challenge failed"
        if self._reference is None:
            return "Look straight at the camera"
        return f"Please {self.current.value}" if self.current else ""

    def update(self, ts: int, ear: float | None, turn: float | None) -> ChallengeStatus:
        if self.status is not ChallengeStatus.PENDING:
            return self.status
        cfg = self.cfg
        if self._t0 is None:
            self._t0 = ts

        blinked = False
        if ear is None:
            self._eyes.pause()
        else:
            blinked = any(e.type is EventType.BLINK for e in self._eyes.update(ts, ear))

        # phase 1: get ready, learn what "facing front" reads for this person
        if self._reference is None:
            if turn is not None:
                self._turns.append(turn)
            if ts - self._t0 >= cfg.get_ready_ms and self._turns:
                self._reference = float(np.median(self._turns))
                self._action_start = None
            return self.status

        action = self.actions[self.index]
        delta = None if turn is None else turn - self._reference

        # an action starts only once the head is centred again, so a turn cannot carry over
        if self._action_start is None:
            centred = action is Action.BLINK_TWICE or (delta is not None and abs(delta) <= cfg.recenter_delta)
            if centred:
                self._action_start, self._blinks, self._hold_since = ts, 0, None
            return self.status
        if ts - self._action_start > cfg.per_action_ms:
            self.status = ChallengeStatus.FAILED
            return self.status

        done = False
        if action is Action.BLINK_TWICE:
            self._blinks += blinked
            done = self._blinks >= cfg.blinks_needed
        else:
            sign = 1.0 if action is Action.TURN_LEFT else -1.0  # left = positive head_turn()
            if delta is not None and sign * delta >= cfg.turn_delta:
                self._hold_since = self._hold_since if self._hold_since is not None else ts
                done = ts - self._hold_since >= cfg.hold_ms
            else:
                self._hold_since = None

        if done:
            self.index += 1
            self._action_start = None
            if self.index >= len(self.actions):
                self.status = ChallengeStatus.PASSED
        return self.status
