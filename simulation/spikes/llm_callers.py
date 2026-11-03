"""Spike S1, part 3: which Groq model plays a believable customer in English, Tagalog and Taglish?

    python simulation/spikes/llm_callers.py

Each model plays the same customer in the same short scripted calls. The agent lines are fixed, so
the models can be compared fairly. You judge how natural the Tagalog and Taglish sound (I cannot
do that reliably), so read the printed lines. Also measures speed and whether the model returns
the JSON the call loop needs. Nothing personal is sent: the customers are fictional.
"""
from __future__ import annotations

import json
import re
import sys
import time

import httpx
import yaml

from _common import GROQ_BASE, SPIKE_DIR, SpikeError, groq_key, normalise

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "prompts"
SCENARIOS = ROOT / "scenarios"

# Checked against the live GET /models of the Groq account (the docs pages were out of date: the Llama
# models are gone). Anything not available is skipped at run time.
MODELS = [
    ("openai/gpt-oss-120b", {"reasoning_effort": "low"}),
    ("openai/gpt-oss-20b", {"reasoning_effort": "low"}),
    ("qwen/qwen3.8-27b", {"reasoning_effort": "none"}),
]
RUNS = [("internet_down", "frustrated"), ("billing_dispute", "calm")]
LANGS = ["en", "tl", "taglish"]
MOODS = {
    "calm": "You are calm and polite, a bit tired of this but patient.",
    "confused": "You are confused about how things work and ask the agent to explain simply.",
    "in a hurry": "You are in a hurry and want this fixed fast. You cut the small talk short.",
    "frustrated": "You are frustrated. This is not the first time. You are not rude, but you let your annoyance show.",
}
AGENT_LINES = {
    "en": [
        "Thank you for calling, how may I help you today?",
        "I'm sorry to hear that. May I have your account number, please?",
        "Thank you. Please hold for a moment while I check that for you.",
    ],
    "tl": [
        "Magandang araw po, ano po ang maitutulong ko sa inyo?",
        "Pasensya na po sa abala. Pwede ko po bang makuha ang account number ninyo?",
        "Salamat po. Sandali lang po, tinitingnan ko po ang account ninyo.",
    ],
    "taglish": [
        "Hello po, thank you for calling, ano po ang maitutulong ko sa inyo today?",
        "Sorry to hear that po. Pwede ko po bang makuha yung account number ninyo?",
        "Thank you po. Please hold lang po saglit habang chine-check ko yung account ninyo.",
    ],
}
TL_WORDS = set("po opo ang ng sa na yung ko ako naman kasi hindi pa pang mo niyo ninyo namin ba lang talaga ito iyon ay mga nga ka kayo sila siya".split())
EN_WORDS = set("the is and you my i it to of for have not this that are was with please can your what how in on at be".split())


def system_prompt(scenario: dict, mood: str, lang: str) -> str:
    facts = "\n".join(f"- {k.replace('_', ' ')}: {v}" for k, v in scenario["facts"].items())
    return (
        (PROMPTS / "base.md").read_text(encoding="utf-8")
        .replace("{customer}", scenario["customer"])
        .replace("{brief}", scenario["brief"])
        .replace("{facts}", facts)
        .replace("{mood}", MOODS[mood])
        .replace("{language_style}", (PROMPTS / f"{lang}.md").read_text(encoding="utf-8").strip())
    )


def parse_reply(raw: str) -> tuple[str, str, bool]:
    """(text, state, json_ok). Tolerates code fences; falls back to the raw text."""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)  # some models print their reasoning
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        obj = json.loads(cleaned)
        return str(obj["text"]).strip(), str(obj.get("state", "continuing")), True
    except (ValueError, KeyError, TypeError):
        return raw.strip(), "continuing", False


def style_ok(lang: str, text: str) -> bool:
    words = normalise(text).split()
    if not words:
        return False
    tl = sum(w in TL_WORDS for w in words) / len(words)
    en = sum(w in EN_WORDS for w in words) / len(words)
    # A crude flag for glaring failures only (English that is not English, Tagalog that is not Tagalog).
    # Whether Taglish SOUNDS natural is for a Filipino speaker to judge.
    return {"en": tl < 0.10, "tl": tl >= 0.25 and en < 0.20, "taglish": tl >= 0.12 and en < 0.50}[lang]


