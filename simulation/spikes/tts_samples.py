"""Spike S1, part 4: which voice should the callers have?

    python simulation/spikes/tts_samples.py            # make the samples and play them, you rate each 1-5
    python simulation/spikes/tts_samples.py --no-play  # only make the files and open the folder

Candidates: Microsoft's neural voices through the edge-tts package (Philippine English and Filipino;
it uses Microsoft's online read-aloud service without an account, which is unofficial, and Azure AI
Speech offers the same voices officially) and Groq's English voices (needs a Groq key, and your
Groq account may first have to accept the voice model's terms in the Groq console).
Use headphones. Only fictional sentences are sent.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

import edge_tts
import httpx
import sounddevice as sd
import soundfile as sf

from _common import GROQ_BASE, SPIKE_DIR, SpikeError, groq_key

OUT = SPIKE_DIR / "tts_samples"
LINES = {
    "en": ["Hello? Hi, I've been waiting on hold for twenty minutes already.", "My internet has been down since yesterday and I really need it for work."],
    "tl": ["Magandang hapon po. Hindi po gumagana ang internet namin mula kahapon.", "Ilang beses na po akong tumawag, wala pa rin pong nangyayari."],
    "taglish": ["Hello po, hindi pa rin gumagana yung internet ko since yesterday.", "Pwede po bang i-check niyo yung billing ko? Parang mali kasi yung charge."],
}
# (provider, voice, languages it is tried on)
VOICES = [
    ("edge", "en-PH-RosaNeural", ["en", "taglish"]),
    ("edge", "en-PH-JamesNeural", ["en", "taglish"]),
    ("edge", "fil-PH-BlessicaNeural", ["tl", "taglish", "en"]),
    ("edge", "fil-PH-AngeloNeural", ["tl", "taglish"]),
    ("groq", "troy", ["en"]),
    ("groq", "hannah", ["en"]),
]


async def edge_synth(text: str, voice: str, path) -> float:
    t0 = time.perf_counter()
    await edge_tts.Communicate(text, voice).save(str(path))
    return time.perf_counter() - t0


def groq_synth(client: httpx.Client, key: str, text: str, voice: str, path) -> float:
    t0 = time.perf_counter()
    r = client.post(
        f"{GROQ_BASE}/audio/speech",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "canopylabs/orpheus-v1-english", "voice": voice, "input": text, "response_format": "wav"},
    )
    if r.status_code >= 400:
        raise SpikeError(f"Groq voice HTTP {r.status_code}: {r.text[:300]}")
    path.write_bytes(r.content)
    return time.perf_counter() - t0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-play", action="store_true")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    try:
        key = groq_key()
    except SpikeError:
        key = ""
        print("(no Groq key: skipping the Groq voices)\n")

    made: list[tuple[str, str, str, int, object, float]] = []  # lang, provider, voice, n, path, synth seconds
    with httpx.Client(timeout=90) as client:
        for provider, voice, langs in VOICES:
            if provider == "groq" and not key:
                continue
            for lang in langs:
                for n, text in enumerate(LINES[lang], 1):
                    ext = "wav" if provider == "groq" else "mp3"
                    path = OUT / f"{lang}_{n}_{voice}.{ext}"
                    try:
                        secs = asyncio.run(edge_synth(text, voice, path)) if provider == "edge" else groq_synth(client, key, text, voice, path)
                    except (SpikeError, Exception) as exc:  # noqa: BLE001 - a failing voice should not stop the others
                        print(f"  {provider}/{voice}: {str(exc)[:200]}")
                        break
                    made.append((lang, provider, voice, n, path, secs))
    if not made:
        print("No samples were made.")
        return 1

    print(f"{'language':<9}{'voice':<26}{'made in':>8}{'audio':>8}")
    for lang, provider, voice, n, path, secs in made:
        data, rate = sf.read(str(path))
        print(f"{lang:<9}{provider + '/' + voice:<26}{secs:>7.2f}s{len(data) / rate:>7.1f}s")
    print(f"\nFiles: {OUT}")

    if args.no_play:
        os.startfile(OUT)  # noqa: S606
        return 0

    print("\nListen with headphones. After each sample type a score from 1 (robotic / wrong accent) to 5 (a believable")
    print("Filipino caller), or just press Enter to skip. For Tagalog and Taglish, the pronunciation matters most.\n")
    ratings: dict[str, int] = {}
    for lang, provider, voice, n, path, secs in made:
        label = f"{lang} line {n} - {provider}/{voice}"
        data, rate = sf.read(str(path))
        print(f"{label}:  \"{LINES[lang][n - 1]}\"")
        sd.play(data, rate)
        sd.wait()
        answer = input("   score 1-5 (r = hear again, Enter = skip): ").strip().lower()
        while answer == "r":
            sd.play(data, rate)
            sd.wait()
            answer = input("   score 1-5 (r = hear again, Enter = skip): ").strip().lower()
        if answer.isdigit() and 1 <= int(answer) <= 5:
            ratings[f"{lang}|{provider}/{voice}|{n}"] = int(answer)
    (SPIKE_DIR / "tts_ratings.json").write_text(json.dumps(ratings, indent=1), encoding="utf-8")

    print("\nAverage score per voice and language:")
    summary: dict[tuple[str, str], list[int]] = {}
    for k, v in ratings.items():
        lang, voice, _ = k.split("|")
        summary.setdefault((lang, voice), []).append(v)
    for (lang, voice), vals in sorted(summary.items()):
        print(f"  {lang:<8}{voice:<28}{sum(vals) / len(vals):.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
