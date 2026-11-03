"""The caller's voice. Spike S1 chose Microsoft's neural voices through the edge-tts package.

Windows has no Filipino voice and Groq's voices have no Tagalog. edge-tts is an unofficial client of
Microsoft's online read-aloud service (no key, no account); Azure AI Speech offers the same voices
officially. Everything sits behind the Synthesizer interface, so swapping providers touches one class.
Synthesis takes 1-2 s per sentence, so speak() synthesizes sentence by sentence and starts playing
the first one while the rest are still being made.
"""
from __future__ import annotations

import asyncio
import io
import queue
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

import numpy as np

VOICES = {"en": "en-PH-RosaNeural", "tl": "fil-PH-BlessicaNeural", "taglish": "fil-PH-BlessicaNeural"}


@dataclass
class Synthesis:
    samples: np.ndarray  # float32 mono, -1..1
    rate: int
    ms: int  # time taken to make it


class Synthesizer(Protocol):
    def synth(self, text: str, language: str) -> Synthesis: ...


class EdgeTtsSynthesizer:
    def __init__(self, voices: dict[str, str] | None = None) -> None:
        self.voices = voices or VOICES

    def synth(self, text: str, language: str) -> Synthesis:
        import edge_tts  # imported late: tests use a fake synthesizer
        import soundfile as sf

        voice = self.voices[language]
        t0 = time.perf_counter()

        async def fetch() -> bytes:
            chunks = []
            async for chunk in edge_tts.Communicate(text, voice).stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])
            return b"".join(chunks)

        audio = asyncio.run(fetch())
        if not audio:
            raise RuntimeError("the voice service returned no audio")
        data, rate = sf.read(io.BytesIO(audio), dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        return Synthesis(data, int(rate), round((time.perf_counter() - t0) * 1000))


_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def split_sentences(text: str, min_chars: int = 14) -> list[str]:
    """Break a reply into sentences to speak one at a time. Tiny fragments ("Oo.", "Ano?") join the next."""
    parts = [p.strip() for p in _SPLIT.split(text.strip()) if p.strip()]
    out: list[str] = []
    carry = ""
    for p in parts:
        p = f"{carry} {p}".strip() if carry else p
        if len(p) < min_chars:
            carry = p
        else:
            out.append(p)
            carry = ""
    if carry:
        if out:
            out[-1] = f"{out[-1]} {carry}"
        else:
            out.append(carry)
    out = out or ([text.strip()] if text.strip() else [])
    return _short_first(out)


_CLAUSE = re.compile(r"[,;:]\s")


def _short_first(sentences: list[str], long_chars: int = 60, min_head: int = 18) -> list[str]:
    """A long opening sentence is cut at its first comma, so the voice can start sooner (synthesis time grows with length)."""
    if not sentences or len(sentences[0]) <= long_chars:
        return sentences
    for m in _CLAUSE.finditer(sentences[0]):
        head, tail = sentences[0][: m.end() - 1], sentences[0][m.end():]
        if len(head) >= min_head and len(tail) >= min_head:
            return [head, tail, *sentences[1:]]
    return sentences


def speak(
    text: str,
    language: str,
    synth: Synthesizer,
    play: Callable[[np.ndarray, int], None],
    on_first_audio: Callable[[int], None] | None = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> int:
    """Say `text`, sentence by sentence. Returns milliseconds until the first sentence was ready to play.
    `cancelled` is checked before every sentence: when it turns true nothing more is made or played."""
    sentences = split_sentences(text)
    ready: queue.Queue[Synthesis | Exception | None] = queue.Queue()

    def producer() -> None:
        try:
            for s in sentences:
                if cancelled():
                    break
                ready.put(synth.synth(s, language))
        except Exception as exc:  # noqa: BLE001 - handed to the playing thread
            ready.put(exc)
        finally:
            ready.put(None)

    t0 = time.perf_counter()
    threading.Thread(target=producer, name="tts", daemon=True).start()
    first_ms = -1
    while True:
        item = ready.get()
        if item is None:
            break
        if cancelled():
            continue  # drain what is left (the producer stops by itself) but play nothing more
        if isinstance(item, Exception):
            raise item
        if first_ms < 0:
            first_ms = round((time.perf_counter() - t0) * 1000)
            if on_first_audio:
                on_first_audio(first_ms)
        play(item.samples, item.rate)
    return max(first_ms, 0)
