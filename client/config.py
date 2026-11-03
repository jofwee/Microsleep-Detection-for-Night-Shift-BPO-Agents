"""Client settings: the Supabase project URL and the PUBLISHABLE key.

Read from environment variables, then from client/.env. The publishable key is designed to be
shipped inside apps (access is controlled by row level security), but the SECRET key must never
be here: it bypasses every rule and lives only in the owner's owner.env.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import sys

# a packaged app keeps its .env next to the exe; in development it is client/.env
ENV_FILE = (Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent) / ".env"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    supabase_url: str
    publishable_key: str


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_config(env_file: Path = ENV_FILE) -> Config:
    file_values = _read_env_file(env_file)
    url = os.environ.get("SUPABASE_URL") or file_values.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_PUBLISHABLE_KEY") or file_values.get("SUPABASE_PUBLISHABLE_KEY", "")
    if not url or not key:
        raise ConfigError(f"set SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY (in the environment or in {env_file})")
    if key.startswith("sb_secret_") or key.startswith("eyJ") and "service_role" in key:
        raise ConfigError("that looks like a SECRET key. The client must only ever hold the publishable key.")
    return Config(url.rstrip("/"), key)
