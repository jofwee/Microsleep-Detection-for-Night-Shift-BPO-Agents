"""The face second factor: challenge -> liveness + challenge on the camera -> verify-face.

1. face-challenge   the server returns a one-time nonce and two random actions.
2. collect_evidence the camera runs MiniFASNet and the actions, and computes ArcFace embeddings.
3. verify-face      the server matches the embeddings against the encrypted template and, if all
                    holds, records a face verification for this login session.

Only embeddings and liveness numbers leave the PC, never images. They are computed here, so a
modified client could lie; the plan lists that as a known limitation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

import numpy as np

from identity.embedder import FaceEmbedder
from identity.liveness import Action, ActiveChallenge, ChallengeStatus, PassiveLiveness
from identity.profile import EnrolledProfile
from vision.calibration import Calibration
from vision.landmarks import FaceLandmarker, primary_face
from vision.metrics import MedianFilter, head_turn, mean_ear

from .supabase_client import Api, ApiError

MIN_FRAMES = 3  # the server needs at least this many embeddings


@dataclass
class Evidence:
    embeddings: list[np.ndarray] = field(default_factory=list)
    passive: list[float] = field(default_factory=list)
    challenge_passed: bool = False
    failure: str | None = None


@dataclass(frozen=True)
class FaceMfaResult:
    verified: bool
    reason: str  # "ok", or why it failed: challenge_failed, liveness_low, no_match, ...
    expires_at: str | None = None


OnFrame = Callable[[np.ndarray, ActiveChallenge, ChallengeStatus, int, "float | None"], bool]


def collect_evidence(
    frames: Iterable[tuple[int, np.ndarray]],
    landmarker: FaceLandmarker,
    embedder: FaceEmbedder,
    liveness: PassiveLiveness,
    actions: Sequence[Action],
    calibration: Calibration | None = None,
    on_frame: OnFrame | None = None,
    sample_ms: int = 250,
    max_samples: int = 12,
    grace_ms: int = 800,
) -> Evidence:
    """Run the challenge while sampling embeddings and passive liveness scores.

    on_frame(frame, challenge, status, face_count, last_liveness) is for drawing prompts;
    return False from it to cancel.
    """
    challenge = ActiveChallenge(actions, calibration)
    smooth = MedianFilter(3)
    ev = Evidence()
    last_sample, passed_at = -10**9, None

    for ts, frame in frames:
        faces = landmarker.detect(frame, ts)
        face = primary_face(faces) if len(faces) == 1 else None  # exactly one person in front
        if face is None:
            ear = turn = None
            smooth.reset()
        else:
            h, w = frame.shape[:2]
            px = face.pixels(w, h)
            ear, turn = smooth.push(mean_ear(px)), head_turn(px)
            if ts - last_sample >= sample_ms and len(ev.embeddings) < max_samples:
                last_sample = ts
                ev.embeddings.append(embedder.embed(frame, face))
                ev.passive.append(float(liveness.score(frame, face)))

        status = challenge.update(ts, ear, turn)
        if on_frame is not None and on_frame(frame, challenge, status, len(faces), ev.passive[-1] if ev.passive else None) is False:
            ev.failure = "cancelled"
            return ev
        if status is ChallengeStatus.FAILED:
            ev.failure = "challenge failed (no blink or turn in time)"
            return ev
        if status is ChallengeStatus.PASSED:
            passed_at = ts if passed_at is None else passed_at
            if ts - passed_at >= grace_ms or len(ev.embeddings) >= max_samples:
                ev.challenge_passed = True
                return ev
    ev.failure = "camera stopped"
    return ev


def run_face_mfa(api: Api, get_evidence: Callable[[list[Action]], Evidence]) -> FaceMfaResult:
    """Raises ApiError for problems before the check (not_enrolled, face_locked, ...)."""
    ch = api.function("face-challenge", {})
    actions = [Action[name] for name in ch["actions"]]
    ev = get_evidence(actions)
    if len(ev.embeddings) < MIN_FRAMES:
        return FaceMfaResult(False, ev.failure or "not_enough_frames")
    try:
        res = api.function(
            "verify-face",
            {
                "challenge_id": ch["challenge_id"],
                "nonce": ch["nonce"],
                "embeddings": [e.tolist() for e in ev.embeddings],
                # a failed challenge is still reported, so the server counts the attempt towards the lockout
                "liveness": {"passive": [round(s, 4) for s in ev.passive], "challenge_passed": ev.challenge_passed},
            },
        )
    except ApiError as exc:
        if exc.code == "verification_failed":
            return FaceMfaResult(False, str(exc.extra.get("reason", "failed")))
        raise
    return FaceMfaResult(bool(res.get("verified")), "ok" if res.get("verified") else "failed", res.get("expires_at"))


def upload_enrollment(api: Api, profile: EnrolledProfile, code: str, consent: bool) -> None:
    """Send the enrolled template to the server (encrypted there) after the agent gives consent."""
    if profile.calibration is None:
        raise ValueError("the profile has no calibration")
    c = profile.calibration
    api.function(
        "enroll-face",
        {
            "code": code,
            "consent": consent,
            "model_name": profile.model_name,
            "model_version": profile.model_version,
            "embeddings": [e.tolist() for e in profile.embeddings],
            "calibration": {
                "ear_open": c.ear_open,
                "ear_closed": c.ear_closed,
                "mar_closed": c.mar_closed,
                "mar_open": c.mar_open,
                "neutral_pitch": c.neutral_pitch,
                "neutral_yaw": c.neutral_yaw,
                "neutral_roll": c.neutral_roll,
            },
        },
    )
