"""The customer search an agent uses during a call (terminal version; the PySide6 desk comes in milestone 6)."""
from __future__ import annotations

DISPOSITIONS = {"r": "resolved", "u": "unresolved", "e": "escalated"}


def format_customer(c: dict | None) -> str:
    if c is None:
        return "  No customer found with that account number."
    p = c.get("profile") or {}
    return (
        f"  {c['full_name']}   account {c['account_no']}   status {c['status']}\n"
        f"  plan {c['plan']}   balance due {float(c['balance']):,.2f}   member since {p.get('member_since', '?')}\n"
        f"  area {p.get('area', '?')}   birthdate {p.get('birthdate', '?')}   mobile {p.get('contact_number', '?')}\n"
        f"  email {p.get('email', '?')}"
    )


def parse_disposition(answer: str) -> str | None:
    """'r' / 'resolved' -> 'resolved', and so on. None if it is not recognised."""
    a = answer.strip().lower()
    if a in DISPOSITIONS:
        return DISPOSITIONS[a]
    return a if a in DISPOSITIONS.values() else None
