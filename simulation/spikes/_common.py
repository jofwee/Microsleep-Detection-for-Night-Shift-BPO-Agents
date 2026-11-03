"""Shared helpers for the Spike S1 scripts: where files go, the Groq key, word/character error rates."""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

HOME = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "thesis-microsleep"
OWNER_ENV = HOME / "owner.env"
SPIKE_DIR = HOME / "spike_s1"  # recordings of your voice stay here: outside OneDrive and git
SENTENCES = Path(__file__).resolve().parent / "sentences.json"

GROQ_BASE = "https://api.groq.com/openai/v1"


class SpikeError(RuntimeError):
    pass


def groq_key() -> str:
    """GROQ_API_KEY from the environment or owner.env. Never printed."""
    key = os.environ.get("GROQ_API_KEY", "")
    if not key and OWNER_ENV.is_file():
        for line in OWNER_ENV.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("GROQ_API_KEY="):
                key = line.partition("=")[2].strip().strip('"').strip("'")
    if not key:
        raise SpikeError(
            f"no Groq key yet. Create one at console.groq.com (API Keys), then put it after GROQ_API_KEY= in {OWNER_ENV}"
        )
    if not key.startswith("gsk_"):
        raise SpikeError("the GROQ_API_KEY value does not look like a Groq key (they start with gsk_)")
    return key


# --- error rates ---------------------------------------------------------------------------
def normalise(text: str) -> str:
    """Lower case, no accents or punctuation. 'i-check' and 'i check' count as the same words."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9\s]", " ", text.lower().replace("-", " "))
    return re.sub(r"\s+", " ", text).strip()


def edit_distance(a: list, b: list) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate on normalised text: (substitutions + deletions + insertions) / reference words."""
    ref, hyp = normalise(reference).split(), normalise(hypothesis).split()
    if not ref:
        return 0.0 if not hyp else 1.0
    return edit_distance(ref, hyp) / len(ref)


def cer(reference: str, hypothesis: str) -> float:
    """Character error rate on normalised text, spaces ignored. Fairer for spelling variants."""
    ref, hyp = list(normalise(reference).replace(" ", "")), list(normalise(hypothesis).replace(" ", ""))
    if not ref:
        return 0.0 if not hyp else 1.0
    return edit_distance(ref, hyp) / len(ref)
