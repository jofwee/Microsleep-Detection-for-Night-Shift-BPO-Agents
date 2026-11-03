"""The simulated call center's content: scenarios, moods, prompts and the fictional customers."""
import os
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "simulation"))
sys.path.insert(0, str(ROOT / "scripts"))
import callers  # noqa: E402
import load_scenarios  # noqa: E402
import seed_customers  # noqa: E402

GOLDEN = ROOT / "simulation" / "prompts" / "golden" / "internet_down_taglish_frustrated.txt"
SAMPLE_CUSTOMER = {
    "full_name": "Maria Santos", "account_no": "AC-1234-5678", "plan": "Fiber 300", "balance": 1500,
    "profile": {"area": "Quezon City", "birthdate": "1990-04-12", "email": "maria.santos@example.com",
                "contact_number": "09171234567", "member_since": "2019"},
}
KNOWN_PLACEHOLDERS = {"full_name", "account_no", "plan", "balance", "area", "birthdate", "email", "contact_number", "member_since"}


def test_every_scenario_file_is_valid():
    moods = callers.load_moods()
    scenarios = callers.load_scenarios()
    assert len(scenarios) >= 7
    for name, sc in scenarios.items():
        assert load_scenarios.validate(sc, moods) == [], name
        assert sc["id"] == name
        assert len(sc["languages"]) == 3 and set(sc["languages"]) == set(callers.LANGUAGES)


def test_every_mood_used_by_a_scenario_is_defined_and_substantial():
    moods = callers.load_moods()
    used = {m for sc in callers.load_scenarios().values() for m in sc["moods"]}
    assert used <= set(moods)
    assert all(len(text) > 150 for text in moods.values())  # the spike showed one-liners are ignored
    assert "from your very first sentence" in moods["frustrated"]


def test_scenarios_only_use_placeholders_a_customer_can_fill():
    for name, sc in callers.load_scenarios().items():
        text = " ".join([sc["customer"], sc["brief"]] + [str(v) for v in sc["facts"].values()])
        assert set(callers.unfilled(text)) <= KNOWN_PLACEHOLDERS, name


def test_prompts_build_for_every_scenario_language_and_mood_with_nothing_left_unfilled():
    for name, sc in callers.load_scenarios().items():
        for lang in callers.LANGUAGES:
            for mood in sc["moods"]:
                prompt = callers.build_system_prompt(sc, SAMPLE_CUSTOMER, mood, lang)
                assert callers.unfilled(prompt) == [], (name, lang, mood)
                assert "Maria Santos" in prompt and "AC-1234-5678" in prompt
                assert callers.load_moods()[mood] in prompt
                assert '{"text"' in prompt  # the JSON output instruction survived


def test_prompt_matches_the_golden_file():
    """The TypeScript builder in the caller-turn function is tested against this same file."""
    sc = callers.load_scenarios()["internet_down"]
    prompt = callers.build_system_prompt(sc, SAMPLE_CUSTOMER, "frustrated", "taglish")
    if os.environ.get("UPDATE_GOLDEN") or not GOLDEN.exists():
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(prompt, encoding="utf-8", newline="\n")
    assert prompt == GOLDEN.read_text(encoding="utf-8")


def test_customer_values_include_the_profile_and_a_formatted_balance():
    v = callers.customer_values(SAMPLE_CUSTOMER)
    assert v["balance"] == "1500.00" and v["area"] == "Quezon City" and v["account_no"] == "AC-1234-5678"


def test_fill_leaves_unknown_placeholders_visible():
    assert callers.fill("Hi {full_name} {nope}", {"full_name": "Ana"}) == "Hi Ana {nope}"


# --- fictional customers ---------------------------------------------------------------------
def test_customers_are_reproducible_unique_and_shaped_for_the_table():
    a = seed_customers.make_customers(60, seed=1)
    assert a == seed_customers.make_customers(60, seed=1)
    assert a != seed_customers.make_customers(60, seed=2)
    assert len({c["account_no"] for c in a}) == 60
    for c in a:
        assert c["account_no"].startswith("AC-") and len(c["account_no"]) == 12
        assert c["status"] in ("ACTIVE", "SUSPENDED") and c["balance"] >= 0
        first, _, rest = c["full_name"].partition(" ")
        assert first in seed_customers.FIRST_NAMES and rest in seed_customers.SURNAMES
        assert c["profile"]["email"].endswith("@example.com")  # never a real domain
        assert c["profile"]["contact_number"].startswith("09") and len(c["profile"]["contact_number"]) == 11
        born = date.fromisoformat(c["profile"]["birthdate"])
        assert 19 <= (date(2026, 11, 3) - born).days / 365.25 <= 72


def test_every_customer_fills_every_scenario_without_leftovers():
    customers = seed_customers.make_customers(10, seed=3)
    for sc in callers.load_scenarios().values():
        for c in customers:
            assert callers.unfilled(callers.build_system_prompt(sc, c, sc["moods"][0], "en")) == []
