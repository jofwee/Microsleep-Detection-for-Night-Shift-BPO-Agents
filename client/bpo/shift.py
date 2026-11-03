"""This shift in numbers, from the agent's own calls (the server lets an agent read only their own)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ShiftStats:
    calls: int = 0  # calls finished (completed) in this shift
    avg_talk_s: float | None = None
    resolved: int = 0
    unresolved: int = 0
    escalated: int = 0

    @property
    def resolved_share(self) -> float | None:
        return self.resolved / self.calls if self.calls else None


def _seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (b - a).total_seconds())


def shift_stats(rows: list[dict]) -> ShiftStats:
    done = [r for r in rows if r.get("status") == "COMPLETED"]
    talk = [t for t in (_seconds(r.get("answered_at"), r.get("ended_at")) for r in done) if t is not None]
    count = lambda d: sum(1 for r in done if r.get("disposition") == d)  # noqa: E731
    return ShiftStats(
        calls=len(done), avg_talk_s=sum(talk) / len(talk) if talk else None,
        resolved=count("resolved"), unresolved=count("unresolved"), escalated=count("escalated"),
    )
