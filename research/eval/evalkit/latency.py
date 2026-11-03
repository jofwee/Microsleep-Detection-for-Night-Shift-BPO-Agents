"""The voice loop's delay, from the transcripts the server keeps (call_turns).

One spoken exchange is: the agent speaks -> speech to text (stt_ms, stored on the agent's turn) -> the caller model
(llm_ms, stored on the caller's reply) -> the caller's voice starts (tts_ms, the time to the first sentence, stored on
that reply once the next turn is sent). The client's own end-to-end timer also counts the network and the functions;
those are not in the table, so this is a lower bound of what the agent feels (the live runs measured 3.1-3.3 s in total).
"""
from __future__ import annotations

from dataclasses import dataclass

TARGET_S = 3.0  # plan: end of the agent's speech -> start of the caller's audio


@dataclass(frozen=True)
class Exchange:
    call_id: str
    seq: int
    language: str
    stt_ms: int
    llm_ms: int
    tts_ms: int

    @property
    def total_ms(self) -> int:
        return self.stt_ms + self.llm_ms + self.tts_ms


def exchanges(turns: list[dict], languages: dict[str, str]) -> list[Exchange]:
    """Pair each agent turn with the caller reply that follows it. Needs all three timings; others are skipped."""
    out: list[Exchange] = []
    by_call: dict[str, list[dict]] = {}
    for t in turns:
        by_call.setdefault(t["call_id"], []).append(t)
    for call_id, rows in by_call.items():
        rows.sort(key=lambda r: r["seq"])
        for a, b in zip(rows, rows[1:]):
            if a["speaker"] != "agent" or b["speaker"] != "caller":
                continue
            stt, llm, tts = a.get("stt_ms"), b.get("llm_ms"), b.get("tts_ms")
            if stt is None or llm is None or tts is None:
                continue
            out.append(Exchange(call_id, int(b["seq"]), languages.get(call_id, "?"), int(stt), int(llm), int(tts)))
    return out


def share_within(items: list[Exchange], limit_s: float = TARGET_S) -> float | None:
    return sum(1 for e in items if e.total_ms <= limit_s * 1000) / len(items) if items else None
