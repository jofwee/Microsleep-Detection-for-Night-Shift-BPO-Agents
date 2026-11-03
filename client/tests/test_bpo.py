"""M5 client: voice activity detection, sentence splitting, speaking, transcription, the call loop, the desk."""
import io
import wave

import time

import numpy as np
import pytest

from auth.supabase_client import ApiError
from bpo import customer_panel
from bpo.audio import stt, tts
from bpo.audio.vad import FRAME, RATE, UtteranceDetector
from bpo.call_session import CallSession
from bpo.status import AgentDesk, HeartbeatThread

rng = np.random.default_rng(1)


def quiet(n=1):
    return [(rng.normal(0, 30, FRAME)).astype(np.int16) for _ in range(n)]


def loud(n=1):
    return [(rng.normal(0, 4000, FRAME)).astype(np.int16) for _ in range(n)]


def run_detector(frames, det=None):
    det = det or UtteranceDetector()
    return [u for u in (det.feed(f) for f in frames) if u is not None]


# --- VAD ---
def test_vad_finds_one_utterance():
    out = run_detector(quiet(30) + loud(25) + quiet(40))
    assert len(out) == 1
    assert 20 * FRAME <= len(out[0]) <= 40 * FRAME


def test_vad_ignores_a_click():
    assert run_detector(quiet(30) + loud(3) + quiet(40)) == []


def test_vad_silence_gives_nothing():
    assert run_detector(quiet(100)) == []


def test_vad_two_utterances():
    assert len(run_detector(quiet(30) + loud(20) + quiet(40) + loud(20) + quiet(40))) == 2


def test_vad_cuts_very_long_speech():
    det = UtteranceDetector(max_utterance_ms=1000)
    assert len(run_detector(quiet(30) + loud(100), det)) >= 1


# --- text and speech ---
def test_split_sentences_keeps_short_fragments_attached():
    parts = tts.split_sentences("Oo. Ano po ang problema ninyo ngayon? Salamat po.")
    assert all(len(p) >= 14 for p in parts)
    assert " ".join(parts) == "Oo. Ano po ang problema ninyo ngayon? Salamat po."


def test_split_sentences_empty_and_single():
    assert tts.split_sentences("") == []
    assert tts.split_sentences("Hello there, good morning") == ["Hello there, good morning"]


class FakeSynth:
    def __init__(self, fail_on=None):
        self.calls, self.fail_on = [], fail_on

    def synth(self, text, language):
        self.calls.append((text, language))
        if self.fail_on and self.fail_on in text:
            raise RuntimeError("voice down")
        return tts.Synthesis(np.zeros(100, dtype=np.float32), 24000, 5)


def test_speak_plays_every_sentence_in_order():
    played, synth = [], FakeSynth()
    ms = tts.speak("Hello po, good morning to you. I have a problem with my bill.", "taglish", synth,
                   lambda s, r: played.append(r))
    assert len(played) == len(synth.calls) == 2
    assert all(lang == "taglish" for _, lang in synth.calls)
    assert ms >= 0


def test_speak_raises_when_voice_fails():
    with pytest.raises(RuntimeError):
        tts.speak("This one is fine. This one will break everything.", "en", FakeSynth("break"), lambda s, r: None)


# --- transcription ---
def test_wav_bytes_is_valid_16k_mono():
    data = stt.wav_bytes(np.ones(1600, dtype=np.int16))
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getnframes()) == (1, RATE, 1600)


class FakeApi:
    def __init__(self, replies=(), errors=()):
        self.replies, self.errors, self.sent = list(replies), list(errors), []

    def function(self, name, payload):
        self.sent.append((name, payload))
        if self.errors:
            e = self.errors.pop(0)
            if e:
                raise e
        return self.replies.pop(0)

    def rpc(self, name, args=None):
        self.sent.append((name, args))
        return self.replies.pop(0) if self.replies else None

    def upload(self, name, fields, filename, content, content_type, timeout=60):
        self.sent.append((name, fields, filename, content_type))
        return self.replies.pop(0)


