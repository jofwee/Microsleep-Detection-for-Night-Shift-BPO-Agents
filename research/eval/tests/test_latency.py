from evalkit.latency import Exchange, exchanges, share_within


def turn(call, seq, speaker, **k):
    return {"call_id": call, "seq": seq, "speaker": speaker, "stt_ms": k.get("stt"), "llm_ms": k.get("llm"), "tts_ms": k.get("tts")}


def test_pairs_each_agent_turn_with_the_caller_reply_that_follows():
    turns = [
        turn("c1", 1, "caller", llm=400, tts=800),  # the opening line: no agent turn before it
        turn("c1", 2, "agent", stt=900),
        turn("c1", 3, "caller", llm=500, tts=700),
        turn("c1", 4, "agent", stt=1100),
        turn("c1", 5, "caller", llm=300, tts=600),
    ]
    out = exchanges(turns, {"c1": "taglish"})
    assert [(e.seq, e.total_ms, e.language) for e in out] == [(3, 2100, "taglish"), (5, 2000, "taglish")]


def test_turns_missing_a_timing_or_out_of_order_are_skipped_and_calls_do_not_mix():
    turns = [
        turn("a", 2, "caller", llm=500, tts=None),  # voice time never reported (the call ended)
        turn("a", 1, "agent", stt=900),
        turn("b", 1, "agent", stt=800),
        turn("b", 2, "caller", llm=400, tts=500),
    ]
    out = exchanges(turns, {"a": "en", "b": "tl"})
    assert [(e.call_id, e.total_ms) for e in out] == [("b", 1700)]


def test_share_within_the_target():
    items = [Exchange("c", i, "en", 1000, 500, 500) for i in range(3)] + [Exchange("c", 9, "en", 2000, 1000, 1000)]
    assert share_within(items, 3.0) == 0.75
    assert share_within([]) is None
