"""Which microphone the agent chose. The choice is kept by device *name* (device numbers change when a
headset is plugged in or out) in a small JSON file next to the other local settings.

Windows lists the same microphone once per audio system (MME, DirectSound, WASAPI...). Only the default
audio system's list is shown, so each microphone appears once.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

try:  # sounddevice needs the PortAudio library; tests run without audio hardware
    import sounddevice as sd
except Exception:  # noqa: BLE001
    sd = None

DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "thesis-microsleep"
SETTINGS = DATA_DIR / "audio.json"


def input_devices() -> list[tuple[int, str]]:
    """(device number, name) of every microphone, in the default audio system, without duplicates."""
    if sd is None:
        return []
    try:
        devices = sd.query_devices()
        host = sd.query_hostapis(sd.default.hostapi) if hasattr(sd.default, "hostapi") else None
    except Exception:  # noqa: BLE001 - no audio system at all
        return []
    allowed = set(host["devices"]) if host else None
    found: list[tuple[int, str]] = []
    seen: set[str] = set()
    for number, info in enumerate(devices):
        if info["max_input_channels"] < 1 or (allowed is not None and number not in allowed):
            continue
        name = str(info["name"]).strip()
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        found.append((number, name))
    return found


def default_input_name() -> str | None:
    if sd is None:
        return None
    try:
        number = sd.default.device[0]
        return str(sd.query_devices(number)["name"]).strip() if number is not None and number >= 0 else None
    except Exception:  # noqa: BLE001
        return None


def saved_name(path: Path = SETTINGS) -> str | None:
    try:
        name = json.loads(path.read_text(encoding="utf-8")).get("microphone")
    except (OSError, ValueError, AttributeError):
        return None
    return name if isinstance(name, str) and name else None


def save_name(name: str | None, path: Path = SETTINGS) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"microphone": name}), encoding="utf-8")
    except OSError:
        pass  # a setting that cannot be saved is only forgotten next time


def device_number(name: str | None, devices: list[tuple[int, str]] | None = None) -> int | None:
    """The number of the microphone with this name right now, or None for the system default
    (nothing chosen, or the chosen one is not plugged in)."""
    if not name:
        return None
    for number, found in (input_devices() if devices is None else devices):
        if found.lower() == name.lower():
            return number
    return None
