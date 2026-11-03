"""Frame rate, CPU and memory of the detection pipeline on this PC.

    python research/eval/bench_pipeline.py [VIDEO] [--frames 600]

Without a video it films itself from the webcam for a few seconds first (nothing is saved) and loops that clip, so the
numbers do not depend on how fast the camera delivers. Two modes: "detection" (landmarks, measurements, state machines) and
"detection + identity" (the same plus the identity lock: ArcFace every 3 s and the passive photo check every 2 s), which is
what runs during a shift. Writes research/eval/results/performance/.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "client"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import psutil  # noqa: E402

from evalkit.bench import TARGET_FPS, summarize  # noqa: E402
from evalkit.paths import results_dir, write_csv  # noqa: E402
from vision.calibration import Calibration  # noqa: E402
from vision.drowsiness import DrowsinessEngine  # noqa: E402
from vision.landmarks import FaceLandmarker  # noqa: E402
from vision.pipeline import FramePipeline  # noqa: E402


def load_clip(video: Path | None, seconds: float = 6.0) -> list[np.ndarray]:
    frames: list[np.ndarray] = []
    if video:
        cap = cv2.VideoCapture(str(video))
        while len(frames) < 450:
            ok, f = cap.read()
            if not ok:
                break
            frames.append(f)
        cap.release()
        return frames
    from capture.camera import Camera

    with Camera(None) as cam:
        end = time.time() + seconds
        while time.time() < end:
            item = cam.read()
            if item:
                frames.append(item[1].copy())
            else:
                time.sleep(0.005)
    return frames


def run_mode(name: str, clip: list[np.ndarray], total: int, full: bool):
    proc = psutil.Process()
    lm = FaceLandmarker()
    arc = live = lock = None
    if full:
        from identity.embedder import ArcFaceOnnx
        from identity.liveness import PassiveLiveness
        from identity.lock import IdentityLock, LockConfig
        from identity.verify import DEFAULT_TAU  # noqa: F401
        from vision.landmarks import primary_face

        arc, live = ArcFaceOnnx(), PassiveLiveness()
        ref = None
        for f in clip:  # the first frame with a face is "the agent"
            faces = lm.detect(f, 0)
            if len(faces) == 1:
                ref = arc.embed(f, primary_face(faces))
                break
        if ref is None:
            raise SystemExit("no face in the clip: cannot benchmark the identity lock")
        lock = IdentityLock(lambda e: float(np.dot(ref, e)), LockConfig())
        lm.close()
        lm = FaceLandmarker()
    pipeline = FramePipeline(lm, DrowsinessEngine(Calibration()), lock=lock, embedder=arc, liveness=live)

    times, cpu, rss = [], [], []
    proc.cpu_percent(None)
    t0 = last = time.perf_counter()
    for i in range(total):
        frame = clip[i % len(clip)]
        a = time.perf_counter()
        pipeline.process(frame, int(i * 1000 / 30), recent_input=False)
        times.append((time.perf_counter() - a) * 1000)
        now = time.perf_counter()
        if now - last >= 1.0:
            cpu.append(proc.cpu_percent(None))
            rss.append(proc.memory_info().rss / 1e6)
            last = now
    wall = time.perf_counter() - t0
    lm.close()
    return summarize(name, times, wall, cpu, rss)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", nargs="?", default=None)
    ap.add_argument("--frames", type=int, default=600, help="frames to process per mode")
    args = ap.parse_args()

    clip = load_clip(Path(args.video) if args.video else None)
    if not clip:
        print("No frames to work with.")
        return 1
    h, w = clip[0].shape[:2]
    print(f"clip: {len(clip)} frames of {w}x{h}; {args.frames} frames per mode; this PC has {psutil.cpu_count(logical=False)} cores ({psutil.cpu_count()} threads)")
    results = [run_mode("detection", clip, args.frames, False), run_mode("detection + identity", clip, args.frames, True)]

    out = results_dir("performance")
    write_csv(out / "pipeline.csv", ["mode", "frames", "mean_ms", "median_ms", "p95_ms", "max_ms", "fps_sustained", "cpu_percent_of_one_core", "memory_mb_peak", "meets_15_fps"],
              [(r.mode, r.frames, round(r.mean_ms, 2), round(r.median_ms, 2), round(r.p95_ms, 2), round(r.max_ms, 2), round(r.fps_sustained, 1),
                None if r.cpu_percent_mean is None else round(r.cpu_percent_mean, 0), None if r.memory_mb_peak is None else round(r.memory_mb_peak, 0), int(r.meets_target)) for r in results])
    print()
    for r in results:
        print(f"{r.mode:<24} {r.fps_sustained:5.1f} frames/s sustained | per frame: mean {r.mean_ms:.1f} ms, 95th percentile {r.p95_ms:.1f} ms, slowest {r.max_ms:.0f} ms"
              f" | CPU {r.cpu_percent_mean or 0:.0f}% of a core | memory {r.memory_mb_peak or 0:.0f} MB | {'meets' if r.meets_target else 'BELOW'} the {TARGET_FPS:.0f} fps target")
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
