"""Build the system prompt for an AI caller from a scenario, a customer record, a mood and a language.

The Edge Function caller-turn builds the same prompt in TypeScript (generated from these same files
by build_prompts.py); a golden-file test keeps the two identical.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
PROMPTS = ROOT / "prompts"
SCENARIOS = ROOT / "scenarios"
LANGUAGES = ("en", "tl", "taglish")
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def load_moods() -> dict[str, str]:
    return yaml.safe_load((PROMPTS / "moods.yaml").read_text(encoding="utf-8"))


def load_styles() -> dict[str, str]:
    return {lang: (PROMPTS / f"{lang}.md").read_text(encoding="utf-8").strip() for lang in LANGUAGES}


def load_base() -> str:
    return (PROMPTS / "base.md").read_text(encoding="utf-8")


def load_scenarios() -> dict[str, dict]:
    return {p.stem: yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(SCENARIOS.glob("*.yaml"))}


def customer_values(customer: dict) -> dict[str, str]:
    """The values that fill {placeholders}: the customer's columns plus everything in their profile."""
    values = {
        "full_name": customer["full_name"],
        "account_no": customer["account_no"],
        "plan": customer["plan"],
        "balance": f"{float(customer['balance']):.2f}",
    }
    values.update({k: str(v) for k, v in (customer.get("profile") or {}).items()})
    return values


def fill(text: str, values: dict[str, str]) -> str:
    return PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def unfilled(text: str) -> list[str]:
    """Placeholders still present after filling. In the base template, only the JSON braces remain."""
    return PLACEHOLDER.findall(text)


def build_system_prompt(scenario: dict, customer: dict, mood: str, language: str) -> str:
    values = customer_values(customer)
    facts = "\n".join(f"- {k.replace('_', ' ')}: {fill(str(v), values)}" for k, v in scenario["facts"].items())
    return (
        load_base()
        .replace("{customer}", fill(scenario["customer"], values))
        .replace("{brief}", fill(scenario["brief"], values))
        .replace("{facts}", facts)
        .replace("{mood}", load_moods()[mood])
        .replace("{language_style}", load_styles()[language])
    )
