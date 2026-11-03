"""A small local log of what the identity lock saw, for explaining a surprise afterwards.

It records lock status changes and every low passive-liveness score with the head pose, one line each, in
%LOCALAPPDATA%/thesis-microsleep/monitor.log. No images, no embeddings. The file is cut back when it grows past 400 KB.
"""
from __future__ import annotations

import time
from pathlib import Path

MAX_BYTES = 400_000


class FileTrace:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size > MAX_BYTES:
            keep = self.path.read_bytes()[-MAX_BYTES // 2:]
            self.path.write_bytes(keep[keep.find(b"\n") + 1:])

    def __call__(self, line: str) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {line}\n")
