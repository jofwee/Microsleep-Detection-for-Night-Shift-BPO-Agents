"""Try the identity features on your webcam (local only, no server yet).

Run from the client/ folder:

  python -m tools.identity_demo enroll            # capture your face + calibration -> %LOCALAPPDATA%/thesis-microsleep/profile.local.npz
  python -m tools.identity_demo enroll --glasses  # two rounds: glasses on, then off
  python -m tools.identity_demo login             # liveness challenge + face match (the sign-in check)
  python -m tools.identity_demo watch             # live identity lock: try leaving, a 2nd person, a photo

The preview is mirrored like a mirror in enroll and login. "Left" and "right" always mean
YOUR left and right. Press q or Esc to quit.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

from capture.camera import Camera
from identity.embedder import ArcFaceOnnx
from identity.enroll import DEFAULT_POSES, EnrollmentError, EnrollmentSession
from identity.liveness import ActiveChallenge, ChallengeStatus, PassiveLiveness, random_actions
from identity.lock import IdentityLock, LockConfig
from identity.profile import EnrolledProfile
from identity.verify import DEFAULT_TAU, verify_embeddings
from tools.overlay import GRAY, GREEN, RED, WHITE, draw, put
from vision.calibration import Calibration, CalibrationError, FrameMeasure, compute_calibration
from vision.drowsiness import DrowsinessEngine, EventType
from vision.input_idle import recent_input
from vision.landmarks import DEFAULT_MODEL, FaceLandmarker, primary_face
from vision.metrics import MedianFilter, euler_from_matrix, head_turn, mean_ear, mouth_aspect_ratio
from vision.pipeline import FramePipeline

ROOT = Path(__file__).resolve().parents[1]
# The face template is biometric data, so it lives OUTSIDE the OneDrive-synced project folder.
DEFAULT_PROFILE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "thesis-microsleep" / "profile.local.npz"
DEFAULT_CAL = ROOT / "calibration.local.json"
LIVENESS_MIN = 0.5
AMBER = (0, 190, 255)


def banner(view, text, y=40, color=WHITE, scale=0.9):
    put(view, text, (16, y), color, scale=scale, thick=2)


def show(frame, mirror=True):
    cv2.imshow("identity demo", cv2.flip(frame, 1) if mirror else frame)
    return cv2.waitKey(1) & 0xFF


def frames(cam):
    """Yield (ts_ms, frame) forever, waiting for new frames."""
    while True:
        item = cam.read()
        if item is None:
            time.sleep(0.002)
            continue
        yield item


def single_face(faces):
    return primary_face(faces) if len(faces) == 1 else None


# --- enroll ----------------------------------------------------------------------------
class UserQuit(Exception):
    """The person closed the window (q or Esc)."""


def capture_profile(cam, lm, arc, glasses: bool = False) -> EnrolledProfile:
    """Guided capture: 5 head poses, then eyes-open and eyes-closed calibration.

    Returns the profile (with its calibration). Raises UserQuit, EnrollmentError or CalibrationError.
    """
    rounds = 2 if glasses else 1
    session = EnrollmentSession(arc, rounds=rounds)
    boundary = len(DEFAULT_POSES)  # stage index where round 2 starts
    phase, phase_end, waiting = "poses", 0.0, False
    open_s, closed_s = [], []

    for ts, frame in frames(cam):
        faces = lm.detect(frame, ts)
        view = cv2.flip(frame, 1)  # mirror the picture first, then draw text on it so the words read normally
        face = single_face(faces)
        msg, color = "", WHITE

        if phase == "poses":
            if rounds == 2 and session._stage == boundary and session._in_stage == 0 and not waiting:
                waiting = True
            if waiting:
                msg, color = "Take your glasses OFF, then press SPACE", AMBER
            else:
                st = session.feed(ts, frame, faces)
                where = f"Round {session._stage // boundary + 1}/{rounds}: glasses {'ON' if session._stage < boundary else 'OFF'}   " if rounds == 2 else ""
                msg = f"{where}{st.message}"
                bar = f"{st.pose.value if st.pose else ''}  {st.collected}/{st.per_pose}    total {st.total}/{st.total_needed}"
                put(view, bar, (16, 80), GRAY, scale=0.6)
                q = session.last_quality
                if q:
                    put(view, f"size {q.face_width:.2f}  sharp {q.sharpness:.0f}  light {q.brightness:.0f}", (16, 108), GRAY, scale=0.5)
                color = GREEN if st.message in ("Good", "Hold that position") else WHITE
                if session.done:
                    phase, phase_end = "cal_open", time.time() + 10
        else:
            remaining = phase_end - time.time()
            msg = ("Look at the screen, eyes OPEN" if phase == "cal_open" else "Close your eyes") + f"   {max(remaining, 0):.0f}s"
            color = AMBER
            if face is not None and face.matrix is not None:
                h, w = frame.shape[:2]
                px = face.pixels(w, h)
                p, y, r = euler_from_matrix(face.matrix)
                (open_s if phase == "cal_open" else closed_s).append(FrameMeasure(mean_ear(px), mouth_aspect_ratio(px), p, y, r))
            if remaining <= 0:
                if phase == "cal_open":
                    phase, phase_end = "cal_closed", time.time() + 3
                else:
                    break

        banner(view, msg, color=color, scale=0.8)
        key = show(view, mirror=False)
        if key in (ord("q"), 27):
            raise UserQuit()
        if key == ord(" "):
            waiting = False

    return session.finalize(compute_calibration(open_s, closed_s))


def run_enroll(cam, lm, arc, args) -> int:
    try:
        profile = capture_profile(cam, lm, arc, args.glasses)
    except UserQuit:
        return 1
    except (CalibrationError, EnrollmentError) as exc:
        print(f"\nENROLLMENT FAILED: {exc}")
        return 1
    cal = profile.calibration
    profile.save(args.profile)
    cal.save(DEFAULT_CAL)
    print(f"\nEnrolled: 5 diverse frames kept. Saved {args.profile}")
    print(f"Calibration: eyes open {cal.ear_open:.2f}, closed {cal.ear_closed:.2f}  (also saved to {DEFAULT_CAL.name})")
    return 0


# --- login -----------------------------------------------------------------------------
def run_login(cam, lm, arc, live, args) -> int:
    profile = EnrolledProfile.load(args.profile)
    actions = random_actions(2)
    print("Challenge:", " then ".join(a.value for a in actions))
    challenge = ActiveChallenge(actions, profile.calibration)
    smooth = MedianFilter(3)
    embs, liveness, last_sample = [], [], 0
    passed_at: int | None = None
    verdict: tuple[bool, str] | None = None

    for ts, frame in frames(cam):
        faces = lm.detect(frame, ts)
        view = cv2.flip(frame, 1)  # mirror the picture first, then draw text on it so the words read normally
        face = single_face(faces)
        if verdict is None:
            if face is None:
                ear = turn = None
                msg = "Only you should be in view" if faces else "I cannot see your face"
                smooth.reset()
            else:
                h, w = frame.shape[:2]
                px = face.pixels(w, h)
                ear, turn = smooth.push(mean_ear(px)), head_turn(px)
                msg = challenge.prompt
                if ts - last_sample >= 250 and len(embs) < 12:  # collect evidence while the challenge runs
                    last_sample = ts
                    embs.append(arc.embed(frame, face))
                    liveness.append(live.score(frame, face))
            status = challenge.update(ts, ear, turn)
            if status is ChallengeStatus.FAILED:
                verdict = (False, "liveness challenge failed (no blink / turn in time)")
            elif status is ChallengeStatus.PASSED:
                passed_at = passed_at or ts
                if ts - passed_at > 800 or len(embs) >= 12:
                    res = verify_embeddings(embs, profile)
                    live_med = float(np.median(liveness)) if liveness else 0.0
                    if not res.passed:
                        verdict = (False, f"face does not match (score {res.score:.2f}, need {DEFAULT_TAU})")
                    elif live_med < LIVENESS_MIN:
                        verdict = (False, f"looks like a photo or screen (liveness {live_med:.2f}, need {LIVENESS_MIN})")
                    else:
                        verdict = (True, f"match {res.score:.2f}, liveness {live_med:.2f}")
            banner(view, msg, color=AMBER)
            if liveness:
                put(view, f"liveness {liveness[-1]:.2f}   frames {len(embs)}", (16, 76), GRAY, scale=0.6)
        else:
            ok, text = verdict
            banner(view, "ACCESS GRANTED" if ok else "ACCESS DENIED", color=GREEN if ok else RED, y=60, scale=1.2)
            put(view, text, (16, 100), WHITE, scale=0.6, thick=1)
        key = show(view, mirror=False)
        if key in (ord("q"), 27):
            return 1
        if verdict is not None and key != 255:
            break
    print(("GRANTED: " if verdict[0] else "DENIED: ") + verdict[1])
    return 0 if verdict[0] else 2


# --- watch -----------------------------------------------------------------------------
def run_watch(cam, lm, arc, live, args) -> int:
    profile = EnrolledProfile.load(args.profile)
    cal = profile.calibration or Calibration()
    lock = IdentityLock(profile.score, LockConfig())
    pipeline = FramePipeline(lm, DrowsinessEngine(cal), lock=lock, embedder=arc, liveness=live)
    log: deque[str] = deque(maxlen=50)
    last, fps = time.perf_counter(), 0.0
    print("Watching. r = forced re-login (reset the lock), q = quit.")
    for ts, frame in frames(cam):
        m, events = pipeline.process(frame, ts, recent_input())
        for e in events:
            if e.type is not EventType.BLINK:
                log.append(f"{time.strftime('%H:%M:%S')}  {e.type.value}" + (f" {e.duration_ms} ms" if e.duration_ms else ""))
        now = time.perf_counter()
        fps = 0.9 * fps + 0.1 / max(now - last, 1e-6)
        last = now
        draw(frame, m, pipeline.engine, log, fps, "")
        key = show(frame, mirror=False)
        if key in (ord("q"), 27):
            break
        if key == ord("r"):
            lock.reset()
            log.append("lock reset (forced re-login)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["enroll", "login", "watch"])
    ap.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    ap.add_argument("--glasses", action="store_true", help="enroll: two rounds, glasses on then off")
    ap.add_argument("--camera", type=int, default=None, help="default: the physical webcam, chosen by name")
    ap.add_argument("--allow-virtual", action="store_true", help="developer escape hatch")
    ap.add_argument("--model", default=str(DEFAULT_MODEL))
    args = ap.parse_args()

    if args.mode != "enroll" and not args.profile.is_file():
        print(f"No profile at {args.profile}. Run first:  python -m tools.identity_demo enroll")
        return 1

    arc = ArcFaceOnnx()
    live = PassiveLiveness() if args.mode != "enroll" else None
    code = 1
    with Camera(args.camera, allow_virtual=args.allow_virtual) as cam, FaceLandmarker(args.model) as lm:
        print(f"camera: {cam.name}")
        if args.mode == "enroll":
            code = run_enroll(cam, lm, arc, args)
        elif args.mode == "login":
            code = run_login(cam, lm, arc, live, args)
        else:
            code = run_watch(cam, lm, arc, live, args)
    cv2.destroyAllWindows()
    return code


if __name__ == "__main__":
    sys.exit(main())
