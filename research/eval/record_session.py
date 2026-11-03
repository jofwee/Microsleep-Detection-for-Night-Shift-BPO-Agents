"""Record one labelled fatigue-test session with the webcam.

    python research/eval/record_session.py --subject P01 --lighting normal --glasses no

A consent screen comes first. Then the screen tells the volunteer what to do and when (calibration phases, then scripted
cues), and the video is saved with the schedule next to it, so the ground truth is known without hand labelling.
About 6 minutes with the defaults (--per-kind 6 --controls 4). Everything is saved under
%LOCALAPPDATA%/thesis-microsleep/eval/fatigue/ (outside OneDrive and git): the video shows a face.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "client"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from capture.camera import Camera  # noqa: E402
from evalkit.paths import data_dir, write_csv  # noqa: E402
from evalkit.protocol import Cue, build_schedule, frames_to_write, phase_at, total_duration  # noqa: E402

FPS = 30
WHITE, INK, GREY = (255, 255, 255), (10, 10, 10), (160, 160, 160)

CONSENT = """RESEARCH RECORDING: please read

This recording is for a thesis on detecting drowsiness at a computer.
  - A video of your face and head (no sound) is recorded for about 6 minutes.
  - It is stored only on this computer, outside any cloud folder. It is used only to test the detection software.
  - No picture or video of you will be published without your separate written permission.
  - You may stop at any time (Esc), and ask for your recording to be deleted at any time.
  - You will be asked to do things like closing your eyes, yawning and looking down. This is acted, not real tiredness.

Press  Y  to agree and start,   N  or Esc  to cancel."""


def put(img, text, org, scale=0.8, colour=WHITE, thick=2):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, INK, thick + 3, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, colour, thick, cv2.LINE_AA)


def consent_screen(size=(900, 560)) -> bool:
    img = np.full((size[1], size[0], 3), 18, np.uint8)
    for i, line in enumerate(CONSENT.splitlines()):
        put(img, line, (30, 50 + i * 34), 0.62 if i else 0.8, WHITE if i else (200, 200, 255), 1 if i else 2)
    cv2.imshow("Recording consent", img)
    while True:
        k = cv2.waitKey(50) & 0xFF
        if k in (ord("y"), ord("Y")):
            cv2.destroyWindow("Recording consent")
            return True
        if k in (ord("n"), ord("N"), 27):
            cv2.destroyWindow("Recording consent")
            return False


def draw_cue(frame, t: float, cues: list[Cue]) -> None:
    view = cv2.flip(frame, 1)
    heading, text, wait = phase_at(t, cues)
    h, w = view.shape[:2]
    cv2.rectangle(view, (0, 0), (w, 110), (18, 18, 18), -1)
    put(view, heading, (20, 48), 1.3, WHITE, 3)
    put(view, text + (f"  ({wait:.0f} s)" if wait is not None else ""), (20, 92), 0.75, (210, 210, 210), 2)
    put(view, f"{t:5.1f} s of {total_duration(cues):.0f} s   Esc = stop", (20, h - 16), 0.55, GREY, 1)
    cv2.imshow("Fatigue test recording", view)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--subject", required=True, help="a code such as P01, not a name")
    ap.add_argument("--lighting", choices=["bright", "normal", "dim"], default="normal")
    ap.add_argument("--glasses", choices=["yes", "no"], default="no")
    ap.add_argument("--seed", type=int, default=None, help="schedule seed (default: from the subject code)")
    ap.add_argument("--per-kind", type=int, default=6)
    ap.add_argument("--controls", type=int, default=4)
    ap.add_argument("--camera", type=int, default=None)
    args = ap.parse_args()

    seed = args.seed if args.seed is not None else sum(map(ord, args.subject))
    cues = build_schedule(seed, args.per_kind, args.controls)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = data_dir("fatigue", f"{args.subject}_{args.lighting}_{stamp}")

    if not consent_screen():
        print("Cancelled: nothing was recorded.")
        return 1

    meta = {"subject": args.subject, "lighting": args.lighting, "glasses": args.glasses, "seed": seed, "per_kind": args.per_kind,
            "controls": args.controls, "consent": True, "consent_at": datetime.now().isoformat(timespec="seconds"), "fps": FPS}
    duration = total_duration(cues)
    with Camera(args.camera) as cam:
        meta["camera"] = cam.name
        print(f"camera: {cam.name}. Recording {duration:.0f} s into {out}")
        first = None
        while first is None:
            first = cam.read()
            time.sleep(0.01)
        _, frame0 = first
        h, w = frame0.shape[:2]
        meta["resolution"] = [w, h]
        writer = cv2.VideoWriter(str(out / "video.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (w, h))
        print("Starting in 3 seconds: sit comfortably, look at the screen.")
        t_end = time.time() + 3
        while time.time() < t_end:
            item = cam.read()
            if item:
                put(item[1], "Starting in a moment…", (20, 60))
                cv2.imshow("Fatigue test recording", cv2.flip(item[1], 1))
            cv2.waitKey(10)

        t0, written, aborted, last = time.perf_counter(), 0, False, frame0
        while True:
            t = time.perf_counter() - t0
            if t >= duration:
                break
            item = cam.read()
            if item is not None:
                last = item[1]
            for _ in range(frames_to_write(t, written, FPS)):
                writer.write(last)  # the video clock follows the wall clock: a gap repeats the last frame
                written += 1
            draw_cue(last.copy(), t, cues)
            if cv2.waitKey(1) & 0xFF == 27:
                aborted = True
                break
        writer.release()
    cv2.destroyAllWindows()

    meta.update(aborted=aborted, frames=written, seconds=round(written / FPS, 2))
    (out / "session.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    write_csv(out / "schedule.csv", ["kind", "start_s", "window_s", "text"], [(c.kind, c.start_s, c.window_s, c.text) for c in cues])
    print(("Stopped early." if aborted else "Done.") + f" Saved {written} frames ({written / FPS:.0f} s) to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
