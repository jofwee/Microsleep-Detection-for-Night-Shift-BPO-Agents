"""Live webcam overlay: landmarks, EAR, MAR, head angles, events, and calibration.

Run from the client/ folder:   python -m tools.overlay

Keys
  o  capture 10 s eyes OPEN, looking at the screen
  c  capture 3 s eyes CLOSED (calibration is computed and saved when this ends)
  m  capture 3 s mouth WIDE OPEN (optional, sharpens the yawn threshold)
  r  reset the detector state
  q / Esc  quit

Head-angle signs: nod your head down and check that pitch goes NEGATIVE. If it does
not, set pitch_sign=-1 in HeadPoseConfig (vision/metrics.py); same idea for roll.
"""
from __future__ import annotations

import argparse
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

from capture.camera import Camera
from vision.calibration import Calibration, CalibrationError, FrameMeasure, compute_calibration
from vision.drowsiness import DrowsinessEngine, Event, EventType, FatigueState
from vision.input_idle import recent_input
from vision.landmarks import DEFAULT_MODEL, FaceLandmarker
from vision.metrics import LEFT_EYE, MOUTH_BOTTOM, MOUTH_LEFT, MOUTH_RIGHT, MOUTH_TOP, RIGHT_EYE
from vision.pipeline import FramePipeline, Measurement

DEFAULT_CAL = Path(__file__).resolve().parents[1] / "calibration.local.json"
PHASES = {"o": ("open", 10.0, "Look at the screen, eyes OPEN"),
          "c": ("closed", 3.0, "Close your eyes"),
          "m": ("mouth", 3.0, "Open your mouth WIDE")}
WHITE, GRAY, RED, GREEN = (255, 255, 255), (170, 170, 170), (40, 40, 230), (90, 200, 90)


def put(img, text, org, color=WHITE, scale=0.55, thick=1):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def fmt(v, spec=".2f"):
    return "--" if v is None else format(v, spec)


def draw(frame, m: Measurement, engine: DrowsinessEngine, log, fps: float, banner: str = "") -> None:
    """Draw everything onto frame in place."""
    cal = engine.cal
    if m.px is not None:
        for idx in (RIGHT_EYE, LEFT_EYE, (MOUTH_LEFT, MOUTH_RIGHT, MOUTH_TOP, MOUTH_BOTTOM)):
            for i in idx:
                cv2.circle(frame, tuple(int(v) for v in m.px[i]), 2, GREEN, -1, cv2.LINE_AA)
    metrics = engine.metrics()
    lines = [
        (f"EAR {fmt(m.ear_smooth)}  (close<{cal.ear_close_threshold:.2f}, open>{cal.ear_reopen_threshold:.2f})", WHITE),
        (f"MAR {fmt(m.mar)}  (yawn>{cal.yawn_threshold:.2f})   jaw {fmt(m.jaw_open)}", WHITE),
        (f"pitch {fmt(m.pitch, '.0f')}  yaw {fmt(m.yaw, '.0f')}  roll {fmt(m.roll, '.0f')}", WHITE),
        (f"faces {m.face_count}   blinks/min {fmt(metrics['blink_rate'], '.0f')}   PERCLOS {fmt(metrics['perclos'])}", GRAY),
        (f"{fps:.0f} FPS" + ("   [looking down: eyes paused]" if m.looking_down else ""), GRAY),
    ]
    for i, (text, color) in enumerate(lines):
        put(frame, text, (12, 24 + 22 * i), color)

    state = engine.state
    color = {FatigueState.ALERT: GREEN, FatigueState.DROWSY: (0, 190, 255), FatigueState.MICROSLEEP: RED}[state]
    put(frame, state.value, (12, 24 + 22 * len(lines) + 12), color, scale=0.9, thick=2)
    if state is FatigueState.MICROSLEEP:
        cv2.rectangle(frame, (0, 0), (frame.shape[1] - 1, frame.shape[0] - 1), RED, 6)

    if m.lock_status is not None:
        lock_color = {"VERIFIED": GREEN, "SEARCHING": (0, 190, 255), "ABSENT": (0, 190, 255)}.get(m.lock_status, RED)
        score = "" if m.lock_score is None else f"   cosine {m.lock_score:.2f}"
        score += "" if m.lock_liveness is None else f"   liveness {m.lock_liveness:.2f}"
        put(frame, f"identity: {m.lock_status}{score}", (12, 24 + 22 * len(lines) + 44), lock_color, scale=0.6, thick=2)

    y = frame.shape[0] - 14
    for line in list(log)[-6:][::-1]:
        put(frame, line, (12, y), GRAY)
        y -= 20
    if banner:
        put(frame, banner, (12, frame.shape[0] - 150), (0, 220, 255), scale=0.7, thick=2)


