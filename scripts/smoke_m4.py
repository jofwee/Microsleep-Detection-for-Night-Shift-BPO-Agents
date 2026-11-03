"""End-to-end smoke test of milestone 4 against the HOSTED project.

    python scripts/smoke_m4.py

Creates two throwaway teams (zz-smoke-...), runs the whole story through the real server with
synthetic face embeddings (no camera needed), and deletes everything it created. Needs
owner.env (it uses the secret key to create teams and to clean up).
"""
from __future__ import annotations

import secrets
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
from auth.supabase_client import Api, ApiError  # noqa: E402

from _supa import Admin, SupaError, load_env, random_password  # noqa: E402
from create_team import create_team  # noqa: E402

results: list[tuple[str, bool]] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    results.append((name, bool(cond)))
    print(("  ok    " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail and not cond else ""))
    return bool(cond)


def expect_error(name: str, fn, status: int | None = None, code: str | None = None) -> ApiError | None:
    try:
        fn()
    except ApiError as exc:
        ok = (status is None or exc.status == status) and (code is None or exc.code == code)
        check(name, ok, f"got HTTP {exc.status} {exc.code}")
        return exc
    check(name, False, "no error was raised")
    return None


def section(title: str) -> None:
    print(f"\n== {title}")


def unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def now_iso(delta_seconds: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat()


def main() -> int:
    env = load_env()
    admin = Admin(env)
    url, key = env["SUPABASE_URL"], env["SUPABASE_PUBLISHABLE_KEY"]
    tag = secrets.token_hex(3)
    rng = np.random.default_rng(int(tag, 16))
    face = unit(rng.normal(size=512))  # "the agent"
    near = lambda: unit(face + 0.05 * unit(rng.normal(size=512)))  # noqa: E731
    stranger = lambda: unit(rng.normal(size=512))  # noqa: E731
    good_live = {"passive": [0.99, 0.98, 1.0], "challenge_passed": True}

    created_users: list[str] = []
    created_teams: list[str] = []
    try:
        section("setup: two teams, each with a leader (the owner script)")
        team_a, leader_a_id, leader_a_pw = create_team(admin, f"zz-smoke-a-{tag}", f"smoke-la-{tag}@example.com", "Smoke Leader A")
        team_b, leader_b_id, leader_b_pw = create_team(admin, f"zz-smoke-b-{tag}", f"smoke-lb-{tag}@example.com", "Smoke Leader B")
        created_teams += [team_a, team_b]
        created_users += [leader_a_id, leader_b_id]
        check("two teams and two leaders created", True)

        la, lb = Api(url, key), Api(url, key)
        la.sign_in(f"smoke-la-{tag}@example.com", leader_a_pw)
        lb.sign_in(f"smoke-lb-{tag}@example.com", leader_b_pw)

        section("team leader: accounts and one-time codes")
        pair_a = la.function("manage-team", {"action": "issue_pairing_code"})["code"]
        pair_b = lb.function("manage-team", {"action": "issue_pairing_code"})["code"]
        email = f"smoke-agent-{tag}@example.com"
        made = la.function("manage-team", {"action": "create_agent", "email": email, "full_name": "Smoke Agent"})
        agent_id, temp_pw = made["agent_id"], made["temp_password"]
        created_users.append(agent_id)
        check("leader created an agent with a temporary password", len(temp_pw) >= 12)
        expect_error("a duplicate email is refused", lambda: la.function("manage-team", {"action": "create_agent", "email": email, "full_name": "Dup"}), 409, "email_taken")
        enroll_code = la.function("manage-team", {"action": "issue_enrollment_code", "agent_id": agent_id})["code"]
        expect_error("a leader cannot manage another team's agent", lambda: lb.function("manage-team", {"action": "issue_enrollment_code", "agent_id": agent_id}), 404, "agent_not_found")

        section("workstation pairing")
        agent_pc = Api(url, key)
        expect_error("a wrong pairing code is refused", lambda: agent_pc.function("pair-workstation", {"code": "AAAAA-BBBBB"}, auth=False), 400, "invalid_code")
        pc = agent_pc.function("pair-workstation", {"code": pair_a, "name": "smoke pc"}, auth=False)
        check("the pairing code pairs a PC and returns a token", len(pc["token"]) >= 40)
        expect_error("a pairing code works only once", lambda: agent_pc.function("pair-workstation", {"code": pair_a}, auth=False), 400, "invalid_code")
        pc_b = Api(url, key).function("pair-workstation", {"code": pair_b, "name": "team B pc"}, auth=False)
        agent = Api(url, key, workstation_token=pc["token"])

        section("agent: password first, then enrollment")
        agent.sign_in(email, temp_pw)
        expect_error("a temporary password cannot reach the face check", lambda: agent.function("face-challenge", {}), 403, "password_change_required")
        expect_error("an agent cannot use the leader functions", lambda: agent.function("manage-team", {"action": "issue_pairing_code"}), 403, "forbidden")
        new_pw = "Smoke-" + random_password(14)
        agent.update_password(new_pw)
        agent.rpc("ack_password_changed")
        expect_error("not enrolled yet", lambda: agent.function("face-challenge", {}), 409, "not_enrolled")

        embeddings = [near().tolist() for _ in range(5)]
        calibration = {"ear_open": 0.31, "ear_closed": 0.12, "mar_closed": 0.02, "mar_open": None, "neutral_pitch": 2.0, "neutral_yaw": 1.0, "neutral_roll": 0.0}
        payload = {"code": enroll_code, "consent": True, "model_name": "smoke", "model_version": "v1", "embeddings": embeddings, "calibration": calibration}
        expect_error("enrollment without consent is refused", lambda: agent.function("enroll-face", {**payload, "consent": False}), 400, "consent_required")
        expect_error("embeddings that are not unit length are refused", lambda: agent.function("enroll-face", {**payload, "embeddings": [[2.0] * 512] * 5}), 400, "invalid_embedding")
        expect_error("a capture mixing two people is refused", lambda: agent.function("enroll-face", {**payload, "embeddings": embeddings[:3] + [stranger().tolist(), stranger().tolist()]}), 400, "inconsistent_embeddings")
        expect_error("enrollment from an unpaired PC is refused", lambda: Api(url, key).function("enroll-face", payload), 401)
        wrong_pc = Api(url, key, workstation_token=pc_b["token"])
        wrong_pc.access_token, wrong_pc.refresh_token, wrong_pc.expires_at = agent.access_token, agent.refresh_token, agent.expires_at
        expect_error("another team's PC cannot be used", lambda: wrong_pc.function("enroll-face", payload), 403, "workstation_not_paired")
        check("enrollment succeeds with the code", agent.function("enroll-face", payload).get("enrolled") is True)
        expect_error("an enrollment code works only once", lambda: agent.function("enroll-face", payload), 400, "invalid_code")

        section("the face check")
        ch = agent.function("face-challenge", {})
        check("the server picks two different random actions", len(set(ch["actions"])) == 2, str(ch["actions"]))
        expect_error("a wrong nonce is refused (and does not burn the challenge)", lambda: agent.function("verify-face", {"challenge_id": ch["challenge_id"], "nonce": "x" * 43, "embeddings": [near().tolist() for _ in range(4)], "liveness": good_live}), 403, "bad_nonce")
        e = expect_error("a stranger's face is refused", lambda: agent.function("verify-face", {"challenge_id": ch["challenge_id"], "nonce": ch["nonce"], "embeddings": [stranger().tolist() for _ in range(4)], "liveness": good_live}), 403, "verification_failed")
        check("the refusal names the reason but not the score", bool(e) and e.extra.get("reason") == "no_match" and "score" not in e.extra)
        expect_error("the same challenge cannot be used twice", lambda: agent.function("verify-face", {"challenge_id": ch["challenge_id"], "nonce": ch["nonce"], "embeddings": [near().tolist() for _ in range(4)], "liveness": good_live}), 409, "challenge_used")

        ch2 = agent.function("face-challenge", {})
        ok_embs = [near().tolist() for _ in range(5)]
        expect_error("a real face with a failed liveness challenge is refused", lambda: agent.function("verify-face", {"challenge_id": ch2["challenge_id"], "nonce": ch2["nonce"], "embeddings": ok_embs, "liveness": {**good_live, "challenge_passed": False}}), 403, "verification_failed")
        ch3 = agent.function("face-challenge", {})
        expect_error("a real face that looks like a screen is refused", lambda: agent.function("verify-face", {"challenge_id": ch3["challenge_id"], "nonce": ch3["nonce"], "embeddings": ok_embs, "liveness": {"passive": [0.01, 0.02, 0.01], "challenge_passed": True}}), 403, "verification_failed")
        ch4 = agent.function("face-challenge", {})
        res = agent.function("verify-face", {"challenge_id": ch4["challenge_id"], "nonce": ch4["nonce"], "embeddings": ok_embs, "liveness": good_live})
        check("the agent's real face with a passed challenge is verified", res.get("verified") is True and res.get("score", 0) > 0.9, str(res))

        section("what the face check unlocks")
        session = agent.insert("sessions", {"workstation_id": pc["workstation_id"]})[0]
        check("a verified login session can start a monitoring session", session["agent_id"] == agent_id and session["team_id"] == team_a)
        ev = agent.insert("events", {"session_id": session["id"], "type": "MICROSLEEP", "ts": now_iso(-5), "duration_ms": 650, "severity": "critical"})[0]
        check("it can record events (agent_id and team_id are filled in by the server)", ev["agent_id"] == agent_id and ev["team_id"] == team_a)
        agent.insert("metrics_minute", {"session_id": session["id"], "minute": now_iso(-60), "perclos": 0.05, "yawns": 1})
        expect_error("an event from the far past is refused", lambda: agent.insert("events", {"session_id": session["id"], "type": "YAWN", "ts": now_iso(-86400 * 10)}), 400)

        second_login = Api(url, key, workstation_token=pc["token"])
        second_login.sign_in(email, new_pw)
        expect_error("the same password in a NEW login session writes nothing (no face check for it)", lambda: second_login.insert("sessions", {"workstation_id": pc["workstation_id"]}), 403, "42501")

        section("who can see what")
        check("the agent sees their own events", len(agent.select("events")) == 1)
        check("leader A sees their team's events", len(la.select("events")) == 1)
        check("leader B sees none of team A's events", len(lb.select("events")) == 0)
        check("leader B sees only their own team's profiles", len(lb.select("profiles")) == 1)
        check("leader A sees their team's profiles (leader + agent)", len(la.select("profiles")) == 2)
        check("leader A can read their face-check history", len(la.select("face_verifications")) == 1)
        expect_error("no client can read face templates", lambda: agent.select("face_templates"), 403, "42501")
        expect_error("no client can read setup codes", lambda: la.select("setup_codes"), 403, "42501")
        expect_error("an agent cannot promote themselves", lambda: agent.patch("profiles", {"id": agent_id}, {"role": "team_leader"}), 403, "42501")
        expect_error("an agent cannot move to another team", lambda: agent.patch("profiles", {"id": agent_id}, {"team_id": team_b}), 403, "42501")
        expect_error("an agent cannot create alerts", lambda: agent.insert("alerts", {"team_id": team_a, "agent_id": agent_id, "kind": "FACE_LOCKOUT", "message": "x"}), 403, "42501")
        expect_error("(leader) token hashes are not readable", lambda: la.select("workstations", {"select": "token_hash"}), 403, "42501")

        section("escalation: an identity event signs the agent out")
        agent.insert("events", {"session_id": session["id"], "type": "IDENTITY_MISMATCH", "ts": now_iso(-1), "duration_ms": 5200, "severity": "critical"})
        expect_error("after IDENTITY_MISMATCH the same login can no longer write", lambda: agent.insert("events", {"session_id": session["id"], "type": "YAWN", "ts": now_iso()}), 403, "42501")
        alerts = la.select("alerts")
        check("the team leader got an IDENTITY_MISMATCH alert", any(a["kind"] == "IDENTITY_MISMATCH" for a in alerts), str([a["kind"] for a in alerts]))
        check("leader B got no alert", len(lb.select("alerts")) == 0)
        check("the agent's session was closed", la.select("sessions", {"id": f"eq.{session['id']}"})[0]["ended_at"] is not None)
        la.rpc("ack_alert", {"p_alert": alerts[0]["id"]})
        check("the leader can acknowledge an alert", la.select("alerts", {"id": f"eq.{alerts[0]['id']}"})[0]["acknowledged_at"] is not None)
        expect_error("leader B cannot acknowledge team A's alert", lambda: lb.rpc("ack_alert", {"p_alert": alerts[0]["id"]}), 403)

        section("lockout after repeated failed face checks")
        relogin = Api(url, key, workstation_token=pc["token"])
        relogin.sign_in(email, new_pw)
        for i in range(5):
            c = relogin.function("face-challenge", {})
            expect_error(f"failed face check {i + 1} of 5", lambda c=c: relogin.function("verify-face", {"challenge_id": c["challenge_id"], "nonce": c["nonce"], "embeddings": [stranger().tolist() for _ in range(4)], "liveness": good_live}), 403, "verification_failed")
        locked = expect_error("the sixth attempt is locked out", lambda: relogin.function("face-challenge", {}), 429, "face_locked")
        check("the lock reports how long to wait", bool(locked) and 800 <= locked.extra.get("retry_after_seconds", 0) <= 900, str(locked.extra if locked else ""))
        check("the leader got a FACE_LOCKOUT alert", any(a["kind"] == "FACE_LOCKOUT" for a in la.select("alerts")))

        section("leader admin actions")
        reset = la.function("manage-team", {"action": "reset_password", "agent_id": agent_id})
        check("a password reset returns a new temporary password", len(reset["temp_password"]) >= 12)
        la.function("manage-team", {"action": "set_active", "agent_id": agent_id, "active": False})
        expect_error("a deactivated agent cannot sign in", lambda: Api(url, key).sign_in(email, reset["temp_password"]), 400)
        la.function("manage-team", {"action": "set_active", "agent_id": agent_id, "active": True})
        la.function("manage-team", {"action": "delete_biometrics", "agent_id": agent_id})
        check("deleting biometrics removes the template", len(admin.select("face_templates", {"agent_id": f"eq.{agent_id}"})) == 0)
        actions = {r["action"] for r in admin.select("audit_log", {"team_id": f"eq.{team_a}"})}
        check("the audit log recorded the actions", {"agent_created", "workstation_paired", "face_enrolled", "face_verified", "password_reset", "biometrics_deleted"} <= actions, str(sorted(actions)))
        check("no secret appears in the audit log", not any("password" in str(r["detail"]).lower() for r in admin.select("audit_log", {"team_id": f"eq.{team_a}"})))
    except SupaError as exc:
        check("setup", False, str(exc))
    except ApiError as exc:
        check("unexpected server error", False, f"HTTP {exc.status} {exc.code} {exc.message}")
    finally:
        section("cleanup")
        try:
            for t in created_teams:
                admin.delete("audit_log", {"team_id": t})  # the audit table outlives its teams, so clear this test's rows first
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
