"""Speech to text: the agent's utterance goes to the `transcribe` Edge Function (Groq Whisper)."""
from __future__ import annotations

import io
import time
import wave

import numpy as np

from auth.supabase_client import Api

from .vad import RATE


def wav_bytes(samples: np.ndarray, rate: int = RATE) -> bytes:
    """16-bit mono PCM WAV."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.asarray(samples, dtype=np.int16).tobytes())
    return buf.getvalue()


class Transcriber:
    def __init__(self, api: Api) -> None:
        self.api = api
        self.last: dict = {}  # the server's answer to the latest call, with its own timings

    def transcribe(self, call_id: str, samples: np.ndarray) -> tuple[str, int]:
        """(text, milliseconds for the whole round trip). The audio is not stored anywhere."""
        t0 = time.perf_counter()
        result = self.api.upload("transcribe", {"call_id": call_id}, "speech.wav", wav_bytes(samples), "audio/wav")
        self.last = result
        return result["text"].strip(), round((time.perf_counter() - t0) * 1000)