def describe(e: Event) -> str:
    stamp = time.strftime("%H:%M:%S")
    extra = f" {e.duration_ms} ms" if e.duration_ms else ""
    return f"{stamp}  {e.type.value}{extra}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", type=int, default=None, help="camera index; default: the physical webcam, chosen by name")
    ap.add_argument("--allow-virtual", action="store_true", help="developer escape hatch: allow a virtual camera index")
    ap.add_argument("--model", default=str(DEFAULT_MODEL))
    ap.add_argument("--calibration", default=str(DEFAULT_CAL))
    args = ap.parse_args()

    cal_path = Path(args.calibration)
    cal = Calibration.load(cal_path) if cal_path.is_file() else Calibration()
    print(f"calibration: {'loaded ' + str(cal_path) if cal_path.is_file() else 'defaults (press o, then c)'}")

    pipeline = FramePipeline(FaceLandmarker(args.model), DrowsinessEngine(cal))
    log: deque[str] = deque(maxlen=50)
    collected: dict[str, list[FrameMeasure]] = {"open": [], "closed": [], "mouth": []}
    phase, phase_end, banner = None, 0.0, ""
    last, fps = time.perf_counter(), 0.0

    with Camera(args.camera, allow_virtual=args.allow_virtual) as cam:
        print(f"camera: {cam.name}")
        while True:
            item = cam.read()
            if item is None:
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
                time.sleep(0.002)
                continue
            ts_ms, frame = item
            m, events = pipeline.process(frame, ts_ms, recent_input())
            for e in events:
                if e.type is not EventType.BLINK:
                    log.append(describe(e))

            now = time.perf_counter()
            fps = 0.9 * fps + 0.1 / max(now - last, 1e-6)
            last = now

            if phase:
                remaining = phase_end - now
                banner = f"{PHASES[next(k for k, v in PHASES.items() if v[0] == phase)][2]}  {max(remaining, 0):.0f}s"
                if m.ear is not None and m.pitch is not None:
                    collected[phase].append(FrameMeasure(m.ear, m.mar, m.pitch, m.yaw, m.roll))
                if remaining <= 0:
                    if phase in ("closed", "mouth") and collected["open"] and collected["closed"]:
                        try:
                            new = compute_calibration(collected["open"], collected["closed"], collected["mouth"] or None)
                            new.save(cal_path)
                            pipeline.set_engine(DrowsinessEngine(new))
                            log.append(f"calibrated: open {new.ear_open:.2f} closed {new.ear_closed:.2f}")
                        except CalibrationError as exc:
                            log.append(f"calibration failed: {exc}")
                    phase, banner = None, ""

            draw(frame, m, pipeline.engine, log, fps, banner)
            cv2.imshow("microsleep monitor - overlay", frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("r"):
                pipeline.set_engine(DrowsinessEngine(pipeline.engine.cal))
                log.append("detector reset")
            ch = chr(key) if key < 128 else ""
            if ch in PHASES and phase is None:
                phase = PHASES[ch][0]
                phase_end = time.perf_counter() + PHASES[ch][1]
                collected[phase] = []
    cv2.destroyAllWindows()
    pipeline.landmarker.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