def test_transcriber_uploads_wav_and_strips_text():
    api = FakeApi([{"text": "  Hello po  "}])
    text, ms = stt.Transcriber(api).transcribe("c1", np.ones(800, dtype=np.int16))
    assert text == "Hello po" and ms >= 0
    assert api.sent[0] == ("transcribe", {"call_id": "c1"}, "speech.wav", "audio/wav")


# --- the call loop ---
class ScriptMic:
    """Yields scripted frames; flush() is counted so half-duplex can be checked."""

    def __init__(self, script):
        self.script, self.flushes = list(script), 0

    def read(self, timeout=0.1):
        return self.script.pop(0) if self.script else None

    def flush(self):
        self.flushes += 1


class FakeTranscriber:
    def __init__(self, texts):
        self.texts = list(texts)

    def transcribe(self, call_id, samples):
        return self.texts.pop(0), 120


def reply(text, state="continuing", ended=False):
    return {"text": text, "state": state, "language": "taglish", "ended": ended, "llm_ms": 400}


def make_session(api, mic, texts=(), **kw):
    lines = []
    s = CallSession(api, "c1", mic, FakeTranscriber(texts), FakeSynth(), lambda s_, r: None,
                    log=lines.append, **kw)
    s.lines = lines
    return s


SPEECH = quiet(20) + loud(20) + quiet(40)


def test_call_full_exchange_ends_when_caller_is_satisfied():
    api = FakeApi([reply("Hello po, may problema ako sa internet."), reply("Salamat po, okay na.", "satisfied", True)])
    out = make_session(api, ScriptMic(SPEECH), ["Good morning, how can I help"]).run()
    assert out.ended_by == "caller" and out.caller_state == "satisfied"
    assert out.turns == 2 and len(out.timings) == 1
    assert api.sent[0][1] == {"call_id": "c1", "opening": True}
    assert api.sent[1][1]["agent_text"] == "Good morning, how can I help" and api.sent[1][1]["stt_ms"] == 120
    assert "tts_ms" in api.sent[1][1]  # the previous caller line's timing is reported on the next turn


def test_call_mic_is_flushed_around_caller_speech():
    api = FakeApi([reply("Hello po."), reply("Bye po.", "hangup", True)])
    mic = ScriptMic(SPEECH)
    make_session(api, mic, ["Hi"]).run()
    assert mic.flushes >= 4


def test_call_ignores_unrecognised_noise():
    api = FakeApi([reply("Hello po."), reply("Bye po.", "satisfied", True)])
    s = make_session(api, ScriptMic(SPEECH + SPEECH), ["", "Hello"])
    assert s.run().turns == 2


def test_call_dead_air_prompts_then_agent_can_end():
    t = [0.0]

    def now():
        t[0] += 3.0
        return t[0]

    polls = {"n": 0}

    def end_requested():
        polls["n"] += 1
        return polls["n"] > 12

    api = FakeApi([reply("Hello po."), reply("Hello? Andyan pa po ba kayo?")])
    s = make_session(api, ScriptMic(quiet(50)), now=now, end_requested=end_requested,
                     silence_after_s=8.0, max_silences=1)
    out = s.run()
    assert out.ended_by == "agent"
    assert sum(1 for _, p in api.sent if p.get("silence")) == 1


def test_call_not_active_ends_quietly():
    api = FakeApi(errors=[ApiError(409, "call_not_active", "gone")])
    assert make_session(api, ScriptMic([])).run().ended_by == "server"


def test_call_other_errors_propagate():
    api = FakeApi(errors=[ApiError(500, "boom", "x")])
    with pytest.raises(ApiError):
        make_session(api, ScriptMic([])).run()


def test_call_retries_a_busy_server(monkeypatch):
    monkeypatch.setattr("bpo.call_session.time.sleep", lambda s: None)
    api = FakeApi([reply("Hello po.", "hangup", True)], errors=[ApiError(429, "too_many_requests", "slow")])
    assert make_session(api, ScriptMic([])).run().ended_by == "caller"


