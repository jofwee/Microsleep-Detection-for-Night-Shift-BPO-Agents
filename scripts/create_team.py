"""Owner-only setup: create a team and its team-leader account.

    python scripts/create_team.py --team "Night Shift" --leader-email leader@example.com --leader-name "Ana Cruz"

Prints a temporary password ONCE. The leader replaces it at first sign-in. Uses the secret key
from %LOCALAPPDATA%\\thesis-microsleep\\owner.env. This is a setup step, not an app role: there
is no admin in the product, and no screen in the apps can create a team.
"""
from __future__ import annotations

import argparse
import sys

from _supa import DEFAULT_ENV, EMAIL_RE, Admin, SupaError, load_env, random_password


def create_team(admin: Admin, team: str, email: str, name: str) -> tuple[str, str, str]:
    """Returns (team_id, leader_id, temp_password). Cleans up after itself if a step fails."""
    password = random_password()
    team_id = leader_id = None
    try:
        team_id = admin.insert("teams", {"name": team})["id"]
        leader_id = admin.create_user(email, password, name)
        admin.insert("profiles", {"id": leader_id, "role": "team_leader", "team_id": team_id, "full_name": name, "must_change_password": True})
        admin.update("teams", {"id": team_id}, {"leader_id": leader_id})
        return team_id, leader_id, password
    except Exception:
        if leader_id:
            admin.delete_user(leader_id)
        if team_id:
            admin.delete("teams", {"id": team_id})
        raise


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--team", required=True)
    ap.add_argument("--leader-email", required=True)
    ap.add_argument("--leader-name", required=True)
    ap.add_argument("--env", default=str(DEFAULT_ENV), help="owner.env location")
    args = ap.parse_args()

    if not EMAIL_RE.match(args.leader_email):
        print("That email address looks wrong.", file=sys.stderr)
        return 2
    try:
        admin = Admin(load_env(__import__("pathlib").Path(args.env)))
        team_id, leader_id, password = create_team(admin, args.team, args.leader_email.lower(), args.leader_name)
    except SupaError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"Team created:   {args.team}  ({team_id})")
    print(f"Team leader:    {args.leader_name} <{args.leader_email}>  ({leader_id})")
    print(f"Temporary password (shown once, change it at first sign-in): {password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
