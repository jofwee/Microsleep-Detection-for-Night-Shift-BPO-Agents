"""Generate fictional customers for the simulated call center and load them into the project.

    python simulation/seed_customers.py --print          # show what would be loaded, change nothing
    python simulation/seed_customers.py                  # load 40 customers (safe to repeat)
    python simulation/seed_customers.py --count 100 --seed 7

Every name, number and address is invented from the seed (common Filipino first names and surnames,
combined at random; Faker's Philippine locales only produce US names), so the same seed always makes
the same customers. Any resemblance to a real person is a coincidence. Account numbers look like AC-1234-5678. Loading uses the
secret key from owner.env.
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from _supa import Admin, SupaError, load_env  # noqa: E402

PLANS = [("Fiber 100", 1299), ("Fiber 200", 1599), ("Fiber 300", 1999), ("Fiber 500", 2699)]
FIRST_NAMES = (
    "Maria Jose Juan Ana Angelica Mark John Jericho Kristine Carlo Jasmine Rico Liza Ramon Cristina Miguel Patricia "
    "Eduardo Grace Joel Marites Rowena Dennis Arlene Bryan Cheska Noel Lourdes Ferdinand Rosario Alvin Joanna "
    "Renato Mylene Jomar Katrina Ronaldo Shiela Emmanuel Charmaine Rodel Bernadette Allan Michelle Jerome Daisy"
).split()
SURNAMES = [
    "Santos", "Reyes", "Cruz", "Bautista", "Ocampo", "Garcia", "Mendoza", "Torres", "Tomas", "Andrada", "Castillo",
    "Flores", "Villanueva", "Ramos", "Dela Cruz", "Aquino", "Navarro", "De Leon", "Salazar", "Pascual", "Domingo",
    "Valdez", "Soriano", "Mercado", "Hernandez", "Lim", "Tan", "Gonzales", "Rivera", "Del Rosario", "Manalo", "Aguilar",
]
AREAS = ["Quezon City", "Makati", "Cebu City", "Davao City", "Pasig", "Taguig", "Mandaluyong", "Caloocan", "Iloilo City", "Baguio"]


def make_customers(count: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    used: set[str] = set()
    out = []
    while len(out) < count:
        account = f"AC-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}"
        if account in used:
            continue
        used.add(account)
        name = f"{rng.choice(FIRST_NAMES)} {rng.choice(SURNAMES)}"
        plan, price = rng.choice(PLANS)
        handle = "".join(c for c in name.lower() if c.isalpha() or c == " ").replace(" ", ".")[:24].strip(".")
        out.append(
            {
                "account_no": account,
                "full_name": name,
                "plan": plan,
                "balance": round(rng.choice([0, 0, price, price, price * 2, rng.randint(100, 3000)]), 2),
                "status": rng.choices(["ACTIVE", "SUSPENDED"], weights=[9, 1])[0],
                "profile": {
                    "area": rng.choice(AREAS),
                    "birthdate": (date(1955, 1, 1) + timedelta(days=rng.randint(0, 365 * 50))).isoformat(),
                    "email": f"{handle}@example.com",
                    "contact_number": "09" + "".join(str(rng.randint(0, 9)) for _ in range(9)),
                    "member_since": str(rng.randint(2015, 2025)),
                },
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=40)
    ap.add_argument("--seed", type=int, default=20261103)
    ap.add_argument("--print", action="store_true", dest="show", help="print a sample and change nothing")
    args = ap.parse_args()

    rows = make_customers(args.count, args.seed)
    if args.show:
        for r in rows[:5]:
            print(r["account_no"], r["full_name"], r["plan"], r["balance"], r["profile"]["area"], r["profile"]["birthdate"])
        print(f"... {len(rows)} customers would be loaded")
        return 0
    try:
        n = Admin(load_env()).upsert("customers", rows, "account_no")
    except SupaError as exc:
        print(f"FAILED: {exc}")
        return 1
    print(f"Loaded {n} fictional customers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