# --- the desk ---
def test_desk_rpc_payloads():
    api = FakeApi([{"status": "AVAILABLE"}])
    d = AgentDesk(api)
    d.heartbeat("LIVE", True, "ALERT")
    d.go_available()
    d.accept("c1")
    d.finish_wrap_up("c1", "resolved", "ok")
    assert api.sent[0] == ("heartbeat", {"p_monitor": "LIVE", "p_identity_ok": True, "p_fatigue": "ALERT"})
    assert api.sent[1] == ("set_status", {"p_status": "AVAILABLE"})
    assert api.sent[2] == ("accept_call", {"p_call": "c1"})
    assert api.sent[3] == ("finish_wrap_up", {"p_call": "c1", "p_disposition": "resolved", "p_notes": "ok"})
    assert d.last["status"] == "AVAILABLE"


def test_heartbeat_thread_survives_errors():
    class Bad:
        def heartbeat(self, *a):
            raise ApiError(500, "x", "down")

    lines = []
    hb = HeartbeatThread(Bad(), log=lines.append)
    assert hb.beat() is None and hb.failures == 1 and len(lines) == 1


# --- the customer panel ---
def test_format_customer_and_dispositions():
    c = {"full_name": "Ana Reyes", "account_no": "A-1", "status": "active", "plan": "Fiber 50", "balance": 1234.5,
         "profile": {"area": "Cebu"}}
    text = customer_panel.format_customer(c)
    assert "Ana Reyes" in text and "1,234.50" in text and "Cebu" in text
    assert "No customer" in customer_panel.format_customer(None)
    assert customer_panel.parse_disposition("R") == "resolved"
    assert customer_panel.parse_disposition("escalated") == "escalated"
    assert customer_panel.parse_disposition("zzz") is None


def test_warm_up_pings_both_functions_and_never_raises():
    class Flaky(FakeApi):
        def function(self, name, payload):
            self.sent.append((name, payload))
            raise ApiError(500, "cold", "x")

    api = Flaky()
    AgentDesk(api).warm_up()
    assert [n for n, _ in api.sent] == ["caller-turn", "transcribe"]
    assert all(p == {"warmup": True} for _, p in api.sent)


def test_transcriber_keeps_server_timings():
    t = stt.Transcriber(FakeApi([{"text": "hi", "ms": 400, "server_ms": 900}]))
    t.transcribe("c1", np.ones(800, dtype=np.int16))
    assert t.last["server_ms"] == 900


def test_long_first_sentence_is_cut_at_a_comma():
    text = "Hello po, ito po si Jasmine Castillo, at kailangan ko pong i-block ang card ko ngayon na. Salamat po."
    parts = tts.split_sentences(text)
    assert parts[0] == "Hello po, ito po si Jasmine Castillo," or len(parts[0]) < len(text.split(". ")[0])
    assert " ".join(parts) == text


def test_short_first_sentence_is_left_alone():
    assert tts.split_sentences("Hello po, kumusta na po kayo ngayon?") == ["Hello po, kumusta na po kayo ngayon?"]


def test_call_uses_a_shorter_end_of_speech_pause():
    from bpo.call_session import END_SILENCE_MS
    s = make_session(FakeApi([]), ScriptMic([]))
    assert END_SILENCE_MS == 500 and s.detector.end_silence_frames == 500 // 32


def test_heartbeat_thread_reports_a_refusal_once_per_failure_and_ignores_other_errors():
    class Refused:
        def heartbeat(self, *a):
            raise ApiError(403, "42501", "face check required")

    class Busy:
        def heartbeat(self, *a):
            raise ApiError(503, "x", "busy")

    seen = []
    HeartbeatThread(Refused(), log=lambda s: None, on_denied=seen.append).beat()
    HeartbeatThread(Busy(), log=lambda s: None, on_denied=seen.append).beat()
    assert seen == ["face check required"]


