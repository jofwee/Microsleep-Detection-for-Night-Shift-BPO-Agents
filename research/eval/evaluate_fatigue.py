"""Score recorded fatigue-test sessions: how well does the system find what the schedule asked for?

    python research/eval/evaluate_fatigue.py                          (every session under the eval folder)
    python research/eval/evaluate_fatigue.py PATH [PATH ...]          (chosen session folders)
    python research/eval/evaluate_fatigue.py --sweep microsleep_ms=300,400,500,600,800,1000

Writes tables and charts to research/eval/results/fatigue/. Sessions that were stopped early or have no consent mark
are skipped. Detections during the calibration phases are not counted.
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

from evalkit.fatigue import evaluate_recording, pool, pool_controls, sweep  # noqa: E402
from evalkit.metrics import describe, wilson  # noqa: E402
from evalkit.paths import DATA, read_csv, results_dir, write_csv  # noqa: E402
from evalkit.protocol import POSITIVE, Cue  # noqa: E402

INK, G400, G200 = "#0a0a0a", "#a3a3a3", "#e9e9e9"


def load_cues(folder: Path) -> list[Cue]:
    return [Cue(r["kind"], float(r["start_s"]), float(r["window_s"]), r["text"]) for r in read_csv(folder / "schedule.csv")]


def find_sessions(paths: list[str]) -> list[Path]:
    roots = [Path(p) for p in paths] or sorted((DATA / "fatigue").glob("*"))
    return [r for r in roots if (r / "session.json").is_file() and (r / "video.mp4").is_file()]


def pct(x) -> str:
    return "–" if x is None else f"{x * 100:.0f}%"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sessions", nargs="*")
    ap.add_argument("--sweep", default=None, help="a threshold and the values to try, e.g. microsleep_ms=300,500,800")
    args = ap.parse_args()

    out = results_dir("fatigue")
    results, kept = [], []
    for folder in find_sessions(args.sessions):
        meta = json.loads((folder / "session.json").read_text(encoding="utf-8"))
        if meta.get("aborted") or not meta.get("consent"):
            print(f"skipping {folder.name}: {'stopped early' if meta.get('aborted') else 'no consent mark'}")
            continue
        print(f"scoring {folder.name} …", flush=True)
        result, samples = evaluate_recording(folder.name, folder / "video.mp4", load_cues(folder))
        results.append(result)
        kept.append((folder.name, load_cues(folder), samples, result.calibration, meta))
        for note in result.notes:
            print("   note:", note)
    if not results:
        print(f"No usable sessions found under {DATA / 'fatigue'}. Record one with record_session.py first.")
        return 1

    # --- per session and pooled tables -----------------------------------------------------------------
    rows = []
    for r in results:
        for kind in POSITIVE:
            s = r.scores[kind]
            rows.append((r.name, kind, s.tp, s.fp, s.fn, s.precision, s.recall, s.f1, describe(s.delays_s).get("median")))
    write_csv(out / "per_session.csv", ["session", "kind", "tp", "fp", "fn", "precision", "recall", "f1", "median_delay_s"], rows)

    pooled = pool(results)
    table = []
    for kind in POSITIVE:
        s = pooled[kind]
        lo, hi = wilson(s.tp, s.tp + s.fn)
        d = describe(s.delays_s)
        table.append((kind, s.tp + s.fn, s.tp, s.fp, s.fn, s.precision, s.recall, lo, hi, s.f1, d.get("median"), d.get("p95")))
    write_csv(out / "pooled.csv", ["kind", "events", "tp", "fp", "fn", "precision", "recall", "recall_ci_low", "recall_ci_high", "f1", "median_delay_s", "p95_delay_s"], table)
    controls = pool_controls(results)
    write_csv(out / "controls.csv", ["control", "windows_with_false_alarm", "windows"], [(k, v[0], v[1]) for k, v in controls.items()])

    print(f"\n{len(results)} session(s): {', '.join(m['subject'] for *_, m in kept)}\n")
    print(f"{'event':<12}{'asked':>7}{'found':>7}{'false':>7}{'precision':>11}{'recall':>9}{'F1':>7}{'median delay':>14}")
    for row in table:
        kind, asked, tp, fp, _fn, prec, rec, _lo, _hi, f1, med, _p95 = row
        print(f"{kind:<12}{asked:>7}{tp:>7}{fp:>7}{pct(prec):>11}{pct(rec):>9}{pct(f1):>7}{('–' if med is None else f'{med:.1f} s'):>14}")
    print("\ncontrols (a false alarm must NOT happen):")
    for k, (bad, n) in controls.items():
        print(f"  {k:<10} {bad} of {n} periods had a false alarm")

    # --- charts ---------------------------------------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(7.5, 3.6))
    x = range(len(POSITIVE))
    w = 0.26
    for i, (name, idx, colour) in enumerate((("precision", 5, G400), ("recall", 6, INK), ("F1", 9, "#525252"))):
        vals = [(row[idx] or 0) for row in table]
        ax.bar([j + (i - 1) * w for j in x], vals, width=w * 0.9, color=colour, edgecolor="white", label=name)
    ax.set_xticks(list(x), [k.lower().replace("_", " ") for k in POSITIVE])
    ax.set_ylim(0, 1.05)
    ax.set_title("Detection of the scripted events", fontsize=10, loc="left")
    ax.legend(frameon=False, fontsize=8, ncol=3, loc="lower right")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(axis="y", color=G200)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(out / "detection_scores.png", dpi=160)
    plt.close(fig)

    delays = [(k.lower().replace("_", " "), pooled[k].delays_s) for k in POSITIVE if pooled[k].delays_s]
    if delays:
        fig, ax = plt.subplots(figsize=(7.5, 3.2))
        ax.boxplot([d for _, d in delays], tick_labels=[n for n, _ in delays], vert=False, medianprops={"color": INK}, boxprops={"color": INK}, whiskerprops={"color": INK}, capprops={"color": INK})
        ax.set_xlabel("seconds from the start of the behaviour to the report (includes the person's reaction)")
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        fig.tight_layout()
        fig.savefig(out / "detection_delay.png", dpi=160)
        plt.close(fig)

    # --- threshold sweep ------------------------------------------------------------------------------------
    if args.sweep:
        name, _, vals = args.sweep.partition("=")
        values = [float(v) for v in vals.split(",")]
        data = sweep([(n, cues, smp, cal) for n, cues, smp, cal, _ in kept], name, values)
        write_csv(out / f"sweep_{name}.csv", list(data[0]), [list(r.values()) for r in data])
        print(f"\nsweep of {name}:")
        for kind in POSITIVE:
            line = [r for r in data if r["kind"] == kind]
            if any(r["tp"] + r["fn"] for r in line):
                print(f"  {kind:<11}" + "  ".join(f"{r['value']:g}: P {pct(r['precision'])} R {pct(r['recall'])}" for r in line))
    print(f"\nresults written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