def chat(client: httpx.Client, key: str, model: str, extra: dict, messages: list[dict]) -> tuple[str, float]:
    body = {"model": model, "messages": messages, "temperature": 0.8, "max_tokens": 300, **extra}
    use_json_mode = True
    for _ in range(6):
        payload = dict(body, **({"response_format": {"type": "json_object"}} if use_json_mode else {}))
        t0 = time.perf_counter()
        r = client.post(f"{GROQ_BASE}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=payload)
        if r.status_code == 429:
            wait = float(r.headers.get("retry-after") or (re.findall(r"in ([\d.]+)s", r.text) or [5])[0])
            print(f"      rate limited, waiting {wait:.0f} s", flush=True)
            time.sleep(wait + 1)
            continue
        if r.status_code == 400 and use_json_mode and "response_format" in r.text.lower():
            use_json_mode = False  # this model does not support JSON mode: ask in the prompt only
            continue
        if r.status_code == 400 and "reasoning_effort" in body and "reasoning_effort" in r.text.lower():
            body = {k: v for k, v in body.items() if k != "reasoning_effort"}  # this model does not take that setting
            continue
        if r.status_code >= 400:
            raise SpikeError(f"{model}: HTTP {r.status_code} {r.text[:300]}")
        return r.json()["choices"][0]["message"]["content"] or "", time.perf_counter() - t0
    raise SpikeError(f"{model}: still rate limited")


def main() -> int:
    try:
        key = groq_key()
    except SpikeError as exc:
        print(exc)
        return 1
    listing = httpx.get(f"{GROQ_BASE}/models", headers={"Authorization": f"Bearer {key}"}, timeout=30).json()
    available = {m["id"] for m in listing.get("data", [])}
    for m, _ in MODELS:
        if m not in available:
            print(f"(skipping {m}: not available to this Groq account)")
    MODELS[:] = [(m, e) for m, e in MODELS if m in available]
    scenarios = {p.stem: yaml.safe_load(p.read_text(encoding="utf-8")) for p in SCENARIOS.glob("*.yaml")}
    stats: dict[tuple[str, str], list] = {}
    md = ["# Spike S1: caller models\n"]
    with httpx.Client(timeout=90) as client:
        for scen_id, mood in RUNS:
            scen = scenarios[scen_id]
            for lang in LANGS:
                head = f"{scen['title']}  |  mood: {mood}  |  language: {lang}"
                print(f"\n{'=' * 78}\n{head}\n{'=' * 78}")
                md.append(f"\n## {head}\n")
                for model, extra in MODELS:
                    print(f"\n-- {model}")
                    md.append(f"\n### {model}\n")
                    messages = [{"role": "system", "content": system_prompt(scen, mood, lang)}]
                    for agent_line in AGENT_LINES[lang]:
                        messages.append({"role": "user", "content": agent_line})
                        raw, secs = chat(client, key, model, extra, messages)
                        text, state, ok = parse_reply(raw)
                        messages.append({"role": "assistant", "content": json.dumps({"text": text, "state": state}, ensure_ascii=False)})
                        stats.setdefault((model, lang), []).append((secs, ok, style_ok(lang, text)))
                        flag = "" if ok else "  [NOT VALID JSON]"
                        print(f"   agent : {agent_line}\n   caller: {text}   ({state}, {secs:.1f} s){flag}")
                        md.append(f"- agent: {agent_line}\n  - caller ({state}, {secs:.1f} s): {text}\n")

    print(f"\n{'=' * 78}\nSummary (mean seconds per reply / valid JSON / language style looks right)\n{'=' * 78}")
    print(f"{'model':<26}" + "".join(f"{lang:>26}" for lang in LANGS))
    for model, _ in MODELS:
        line = f"{model:<26}"
        for lang in LANGS:
            rows = stats[(model, lang)]
            line += f"{sum(r[0] for r in rows) / len(rows):>8.1f}s /{sum(r[1] for r in rows) / len(rows):>5.0%} /{sum(r[2] for r in rows) / len(rows):>5.0%}"
        print(line)
    (SPIKE_DIR).mkdir(parents=True, exist_ok=True)
    (SPIKE_DIR / "llm_samples.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\nSaved the full conversations to {SPIKE_DIR / 'llm_samples.md'}")
    print("\nNow rate, for each model, how natural its TAGALOG and TAGLISH sounded, 1 (robotic) to 5 (a real Filipino caller).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
