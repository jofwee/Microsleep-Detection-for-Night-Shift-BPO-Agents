"""The simulated BPO from the agent's seat, in the terminal (milestone 5; the PySide6 desk comes in milestone 6).

Run from the client/ folder:   python -m tools.call_demo

You pair this PC (first time), sign in with email + password + face, then work the desk:
  g  go Available (calls can ring)      b  break (Not ready)      q  sign out
  while a call rings:   a  accept   d  decline
  during a call:        e  end the call
Use a headset: the caller is a voice, and the microphone is muted while it speaks.
The camera monitor is simulated here (always LIVE and ALERT); milestone 6 connects the real one.
A team leader (or scripts/leader_cli.py start-run) must have a simulation run going for calls to arrive.
"""
from __future__ import annotations

import argparse
import msvcrt
import sys
import threading
import time

import cv2

from auth import face_mfa
from auth.supabase_client import Api, ApiError
from bpo import customer_panel
from bpo.audio.io import Microphone, Speaker
from bpo.audio.stt import Transcriber
from bpo.audio.tts import EdgeTtsSynthesizer
from bpo.call_session import CallSession
from bpo.status import AgentDesk, HeartbeatThread
from capture.camera import Camera
from config import ConfigError, load_config
from identity.embedder import ArcFaceOnnx
from identity.liveness import PassiveLiveness
from tools.identity_demo import UserQuit, frames
from tools.signin_demo import ask, draw_prompt, enroll_flow, force_new_password, load_my_calibration, pair_if_needed, sign_in_interactively
from vision.landmarks import FaceLandmarker


def key_pressed() -> str | None:
    return msvcrt.getwch().lower() if msvcrt.kbhit() else None


def face_sign_in(api: Api, camera: int | None) -> bool:
    arc, live = ArcFaceOnnx(), PassiveLiveness()
    with Camera(camera) as cam, FaceLandmarker() as lm:
        for attempt in range(1, 4):
            try:
                calibration = load_my_calibration(api)
                result = face_mfa.run_face_mfa(
                    api, lambda actions: face_mfa.collect_evidence(frames(cam), lm, arc, live, actions, calibration, draw_prompt))
            except ApiError as exc:
                if exc.code == "not_enrolled":
                    enroll_flow(api, cam, lm, arc, False)
                    continue
                print(f"  {exc.message or exc.code}")
                return False
            if result.verified:
                cv2.destroyAllWindows()
                return True
            print(f"Face check failed ({result.reason}). Attempt {attempt} of 3.")
    cv2.destroyAllWindows()
    return False


def wrap_up(desk: AgentDesk, call_id: str) -> None:
    print("\nWrap-up. Look up the customer by account number (blank to skip), then choose a result.")
    while True:
        account = ask("  account no: ")
        if not account:
            break
        print(customer_panel.format_customer(desk.lookup_customer(account)))
    while True:
        disposition = customer_panel.parse_disposition(ask("  result [r]esolved / [u]nresolved / [e]scalated: "))
        if disposition:
            break
    desk.finish_wrap_up(call_id, disposition, ask("  notes (optional): ") or None)
    print("Done. The desk returns to Available on its own; press b for a break.")


def take_call(api: Api, desk: AgentDesk, call_id: str, mic: Microphone, speaker: Speaker) -> None:
    desk.accept(call_id)
    print("\nCALL CONNECTED. Speak to the caller. Press e to end the call.\n")
    fresh = [True]

    def end_requested() -> bool:
        if fresh[0]:  # the first check comes right after the caller's opening line: forget keys pressed before it
            fresh[0] = False
            while key_pressed():
                pass
            return False
        return key_pressed() == "e"

    session = CallSession(api, call_id, mic, Transcriber(api), EdgeTtsSynthesizer(),
                          lambda samples, rate: speaker.play(samples, rate, cancel=end_requested),
                          end_requested=end_requested)
    outcome = session.run()
    if outcome.ended_by == "agent":
        desk.end_call(call_id)
    n = len(outcome.timings)
    if n:
        avg = sum(t.total_ms for t in outcome.timings) / n / 1000
        print(f"\nCall over ({outcome.ended_by}). {n} spoken turns, {avg:.1f} s on average to the caller's voice.")
    else:
        print(f"\nCall over ({outcome.ended_by}).")
    wrap_up(desk, call_id)


def desk_loop(api: Api, desk: AgentDesk, mic: Microphone, speaker: Speaker) -> None:
    heart = HeartbeatThread(desk, interval_s=1.0)
    heart.start()
    desk.sign_in_ready()
    threading.Thread(target=desk.warm_up, daemon=True).start()
    print(__doc__.split("You pair")[1].split("Use a headset")[0].rstrip())
    shown = None
    try:
        while True:
            status, call_id = desk.last.get("status"), desk.last.get("call_id")
            if status != shown:
                print(f"[{status}]" + ("  a = accept, d = decline" if status == "RINGING" else ""))
                if status in ("AVAILABLE", "RINGING"):
                    threading.Thread(target=desk.warm_up, daemon=True).start()
                shown = status
            k = key_pressed()
            if k == "q":
                return
            if k == "g":
                desk.go_available()
            elif k == "b":
                desk.not_ready("break")
            elif status == "RINGING" and call_id and k in ("a", "d"):
                if k == "d":
                    desk.decline(call_id)
                else:
                    take_call(api, desk, call_id, mic, speaker)
                shown = None
            elif status == "ON_CALL" and call_id:
                print("A call was left open from an earlier run; ending it.")
                desk.end_call(call_id)
                shown = None
            time.sleep(0.15)
    finally:
        heart.stop()
        try:
            desk.sign_out()
        except ApiError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", type=int, default=None)
    ap.add_argument("--mic", type=int, default=None, help="input device number (python -m sounddevice lists them)")
    ap.add_argument("--speaker", type=int, default=None, help="output device number")
    args = ap.parse_args()
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(exc)
        return 1
    api = Api(cfg.supabase_url, cfg.publishable_key)
    try:
        pair_if_needed(api)
        account = sign_in_interactively(api)
        if account.must_change_password:
            force_new_password(api)
        if not face_sign_in(api, args.camera):
            print("Not signed in.")
            return 2
        print("Face check passed.")
        desk = AgentDesk(api)
        with Microphone(args.mic) as mic:
            desk_loop(api, desk, mic, Speaker(mic, args.speaker))
        return 0
    except (UserQuit, KeyboardInterrupt):
        print("\nCancelled.")
        return 1
    except ApiError as exc:
        print(f"\nFAILED: {exc.message or exc.code}")
        return 1
    finally:
        api.sign_out()


if __name__ == "__main__":
    sys.exit(main())
