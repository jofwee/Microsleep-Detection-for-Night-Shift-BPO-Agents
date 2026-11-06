"""One live call: listen to the agent, have the words transcribed, ask the AI caller what it says next,
and speak the answer. Half-duplex: the microphone is muted while the caller talks.

    caller speaks (opening) -> agent speaks -> [silence detected] -> transcribe -> caller-turn -> caller speaks -> ...

The loop ends when the caller finishes the call (satisfied, hangs up, asks for a supervisor), when the
agent asks to end it, or when the server says the call is no longer active.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

import numpy as np

from auth.supabase_client import Api, ApiError

from .audio.tts import Synthesizer, speak
from .audio.vad import UtteranceDetector


STATES = ("connecting", "caller", "listening", "hearing", "thinking")  # what the call is doing right now
END_SILENCE_MS = 500 # how long the agent must be quiet before the caller answers; 700 felt slow, 400 cuts people off


class Mic(Protocol):
    def read(self, timeout: float = 0.1) -> np.ndarray | None: ...
    def flush(self) -> None: ...


class Transcribing(Protocol):
    def transcribe(self, call_id: str, samples: np.ndarray) -> tuple[str, int]: ...


@dataclass
class TurnTiming:
    """Milliseconds for one spoken exchange, measured from the moment the agent's sentence was detected as finished."""
    stt_ms: int
    caller_ms: int  # the caller model, as measured by the server
    voice_ms: int  # until the first sentence of the reply was ready to play
    total_ms: int  # detection of the end of speech -> the caller starts speaking


@dataclass
class CallOutcome:
    ended_by: str  # "caller", "agent" or "server"
    caller_state: str | None = None  # satisfied / hangup / escalating when the caller ended it
    turns: int = 0
    timings: list[TurnTiming] = field(default_factory=list)


