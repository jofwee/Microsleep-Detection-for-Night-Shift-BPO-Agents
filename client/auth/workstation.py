"""Workstation pairing: this PC trades a team leader's one-time code for a secret token.

The token is kept in the Windows Credential Manager (through the `keyring` package), not in a
file. The server stores only its hash. Without it the server refuses face checks and writes
from this PC.
"""
from __future__ import annotations

import json
import platform
from dataclasses import dataclass
from typing import Protocol

import keyring

from .supabase_client import Api

SERVICE = "thesis-microsleep"
ACCOUNT = "workstation"


class Store(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...
    def set_password(self, service: str, username: str, password: str) -> None: ...
    def delete_password(self, service: str, username: str) -> None: ...


@dataclass(frozen=True)
class Workstation:
    id: str
    team_id: str
    token: str


def load(store: Store = keyring) -> Workstation | None:
    raw = store.get_password(SERVICE, ACCOUNT)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return Workstation(data["id"], data["team_id"], data["token"])
    except (ValueError, KeyError):
        return None


def save(ws: Workstation, store: Store = keyring) -> None:
    store.set_password(SERVICE, ACCOUNT, json.dumps({"id": ws.id, "team_id": ws.team_id, "token": ws.token}))


def forget(store: Store = keyring) -> None:
    try:
        store.delete_password(SERVICE, ACCOUNT)
    except keyring.errors.PasswordDeleteError:
        pass


def pair(api: Api, code: str, name: str | None = None, store: Store = keyring) -> Workstation:
    """Redeem a pairing code. Needs no login: the code itself is the credential."""
    data = api.function("pair-workstation", {"code": code, "name": name or platform.node() or "workstation"}, auth=False)
    ws = Workstation(data["workstation_id"], data["team_id"], data["token"])
    save(ws, store)
    api.workstation_token = ws.token
    return ws
