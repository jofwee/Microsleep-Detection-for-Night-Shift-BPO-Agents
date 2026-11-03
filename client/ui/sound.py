"""The alert ping.

The first version used Qt's beep, which is the Windows "Default Beep" system sound: it is silent whenever the
sound scheme is "No Sounds" or that sound is muted. This makes its own two-tone ping (a WAV written once next to
the other local files) and plays it through the Windows mixer, so it is heard whatever the scheme says, and it
mixes with the caller's voice instead of cutting it off.
"""
from __future__ import annotations

import math
import os
import struct
import sys
import wave
from pathlib import Path

RATE = 22_050
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "thesis-microsleep"


def ping_samples(rate: int = RATE) -> list[int]:
    """Two rising tones (A5, then D6), 0.55 s in total, with short fades so there are no clicks."""
    out: list[int] = []
    for freq, seconds in ((880.0, 0.22), (1174.7, 0.33)):
        n = int(rate * seconds)
        fade = int(rate * 0.012)
        for i in range(n):
            gain = min(1.0, i / fade, (n - i) / fade)
            out.append(int(0.7 * 32767 * gain * math.sin(2 * math.pi * freq * i / rate)))
    return out


def write_ping(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(struct.pack(f"<{len(ping_samples())}h", *ping_samples()))
    return path


def play_ping(path: Path | None = None) -> bool:
    """Play the ping without waiting for it to end. Returns False if it could not be played."""
    if sys.platform != "win32":
        return False
    try:
        import winsound

        target = path or DATA_DIR / "ping.wav"
        if not target.is_file():
            write_ping(target)
        winsound.PlaySound(str(target), winsound.SND_FILENAME | winsound.SND_ASYNC)
        return True
    except Exception:  # noqa: BLE001 - an alert must never crash the app because of its sound
        return False
