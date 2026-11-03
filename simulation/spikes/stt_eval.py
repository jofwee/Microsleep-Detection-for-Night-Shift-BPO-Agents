"""Spike S1, part 2: how well does Groq's Whisper understand your recordings?

    python simulation/spikes/stt_eval.py

Sends each recording (from record_utterances.py) to Groq in several settings, compares the
transcript with the sentence you read, and prints the word and character error rate (lower is
better) and the speed. The recordings are sent to Groq's speech-to-text API for this test.
Results are saved next to the recordings, outside OneDrive.
"""
from __future__ import annotations

import json
import re
import sys
import time

import httpx

from _common import GROQ_BASE, SENTENCES, SPIKE_DIR, SpikeError, cer, groq_key, wer

REC_DIR = SPIKE_DIR / "recordings"
LANGS = ["en", "tl", "taglish"]
# Tagalog audio is "tl"; Taglish is mostly Tagalog grammar, so "tl" is the natural hint for it too.
LANGUAGE_HINT = {"en": "en", "tl": "tl", "taglish": "tl"}
PROMPT = (
    "Customer service call between a Filipino agent and a customer, in English, Tagalog or Taglish "
    "(a mix of both). Words like po, opo, yung, account number, refund, billing, i-check, ma-process."
)

# (label, model, set the language hint?, add the Taglish prompt?)
COMBOS = [
    ("turbo, auto language", "whisper-large-v3-turbo", False, False),
    ("v3, auto language", "whisper-large-v3", False, False),
    ("v3, language hint", "whisper-large-v3", True, False),
    ("v3, auto + prompt", "whisper-large-v3", False, True),
    ("v3, hint + prompt", "whisper-large-v3", True, True),
    ("turbo, hint + prompt", "whisper-large-v3-turbo", True, True),
]


def transcribe(client: httpx.Client, key: str, wav: bytes, model: str, language: str | None, prompt: str | None) -> tuple[str, float]:
    data = {"model": model, "response_format": "json", "temperature": "0"}
    if language:
        data["language"] = language
    if prompt:
        data["prompt"] = prompt
    for attempt in range(6):
        t0 = time.perf_counter()
        r = client.post(
            f"{GROQ_BASE}/audio/transcriptions",
            headers={"Authorization": f"Bearer {key}"},
            data=data,
            files={"file": ("clip.wav", wav, "audio/wav")},
        )
        if r.status_code == 429:  # rate limited: wait as long as Groq asks
            wait = float(r.headers.get("retry-after") or (re.findall(r"in ([\d.]+)s", r.text) or [5])[0])
            print(f"      rate limited, waiting {wait:.0f} s", flush=True)
            time.sleep(wait + 1)
            continue
        if r.status_code >= 400:
            raise SpikeError(f"Groq answered HTTP {r.status_code}: {r.text[:300]}")
        return r.json().get("text", "").strip(), time.perf_counter() - t0
    raise SpikeError("still rate limited after several waits")


def main() -> int:
    try:
        key = groq_key()
    except SpikeError as exc:
        print(exc)
        return 1
    refs = json.loads(SENTENCES.read_text(encoding="utf-8"))
    clips = [(lang, n, REC_DIR / f"{lang}_{n}.wav") for lang in LANGS for n in range(1, len(refs[lang]) + 1)]
    clips = [c for c in clips if c[2].is_file()]
    if not clips:
        print(f"No recordings in {REC_DIR}. Run record_utterances.py first.")
        return 1
    print(f"{len(clips)} recordings x {len(COMBOS)} settings = {len(clips) * len(COMBOS)} requests (a few minutes)\n")

    rows: list[dict] = []
    with httpx.Client(timeout=90) as client:
        for label, model, use_hint, use_prompt in COMBOS:
            print(f"== {label}", flush=True)
            for lang, n, path in clips:
                hyp, secs = transcribe(
                    client, key, path.read_bytes(), model,
                    LANGUAGE_HINT[lang] if use_hint else None, PROMPT if use_prompt else None,
                )
                ref = refs[lang][n - 1]
                rows.append({"combo": label, "lang": lang, "n": n, "ref": ref, "hyp": hyp, "wer": wer(ref, hyp), "cer": cer(ref, hyp), "secs": secs})

    (SPIKE_DIR / "stt_results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    def mean(xs):
        return sum(xs) / len(xs)

    print("\nMean word error rate (WER) / character error rate (CER), lower is better; seconds per clip")
    print(f"{'setting':<24}" + "".join(f"{lang:>20}" for lang in LANGS) + f"{'speed':>9}")
    best: dict[str, tuple[float, str]] = {}
    for label, *_ in COMBOS:
        line = f"{label:<24}"
        for lang in LANGS:
            sel = [r for r in rows if r["combo"] == label and r["lang"] == lang]
            if not sel:
                line += f"{'-':>20}"
                continue
            w, c = mean([r["wer"] for r in sel]), mean([r["cer"] for r in sel])
            line += f"{f'{w:.0%} / {c:.0%}':>20}"
            if lang not in best or w < best[lang][0]:
                best[lang] = (w, label)
        line += f"{mean([r['secs'] for r in rows if r['combo'] == label]):>8.2f}s"
        print(line)
    print("\nBest setting per language:", {lang: f"{label} ({w:.0%} WER)" for lang, (w, label) in best.items()})

    print("\nWhat it heard with the best setting for Taglish (reference -> transcript):")
    label = best.get("taglish", (0, COMBOS[0][0]))[1]
    for r in [r for r in rows if r["combo"] == label and r["lang"] == "taglish"]:
        print(f"  [{r['wer']:.0%}] {r['ref']}\n        -> {r['hyp']}")
    print(f"\nFull results: {SPIKE_DIR / 'stt_results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
