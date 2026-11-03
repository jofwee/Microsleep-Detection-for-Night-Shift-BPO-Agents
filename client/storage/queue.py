"""The offline notebook: everything the monitor wants to tell the server is written here first (SQLite),
and a background uploader sends it when the network and the login allow. Nothing is lost if the PC goes
offline, the app is closed, or the face check has expired.

Rows keep their order (id). Four kinds: session_open, event, metric, session_close.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

KINDS = ("session_open", "event", "metric", "session_close")


@dataclass(frozen=True)
class QueuedRow:
    id: int
    kind: str
    payload: dict
    attempts: int


class OfflineQueue:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute(
                "create table if not exists queue ("
                " id integer primary key autoincrement,"
                " kind text not null,"
                " payload text not null,"
                " attempts integer not null default 0,"
                " created text not null default current_timestamp)"
            )
            self._db.commit()

    def put(self, kind: str, payload: dict) -> int:
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r}")
        with self._lock:
            cur = self._db.execute("insert into queue (kind, payload) values (?, ?)", (kind, json.dumps(payload)))
            self._db.commit()
            return int(cur.lastrowid)

    def next_run(self, limit: int = 100) -> list[QueuedRow]:
        """The oldest rows that share one kind (so they can go to the server as one request, in order)."""
        with self._lock:
            rows = self._db.execute("select id, kind, payload, attempts from queue order by id limit ?", (limit,)).fetchall()
        run: list[QueuedRow] = []
        for id_, kind, payload, attempts in rows:
            if run and run[0].kind != kind:
                break
            run.append(QueuedRow(id_, kind, json.loads(payload), attempts))
        return run

    def done(self, ids: list[int]) -> None:
        with self._lock:
            self._db.executemany("delete from queue where id = ?", [(i,) for i in ids])
            self._db.commit()

    def failed(self, ids: list[int]) -> None:
        with self._lock:
            self._db.executemany("update queue set attempts = attempts + 1 where id = ?", [(i,) for i in ids])
            self._db.commit()

    def count(self, kind: str | None = None) -> int:
        with self._lock:
            if kind:
                return int(self._db.execute("select count(*) from queue where kind = ?", (kind,)).fetchone()[0])
            return int(self._db.execute("select count(*) from queue").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._db.close()
