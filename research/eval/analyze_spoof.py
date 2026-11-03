"""Score the attack trials: how often does each fake get through, and how often is a real person turned away?

    python research/eval/analyze_spoof.py

Applies several acceptance rules to the same logged trials (see evalkit/spoof.py), so the value of the challenge and of the
passive model can be told apart. Writes tables and charts to research/eval/results/spoof/.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "client"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from evalkit.paths import DATA, read_csv, results_dir, write_csv  # noqa: E402
from evalkit.spoof import ATTACKS, BONAFIDE, RULES, SpoofTrial, evaluate, interval  # noqa: E402

INK, G400, G500, G200 = "#0a0a0a", "#a3a3a3", "#737373", "#e9e9e9"


def num(x: str) -> float | None:
    return float(x) if x not in ("", None) else None


def load() -> list[SpoofTrial]:
    path = DATA / "spoof" / "trials.csv"
    if not path.exists():
        return []
    return [SpoofTrial(r["subject"], r["attack"], r["lighting"], r["challenge_passed"] == "1", num(r["passive_median"]), num(r["passive_min"]), num(r["match"]), int(r["frames"] or 0))
            for r in read_csv(path)]


def main() -> int:
    trials = load()
    if not trials:
        print(f"No trials yet. Run spoof_trials.py first (they are saved under {DATA / 'spoof'}).")
        return 1
    out = results_dir("spoof")
    kinds = [k for k in (*ATTACKS,) if any(t.attack == k for t in trials)]
    counts = {k: sum(1 for t in trials if t.attack == k) for k in (BONAFIDE, *kinds)}
    print("trials:", ", ".join(f"{k} {n}" for k, n in counts.items()))

    rows, summary = [], []
    for rule in RULES:
        r = evaluate(trials, rule)
        summary.append((rule, r))
        for k, (acc, n) in r["per_attack"].items():
            lo, hi = interval(acc, n)
            rows.append((rule.name, k, acc, n, acc / n, lo, hi))
        lo, hi = interval(r["bona_rejected"], r["bona_total"]) if r["bona_total"] else (None, None)
        rows.append((rule.name, "BPCER (genuine rejected)", r["bona_rejected"], r["bona_total"], r["bpcer"], lo, hi))
    write_csv(out / "attack_rates.csv", ["rule", "presentation", "accepted_or_rejected", "trials", "rate", "ci_low", "ci_high"], rows)

    print(f"\n{'rule':<66}" + "".join(f"{k:>14}" for k in kinds) + f"{'genuine rejected':>20}")
    for rule, r in summary:
        cells = "".join(f"{(str(r['per_attack'][k][0]) + '/' + str(r['per_attack'][k][1])) if k in r['per_attack'] else '–':>14}" for k in kinds)
        print(f"{rule.name:<66}{cells}{(str(r['bona_rejected']) + '/' + str(r['bona_total'])) if r['bona_total'] else '–':>20}")
    print("(attacks: accepted/total, lower is better; genuine: rejected/total, lower is better)")

    # chart: acceptance of each attack under each rule
    if kinds:
        fig, ax = plt.subplots(figsize=(8, 3.8))
        width = 0.8 / len(RULES)
        shades = [INK, "#525252", G500, G400, G200]
        for i, (rule, r) in enumerate(summary):
            vals = [100 * (r["per_attack"][k][0] / r["per_attack"][k][1]) if k in r["per_attack"] else 0 for k in kinds]
            ax.bar([j + (i - len(RULES) / 2) * width + width / 2 for j in range(len(kinds))], vals, width=width * 0.9, color=shades[i], edgecolor="white", label=rule.name)
        ax.set_xticks(list(range(len(kinds))), [k.replace("_", " ") for k in kinds])
        ax.set_ylabel("attacks accepted (%)")
        ax.set_title("How often each fake gets through, by acceptance rule", fontsize=10, loc="left")
        ax.legend(frameon=False, fontsize=6.5, loc="upper right")
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        fig.tight_layout()
        fig.savefig(out / "attack_acceptance.png", dpi=160)
        plt.close(fig)

    # chart: the passive score of every presentation type
    groups = [(k, [t.passive_median for t in trials if t.attack == k and t.passive_median is not None]) for k in (BONAFIDE, *kinds)]
    groups = [(k, v) for k, v in groups if v]
    if groups:
        fig, ax = plt.subplots(figsize=(7.5, 3.2))
        ax.boxplot([v for _, v in groups], tick_labels=[k.replace("_", " ") for k, _ in groups], vert=False, medianprops={"color": INK}, boxprops={"color": INK}, whiskerprops={"color": INK}, capprops={"color": INK})
        ax.axvline(0.5, color=INK, linewidth=1, linestyle=(0, (2, 3)))
        ax.axvline(0.35, color=G400, linewidth=1, linestyle=(0, (2, 3)))
        ax.set_xlabel("passive photo-check score (median of a trial); dashed lines: 0.5 server, 0.35 in-shift lock")
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        fig.tight_layout()
        fig.savefig(out / "passive_scores.png", dpi=160)
        plt.close(fig)
    print(f"results written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
