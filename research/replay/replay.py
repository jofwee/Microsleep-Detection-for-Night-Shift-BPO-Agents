"""Replay a video file through the same pipeline as the live app and write the events.

    python research/replay/replay.py VIDEO.mp4 --out events.csv [--calibration cal.json]

The CSV has one row per event: ts_ms (video time), type, duration_ms, value, severity.
BLINK rows are included here (the live app only counts them) so blink datasets such as
Eyeblink8 can be scored. Comparing against ground truth (precision, recall, F1, alert
latency) is added in the evaluation milestone.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import Counter
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "client"))

from vision.calibration import Calibration  # noqa: E402
from vision.drowsiness import DrowsinessEngine  # noqa: E402
from vision.landmarks import DEFAULT_MODEL, FaceLandmarker  # noqa: E402
from vision.pipeline import FramePipeline  # noqa: E402


def replay(video: Path, model: Path, cal: Calibration, out: Path | None) -> Counter:
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"cannot open video: {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    counts: Counter = Counter()
    rows, frames, faces_seen = [], 0, 0
    started = time.perf_counter()

    with FaceLandmarker(model) as landmarker:
        pipeline = FramePipeline(landmarker, DrowsinessEngine(cal))
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            ts_ms = int(cap.get(cv2.CAP_PROP_POS_MSEC)) or int(frames * 1000 / fps)
            measurement, events = pipeline.process(frame, ts_ms)  # no keyboard data in a recording
            frames += 1
            faces_seen += measurement.face_count > 0
            for e in events:
                counts[e.type.value] += 1
                rows.append((e.ts_ms, e.type.value, e.duration_ms, round(e.value, 4), e.severity))
    cap.release()

    elapsed = time.perf_counter() - started
    video_s = frames / fps
    print(f"{video.name}: {frames} frames ({video_s:.1f} s of video), face found in {faces_seen}")
    print(f"processed in {elapsed:.1f} s = {video_s / max(elapsed, 1e-9):.1f}x real time")
    for kind, n in sorted(counts.items()):
        print(f"  {kind:<11} {n}")
    if not counts:
        print("  no events")

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ts_ms", "type", "duration_ms", "value", "severity"])
            w.writerows(rows)
        print(f"wrote {len(rows)} events to {out}")
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--calibration", type=Path, default=None, help="calibration JSON; defaults are used if omitted")
    ap.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    args = ap.parse_args()
    cal = Calibration.load(args.calibration) if args.calibration else Calibration()
    replay(args.video, args.model, cal, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
