"""The agent's desk: status, heartbeat and the call RPCs of the database.

The state machine itself lives in the database (migration 20261103130200), so a modified client cannot
reach a state it is not allowed to reach. This is just a thin, typed wrapper around those functions.
"""
from __future__ import annotations

import threading
from typing import Callable

from auth.supabase_client import Api, ApiError

# (monitor_state, identity_ok, fatigue_state). In milestone 5 the camera monitor is simulated;
# milestone 6 wires the real camera pipeline in here.
MonitorState = tuple[str, bool, str]
SIMULATED_MONITOR: MonitorState = ("LIVE", True, "ALERT")


class AgentDesk:
    def __init__(self, api: Api) -> None:
        self.api = api
        self.last: dict = {}  # the most recent heartbeat answer: status, reason, call_id, offer_expires_at

    def warm_up(self) -> None:
        """Wake the two call functions so the first utterance does not pay for a cold start. Never raises."""
        for name in ("caller-turn", "transcribe"):
            try:
                self.api.function(name, {"warmup": True})
            except Exception:  # noqa: BLE001 - a missed warm-up only costs a slower first turn
                pass

    def sign_in_ready(self) -> None:
        self.api.rpc("set_status", {"p_status": "NOT_READY", "p_reason": "signed in"})
        self._now("NOT_READY", "signed in")

    def heartbeat(self, monitor: str = "LIVE", identity_ok: bool = True, fatigue: str = "ALERT") -> dict:
        self.last = self.api.rpc("heartbeat", {"p_monitor": monitor, "p_identity_ok": identity_ok, "p_fatigue": fatigue}) or {}
        return self.last

    def _now(self, status: str, reason: str | None = None) -> None:
        """The server accepted a status change: show it at once instead of waiting for the next heartbeat
        (until then the old status would make the desk, for example, close the camera it just opened)."""
        self.last = {**self.last, "status": status, "reason": reason}

    def go_available(self) -> None:
        self.api.rpc("set_status", {"p_status": "AVAILABLE"})
        self._now("AVAILABLE")

    def not_ready(self, reason: str = "break") -> None:
        self.api.rpc("set_status", {"p_status": "NOT_READY", "p_reason": reason})
        self._now("NOT_READY", reason)

    def sign_out(self) -> None:
        self.api.rpc("set_status", {"p_status": "OFFLINE", "p_reason": "signed out"})
        self._now("OFFLINE", "signed out")

    def accept(self, call_id: str) -> None:
        self.api.rpc("accept_call", {"p_call": call_id})

    def decline(self, call_id: str) -> None:
        self.api.rpc("decline_call", {"p_call": call_id})

    def end_call(self, call_id: str) -> None:
        self.api.rpc("end_call", {"p_call": call_id})

    def finish_wrap_up(self, call_id: str, disposition: str, notes: str | None = None) -> None:
        self.api.rpc("finish_wrap_up", {"p_call": call_id, "p_disposition": disposition, "p_notes": notes})

    def my_calls(self, since_iso: str) -> list[dict]:
        """This agent's own calls since a time (the server shows an agent only their own)."""
        return self.api.select("calls", {"queued_at": f"gte.{since_iso}", "select": "status,answered_at,ended_at,disposition",
                                         "order": "queued_at.desc", "limit": "200"})

    def lookup_customer(self, account: str) -> dict | None:
        return self.api.rpc("lookup_customer", {"p_account": account})


class HeartbeatThread(threading.Thread):
    """Sends a heartbeat every couple of seconds, for as long as the app runs, including during calls."""

    def __init__(self, desk: AgentDesk, monitor: Callable[[], MonitorState] = lambda: SIMULATED_MONITOR,
                 interval_s: float = 2.0, log: Callable[[str], None] = print,
                 on_denied: Callable[[str], None] | None = None) -> None:
        super().__init__(name="heartbeat", daemon=True)
        self.desk, self.monitor, self.interval_s, self.log = desk, monitor, interval_s, log
        self.on_denied = on_denied  # the server refuses us (401/403): the face check or the login is gone
        self._stop_event = threading.Event()
        self.failures = 0

    def beat(self) -> dict | None:
        try:
            state, identity_ok, fatigue = self.monitor()
            result = self.desk.heartbeat(state, identity_ok, fatigue)
            self.failures = 0
            return result
        except ApiError as exc:
            if exc.status in (401, 403) and self.on_denied:
                self.on_denied(exc.message or exc.code)
            self.failures += 1
            if self.failures in (1, 5):
                self.log(f"  (heartbeat problem: {exc.message or exc.code})")
            return None

    def run(self) -> None:
        while not self._stop_event.wait(self.interval_s):
            self.beat()

    def stop(self) -> None:
        self._stop_event.set()
