"""Sends the offline queue to Supabase, oldest first, with retry and backoff.

What a failure means:
  - no network / server error / rate limit: keep the rows and try again later (backoff)
  - face check expired (403): keep the rows; they go out after the next sign-in
  - the server refuses a row (400, 409, 422, ...): count an attempt; after MAX_ATTEMPTS the row is dropped
    so one bad row cannot block everything behind it
Every send is safe to repeat: sessions have a client-chosen id, events are inserted with
"ignore duplicates" (unique on session, type, time) and per-minute metrics are keyed by (session, minute).
"""
from __future__ import annotations

import threading
from typing import Callable

import httpx

from auth.supabase_client import Api, ApiError
from storage.queue import OfflineQueue, QueuedRow

MAX_ATTEMPTS = 5
BATCH = 100


class Uploader:
    def __init__(self, api: Api, queue: OfflineQueue, log: Callable[[str], None] = print) -> None:
        self.api, self.queue, self.log = api, queue, log
        self.last_error: str | None = None

    @staticmethod
    def _blocked(exc: ApiError) -> bool:
        """The login or the face check is the problem (fixed by signing in again), not this particular row.
        Both come back as 403, so the message decides: a row-level-security denial means no valid face check;
        the database triggers' own refusals (unknown or closed session, out-of-range time) mean a bad row."""
        if exc.status == 401:
            return True
        return exc.status == 403 and any(w in f"{exc.code} {exc.message}".lower() for w in ("row-level security", "face", "jwt"))

    def _send(self, run: list[QueuedRow]) -> None:
        kind, rows = run[0].kind, [r.payload for r in run]
        if kind == "session_open":
            for row in rows:
                self.api.insert("sessions", row, ignore_duplicates=True)
        elif kind == "event":
            self.api.insert("events", rows, ignore_duplicates=True, on_conflict="session_id,type,ts")
        elif kind == "metric":
            self.api.insert("metrics_minute", rows, ignore_duplicates=True)
        elif kind == "session_close":
            for row in rows:
                self.api.patch("sessions", {"id": row["id"]}, {"ended_at": row["ended_at"]})

    def flush(self) -> str:
        """Send what can be sent. Returns "empty", "ok", "offline" (try later) or "blocked" (needs a sign-in)."""
        sent_any = False
        while True:
            run = self.queue.next_run(BATCH)
            if not run:
                return "ok" if sent_any else "empty"
            ids = [r.id for r in run]
            try:
                self._send(run)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                self.last_error = f"offline: {exc.__class__.__name__}"
                return "offline"
            except ApiError as exc:
                self.last_error = exc.message or exc.code
                if self._blocked(exc):
                    return "blocked"
                if exc.status in (0, 429) or exc.status >= 500:  # 0 = no network
                    return "offline"
                self.queue.failed(ids)
                bad = [r for r in run if r.attempts + 1 >= MAX_ATTEMPTS]
                if bad:
                    self.log(f"  (dropping {len(bad)} {run[0].kind} row(s) the server keeps refusing: {self.last_error})")
                    self.queue.done([r.id for r in bad])
                if len(run) > 1 and self._split(run):  # the good rows of a refused batch still go out
                    sent_any = True
                continue
            self.queue.done(ids)
            sent_any = True
            self.last_error = None

    def _split(self, run: list[QueuedRow]) -> bool:
        """A batch was refused: retry its rows one by one so the good ones still get through."""
        sent = False
        for r in run:
            if r.attempts + 1 >= MAX_ATTEMPTS:
                continue
            try:
                self._send([r])
            except ApiError:
                continue
            except (httpx.TransportError, httpx.TimeoutException):
                return sent
            self.queue.done([r.id])
            sent = True
        return sent


class UploaderThread(threading.Thread):
    """Calls flush() every few seconds, backing off while the network is down."""

    def __init__(self, uploader: Uploader, interval_s: float = 5.0, max_backoff_s: float = 60.0) -> None:
        super().__init__(name="uploader", daemon=True)
        self.uploader, self.interval_s, self.max_backoff_s = uploader, interval_s, max_backoff_s
        self._stop_event = threading.Event()
        self._wake = threading.Event()
        self._delay = interval_s
        self.state = "empty"

    def run(self) -> None:
        while not self._stop_event.is_set():
            self.state = self.uploader.flush()
            if self.state in ("offline", "blocked"):
                self._delay = min(self._delay * 2, self.max_backoff_s)
            else:
                self._delay = self.interval_s
            self._wake.wait(self._delay)
            self._wake.clear()

    def poke(self) -> None:
        """Try right away (a new sign-in, or the network came back)."""
        self._delay = self.interval_s
        self._wake.set()

    def stop(self, final_flush: bool = True) -> None:
        self._stop_event.set()
        self._wake.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout=10)  # let a flush in progress finish before anyone closes the queue
        if final_flush:
            try:
                self.uploader.flush()
            except Exception:  # noqa: BLE001 - leaving the app must not fail on the network
                pass
