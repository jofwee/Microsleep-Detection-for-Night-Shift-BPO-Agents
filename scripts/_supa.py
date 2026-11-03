"""Small shared helpers for the owner scripts: read owner.env, call the Supabase REST/Auth APIs.

The secret key (sb_secret_...) bypasses all access rules. It lives only in owner.env, outside
OneDrive and git, and is used only by these scripts. Never put it in the client or dashboard.
"""
from __future__ import annotations

import os
import re
import secrets
import string
from pathlib import Path

import httpx

DEFAULT_ENV = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "thesis-microsleep" / "owner.env"


class SupaError(RuntimeError):
    pass


def load_env(path: Path = DEFAULT_ENV) -> dict[str, str]:
    if not path.is_file():
        raise SupaError(f"{path} not found")
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    for needed in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_SECRET_KEY"):
        if not env.get(needed):
            raise SupaError(f"{needed} is empty in {path}")
    env["SUPABASE_URL"] = env["SUPABASE_URL"].rstrip("/")
    return env


def key_headers(key: str) -> dict[str, str]:
    """New-style keys (sb_...) go in `apikey` only. A legacy JWT key goes in both headers."""
    h = {"apikey": key}
    if key.startswith("eyJ"):
        h["Authorization"] = f"Bearer {key}"
    return h


class Admin:
    """The secret-key client for the owner scripts."""

    def __init__(self, env: dict[str, str], timeout: float = 30.0) -> None:
        self.url = env["SUPABASE_URL"]
        self.headers = key_headers(env["SUPABASE_SECRET_KEY"])
        self.http = httpx.Client(timeout=timeout)

    def _check(self, r: httpx.Response, what: str) -> httpx.Response:
        if r.status_code >= 400:
            raise SupaError(f"{what} failed: HTTP {r.status_code} {r.text[:300]}")
        return r

    def create_user(self, email: str, password: str, full_name: str) -> str:
        r = self.http.post(
            f"{self.url}/auth/v1/admin/users",
            headers=self.headers,
            json={"email": email, "password": password, "email_confirm": True, "user_metadata": {"full_name": full_name}},
        )
        return self._check(r, "create user").json()["id"]

    def delete_user(self, user_id: str) -> None:
        self.http.delete(f"{self.url}/auth/v1/admin/users/{user_id}", headers=self.headers)

    def insert(self, table: str, row: dict) -> dict:
        r = self.http.post(
            f"{self.url}/rest/v1/{table}",
            headers={**self.headers, "Prefer": "return=representation"},
            json=row,
        )
        return self._check(r, f"insert into {table}").json()[0]

    def upsert(self, table: str, rows: list[dict], on_conflict: str) -> int:
        """Insert rows, replacing any that already exist (matched on `on_conflict`). Returns the row count."""
        r = self.http.post(
            f"{self.url}/rest/v1/{table}",
            headers={**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": on_conflict},
            json=rows,
        )
        self._check(r, f"upsert into {table}")
        return len(rows)

    def update(self, table: str, match: dict[str, str], values: dict) -> None:
        params = {k: f"eq.{v}" for k, v in match.items()}
        r = self.http.patch(f"{self.url}/rest/v1/{table}", headers=self.headers, params=params, json=values)
        self._check(r, f"update {table}")

    def delete(self, table: str, match: dict[str, str]) -> None:
        params = {k: f"eq.{v}" for k, v in match.items()}
        self.http.delete(f"{self.url}/rest/v1/{table}", headers=self.headers, params=params)

    def select(self, table: str, params: dict[str, str]) -> list[dict]:
        r = self.http.get(f"{self.url}/rest/v1/{table}", headers=self.headers, params=params)
        return self._check(r, f"select from {table}").json()


def random_password(length: int = 16) -> str:
    """At least one upper, lower and digit; no look-alike characters (the project's 12+ policy)."""
    upper, lower, digit = "ABCDEFGHJKMNPQRSTUVWXYZ", "abcdefghijkmnpqrstuvwxyz", "23456789"
    chars = [secrets.choice(upper), secrets.choice(lower), secrets.choice(digit)]
    chars += [secrets.choice(upper + lower + digit) for _ in range(length - 3)]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
