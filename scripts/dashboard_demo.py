"""Throwaway demo team with realistic data on the HOSTED project, for trying the team-leader dashboard.

    python scripts/dashboard_demo.py --out C:\\Users\\Mara\\build\\demo.json

Creates a team "zz-demo-..." with a team leader (still on the temporary password, so the first-sign-in flow can
be tried), three agents who really pass the face check (synthetic embeddings), and about a day of calls,
transcripts, fatigue events and per-minute metrics. One agent is on a call, one is on hold after a microsleep and
one is on a break. It keeps their heartbeats going until you create the stop file (or press Ctrl-C), then deletes
everything it made. The login is written to --out (keep it outside OneDrive; it holds a temporary password).
"""
from __future__ import annotations

import argparse
import json
import random
import secrets
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
from auth.supabase_client import Api, ApiError  # noqa: E402

from _supa import Admin, load_env, random_password  # noqa: E402
from create_team import create_team  # noqa: E402

CALIBRATION = {"ear_open": 0.31, "ear_closed": 0.12, "mar_closed": 0.02, "mar_open": None, "neutral_pitch": 2.0, "neutral_yaw": 1.0, "neutral_roll": 0.0}
SCENARIOS = ["internet_down", "billing_dispute", "account_access", "order_refund", "plan_change", "complaint_escalation", "card_fraud"]
MOODS = ["calm", "confused", "in a hurry", "frustrated"]
TRANSCRIPT = [
    ("caller", "Magandang hapon po. Hindi po gumagana yung internet namin simula kagabi."),
    ("agent", "Good afternoon po. Pasensya na po sa abala. May I have your account number?"),
    ("caller", "Opo, 4417 po. Ang tagal na po kasi, nagwo-work from home ako."),
    ("agent", "I understand po. Let me check that for you. I see a fault in your area, and we will fix it within two hours."),
    ("caller", "Okay po, salamat. Pakitawagan po ako kung may update."),
]


def unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def make_agent(url: str, key: str, leader: Api, name: str, email: str, rng: np.random.Generator) -> dict:
    face = unit(rng.normal(size=512))
    near = lambda: unit(face + 0.05 * unit(rng.normal(size=512)))  # noqa: E731
    made = leader.function("manage-team", {"action": "create_agent", "email": email, "full_name": name})
    pair = leader.function("manage-team", {"action": "issue_pairing_code"})["code"]
    enroll = leader.function("manage-team", {"action": "issue_enrollment_code", "agent_id": made["agent_id"]})["code"]
    pc = Api(url, key).function("pair-workstation", {"code": pair, "name": f"{name.split()[0]}'s pc"}, auth=False)
    api = Api(url, key, workstation_token=pc["token"])
    api.sign_in(email, made["temp_password"])
    api.update_password("Demo-" + random_password(14))
    api.rpc("ack_password_changed")
    api.function("enroll-face", {"code": enroll, "consent": True, "model_name": "demo", "model_version": "v1",
                                 "embeddings": [near().tolist() for _ in range(5)], "calibration": CALIBRATION})
    ch = api.function("face-challenge", {})
    res = api.function("verify-face", {"challenge_id": ch["challenge_id"], "nonce": ch["nonce"], "embeddings": [near().tolist() for _ in range(5)],
                                       "liveness": {"passive": [0.99, 0.98, 1.0], "challenge_passed": True}})
    assert res.get("verified"), "the demo agent did not pass the face check"
    return {"id": made["agent_id"], "name": name, "email": email, "api": api, "workstation_id": pc["workstation_id"]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="where to write the login (JSON), outside OneDrive")
    ap.add_argument("--stop-file", default=None, help="the demo ends and cleans up when this file exists (default: <out>.stop)")
    args = ap.parse_args()
    out = Path(args.out)
    stop = Path(args.stop_file) if args.stop_file else out.with_suffix(".stop")
    stop.unlink(missing_ok=True)

    env = load_env()
    admin = Admin(env)
    url, key = env["SUPABASE_URL"], env["SUPABASE_PUBLISHABLE_KEY"]
    tag = secrets.token_hex(3)
    rng = np.random.default_rng(int(tag, 16))
    py = random.Random(int(tag, 16))
    created_users: list[str] = []
    team_id: str | None = None
    agents: list[dict] = []
    try:
        print("creating the team and the leader…", flush=True)
        team_id, leader_id, leader_pw = create_team(admin, f"zz-demo-{tag}", f"demo-leader-{tag}@example.com", "Dana Leader")
        created_users.append(leader_id)
        leader = Api(url, key)
        leader.sign_in(f"demo-leader-{tag}@example.com", leader_pw)

        print("creating three agents who pass the real face check…", flush=True)
        for name, email in (("Ana Reyes", f"demo-ana-{tag}@example.com"), ("Ben Santos", f"demo-ben-{tag}@example.com"), ("Carla Lim", f"demo-carla-{tag}@example.com")):
            a = make_agent(url, key, leader, name, email, rng)
            created_users.append(a["id"])
            agents.append(a)
        ana, ben, carla = agents

        # --- history: a day of calls, transcripts, events and metrics ------------------------------------
        print("writing a day of calls, events and metrics…", flush=True)
        customer = admin.select("customers", {"select": "id", "limit": "1"})[0]["id"]
        now = datetime.now(timezone.utc)
        sessions = {}
        for a in agents:
            sid = str(uuid.uuid4())
            a["api"].insert("sessions", {"id": sid, "workstation_id": a["workstation_id"]})
            sessions[a["id"]] = sid

        calls: list[dict] = []
        for i in range(36):
            queued = now - timedelta(minutes=py.randint(15, 22 * 60))
            agent = py.choice(agents)
            abandoned = py.random() < 0.12
            wait = py.randint(3, 75) if abandoned else py.choice([4, 6, 8, 11, 14, 19, 26, 38])
            talk = py.randint(150, 520)
            row = {
                "id": str(uuid.uuid4()), "team_id": team_id, "scenario_id": py.choice(SCENARIOS), "customer_id": customer,
                "language": py.choices(["en", "taglish", "tl"], [4, 4, 2])[0], "mood": py.choice(MOODS),
                "queued_at": iso(queued), "patience_s": 90, "offer_attempts": 1,
            }
            if abandoned:
                row.update(status="ABANDONED", ended_at=iso(queued + timedelta(seconds=wait)), end_reason="abandoned")
            else:
                answered = queued + timedelta(seconds=wait)
                ended = answered + timedelta(seconds=talk)
                row.update(status="COMPLETED", agent_id=agent["id"], offered_at=iso(queued + timedelta(seconds=1)), answered_at=iso(answered), ended_at=iso(ended),
                           wrap_up_ended_at=iso(ended + timedelta(seconds=40)), end_reason=py.choice(["caller_satisfied", "agent_ended", "caller_hangup"]),
                           disposition=py.choices(["resolved", "unresolved", "escalated"], [7, 2, 1])[0], notes=py.choice([None, "Customer asked for a callback.", "Credit applied to the account."]))
            admin.insert("calls", row)
            calls.append(row)

        answered = [c for c in calls if c["status"] == "COMPLETED"]
        first = answered[0]
        for seq, (who, text) in enumerate(TRANSCRIPT, start=1):
            admin.insert("call_turns", {
                "call_id": first["id"], "agent_id": first["agent_id"], "team_id": team_id, "seq": seq, "speaker": who, "text": text, "lang": first["language"],
                "created_at": iso(datetime.fromisoformat(first["answered_at"]) + timedelta(seconds=seq * 14)),
                "stt_ms": 900 + seq * 40 if who == "agent" else None, "llm_ms": 380 + seq * 25 if who == "caller" else None, "tts_ms": 820 if who == "caller" else None,
            })

        events_by_agent: dict[str, list[dict]] = {a["id"]: [] for a in agents}
        for c in answered:
            start = datetime.fromisoformat(c["answered_at"])
            for _ in range(py.choices([0, 1, 2, 3], [4, 3, 2, 1])[0]):
                kind = py.choices(["YAWN", "HEAD_NOD", "LONG_BLINK", "MICROSLEEP", "HEAD_TILT"], [4, 2, 3, 1.2, 1])[0]
                ts = start + timedelta(seconds=py.randint(10, 140))
                events_by_agent[c["agent_id"]].append({
                    "session_id": sessions[c["agent_id"]], "type": kind, "ts": iso(ts), "duration_ms": {"YAWN": 2400, "MICROSLEEP": 700}.get(kind, 400 if kind == "LONG_BLINK" else 0),
                    "value": 0.1, "severity": "critical" if kind == "MICROSLEEP" else "warning" if kind == "YAWN" else "info", "call_id": c["id"],
                })
        for a in agents:
            evs = sorted(events_by_agent[a["id"]], key=lambda e: e["ts"])
            for i in range(0, len(evs), 40):
                a["api"].insert("events", evs[i:i + 40], ignore_duplicates=True, on_conflict="session_id,type,ts")
            metrics = []
            for m in range(0, 180, 2):
                minute = (now - timedelta(minutes=m)).replace(second=0, microsecond=0)
                fatigue = 0.04 + 0.05 * (1 - m / 180) + py.random() * 0.04
                metrics.append({"session_id": sessions[a["id"]], "minute": iso(minute), "blink_rate": round(17 - 4 * (1 - m / 180) + py.random() * 3, 1),
                                "perclos": round(fatigue, 3), "mean_ear": round(0.3 - fatigue / 4, 3), "yawns": py.choice([0, 0, 0, 1]), "nods": py.choice([0, 0, 1]), "status": "ON_CALL"})
            a["api"].insert("metrics_minute", metrics, ignore_duplicates=True)

        for i, (rate, seed, minutes) in enumerate(((2.0, 7, 95), (4.0, 11, 40))):
            started = now - timedelta(hours=6 + 5 * i)
            admin.insert("simulation_runs", {"team_id": team_id, "started_by": leader_id, "seed": seed, "status": "STOPPED", "started_at": iso(started), "ended_at": iso(started + timedelta(minutes=minutes)),
                                             "calls_created": 22 + 9 * i, "config": {"arrival_per_min": rate, "max_concurrent": 3, "patience_s": 90, "language_mix": {"en": 40, "taglish": 40, "tl": 20}}})
        for hours, who, kind, text in (
            (3, carla, "IDENTITY_MISMATCH", "Someone other than the signed-in agent sat at the desk for more than 5 seconds."),
            (5, ben, "SPOOF_SUSPECTED", "Possible photo, screen or video of the agent. The agent was signed out."),
            (8, carla, "FACE_LOCKOUT", "5 failed face checks in 15 minutes. Face sign-in is locked for 15 minutes."),
            (11, ana, "IDENTITY_MISMATCH", "Someone other than the signed-in agent sat at the desk for more than 5 seconds."),
        ):
            admin.insert("alerts", {"team_id": team_id, "agent_id": who["id"], "kind": kind, "message": text, "created_at": iso(now - timedelta(hours=hours))})

        # --- now: Ana on a call, Ben on hold after a microsleep, Carla on a break ---------------------------
        print("putting the agents in their states…", flush=True)
        for a in agents:
            a["api"].rpc("set_status", {"p_status": "NOT_READY", "p_reason": "signed in"})
            a["api"].rpc("heartbeat", {"p_monitor": "LIVE", "p_identity_ok": True, "p_fatigue": "ALERT"})
        ana["api"].rpc("set_status", {"p_status": "AVAILABLE"})
        ben["api"].rpc("set_status", {"p_status": "AVAILABLE"})
        ben["api"].rpc("heartbeat", {"p_monitor": "LIVE", "p_identity_ok": True, "p_fatigue": "MICROSLEEP"})  # -> on hold
        carla["api"].rpc("set_status", {"p_status": "NOT_READY", "p_reason": "break"})
        live_call = {
            "id": str(uuid.uuid4()), "team_id": team_id, "scenario_id": "card_fraud", "customer_id": customer, "language": "taglish", "mood": "frustrated",
            "queued_at": iso(now), "patience_s": 90,
        }
        admin.insert("calls", live_call)
        for _ in range(20):  # the database offers it to the one available agent
            time.sleep(0.5)
            st = ana["api"].rpc("heartbeat", {"p_monitor": "LIVE", "p_identity_ok": True, "p_fatigue": "ALERT"})
            if st["status"] == "RINGING":
                ana["api"].rpc("accept_call", {"p_call": live_call["id"]})
                break
        for seq, (who, text) in enumerate(TRANSCRIPT[:3], start=1):
            admin.insert("call_turns", {"call_id": live_call["id"], "agent_id": ana["id"], "team_id": team_id, "seq": seq, "speaker": who, "text": text, "lang": "taglish"})

        out.write_text(json.dumps({
            "leader_email": f"demo-leader-{tag}@example.com", "leader_password": leader_pw, "team": f"zz-demo-{tag}",
            "agents": [{"id": a["id"], "name": a["name"]} for a in agents], "live_call": live_call["id"], "history_call": first["id"],
        }, indent=1), encoding="utf-8")
        print(f"READY. Login written to {out}. Create {stop} (or press Ctrl-C) to finish and clean up.", flush=True)

        beats = {
            ana["id"]: (ana, ("LIVE", True, "ALERT")), ben["id"]: (ben, ("LIVE", True, "MICROSLEEP")), carla["id"]: (carla, ("OFF", False, "ALERT")),
        }
        turn_file = out.with_suffix(".turn")  # a test creates this file to make the live call say one more thing
        while not stop.exists():
            if turn_file.exists():
                turn_file.unlink(missing_ok=True)
                last = admin.select("call_turns", {"call_id": f"eq.{live_call['id']}", "select": "seq", "order": "seq.desc", "limit": "1"})
                admin.insert("call_turns", {"call_id": live_call["id"], "agent_id": ana["id"], "team_id": team_id, "seq": (last[0]["seq"] if last else 0) + 1,
                                            "speaker": "caller", "text": "LIVE-TURN-MARKER: salamat po sa tulong ninyo.", "lang": "taglish"})
            for a, (m, ok, f) in beats.values():
                try:
                    a["api"].rpc("heartbeat", {"p_monitor": m, "p_identity_ok": ok, "p_fatigue": f})
                except ApiError as exc:
                    print(f"  heartbeat for {a['name']}: {exc.message or exc.code}", flush=True)
            time.sleep(5)
    except KeyboardInterrupt:
        print("interrupted", flush=True)
    finally:
        print("cleaning up…", flush=True)
        try:
            if team_id:
                admin.delete("audit_log", {"team_id": team_id})
                admin.update("teams", {"id": team_id}, {"leader_id": None})
            members = [m["id"] for m in admin.select("profiles", {"team_id": f"eq.{team_id}", "select": "id"})] if team_id else []
            for u in dict.fromkeys(created_users + members):  # also anyone created through the dashboard while it ran
                admin.delete_user(u)
            time.sleep(0.5)
            if team_id:
                admin.delete("teams", {"id": team_id})
            left = admin.select("teams", {"name": "like.zz-demo-*"})
            print("all demo data removed" if not left else f"WARNING: {len(left)} demo teams left", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"cleanup problem: {exc}", flush=True)
        stop.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
