"""Spike S1, part 2b: can a better Whisper prompt fix the Tagalog transcripts?

    python simulation/spikes/stt_context_eval.py

Re-sends your saved recordings with different prompts and compares the word error rate (lower is better).
Whisper treats the prompt as "what came just before", so a prompt written in the same style (and the
caller's last line) can steer spelling and language. None of the prompts below repeat the sentences you
read, so a better score is not the test sentences leaking back. The recordings go to Groq's API for this test.
"""
from __future__ import annotations

import json
import sys

import httpx

from _common import SENTENCES, SPIKE_DIR, SpikeError, cer, groq_key, wer
from stt_eval import LANGS, REC_DIR, transcribe

MODEL = "whisper-large-v3"

BASE = (
    "Customer service call between a Filipino agent and a customer, in English, Tagalog or Taglish "
    "(a mix of both). Words like po, opo, yung, account number, refund, billing, i-check, ma-process."
)
# written the way a Filipino call-centre agent talks: natural Tagalog and Taglish, no sentence from the test set
STYLE = (
    "Opo, sandali lang po. Nakikita ko po na may balanse po kayo sa account. Pasensya na po kayo, "
    "ire-reset ko po yung modem ninyo. Sir, pwede po bang i-restart niyo muna? Okay po, na-process na po yung request ninyo."
)
CALLER_LINE = "Hello po, bagal po ng internet namin, gusto ko po sanang mag-upgrade ng plan."
CONTEXT = f"Customer: {CALLER_LINE}\nAgent:"
STYLE_CONTEXT = f"{STYLE}\nCustomer: {CALLER_LINE}\nAgent:"

# (label, language hint, prompt)
VARIANTS = [
    ("current prompt", None, BASE),
    ("Taglish-style prompt", None, STYLE),
    ("caller's last line", None, CONTEXT),
    ("style + last line", None, STYLE_CONTEXT),
    ("style + last line + tl", "tl", STYLE_CONTEXT),
]


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
        print(f"No recordings in {REC_DIR}.")
        return 1
    print(f"{len(clips)} recordings x {len(VARIANTS)} prompts (a few minutes)\n")

    rows: list[dict] = []
    with httpx.Client(timeout=90) as client:
        for label, hint, prompt in VARIANTS:
            print(f"== {label}", flush=True)
            for lang, n, path in clips:
                hyp, secs = transcribe(client, key, path.read_bytes(), MODEL, hint, prompt)
                ref = refs[lang][n - 1]
                rows.append({"variant": label, "lang": lang, "n": n, "ref": ref, "hyp": hyp, "wer": wer(ref, hyp), "cer": cer(ref, hyp), "secs": secs})
    (SPIKE_DIR / "stt_context_results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    def mean(xs):
        return sum(xs) / len(xs)

    print("\nMean word error rate / character error rate (lower is better)")
    print(f"{'prompt':<26}" + "".join(f"{lang:>18}" for lang in LANGS))
    for label, *_ in VARIANTS:
        line = f"{label:<26}"
        for lang in LANGS:
            sel = [r for r in rows if r["variant"] == label and r["lang"] == lang]
            line += f"{f'{mean([r['wer'] for r in sel]):.0%} / {mean([r['cer'] for r in sel]):.0%}':>18}"
        print(line)

    print("\nTagalog clips: current prompt vs the best idea (reference / current / style + last line)")
    for n in range(1, len(refs["tl"]) + 1):
        pick = lambda v: next(r["hyp"] for r in rows if r["variant"] == v and r["lang"] == "tl" and r["n"] == n)  # noqa: E731
        print(f"  {refs['tl'][n - 1]}\n    now : {pick('current prompt')}\n    new : {pick('style + last line')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
