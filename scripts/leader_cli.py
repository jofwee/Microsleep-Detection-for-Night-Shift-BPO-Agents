"""Team-leader actions from the terminal, until the dashboard exists (milestone 7).

    python scripts/leader_cli.py --email you@example.com change-password
    python scripts/leader_cli.py --email you@example.com pairing-code
    python scripts/leader_cli.py --email you@example.com create-agent --name "Ana Cruz" --agent-email ana@example.com
    python scripts/leader_cli.py --email you@example.com agents
    python scripts/leader_cli.py --email you@example.com enrollment-code --agent "Ana"
    python scripts/leader_cli.py --email you@example.com reset-password --agent "Ana"
    python scripts/leader_cli.py --email you@example.com deactivate --agent "Ana"     (or: activate)
    python scripts/leader_cli.py --email you@example.com delete-biometrics --agent "Ana"
    python scripts/leader_cli.py --email you@example.com start-run --rate 2 --max-concurrent 2 --seed 7
    python scripts/leader_cli.py --email you@example.com status          (agents and the queue right now)
    python scripts/leader_cli.py --email you@example.com calls           (recent calls)
    python scripts/leader_cli.py --email you@example.com transcript --call 3f2a
    python scripts/leader_cli.py --email you@example.com stop-run

--agent takes an agent id or part of their name (see `agents`). It signs in as the team leader with
the PUBLISHABLE key only, exactly like the dashboard will, so it can do nothing a leader could not.
Passwords and codes are shown once; the server stores only hashes.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
from auth.login import change_password, password_problems  # noqa: E402
from auth.supabase_client import Api, ApiError  # noqa: E402
from config import ConfigError, load_config  # noqa: E402


def resolve_agent(api: Api, query: str) -> dict:
    agents = api.select("profiles", {"role": "eq.agent", "select": "id,full_name,active,consent_at"})
    exact = [a for a in agents if a["id"] == query]
    matches = exact or [a for a in agents if query.lower() in a["full_name"].lower()]
    if len(matches) != 1:
        names = ", ".join(a["full_name"] for a in matches) or "none"
        raise SystemExit(f"'{query}' matches {len(matches)} agents ({names}). Use `agents` to see ids.")
    return matches[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--email", required=True, help="your team-leader email")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("change-password")
    sub.add_parser("pairing-code")
    sub.add_parser("agents")
    c = sub.add_parser("create-agent")
    c.add_argument("--name", required=True)
    c.add_argument("--agent-email", required=True)
    for name in ("enrollment-code", "reset-password", "deactivate", "activate", "delete-biometrics"):
        sub.add_parser(name).add_argument("--agent", required=True)
    r = sub.add_parser("start-run")
    r.add_argument("--rate", type=float, default=2.0, help="calls arriving per minute (0.5-30)")
    r.add_argument("--max-concurrent", type=int, default=3, help="most calls at once (1-50)")
    r.add_argument("--patience", type=int, default=90, help="seconds a caller waits before hanging up")
    r.add_argument("--mix", default="40,40,20", help="English,Taglish,Tagalog weights")
    r.add_argument("--seed", type=int, default=None, help="same seed = same callers, for repeatable experiments")
    sub.add_parser("stop-run")
    sub.add_parser("status")
    k = sub.add_parser("calls")
    k.add_argument("--limit", type=int, default=15)
    sub.add_parser("transcript").add_argument("--call", required=True, help="call id or the start of it")
    args = ap.parse_args()

    try:
        cfg = load_config()
    except ConfigError as exc:
        print(exc)
        return 1
    api = Api(cfg.supabase_url, cfg.publishable_key)
    try:
        api.sign_in(args.email.strip().lower(), getpass.getpass("Password: "))
        me = api.select("profiles", {"id": f"eq.{api.user_id}", "select": "role,full_name,must_change_password"})
        if not me or me[0]["role"] != "team_leader":
            print("This account is not a team leader.")
            return 1
        print(f"Signed in as {me[0]['full_name']} (team leader).")

        if args.cmd == "change-password":
            while True:
                new = getpass.getpass("New password: ")
                problems = password_problems(new)
                if problems:
                    print("  " + ", ".join(problems))
                elif new != getpass.getpass("Repeat it: "):
                    print("  they do not match")
                else:
                    break
            change_password(api, new)
            print("Password changed.")
        elif me[0]["must_change_password"] and args.cmd != "agents":
            print("Tip: you are still on your temporary password. Run `change-password` first.")

        if args.cmd == "pairing-code":
            r = api.function("manage-team", {"action": "issue_pairing_code"})
            print(f"Pairing code: {r['code']}   (single use, valid until {r['expires_at']})")
        elif args.cmd == "agents":
            rows = api.select("profiles", {"role": "eq.agent", "select": "id,full_name,active,consent_at", "order": "full_name"})
            print(f"{len(rows)} agent(s) in your team:")
            for a in rows:
                state = ("active" if a["active"] else "INACTIVE") + (", face enrolled" if a["consent_at"] else ", no face yet")
                print(f"  {a['full_name']:<24} {a['id']}  {state}")
        elif args.cmd == "create-agent":
            r = api.function("manage-team", {"action": "create_agent", "email": args.agent_email, "full_name": args.name})
            print(f"Created {args.name} <{r['email']}>  ({r['agent_id']})")
            print(f"Temporary password (shown once): {r['temp_password']}")
        elif args.cmd == "enrollment-code":
            a = resolve_agent(api, args.agent)
            r = api.function("manage-team", {"action": "issue_enrollment_code", "agent_id": a["id"]})
            print(f"Enrollment code for {a['full_name']}: {r['code']}   (single use, valid until {r['expires_at']})")
        elif args.cmd == "reset-password":
            a = resolve_agent(api, args.agent)
            r = api.function("manage-team", {"action": "reset_password", "agent_id": a["id"]})
            print(f"New temporary password for {a['full_name']} (shown once): {r['temp_password']}")
        elif args.cmd in ("deactivate", "activate"):
            a = resolve_agent(api, args.agent)
            api.function("manage-team", {"action": "set_active", "agent_id": a["id"], "active": args.cmd == "activate"})
            print(f"{a['full_name']} is now {'active' if args.cmd == 'activate' else 'inactive'}.")
        elif args.cmd == "start-run":
            en, taglish, tl = (float(x) for x in args.mix.split(","))
            config = {"arrival_per_min": args.rate, "max_concurrent": args.max_concurrent, "patience_s": args.patience,
                      "language_mix": {"en": en, "taglish": taglish, "tl": tl}}
            run = api.rpc("start_run", {"p_config": config, "p_seed": args.seed})
            print(f"Run started ({run}). Callers will arrive for agents who are Available.")
        elif args.cmd == "stop-run":
            runs = api.select("simulation_runs", {"status": "eq.RUNNING", "select": "id"})
            if not runs:
                print("No run is going.")
            for run in runs:
                api.rpc("stop_run", {"p_run": run["id"]})
                print(f"Stopped {run['id']}. Queued and ringing calls were closed.")
        elif args.cmd == "status":
            for a in api.select("agent_status", {"select": "status,reason,monitor_state,identity_ok,fatigue_state,last_heartbeat_at,agent_id"}):
                name = next(iter(api.select("profiles", {"id": f"eq.{a['agent_id']}", "select": "full_name"})), {}).get("full_name", a["agent_id"])
                print(f"  {name:<22} {a['status']:<12} monitor {a['monitor_state']:<4} {a['fatigue_state']:<10} heartbeat {a['last_heartbeat_at']}")
            queue = api.select("calls", {"status": "in.(QUEUED,OFFERED,ACTIVE,WRAP_UP)", "select": "status", "limit": "500"})
            counts = {s: sum(1 for c in queue if c["status"] == s) for s in ("QUEUED", "OFFERED", "ACTIVE", "WRAP_UP")}
            print("  calls now: " + ", ".join(f"{n} {s.lower()}" for s, n in counts.items()))
        elif args.cmd == "calls":
            for c in api.select("calls", {"select": "id,status,language,mood,scenario_id,end_reason,disposition,queued_at",
                                          "order": "queued_at.desc", "limit": str(args.limit)}):
                print(f"  {c['id'][:8]}  {c['status']:<10} {c['language']:<8} {c['scenario_id']:<22} {c['mood']:<11} "
                      f"{c['end_reason'] or '-':<17} {c['disposition'] or '-'}")
        elif args.cmd == "transcript":
            found = [c for c in api.select("calls", {"select": "id,scenario_id,language,mood"}) if c["id"].startswith(args.call)]
            if len(found) != 1:
                print(f"'{args.call}' matches {len(found)} calls.")
                return 1
            print(f"{found[0]['scenario_id']} / {found[0]['language']} / {found[0]['mood']}")
            for t in api.select("call_turns", {"call_id": f"eq.{found[0]['id']}", "order": "seq", "select": "seq,speaker,text,stt_ms,llm_ms,tts_ms"}):
                timing = f"  [{', '.join(f'{k[:-3]} {t[k]} ms' for k in ('stt_ms', 'llm_ms', 'tts_ms') if t[k] is not None)}]"
                print(f"  {t['speaker']:>6}: {t['text']}{timing if timing != '  []' else ''}")
        elif args.cmd == "delete-biometrics":
            a = resolve_agent(api, args.agent)
            if input(f"Delete the stored face and calibration of {a['full_name']}? [yes/no] ").strip().lower() != "yes":
                print("Nothing deleted.")
                return 0
            api.function("manage-team", {"action": "delete_biometrics", "agent_id": a["id"]})
            print("Deleted. They must enroll again to use the face check.")
    except ApiError as exc:
        print(f"FAILED: {exc.message or exc.code}")
        return 1
    finally:
        api.sign_out()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
