"""Where the evaluation keeps things. Recordings, face embeddings and trial logs contain biometric data, so they live
outside OneDrive and outside git; the small result tables and charts (no faces) are written into the repo."""
from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Iterable, Sequence

HERE = Path(__file__).resolve().parents[1]
RESULTS = Path(os.environ["EVAL_RESULTS"]) if os.environ.get("EVAL_RESULTS") else HERE / "results"  # tables and charts: small, no faces, committed
DATA = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "thesis-microsleep" / "eval"  # recordings and logs: private


def data_dir(*parts: str) -> Path:
    p = DATA.joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


def results_dir(*parts: str) -> Path:
    p = RESULTS.joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_csv(path: Path, header: Sequence[str], row: Sequence) -> None:
    """Add one row, writing the header first if the file is new. One row per trial, so nothing is lost if a session stops."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(row)
