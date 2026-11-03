"""End-to-end smoke test of milestone 6 against the HOSTED project: the offline queue, the uploader and the
camera-monitor rules of the status RPCs.

    python scripts/smoke_m6.py

Creates a throwaway team (zz-smoke-...) with an agent who passes the real face check (synthetic embeddings),
then pushes events and metrics through the client's own OfflineQueue + Uploader and checks what the server
holds. Deletes everything it created. Needs owner.env (secret key) and the events_dedupe migration.
"""
from __future__ import annotations

import secrets
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
from auth.supabase_client import Api, ApiError  # noqa: E402
from storage.queue import OfflineQueue  # noqa: E402
from sync.uploader import Uploader  # noqa: E402

from _supa import Admin, load_env, random_password  # noqa: E402
from create_team import create_team  # noqa: E402
from smoke_m4 import check, expect_error, now_iso, results, section, unit  # noqa: E402


def main() -> int:
    env = load_env()
    admin = Admin(env)
    url, key = env["SUPABASE_URL"], env["SUPABASE_PUBLISHABLE_KEY"]
    tag = secrets.token_hex(3)
    rng = np.random.default_rng(int(tag, 16))
    face = unit(rng.normal(size=512))
    near = lambda: unit(face + 0.05 * unit(rng.normal(size=512)))  # noqa: E731
    created_users: list[str] = []
    created_teams: list[str] = []
    try:
        section("setup: a team, an agent who passes the face check")
        team, leader_id, leader_pw = create_team(admin, f"zz-smoke-m6-{tag}", f"smoke6-l-{tag}@example.com", "Smoke Leader")
        created_teams.append(team)
        created_users.append(leader_id)
        leader = Api(url, key)
        leader.sign_in(f"smoke6-l-{tag}@example.com", leader_pw)
        pair = leader.function("manage-team", {"action": "issue_pairing_code"})["code"]
        email = f"smoke6-a-{tag}@example.com"
        made = leader.function("manage-team", {"action": "create_agent", "email": email, "full_name": "Smoke Agent"})
        agent_id = made["agent_id"]
        created_users.append(agent_id)
        enroll_code = leader.function("manage-team", {"action": "issue_enrollment_code", "agent_id": agent_id})["code"]
        pc = Api(url, key).function("pair-workstation", {"code": pair, "name": "smoke pc"}, auth=False)
        agent = Api(url, key, workstation_token=pc["token"])
        agent.sign_in(email, made["temp_password"])
        agent.update_password("Smoke-" + random_password(14))
        agent.rpc("ack_password_changed")
        calibration = {"ear_open": 0.31, "ear_closed": 0.12, "mar_closed": 0.02, "mar_open": None, "neutral_pitch": 2.0, "neutral_yaw": 1.0, "neutral_roll": 0.0}
        agent.function("enroll-face", {"code": enroll_code, "consent": True, "model_name": "smoke", "model_version": "v1",
                                       "embeddings": [near().tolist() for _ in range(5)], "calibration": calibration})
        ch = agent.function("face-challenge", {})
        res = agent.function("verify-face", {"challenge_id": ch["challenge_id"], "nonce": ch["nonce"],
                                             "embeddings": [near().tolist() for _ in range(5)],
                                             "liveness": {"passive": [0.99, 0.98, 1.0], "challenge_passed": True}})
        check("the agent is face-verified", res.get("verified") is True)

        section("the offline queue and the uploader")
        import uuid

        sid = str(uuid.uuid4())
        q = OfflineQueue(":memory:")
        lines: list[str] = []
        up = Uploader(agent, q, log=lines.append)
        q.put("session_open", {"id": sid, "workstation_id": pc["workstation_id"]})
        events = [
            {"session_id": sid, "type": "YAWN", "ts": now_iso(-30), "duration_ms": 2400, "value": 0.7, "severity": "warning"},
            {"session_id": sid, "type": "MICROSLEEP", "ts": now_iso(-20), "duration_ms": 800, "value": 0.1, "severity": "critical"},
            {"session_id": sid, "type": "HEAD_NOD", "ts": now_iso(-10), "duration_ms": 0, "value": 0.0, "severity": "info"},
        ]
        for e in events:
            q.put("event", e)
        minute = now_iso(-120)[:16] + ":00+00:00"
        metric = {"session_id": sid, "minute": minute, "blink_rate": 14.5, "perclos": 0.06, "mean_ear": 0.29, "yawns": 1, "nods": 1, "status": "AVAILABLE"}
        q.put("metric", metric)
        check("a first flush sends everything", up.flush() == "ok" and q.count() == 0, str(lines))
        mine = lambda: leader.select("events", {"session_id": f"eq.{sid}", "select": "type,duration_ms,severity"})  # noqa: E731
        check("the team leader sees the 3 events", len(mine()) == 3, str(mine()))
        check("the per-minute metric arrived", len(leader.select("metrics_minute", {"session_id": f"eq.{sid}"})) == 1)
        check("the session was created with the client's id", leader.select("sessions", {"id": f"eq.{sid}"})[0]["agent_id"] == agent_id)

        for e in events:  # the app was closed before it knew the upload worked: everything is sent again
            q.put("event", e)
        q.put("metric", metric)
        q.put("session_open", {"id": sid, "workstation_id": pc["workstation_id"]})
        check("a full resend is accepted", up.flush() in ("ok",) and q.count() == 0, str(lines))
        check("...and creates no duplicate events", len(mine()) == 3)
        check("...and no duplicate metric", len(leader.select("metrics_minute", {"session_id": f"eq.{sid}"})) == 1)

        stray = {"session_id": str(uuid.uuid4()), "type": "YAWN", "ts": now_iso(-5), "duration_ms": 1, "value": 0, "severity": "info"}
        q.put("event", stray)
        q.put("event", {**events[0], "ts": now_iso(-3)})  # a good row behind the bad one
        for _ in range(6):
            up.flush()
        check("an event for an unknown session is dropped without blocking the good row behind it", q.count() == 0 and len(mine()) == 4, f"left={q.count()} events={len(mine())} {lines}")
        old = {**events[0], "ts": "2020-01-01T00:00:00+00:00"}
        q.put("event", old)
        for _ in range(6):
            up.flush()
        check("an event older than 7 days is refused and dropped", q.count() == 0 and len(mine()) == 4)

        q.put("session_close", {"id": sid, "ended_at": now_iso()})
        check("the session is closed", up.flush() == "ok" and leader.select("sessions", {"id": f"eq.{sid}"})[0]["ended_at"] is not None)
        q.put("event", {**events[0], "ts": now_iso(-1)})
        for _ in range(6):
            up.flush()
        check("an event for a closed session is refused and dropped", q.count() == 0 and len(mine()) == 4)

        section("the camera-monitor rules of the status functions")
        agent.rpc("set_status", {"p_status": "NOT_READY", "p_reason": "signed in"})
        expect_error("Available is refused while the monitor is off", lambda: agent.rpc("set_status", {"p_status": "AVAILABLE"}), None, "55000")
        agent.rpc("heartbeat", {"p_monitor": "LIVE", "p_identity_ok": False, "p_fatigue": "ALERT"})
        expect_error("Available is refused when the identity is not confirmed", lambda: agent.rpc("set_status", {"p_status": "AVAILABLE"}), None, "55000")
        agent.rpc("heartbeat", {"p_monitor": "LIVE", "p_identity_ok": True, "p_fatigue": "ALERT"})
        agent.rpc("set_status", {"p_status": "AVAILABLE"})
        check("Available works once the monitor is live and the identity confirmed", agent.rpc("heartbeat", {"p_monitor": "LIVE", "p_identity_ok": True, "p_fatigue": "ALERT"})["status"] == "AVAILABLE")
        held = agent.rpc("heartbeat", {"p_monitor": "LIVE", "p_identity_ok": True, "p_fatigue": "MICROSLEEP"})
        check("a microsleep puts an Available agent on hold", held["status"] == "SYSTEM_HOLD" and held["reason"] == "microsleep", str(held))
        expect_error("Available is refused while on hold", lambda: agent.rpc("set_status", {"p_status": "AVAILABLE"}), None, "55000")
        agent.rpc("set_status", {"p_status": "NOT_READY", "p_reason": "acknowledged"})
        check("the agent acknowledges and is Not ready", agent.rpc("heartbeat", {"p_monitor": "OFF", "p_identity_ok": False, "p_fatigue": "ALERT"})["status"] == "NOT_READY")
        agent.rpc("set_status", {"p_status": "OFFLINE", "p_reason": "signed out"})
        check("signing out works", agent.rpc("heartbeat", {"p_monitor": "OFF", "p_identity_ok": False, "p_fatigue": "ALERT"})["status"] == "OFFLINE")
    finally:
        section("cleanup")
        try:
            for t in created_teams:
                admin.delete("audit_log", {"team_id": t})
                admin.update("teams", {"id": t}, {"leader_id": None})
            for u in created_users:
                admin.delete_user(u)
            time.sleep(0.5)
            for t in created_teams:
                admin.delete("teams", {"id": t})
            left = admin.select("teams", {"name": "like.zz-smoke-*"})
            check("all throwaway data was removed", len(left) == 0, f"{len(left)} smoke teams left")
        except Exception as exc:  # noqa: BLE001
            check("cleanup", False, str(exc))

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} of {len(results)} checks passed")
    for n in failed:
        print("  FAILED:", n)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
