"""Score the collected identity trials: FAR, FRR and the equal error rate, with the threshold the app uses.

    python research/eval/analyze_identity.py [--tau 0.45]

Writes tables and charts to research/eval/results/identity/.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "client"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from evalkit.identity import Probe, all_trials, by_condition, operating_points, split, summarize  # noqa: E402
from evalkit.metrics import wilson  # noqa: E402
from evalkit.paths import DATA, results_dir, write_csv  # noqa: E402
from identity.profile import EnrolledProfile  # noqa: E402
from identity.verify import DEFAULT_TAU  # noqa: E402

INK, G400, G200 = "#0a0a0a", "#a3a3a3", "#e9e9e9"


def load() -> tuple[dict[str, EnrolledProfile], list[Probe]]:
    profiles, probes = {}, []
    for folder in sorted((DATA / "identity").glob("*")):
        if (folder / "profile.npz").is_file():
            profiles[folder.name] = EnrolledProfile.load(folder / "profile.npz")
        for f in sorted(folder.glob("verify_*.npz")):
            z = np.load(f, allow_pickle=False)
            meta = json.loads(str(z["meta"]))
            probes.append(Probe(meta["subject"], z["embeddings"], meta.get("lighting", "normal"), meta.get("glasses", "no"), meta.get("pose", "front"), f.name))
    return profiles, probes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tau", type=float, default=DEFAULT_TAU)
    args = ap.parse_args()

    profiles, probes = load()
    known = [p for p in probes if p.subject in profiles]
    if len(profiles) < 2 or not known:
        print(f"Found {len(profiles)} enrolled volunteer(s) and {len(known)} attempt(s). Impostor trials need at least two volunteers.")
        return 1
    trials = all_trials(profiles, known)
    s = summarize(trials, args.tau)
    out = results_dir("identity")
    write_csv(out / "trials.csv", ["profile", "probe", "genuine", "score", "lighting", "glasses", "pose"], [(t.profile, t.probe, int(t.genuine), round(t.score, 4), t.lighting, t.glasses, t.pose) for t in trials])
    write_csv(out / "summary.csv", ["genuine_trials", "impostor_trials", "tau", "FAR", "FAR_ci_low", "FAR_ci_high", "FRR", "FRR_ci_low", "FRR_ci_high", "EER", "EER_threshold"],
              [(s.genuine, s.impostor, s.tau, s.far, *s.far_ci, s.frr, *s.frr_ci, s.eer, s.eer_threshold)])
    cond = by_condition(trials, args.tau)
    write_csv(out / "by_condition.csv", ["lighting", "glasses", "pose", "rejected", "attempts"], [(*k, *v) for k, v in sorted(cond.items())])

    print(f"{len(profiles)} volunteers, {len(known)} attempts -> {s.genuine} genuine and {s.impostor} impostor trials")
    print(f"at the app's threshold {s.tau}:  FAR {s.far * 100:.1f}% (95% CI {s.far_ci[0] * 100:.1f}-{s.far_ci[1] * 100:.1f}%)   FRR {s.frr * 100:.1f}% (95% CI {s.frr_ci[0] * 100:.1f}-{s.frr_ci[1] * 100:.1f}%)")
    print(f"equal error rate {s.eer * 100:.1f}% at threshold {s.eer_threshold:.2f}")
    print("genuine attempts rejected, by condition:")
    for (light, glasses, pose), (bad, n) in sorted(cond.items()):
        lo, hi = wilson(bad, n)
        print(f"  lighting {light:<7} glasses {glasses:<3} pose {pose:<7} {bad} of {n}   (95% CI {lo * 100:.0f}-{hi * 100:.0f}%)")

    g, i = split(trials)
    fig, ax = plt.subplots(figsize=(7.5, 3.4))
    bins = np.linspace(min(g + i + [0]) - 0.02, 1.0, 40)
    ax.hist(i, bins=bins, color=G400, label=f"impostor ({len(i)})", edgecolor="white")
    ax.hist(g, bins=bins, color=INK, label=f"genuine ({len(g)})", edgecolor="white", alpha=0.9)
    ax.axvline(args.tau, color=INK, linewidth=1, linestyle=(0, (2, 3)))
    ax.text(args.tau + 0.01, ax.get_ylim()[1] * 0.92, f"threshold {args.tau}", fontsize=8)
    ax.set_xlabel("match score (cosine similarity)")
    ax.legend(frameon=False, fontsize=8)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    fig.tight_layout()
    fig.savefig(out / "score_distributions.png", dpi=160)
    plt.close(fig)

    pts = operating_points(trials)
    fig, ax = plt.subplots(figsize=(4.4, 4.2))
    ax.plot([p[1] * 100 for p in pts], [p[2] * 100 for p in pts], color=INK, linewidth=2)
    ax.plot([0, 100], [0, 100], color=G200, linewidth=1)
    ax.scatter([s.far * 100], [s.frr * 100], color=INK, s=40, zorder=3)
    ax.annotate(f"app threshold {args.tau}", (s.far * 100, s.frr * 100), textcoords="offset points", xytext=(8, 8), fontsize=8)
    ax.set_xlabel("false accept rate (%)")
    ax.set_ylabel("false reject rate (%)")
    ax.set_title("Trade-off between the two errors", fontsize=10, loc="left")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    fig.tight_layout()
    fig.savefig(out / "det_curve.png", dpi=160)
    plt.close(fig)
    print(f"results written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
