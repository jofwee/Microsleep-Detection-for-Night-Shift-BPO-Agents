"""Collect face-verification trials from volunteers. Only embeddings (numbers) are saved, never photos.

    python research/eval/identity_trials.py enroll --subject P01
    python research/eval/identity_trials.py verify --subject P01 --lighting normal --glasses no [--attempts 3]
    python research/eval/identity_trials.py verify --subject P01 --lighting dim --glasses yes --pose turned

Each volunteer enrolls once, then makes a few attempts in different conditions. Afterwards `analyze_identity.py` scores every
attempt against every volunteer's profile (own profile = genuine, anyone else's = impostor). At least two volunteers are
needed for impostor trials; five or more make the numbers meaningful. Files go to %LOCALAPPDATA%/thesis-microsleep/eval/identity/.
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
from evalkit.paths import data_dir  # noqa: E402
from identity.embedder import ArcFaceOnnx  # noqa: E402
from tools.identity_demo import UserQuit, capture_profile  # noqa: E402
from vision.landmarks import FaceLandmarker, primary_face  # noqa: E402

FRAMES_PER_ATTEMPT = 12
SPACING_MS = 250

CONSENT = ("Research use: your face is turned into numbers (not saved as pictures) to test how reliably the software tells people apart. "
           "The numbers stay on this computer. You can stop at any time and ask for them to be deleted.\nType yes to agree: ")


def attempt(cam, lm, arc, label: str) -> np.ndarray | None:
    """About three seconds of looking at the camera: a dozen embeddings of frames with exactly one face."""
    embs, last, start = [], -10**9, time.perf_counter()
    while len(embs) < FRAMES_PER_ATTEMPT and time.perf_counter() - start < 25:
        item = cam.read()
        if item is None:
            time.sleep(0.005)
            continue
        ts, frame = item
        faces = lm.detect(frame, ts)
        face = primary_face(faces) if len(faces) == 1 else None
        view = cv2.flip(frame, 1)
        msg = f"{label}: look at the camera ({len(embs)}/{FRAMES_PER_ATTEMPT})" if face else "I need to see exactly one face"
        cv2.putText(view, msg, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (10, 10, 10), 5, cv2.LINE_AA)
        cv2.putText(view, msg, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.imshow("Identity trial", view)
        if cv2.waitKey(1) & 0xFF == 27:
            raise UserQuit
        if face is not None and ts - last >= SPACING_MS:
            last = ts
            embs.append(arc.embed(frame, face))
    return np.stack(embs) if len(embs) >= 3 else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["enroll", "verify"])
    ap.add_argument("--subject", required=True, help="a code such as P01, not a name")
    ap.add_argument("--lighting", choices=["bright", "normal", "dim"], default="normal")
    ap.add_argument("--glasses", choices=["yes", "no"], default="no")
    ap.add_argument("--pose", choices=["front", "turned"], default="front", help="turned = a natural, slightly turned or tilted head")
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--camera", type=int, default=None)
    args = ap.parse_args()

    folder = data_dir("identity", args.subject)
    if args.mode == "enroll" and not (folder / "consent.txt").exists():
        if input(CONSENT).strip().lower() != "yes":
            print("Nothing was saved.")
            return 1
        (folder / "consent.txt").write_text(f"consent given {datetime.now().isoformat(timespec='seconds')}\n", encoding="utf-8")
    if args.mode == "verify" and not (folder / "profile.npz").exists():
        print(f"{args.subject} has no profile yet. Run enroll first.")
        return 1

    arc = ArcFaceOnnx()
    try:
        with Camera(args.camera) as cam, FaceLandmarker() as lm:
            print(f"camera: {cam.name}")
            if args.mode == "enroll":
                profile = capture_profile(cam, lm, arc, glasses=args.glasses == "yes")
                profile.save(folder / "profile.npz")
                print(f"Enrolled {args.subject}. Now make some verification attempts (verify).")
            else:
                for n in range(1, args.attempts + 1):
                    embs = attempt(cam, lm, arc, f"attempt {n}/{args.attempts}")
                    if embs is None:
                        print(f"attempt {n}: not enough usable frames, skipped")
                        continue
                    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                    meta = {"subject": args.subject, "lighting": args.lighting, "glasses": args.glasses, "pose": args.pose, "camera": cam.name}
                    np.savez_compressed(folder / f"verify_{args.lighting}_{args.glasses}_{args.pose}_{stamp}.npz", embeddings=embs, meta=json.dumps(meta))
                    print(f"attempt {n}: saved {len(embs)} embeddings")
                    time.sleep(0.8)
    except UserQuit:
        print("Stopped.")
    finally:
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