class CallSession:
    def __init__(
        self,
        api: Api,
        call_id: str,
        mic: Mic,
        transcriber: Transcribing,
        synth: Synthesizer,
        play: Callable[[np.ndarray, int], None],
        *,
        make_detector: Callable[[], UtteranceDetector] = lambda: UtteranceDetector(end_silence_ms=END_SILENCE_MS),
        now: Callable[[], float] = time.monotonic,
        end_requested: Callable[[], bool] = lambda: False,
        silence_after_s: float = 8.0,
        max_silences: int = 3,
        log: Callable[[str], None] = print,
        on_turn: Callable[[str, str], None] | None = None,
        on_state: Callable[[str], None] | None = None,
    ) -> None:
        self.api, self.call_id, self.mic = api, call_id, mic
        self.transcriber, self.synth, self.play = transcriber, synth, play
        self.detector = make_detector()
        self.now, self.end_requested = now, end_requested
        self.silence_after_s, self.max_silences, self.log = silence_after_s, max_silences, log
        self.on_turn = on_turn  # (speaker, text), for a window that shows the conversation
        self.on_state = on_state  # one of STATES, for the "can I speak now?" indicator
        self._state = ""
        self._ended = False
        self.language = "taglish"
        self._tts_ms: int | None = None  # how long the previous caller line took to start, reported on the next turn
        self.outcome = CallOutcome("server")

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            if self.on_state:
                self.on_state(state)

    # --- server and voice -------------------------------------------------------------------
    def _turn(self, fields: dict) -> dict:
        payload = {"call_id": self.call_id, **fields}
        if self._tts_ms is not None:
            payload["tts_ms"] = self._tts_ms
            self._tts_ms = None
        for attempt in range(3):
            try:
                reply = self.api.function("caller-turn", payload)
                self.language = reply.get("language", self.language)
                return reply
            except ApiError as exc:
                if exc.code in ("too_many_requests", "caller_unavailable", "turn_conflict") and attempt < 2:
                    time.sleep(1.0 + attempt)  # a moment of patience, then try again
                    continue
                raise
        raise AssertionError("unreachable")

    def _ending(self) -> bool:
        """True once the agent asked to end the call (remembered, because asking may consume a key press)."""
        if not self._ended and self.end_requested():
            self._ended = True
        return self._ended

    def _say(self, text: str) -> int:
        if self._ending():
            return 0  # the agent already hung up: the caller does not talk
        self.log(f"  caller: {text}")
        if self.on_turn:
            self.on_turn("caller", text)
        self.mic.flush()
        self._set_state("caller")
        first_ms = speak(text, self.language, self.synth, self.play, cancelled=self._ending)
        self.mic.flush()
        self._tts_ms = first_ms
        self._set_state("listening")  # the caller finished: the agent may speak
        return first_ms

    # --- the loop ---------------------------------------------------------------------------
    def run(self) -> CallOutcome:
        try:
            self._set_state("connecting")
            self._run()
        except ApiError as exc:
            if exc.code in ("call_not_active", "call_not_found"):
                self.log("  (the call is no longer active)")
                self.outcome.ended_by = "server"
            else:
                raise
        return self.outcome

    def _caller_finished(self, reply: dict) -> bool:
        if reply.get("ended"):
            self.outcome.ended_by, self.outcome.caller_state = "caller", reply.get("state")
            return True
        return False

    def _run(self) -> None:
        if self._ending():
            self.outcome.ended_by = "agent"
            return
        reply = self._turn({"opening": True})
        self.outcome.turns += 1
        self._say(reply["text"])
        if self._caller_finished(reply):
            return
        self.detector.reset()
        last_caller_end, silences = self.now(), 0

        while True:
            if self._ending():
                self.outcome.ended_by = "agent"
                return
            frame = self.mic.read(0.1)
            utterance = self.detector.feed(frame) if frame is not None else None
            if utterance is None and self._state in ("listening", "hearing"):
                self._set_state("hearing" if self.detector.in_speech else "listening")

            if utterance is None:
                quiet_for = self.now() - last_caller_end
                if not self.detector.in_speech and quiet_for >= self.silence_after_s and silences < self.max_silences:
                    silences += 1
                    self.log("  (dead air: the caller asks if anyone is there)")
                    reply = self._turn({"silence": True})
                    self.outcome.turns += 1
                    self._say(reply["text"])
                    last_caller_end = self.now()
                    self.detector.reset()
                    if self._caller_finished(reply):
                        return
                continue

            # the agent finished a sentence
            t_end = self.now()
            self._set_state("thinking")
            text, stt_ms = self.transcriber.transcribe(self.call_id, utterance)
            if not text:
                self._set_state("listening")
                continue  # a cough or a click: nothing recognised
            self.log(f"  you   : {text}")
            if self.on_turn:
                self.on_turn("agent", text)
            reply = self._turn({"agent_text": text, "stt_ms": stt_ms})
            self.outcome.turns += 1
            before_voice = self.now()
            voice_ms = self._say(reply["text"])
            self.outcome.timings.append(TurnTiming(
                stt_ms=stt_ms, caller_ms=int(reply.get("llm_ms", 0)), voice_ms=voice_ms,
                total_ms=round((before_voice - t_end) * 1000) + voice_ms,
            ))
            t = self.outcome.timings[-1]
            self.log(f"        [{t.total_ms / 1000:.1f} s to the caller's voice: speech to text {t.stt_ms} ms, "
                     f"caller {t.caller_ms} ms, first sentence {t.voice_ms} ms]")
            heard = getattr(self.transcriber, "last", None) or {}
            if "server_ms" in heard and "server_ms" in reply:
                self.log(f"        [inside the functions: transcribe {heard['server_ms']} ms (Whisper {heard.get('ms', '?')}), "
                         f"caller-turn {reply['server_ms']} ms (lookups {reply.get('prep_ms', '?')}, model {t.caller_ms}); "
                         f"the rest is the network]")
            last_caller_end, silences = self.now(), 0
            self.detector.reset()
            if self._caller_finished(reply):
                return
