"""Keyboard/mouse idle time from Windows GetLastInputInfo.

This reads only "milliseconds since the last input". It does not log keys or clicks.
"""
from __future__ import annotations

import ctypes
import sys


class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_ms() -> int | None:
    """Milliseconds since the last keyboard/mouse input, or None if unavailable."""
    if sys.platform != "win32":
        return None
    info = _LastInputInfo()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    # GetTickCount is a 32-bit counter that wraps; the mask keeps the subtraction correct.
    return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF


def recent_input(window_ms: int = 2000) -> bool:
    """True when the agent touched the keyboard or mouse in the last window_ms."""
    idle = idle_ms()
    return idle is not None and idle < window_ms
