"""Spike S1, part 1: record yourself reading the agent sentences (English, Tagalog, Taglish).

    python simulation/spikes/record_utterances.py                 # all 24 sentences, ~5 minutes
    python simulation/spikes/record_utterances.py --only taglish  # just one language
    python simulation/spikes/record_utterances.py --device 1      # choose a microphone by its number
    python simulation/spikes/record_utterances.py --redo          # record everything again

For each sentence: press Enter, read it out loud the way you would on a call, press Enter again.
Recordings are saved in %LOCALAPPDATA%\\thesis-microsleep\\spike_s1\\recordings, outside OneDrive
and git, because they are your voice. Nothing is sent anywhere by this script.

Use your headset microphone, in a quiet room. Speak at normal call speed.
"""
from __future__ import annotations

import argparse
import json
import sys
import wave

import numpy as np
import sounddevice as sd

from _common import SENTENCES, SPIKE_DIR

RATE = 16_000  # what speech-to-text models want
REC_DIR = SPIKE_DIR / "recordings"
LABELS = {"en": "English", "tl": "Tagalog", "taglish": "Taglish"}


def pick_device(spec: str | None) -> int | None:
    if spec is None:
        return None
    if spec.isdigit():
        return int(spec)
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and spec.lower() in d["name"].lower():
            return i
    raise SystemExit(f"no microphone matching '{spec}'. Run without --device to use the default.")


def record_once(device: int | None) -> np.ndarray:
    chunks: list[np.ndarray] = []

    def callback(indata, frames, time, status):
        chunks.append(indata.copy())

    with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", device=device, callback=callback):
        input("   recording... press Enter when you have finished the sentence ")
    return np.concatenate(chunks)[:, 0] if chunks else np.zeros(0, dtype=np.int16)


def save_wav(path, samples: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.tobytes())


def check(samples: np.ndarray) -> tuple[float, str]:
    seconds = len(samples) / RATE
    peak = float(np.abs(samples).max()) / 32768 if len(samples) else 0.0
    if seconds < 1.0:
        return seconds, "too short: did you press Enter too early?"
    if peak < 0.03:
        return seconds, "very quiet: move closer to the microphone or raise its volume"
    if peak > 0.98:
        return seconds, "clipping: too loud, move back a little"
    return seconds, ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", help="microphone number or part of its name (default: the system default)")
    ap.add_argument("--only", choices=list(LABELS), help="record just one language")
    ap.add_argument("--redo", action="store_true", help="record sentences that already have a recording")
    args = ap.parse_args()

    device = pick_device(args.device)
    shown = sd.query_devices(device if device is not None else sd.default.device[0])
    print(f"Microphone: {shown['name']}")
    print(f"Recordings go to: {REC_DIR}\n")

    sentences = json.loads(SENTENCES.read_text(encoding="utf-8"))
    for lang in (args.only,) if args.only else LABELS:
        items = sentences[lang]
        for n, text in enumerate(items, 1):
            path = REC_DIR / f"{lang}_{n}.wav"
            if path.exists() and not args.redo:
                continue
            while True:
                print(f"[{LABELS[lang]} {n}/{len(items)}]  {text}")
                answer = input("   Enter = start recording, s = skip, q = quit: ").strip().lower()
                if answer == "q":
                    return 0
                if answer == "s":
                    break
                samples = record_once(device)
                seconds, problem = check(samples)
                print(f"   {seconds:.1f} s recorded" + (f"  -- {problem}" if problem else ""))
                keep = input("   Enter = keep it, r = record again: ").strip().lower()
                if keep != "r":
                    save_wav(path, samples)
                    break
            print()
    have = sorted(p.name for p in REC_DIR.glob("*.wav"))
    print(f"Done. {len(have)} recordings saved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
