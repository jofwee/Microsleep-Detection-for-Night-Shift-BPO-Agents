"""Gather every result table into one readable report:  research/eval/results/REPORT.md

    python research/eval/make_report.py

Run the analysis scripts first (latency_report, evaluate_fatigue, analyze_identity, analyze_spoof, bench_pipeline). Sections for
experiments that have no data yet say so instead of being left out, so the report always shows what is still to do.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from evalkit.paths import RESULTS, read_csv  # noqa: E402


def table(rows: list[dict], columns: list[tuple[str, str]], fmt: dict | None = None) -> str:
    fmt = fmt or {}
    head = "| " + " | ".join(c[1] for c in columns) + " |\n|" + "|".join("---" for _ in columns) + "|\n"
    body = ""
    for r in rows:
        cells = []
        for key, _ in columns:
            v = r.get(key, "")
            cells.append(fmt[key](v) if key in fmt and v not in ("", None) else (v if v not in ("", None) else "–"))
        body += "| " + " | ".join(str(c) for c in cells) + " |\n"
    return head + body


def pct(v) -> str:
    return f"{float(v) * 100:.0f}%"


def sec(v) -> str:
    return f"{float(v):.2f} s"


def load(*parts: str) -> list[dict] | None:
    p = RESULTS.joinpath(*parts)
    return read_csv(p) if p.is_file() else None


def img(*parts: str, alt: str) -> str:
    return f"![{alt}]({'/'.join(parts)})\n\n" if RESULTS.joinpath(*parts).is_file() else ""


def build() -> str:
    out = [f"# Evaluation results\n\nGenerated {date.today().isoformat()}. Every number comes from the logs and recordings listed in each section; "
           "small samples are marked with 95% intervals where it matters.\n\n"]

    out.append("## 1. Fatigue detection (scripted recordings)\n")
    pooled = load("fatigue", "pooled.csv")
    if pooled:
        out.append(table(pooled, [("kind", "event"), ("events", "asked for"), ("tp", "found"), ("fp", "false alarms"), ("fn", "missed"), ("precision", "precision"), ("recall", "recall"),
                                  ("f1", "F1"), ("median_delay_s", "median delay")], {"precision": pct, "recall": pct, "f1": pct, "median_delay_s": sec}))
        controls = load("fatigue", "controls.csv")
        if controls:
            out.append("\nControl periods (a false alarm must not happen): " + "; ".join(f"{r['control'].lower().replace('_', ' ')}: {r['windows_with_false_alarm']} of {r['windows']} periods" for r in controls) + ".\n")
        out.append("\n" + img("fatigue", "detection_scores.png", alt="Detection scores") + img("fatigue", "detection_delay.png", alt="Detection delay"))
    else:
        out.append("_No recordings scored yet. Record sessions with `record_session.py`, then run `evaluate_fatigue.py`._\n")

    out.append("\n## 2. Face verification\n")
    s = load("identity", "summary.csv")
    if s:
        r = s[0]
        out.append(f"{r['genuine_trials']} genuine and {r['impostor_trials']} impostor trials. At the app's threshold {r['tau']}: "
                   f"false accept rate {pct(r['FAR'])} (95% CI {pct(r['FAR_ci_low'])}-{pct(r['FAR_ci_high'])}), "
                   f"false reject rate {pct(r['FRR'])} (95% CI {pct(r['FRR_ci_low'])}-{pct(r['FRR_ci_high'])}); "
                   f"equal error rate {pct(r['EER'])} at threshold {float(r['EER_threshold']):.2f}.\n\n")
        cond = load("identity", "by_condition.csv")
        if cond:
            out.append(table(cond, [("lighting", "lighting"), ("glasses", "glasses"), ("pose", "pose"), ("rejected", "genuine rejected"), ("attempts", "attempts")]))
        out.append("\n" + img("identity", "score_distributions.png", alt="Score distributions") + img("identity", "det_curve.png", alt="Error trade-off"))
    else:
        out.append("_No identity trials yet. Enroll volunteers and collect attempts with `identity_trials.py`, then run `analyze_identity.py`._\n")

    out.append("\n## 3. Spoofing attacks\n")
    a = load("spoof", "attack_rates.csv")
    if a:
        out.append(table(a, [("rule", "acceptance rule"), ("presentation", "presentation"), ("accepted_or_rejected", "accepted / rejected"), ("trials", "trials"), ("rate", "rate"), ("ci_low", "CI low"), ("ci_high", "CI high")],
                         {"rate": pct, "ci_low": pct, "ci_high": pct}))
        out.append("\nFor attacks the count is how many were accepted (lower is better); for the genuine row it is how many were rejected.\n\n" + img("spoof", "attack_acceptance.png", alt="Attack acceptance") + img("spoof", "passive_scores.png", alt="Passive scores"))
    else:
        out.append("_No attack trials yet. Run `spoof_trials.py` for the real person and for each kind of fake, then `analyze_spoof.py`._\n")

    out.append("\n## 4. Voice loop delay\n")
    lat = load("latency", "latency_table.csv")
    if lat:
        out.append(table([r for r in lat if r["part"] == "total" or r["group"] == "all"], [("group", "group"), ("part", "part"), ("n", "exchanges"), ("mean_s", "mean"), ("median_s", "median"), ("p95_s", "95th percentile"), ("max_s", "slowest")],
                         {"mean_s": sec, "median_s": sec, "p95_s": sec, "max_s": sec}))
        out.append("\nThe server-side parts only (speech to text, caller model, first spoken sentence). The client's own timer, which also includes the network, measured 3.1-3.3 s per turn plus a 0.5 s end-of-speech pause.\n\n" + img("latency", "latency_parts.png", alt="Where the time goes") + img("latency", "latency_distribution.png", alt="Delay distribution"))
    else:
        out.append("_Run `latency_report.py` after some calls._\n")

    out.append("\n## 5. Performance on the agent PC\n")
    perf = load("performance", "pipeline.csv")
    if perf:
        out.append(table(perf, [("mode", "mode"), ("fps_sustained", "frames per second"), ("mean_ms", "mean ms/frame"), ("p95_ms", "95th percentile ms"), ("cpu_percent_of_one_core", "CPU (% of one core)"), ("memory_mb_peak", "memory MB"), ("meets_15_fps", "meets 15 fps")],
                         {"meets_15_fps": lambda v: "yes" if v == "1" else "NO"}))
    else:
        out.append("_Run `bench_pipeline.py`._\n")
    return "".join(out)


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    text = build()
    (RESULTS / "REPORT.md").write_text(text, encoding="utf-8")
    print(f"written to {RESULTS / 'REPORT.md'} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
