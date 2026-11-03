"""Load the scenario files (simulation/scenarios/*.yaml) into the scenarios table.

    python simulation/load_scenarios.py --check    # validate the files, change nothing
    python simulation/load_scenarios.py            # load them (safe to repeat: existing ones are replaced)

Uses the secret key from owner.env. The scenarios table cannot be read by any client, because a
scenario holds the caller's private facts.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import callers  # noqa: E402
from _supa import Admin, SupaError, load_env  # noqa: E402

REQUIRED = ["id", "category", "title", "brief", "customer", "facts", "difficulty", "languages", "moods"]


def validate(sc: dict, moods: dict[str, str]) -> list[str]:
    problems = [f"missing '{k}'" for k in REQUIRED if k not in sc]
    if problems:
        return problems
    if not 1 <= sc["difficulty"] <= 3:
        problems.append("difficulty must be 1-3")
    problems += [f"unknown language '{x}'" for x in sc["languages"] if x not in callers.LANGUAGES]
    problems += [f"unknown mood '{m}'" for m in sc["moods"] if m not in moods]
    return problems


def to_row(sc: dict) -> dict:
    return {
        "id": sc["id"], "category": sc["category"], "title": sc["title"], "brief": sc["brief"],
        "customer": sc["customer"], "facts": sc["facts"],
        "resolution_steps": sc.get("resolution_steps", []), "difficulty": sc["difficulty"],
        "languages": sc["languages"], "moods": sc["moods"],
        "expected_turns": sc.get("expected_turns", 8), "active": sc.get("active", True),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="validate only")
    args = ap.parse_args()

    moods = callers.load_moods()
    scenarios = callers.load_scenarios()
    bad = False
    for name, sc in scenarios.items():
        problems = validate(sc, moods)
        if sc.get("id") != name:
            problems.append(f"id '{sc.get('id')}' does not match the file name '{name}'")
        print(f"{'ok  ' if not problems else 'FAIL'} {name}" + ("" if not problems else ": " + "; ".join(problems)))
        bad |= bool(problems)
    if bad:
        return 1
    if args.check:
        return 0
    try:
        n = Admin(load_env()).upsert("scenarios", [to_row(sc) for sc in scenarios.values()], "id")
    except SupaError as exc:
        print(f"FAILED: {exc}")
        return 1
    print(f"Loaded {n} scenarios.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
