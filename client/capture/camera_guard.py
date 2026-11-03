"""Find the physical webcam and refuse virtual cameras.

Real-time face-swap tools (DeepFaceLive and similar) and recorded-video injectors reach an
app by posing as a camera: OBS Virtual Camera, ManyCam, DroidCam, and so on. The client
therefore opens only a camera that is not on the virtual list.

Camera indexes differ between Windows backends, so names are read from the same backend the
client opens (Media Foundation). A real USB webcam also reports a USB vendor/product id,
which software cameras usually lack; that only breaks ties between non-virtual cameras.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass

import cv2
from cv2_enumerate_cameras import enumerate_cameras

# Word-boundary patterns, case-insensitive. Extend as new tools appear.
VIRTUAL_PATTERNS = [
    r"\bobs\b", r"virtual", r"droidcam", r"manycam", r"xsplit", r"snap camera", r"iriun", r"epoccam",
    r"\bivcam\b", r"camtwist", r"streamlabs", r"\bvcam\b", r"nvidia broadcast", r"mmhmm", r"splitcam",
    r"youcam", r"e2esoft", r"webcamoid", r"unity video capture", r"deepfacelive", r"deepfake", r"\bfake\b",
    r"\bcamo\b", r"reincubate", r"kinoni", r"wirecast", r"vmix", r"\bndi\b",
]
_VIRTUAL_RE = re.compile("|".join(VIRTUAL_PATTERNS), re.IGNORECASE)


def default_backend() -> int:
    return cv2.CAP_MSMF if sys.platform == "win32" else cv2.CAP_ANY


def is_virtual_name(name: str) -> bool:
    return bool(_VIRTUAL_RE.search(name or ""))


@dataclass(frozen=True)
class CameraInfo:
    index: int
    name: str
    backend: int
    vid: int | None
    pid: int | None
    virtual: bool

    @property
    def has_usb_id(self) -> bool:
        return self.vid is not None and self.pid is not None


def list_cameras(backend: int | None = None) -> list[CameraInfo]:
    backend = default_backend() if backend is None else backend
    return [
        CameraInfo(c.index, c.name, backend, getattr(c, "vid", None), getattr(c, "pid", None), is_virtual_name(c.name))
        for c in enumerate_cameras(backend)
    ]


def find_physical(backend: int | None = None) -> CameraInfo | None:
    """The best non-virtual camera (one with a USB id first), or None if only virtual ones exist."""
    real = [c for c in list_cameras(backend) if not c.virtual]
    real.sort(key=lambda c: (not c.has_usb_id, c.index))
    return real[0] if real else None