# --- ending a call while the caller is still talking -----------------------------------------------------------------
def test_speaking_stops_between_sentences_when_cancelled():
    played, synth = [], FakeSynth()
    state = {"stop": False}

    def play(samples, rate):
        played.append(1)
        state["stop"] = True  # the agent presses End call while the first sentence is playing

    tts.speak("Hello po, good morning to you. I have a problem with my bill. Please help me now.", "en", synth, play,
              cancelled=lambda: state["stop"])
    assert len(played) == 1  # the rest of the sentences are never played


def test_speaking_does_nothing_when_already_cancelled():
    played, synth = [], FakeSynth()
    tts.speak("Hello there, good morning to you.", "en", synth, lambda s, r: played.append(1), cancelled=lambda: True)
    assert played == []


def test_the_speaker_cuts_the_sound_in_the_middle_of_a_sentence(monkeypatch):
    import types

    from bpo.audio import io as audio_io

    calls = []
    fake_sd = types.SimpleNamespace(play=lambda *a, **k: calls.append("play"), stop=lambda: calls.append("stop"), wait=lambda: calls.append("wait"))
    monkeypatch.setattr(audio_io, "sd", fake_sd)
    speaker = audio_io.Speaker()
    t0 = time.monotonic()
    speaker.play(np.zeros(24000 * 5, dtype=np.float32), 24000, cancel=lambda: True)  # a 5 s sentence, cancelled at once
    assert time.monotonic() - t0 < 0.5
    assert calls[:2] == ["play", "stop"]


def test_ending_the_call_while_the_caller_speaks_hangs_up_without_waiting():
    api = FakeApi([reply("Hello po, may problema ako sa internet. Matagal na po itong hindi gumagana. Pakiayos po.")])
    said = []
    flag = {"end": False}

    def play(samples, rate):
        said.append(1)
        flag["end"] = True  # End call is pressed during the first sentence of the opening line

    s = CallSession(api, "c1", ScriptMic([]), FakeTranscriber([]), FakeSynth(), play, end_requested=lambda: flag["end"], log=lambda x: None)
    out = s.run()
    assert out.ended_by == "agent" and len(said) == 1
    assert [name for name, _ in api.sent] == ["caller-turn"]  # no further turns were asked for


def test_ending_before_the_opening_line_arrives_asks_for_nothing():
    api = FakeApi([])
    s = CallSession(api, "c1", ScriptMic([]), FakeTranscriber([]), FakeSynth(), lambda s_, r: None, end_requested=lambda: True, log=lambda x: None)
    out = s.run()
    assert out.ended_by == "agent" and api.sent == []


def test_ending_while_a_reply_is_on_its_way_means_it_is_never_spoken():
    api = FakeApi([reply("Hello po."), reply("A long answer that should never be heard. Really never at all.")])
    played = []
    state = {"after_opening": False}

    def play(samples, rate):
        played.append(1)

    # the agent speaks, then presses End call while the server is still working on the caller's answer
    def end_requested():
        return state["after_opening"] and any(n == "caller-turn" and p.get("agent_text") for n, p in api.sent)

    s = CallSession(api, "c1", ScriptMic(SPEECH), FakeTranscriber(["Hi, how can I help"]), FakeSynth(), play, end_requested=end_requested, log=lambda x: None)
    state["after_opening"] = True
    out = s.run()
    assert out.ended_by == "agent"
    assert len(played) == 1  # only the opening line was ever played


def test_a_key_press_seen_while_speaking_is_remembered():
    asks = iter([False, True, False, False])  # a key press consumed once: later checks would say False
    api = FakeApi([reply("Hello po, ito po ang opening. May tanong po ako.")])
    s = CallSession(api, "c1", ScriptMic([]), FakeTranscriber([]), FakeSynth(), lambda s_, r: None, end_requested=lambda: next(asks, False), log=lambda x: None)
    assert s.run().ended_by == "agent"
