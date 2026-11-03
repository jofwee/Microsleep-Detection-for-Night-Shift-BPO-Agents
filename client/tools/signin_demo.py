"""Sign in the way an agent will: pair this PC, email + password, then the face check.

Run from the client/ folder:   python -m tools.signin_demo

It needs client/.env (SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY) and a team leader who has:
  - created your account (you get a temporary password),
  - given you a pairing code (first time on this PC),
  - given you an enrollment code (first time you use the face check).

On success it opens a monitoring session on the server, records one test event, and closes it.
"""
from __future__ import annotations

import argparse
import getpass
import sys

import cv2

from auth import face_mfa, login, workstation
from auth.supabase_client import Api, ApiError
from capture.camera import Camera
from config import ConfigError, load_config
from identity.embedder import ArcFaceOnnx
from identity.enroll import EnrollmentError
from identity.liveness import PassiveLiveness
from tools.identity_demo import AMBER, GRAY, GREEN, RED, UserQuit, banner, capture_profile, frames, put, show
from vision.calibration import Calibration, CalibrationError
from vision.landmarks import FaceLandmarker

CONSENT = """
CONSENT (draft wording, to be reviewed before real use)
  To check that it is really you, this system keeps a numeric template of your face
  (not photos) on a server, encrypted. It is used only to confirm your identity while you
  work and to monitor your alertness. Your team leader can see whether your face checks
  passed. You can ask your team leader to delete your data at any time.
"""


def ask(prompt: str) -> str:
    return input(prompt).strip()


def yes(prompt: str) -> bool:
    return ask(prompt + " [yes/no] ").lower() in ("y", "yes")


def pair_if_needed(api: Api) -> workstation.Workstation:
    ws = workstation.load()
    if ws:
        api.workstation_token = ws.token
        return ws
    print("This PC is not paired yet. Your team leader can issue a pairing code.")
    while True:
        try:
            ws = workstation.pair(api, ask("Pairing code: "))
            print("Paired.")
            return ws
        except ApiError as exc:
            print(f"  {exc.message or exc.code}")


def sign_in_interactively(api: Api) -> login.Account:
    while True:
        email = ask("Email: ")
        try:
            account = login.sign_in(api, email, getpass.getpass("Password: "))
        except ApiError as exc:
            print(f"  sign-in failed: {exc.message or exc.code}")
            continue
        except login.NotAnAgent as exc:
            print(f"  {exc}")
            continue
        print(f"Signed in as {account.full_name}.")
        return account


def force_new_password(api: Api) -> None:
    print("You are using a temporary password. Choose a new one (12+ characters, upper, lower, digit).")
    while True:
        new = getpass.getpass("New password: ")
        problems = login.password_problems(new)
        if problems:
            print("  " + ", ".join(problems))
            continue
        if new != getpass.getpass("Repeat it: "):
            print("  they do not match")
            continue
        login.change_password(api, new)
        print("Password changed.")
        return


def load_my_calibration(api: Api) -> Calibration | None:
    rows = api.select("calibration_profiles", {"agent_id": f"eq.{api.user_id}"})
    if not rows:
        return None
    keep = {k: rows[0][k] for k in Calibration.__dataclass_fields__ if k in rows[0] and rows[0][k] is not None}
    return Calibration(**keep)


def draw_prompt(frame, challenge, status, n_faces, live) -> bool:
    view = cv2.flip(frame, 1)
    msg = challenge.prompt if n_faces == 1 else ("Only you should be in view" if n_faces else "I cannot see your face")
    banner(view, msg, color=AMBER if n_faces == 1 else RED, scale=0.8)
    if live is not None:
        put(view, f"liveness {live:.2f}", (16, 76), GRAY, scale=0.6)
    return show(view, mirror=False) not in (ord("q"), 27)


def enroll_flow(api: Api, cam, lm, arc, glasses: bool) -> None:
    print("\nNo face is enrolled for this account yet.")
    print(CONSENT)
    if not yes("Do you agree to enroll your face?"):
        raise SystemExit("Enrollment needs consent. Nothing was stored.")
    code = ask("Enrollment code from your team leader: ")
    print("A window will guide you through 5 head poses, then eyes open / closed.")
    profile = capture_profile(cam, lm, arc, glasses)
    face_mfa.upload_enrollment(api, profile, code, consent=True)
    print("Face enrolled (stored encrypted on the server).")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", type=int, default=None, help="default: the physical webcam, chosen by name")
    ap.add_argument("--allow-virtual", action="store_true", help="developer escape hatch")
    ap.add_argument("--glasses", action="store_true", help="enrollment: two rounds, glasses on then off")
    args = ap.parse_args()

    try:
        cfg = load_config()
    except ConfigError as exc:
        print(exc)
        return 1
    api = Api(cfg.supabase_url, cfg.publishable_key)
    try:
        ws = pair_if_needed(api)
        account = sign_in_interactively(api)
        if account.must_change_password:
            force_new_password(api)

        arc, live = ArcFaceOnnx(), PassiveLiveness()
        with Camera(args.camera, allow_virtual=args.allow_virtual) as cam, FaceLandmarker() as lm:
            print(f"camera: {cam.name}")
            for attempt in range(1, 4):
                try:
                    calibration = load_my_calibration(api)
                    result = face_mfa.run_face_mfa(
                        api,
                        lambda actions: face_mfa.collect_evidence(frames(cam), lm, arc, live, actions, calibration, draw_prompt),
                    )
                except ApiError as exc:
                    if exc.code == "not_enrolled":
                        enroll_flow(api, cam, lm, arc, args.glasses)
                        continue
                    if exc.code == "face_locked":
                        wait = int(exc.extra.get("retry_after_seconds", 0))
                        print(f"Face sign-in is locked after too many failed checks. Try again in {wait // 60 + 1} minutes.")
                        return 3
                    raise
                if result.verified:
                    break
                print(f"Face check failed ({result.reason}). Attempt {attempt} of 3.")
            else:
                print("Not signed in.")
                return 2
        cv2.destroyAllWindows()

        print(f"\nFACE CHECK PASSED. Signed in with email + password + face (valid until {result.expires_at}).")
        session = api.insert("sessions", {"workstation_id": ws.id})[0]
        api.insert("events", {"session_id": session["id"], "type": "YAWN", "ts": _now(), "duration_ms": 2100, "severity": "warning"})
        api.patch("sessions", {"id": session["id"]}, {"ended_at": _now()})
        print(f"Wrote and closed a test session on the server ({session['id']}).")
        return 0
    except (UserQuit, KeyboardInterrupt):
        print("\nCancelled.")
        return 1
    except (EnrollmentError, CalibrationError, ApiError, ValueError) as exc:
        print(f"\nFAILED: {exc}")
        return 1
    finally:
        cv2.destroyAllWindows()
        api.sign_out()


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    sys.exit(main())
