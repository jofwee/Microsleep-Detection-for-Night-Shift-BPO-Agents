"""Email + password sign-in for agents, including the "replace your temporary password" gate.

The face check (the second factor) comes after this: auth/face_mfa.py. A password alone gets
an agent into the app but writes nothing, because the server only accepts writes from a login
session that also passed a face check.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .supabase_client import Api, ApiError


class NotAnAgent(Exception):
    """A team leader tried to use the desktop app. Leaders sign in on the dashboard."""


@dataclass(frozen=True)
class Account:
    user_id: str
    full_name: str
    team_id: str
    must_change_password: bool


def sign_in(api: Api, email: str, password: str) -> Account:
    api.sign_in(email.strip().lower(), password)
    rows = api.select("profiles", {"id": f"eq.{api.user_id}", "select": "role,team_id,full_name,must_change_password,active"})
    profile = rows[0] if rows else None
    if profile is None or not profile["active"]:
        api.sign_out()
        raise ApiError(403, "inactive", "this account is not active")
    if profile["role"] != "agent":
        api.sign_out()
        raise NotAnAgent("team leaders sign in on the dashboard, not in this app")
    return Account(api.user_id or "", profile["full_name"], profile["team_id"], bool(profile["must_change_password"]))


def password_problems(password: str) -> list[str]:
    """The project's policy: 12+ characters with upper case, lower case and a digit."""
    problems = []
    if len(password) < 12:
        problems.append("use at least 12 characters")
    if not re.search(r"[a-z]", password):
        problems.append("add a lower-case letter")
    if not re.search(r"[A-Z]", password):
        problems.append("add an upper-case letter")
    if not re.search(r"[0-9]", password):
        problems.append("add a digit")
    return problems


def change_password(api: Api, new_password: str) -> None:
    problems = password_problems(new_password)
    if problems:
        raise ValueError("password too weak: " + ", ".join(problems))
    api.update_password(new_password)
    api.rpc("ack_password_changed")  # opens the "change your password" gate on the server side
