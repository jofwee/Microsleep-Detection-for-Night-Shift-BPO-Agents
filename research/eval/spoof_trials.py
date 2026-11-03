"""Presentation-attack trials: run the app's real sign-in check against genuine users and against fakes.

    python research/eval/spoof_trials.py --subject P01 --attack bonafide --trials 10
    python research/eval/spoof_trials.py --subject P01 --attack print --trials 10
    python research/eval/spoof_trials.py --subject P01 --attack phone_photo --lighting dim --trials 10

--attack is bonafide (the real person) or one of: print (a printed photo of the volunteer), phone_photo (a photo on a phone
screen), phone_video (a video of the volunteer on a phone) or screen_replay (a video on a second monitor or tablet).
For an attack, hold the fake in front of the camera when the window opens, as an attacker would. The volunteer must be
enrolled first (identity_trials.py enroll). Each trial runs the real challenge (two random actions: blink twice / turn your
head), the passive photo check and the face match, and logs the raw numbers. Nothing is sent to any server.
Results are appended to %LOCALAPPDATA%/thesis-microsleep/eval/spoof/trials.csv.
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

from auth import face_mfa  # noqa: E402
from capture.camera import Camera  # noqa: E402
from evalkit.paths import append_csv, data_dir  # noqa: E402
from evalkit.spoof import ATTACKS, BONAFIDE  # noqa: E402
from identity.embedder import ArcFaceOnnx  # noqa: E402
from identity.liveness import PassiveLiveness, random_actions  # noqa: E402
from identity.profile import EnrolledProfile  # noqa: E402
from identity.verify import verify_embeddings  # noqa: E402
from tools.identity_demo import UserQuit, frames  # noqa: E402
from tools.signin_demo import draw_prompt  # noqa: E402
from vision.calibration import Calibration  # noqa: E402
from vision.landmarks import FaceLandmarker  # noqa: E402

HEADER = ["time", "subject", "attack", "lighting", "challenge_passed", "passive_median", "passive_min", "passive_all", "match", "frames", "failure"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--subject", required=True)
    ap.add_argument("--attack", required=True, choices=[BONAFIDE, *ATTACKS])
    ap.add_argument("--lighting", choices=["bright", "normal", "dim"], default="normal")
    ap.add_argument("--trials", type=int, default=10)
    ap.add_argument("--actions", type=int, choices=[1, 2], default=2, help="how many live actions per trial (the app asks for 2)")
    ap.add_argument("--camera", type=int, default=None)
    args = ap.parse_args()

    profile_path = data_dir("identity", args.subject) / "profile.npz"
    if not profile_path.exists():
        print(f"{args.subject} is not enrolled. Run identity_trials.py enroll first.")
        return 1
    profile = EnrolledProfile.load(profile_path)
    arc, live = ArcFaceOnnx(), PassiveLiveness()
    log = data_dir("spoof") / "trials.csv"

    done = 0
    try:
        with Camera(args.camera) as cam, FaceLandmarker() as lm:
            print(f"camera: {cam.name}")
            for n in range(1, args.trials + 1):
                input(f"\nTrial {n}/{args.trials} ({args.attack}). Press Enter, then {'be yourself' if args.attack == BONAFIDE else 'hold the fake in front of the camera'}: ")
                ev = face_mfa.collect_evidence(frames(cam), lm, arc, live, random_actions(args.actions), profile.calibration or Calibration(), draw_prompt)
                match = verify_embeddings(list(ev.embeddings), profile).score if len(ev.embeddings) >= 3 else None
                passive = list(map(float, ev.passive))
                append_csv(log, HEADER, [
                    datetime.now().isoformat(timespec="seconds"), args.subject, args.attack, args.lighting, int(ev.challenge_passed),
                    "" if not passive else round(float(np.median(passive)), 4), "" if not passive else round(min(passive), 4),
                    json.dumps([round(p, 3) for p in passive]), "" if match is None else round(match, 4), len(ev.embeddings), ev.failure or "",
                ])
                done += 1
                print(f"   challenge {'passed' if ev.challenge_passed else 'NOT passed'}, passive median "
                      f"{'–' if not passive else f'{np.median(passive):.2f}'}, match {'–' if match is None else f'{match:.2f}'}")
                cv2.destroyAllWindows()
                time.sleep(0.5)
    except (UserQuit, KeyboardInterrupt):
        print("Stopped.")
    finally:
        cv2.destroyAllWindows()
    print(f"{done} trial(s) logged to {log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
