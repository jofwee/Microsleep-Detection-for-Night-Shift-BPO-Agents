"""Voice-loop delay report from the real calls on the hosted project.

    python research/eval/latency_report.py [--team "Team A"]

Reads call_turns with the owner key (owner.env), pairs agent and caller turns, and writes a table and charts to
research/eval/results/latency/. Throwaway teams (zz-*) are left out.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from _supa import Admin, load_env  # noqa: E402
from evalkit.latency import TARGET_S, exchanges, share_within  # noqa: E402
from evalkit.metrics import describe  # noqa: E402
from evalkit.paths import results_dir, write_csv  # noqa: E402

INK, G400, G200 = "#0a0a0a", "#a3a3a3", "#e9e9e9"
LANGS = {"en": "English", "taglish": "Taglish", "tl": "Tagalog"}


def fetch(admin: Admin, team_name: str | None) -> tuple[list[dict], dict[str, str]]:
    teams = [t for t in admin.select("teams", {"select": "id,name"}) if not t["name"].startswith("zz-") and (not team_name or t["name"] == team_name)]
    turns, langs = [], {}
    for t in teams:
        calls = admin.select("calls", {"team_id": f"eq.{t['id']}", "select": "id,language", "limit": "5000"})
        langs.update({c["id"]: c["language"] for c in calls})
        turns += admin.select("call_turns", {"team_id": f"eq.{t['id']}", "select": "call_id,seq,speaker,stt_ms,llm_ms,tts_ms", "limit": "20000"})
    return turns, langs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--team", default=None, help="only this team (default: every real team)")
    args = ap.parse_args()

    turns, langs = fetch(Admin(load_env()), args.team)
    ex = exchanges(turns, langs)
    out = results_dir("latency")
    if not ex:
        print("No exchanges with all three timings yet. Make a few calls first.")
        return 1

    rows = []
    groups = {"all": ex, **{LANGS.get(k, k): [e for e in ex if e.language == k] for k in sorted({e.language for e in ex})}}
    for name, items in groups.items():
        for part, getter in (("speech to text", lambda e: e.stt_ms), ("caller model", lambda e: e.llm_ms), ("voice (first sentence)", lambda e: e.tts_ms), ("total", lambda e: e.total_ms)):
            d = describe([getter(e) / 1000 for e in items])
            rows.append((name, part, d["n"], round(d["mean"], 2), round(d["median"], 2), round(d["p95"], 2), round(d["max"], 2)))
    write_csv(out / "latency_table.csv", ["group", "part", "n", "mean_s", "median_s", "p95_s", "max_s"], rows)
    write_csv(out / "exchanges.csv", ["call_id", "seq", "language", "stt_ms", "llm_ms", "tts_ms", "total_ms"], [(e.call_id, e.seq, e.language, e.stt_ms, e.llm_ms, e.tts_ms, e.total_ms) for e in ex])

    # chart 1: where the time goes (stacked mean per language), with the target line
    fig, ax = plt.subplots(figsize=(7, 3.6))
    names = list(groups)
    parts = [("speech to text", [e.stt_ms for e in ex], INK), ("caller model", None, "#737373"), ("voice (first sentence)", None, G400)]
    bottoms = [0.0] * len(names)
    for label, _, colour in parts:
        vals = []
        for n in names:
            items = groups[n]
            vals.append(sum({"speech to text": e.stt_ms, "caller model": e.llm_ms, "voice (first sentence)": e.tts_ms}[label] for e in items) / len(items) / 1000)
        ax.bar(names, vals, bottom=bottoms, color=colour, edgecolor="white", linewidth=2, width=0.5, label=label)
        bottoms = [b + v for b, v in zip(bottoms, vals)]
    ax.axhline(TARGET_S, color=INK, linewidth=1, linestyle=(0, (2, 3)))
    ax.text(len(names) - 0.5, TARGET_S + 0.05, "target 3 s", ha="right", fontsize=8, color=INK)
    ax.set_ylabel("seconds (mean)")
    ax.set_title("Where the time of one spoken turn goes (without network)", fontsize=10, loc="left")
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(axis="y", color=G200)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(out / "latency_parts.png", dpi=160)
    plt.close(fig)

    # chart 2: distribution of the total
    fig, ax = plt.subplots(figsize=(7, 3.2))
    ax.hist([e.total_ms / 1000 for e in ex], bins=20, color=INK, edgecolor="white")
    ax.axvline(TARGET_S, color=INK, linewidth=1, linestyle=(0, (2, 3)))
    ax.set_xlabel("seconds from the end of the agent's speech to the caller's voice (server-side parts)")
    ax.set_ylabel("exchanges")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(out / "latency_distribution.png", dpi=160)
    plt.close(fig)

    d = describe([e.total_ms / 1000 for e in ex])
    share = share_within(ex)
    print(f"{d['n']} spoken exchanges from {len({e.call_id for e in ex})} calls (the part the server measures, without network and the 0.5 s pause)")
    print(f"  total: mean {d['mean']:.2f} s, median {d['median']:.2f} s, 95th percentile {d['p95']:.2f} s, slowest {d['max']:.2f} s")
    print(f"  within {TARGET_S:.0f} s: {share * 100:.0f}%")
    for name, items in groups.items():
        if name != "all":
            print(f"  {name}: {len(items)} exchanges, mean {sum(e.total_ms for e in items) / len(items) / 1000:.2f} s")
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
