"""A small Supabase client for the desktop app: Auth, the data API, and Edge Functions.

The login session lives in memory only. Nothing is written to disk, so closing the app signs
the agent out. Only the publishable key is used here; the secret key never reaches a PC.
"""
from __future__ import annotations

import base64
import json
import threading
import time
from typing import Any

import httpx


class ApiError(Exception):
    """A non-2xx answer. `code` is stable and machine-readable, e.g. "verification_failed"."""

    def __init__(self, status: int, code: str, message: str = "", extra: dict | None = None) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra or {}


def jwt_claims(token: str) -> dict:
    """The payload of a JWT. Not verified: the server verifies, the client only reads its own session id."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        return {}


class Api:
    def __init__(
        self,
        url: str,
        publishable_key: str,
        workstation_token: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.key = publishable_key
        self.workstation_token = workstation_token
        self._http = client or httpx.Client(timeout=30.0)
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.expires_at = 0.0
        self._refresh_lock = threading.Lock()  # heartbeats and calls share one session

    # --- session ---------------------------------------------------------------------------
    @property
    def signed_in(self) -> bool:
        return self.access_token is not None

    @property
    def user_id(self) -> str | None:
        return jwt_claims(self.access_token).get("sub") if self.access_token else None

    @property
    def session_id(self) -> str | None:
        return jwt_claims(self.access_token).get("session_id") if self.access_token else None

    def _store(self, data: dict) -> None:
        self.access_token = data["access_token"]
        self.refresh_token = data.get("refresh_token")
        self.expires_at = time.time() + float(data.get("expires_in", 3600))

    def sign_in(self, email: str, password: str) -> dict:
        r = self._send("POST", "/auth/v1/token", auth=False, params={"grant_type": "password"},
                       json={"email": email, "password": password})
        data = r.json()
        self._store(data)
        return data.get("user", {})

    def _refresh(self) -> None:
        r = self._send("POST", "/auth/v1/token", auth=False, params={"grant_type": "refresh_token"},
                       json={"refresh_token": self.refresh_token})
        self._store(r.json())

    def sign_out(self) -> None:
        try:
            if self.access_token:
                self._send("POST", "/auth/v1/logout", params={"scope": "local"})
        except ApiError:
            pass  # the token may already be dead; either way this app forgets it
        finally:
            self.access_token = self.refresh_token = None
            self.expires_at = 0.0

    def update_password(self, new_password: str) -> None:
        self._send("PUT", "/auth/v1/user", json={"password": new_password})

    # --- data API and functions ------------------------------------------------------------
    def rpc(self, name: str, params: dict | None = None) -> Any:
        r = self._send("POST", f"/rest/v1/rpc/{name}", json=params or {})
        return r.json() if r.content else None

    def select(self, table: str, params: dict[str, str] | None = None) -> list[dict]:
        return self._send("GET", f"/rest/v1/{table}", params=params).json()

    def insert(self, table: str, rows: dict | list[dict], *, ignore_duplicates: bool = False,
               on_conflict: str | None = None) -> list[dict]:
        """on_conflict: comma-separated columns of a unique index; the default is the primary key."""
        prefer = "return=representation" + (",resolution=ignore-duplicates" if ignore_duplicates else "")
        r = self._send("POST", f"/rest/v1/{table}", json=rows, headers={"Prefer": prefer},
                       params={"on_conflict": on_conflict} if on_conflict else None)
        return r.json() if r.content else []

    def patch(self, table: str, match: dict[str, str], values: dict) -> None:
        self._send("PATCH", f"/rest/v1/{table}", params={k: f"eq.{v}" for k, v in match.items()}, json=values)

    def function(self, name: str, payload: dict | None = None, *, auth: bool = True) -> dict:
        r = self._send("POST", f"/functions/v1/{name}", auth=auth, json=payload or {})
        return r.json()

    def upload(self, name: str, fields: dict[str, str], filename: str, content: bytes, content_type: str, *, timeout: float = 60.0) -> dict:
        """Send a file to an Edge Function as multipart/form-data (the audio of a spoken turn)."""
        r = self._send("POST", f"/functions/v1/{name}", data=fields, files={"file": (filename, content, content_type)}, timeout=timeout)
        return r.json()

    # --- plumbing --------------------------------------------------------------------------
    def _headers(self, auth: bool, extra: dict | None) -> dict[str, str]:
        h = {"apikey": self.key}
        if auth and self.access_token:
            h["Authorization"] = f"Bearer {self.access_token}"
        if auth and self.workstation_token:
            h["x-workstation-token"] = self.workstation_token
        if extra:
            h.update(extra)
        return h

    def _send(self, method: str, path: str, *, auth: bool = True, params: dict | None = None,
              json: Any = None, headers: dict | None = None, data: dict | None = None,
              files: dict | None = None, timeout: float | None = None) -> httpx.Response:
        if auth and self.refresh_token and time.time() > self.expires_at - 60:
            with self._refresh_lock:  # keep the login alive across the one-hour token lifetime
                if time.time() > self.expires_at - 60:  # another thread may have just done it
                    self._refresh()
        extra = {} if timeout is None else {"timeout": timeout}
        try:
            r = self._http.request(method, self.url + path, params=params, json=json, data=data, files=files,
                                   headers=self._headers(auth, headers), **extra)
        except httpx.TransportError as exc:
            raise ApiError(0, "network_error", str(exc)) from exc
        if r.status_code >= 400:
            raise self._error(r)
        return r

    @staticmethod
    def _error(r: httpx.Response) -> ApiError:
        try:
            body = r.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        code = body.get("error_code") or body.get("error") or body.get("code") or f"http_{r.status_code}"
        message = body.get("message") or body.get("msg") or body.get("error_description") or r.text[:200]
        extra = {k: v for k, v in body.items() if k not in ("error_code", "error", "code", "message", "msg")}
        return ApiError(r.status_code, str(code), str(message), extra)
