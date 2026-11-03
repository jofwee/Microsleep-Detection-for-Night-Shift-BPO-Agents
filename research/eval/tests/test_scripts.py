"""The analysis scripts, run the way a person runs them, on synthetic trial files in a temporary folder."""
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

from evalkit.spoof import ATTACKS
from identity.profile import EnrolledProfile

HERE = Path(__file__).resolve().parents[1]
rng = np.random.default_rng(11)


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def run(script, tmp_path, *args):
    env = {**os.environ, "LOCALAPPDATA": str(tmp_path / "appdata"), "EVAL_RESULTS": str(tmp_path / "results")}
    return subprocess.run([sys.executable, str(HERE / script), *args], capture_output=True, text=True, env=env, timeout=120)


def test_the_scripts_say_what_is_missing_instead_of_crashing_when_there_is_no_data(tmp_path):
    for script in ("analyze_identity.py", "analyze_spoof.py", "evaluate_fatigue.py"):
        r = run(script, tmp_path)
        assert r.returncode == 1 and r.stdout.strip(), (script, r.stderr[-300:])
        assert "Traceback" not in r.stderr


def test_identity_analysis_end_to_end(tmp_path):
    base = tmp_path / "appdata" / "thesis-microsleep" / "eval" / "identity"
    faces = {n: unit(rng.normal(size=512)) for n in ("P01", "P02", "P03")}
    for name, face in faces.items():
        folder = base / name
        folder.mkdir(parents=True)
        embs = unit(np.stack([face + 0.01 * rng.normal(size=512) for _ in range(5)]))
        EnrolledProfile(embeddings=embs, mean=unit(embs.mean(axis=0)), model_name="t", model_version="1").save(folder / "profile.npz")
        for i, (light, glasses) in enumerate((("normal", "no"), ("dim", "yes"))):
            probe = unit(np.stack([face + 0.02 * rng.normal(size=512) for _ in range(12)]))
            meta = json.dumps({"subject": name, "lighting": light, "glasses": glasses, "pose": "front"})
            np.savez_compressed(folder / f"verify_{light}_{glasses}_front_{i}.npz", embeddings=probe, meta=meta)
    r = run("analyze_identity.py", tmp_path)
    assert r.returncode == 0, r.stderr[-500:]
    assert "6 genuine and 12 impostor trials" in r.stdout and "FAR 0.0%" in r.stdout and "equal error rate 0.0%" in r.stdout
    for f in ("trials.csv", "summary.csv", "by_condition.csv", "score_distributions.png", "det_curve.png"):
        assert (tmp_path / "results" / "identity" / f).is_file(), f


def test_spoof_analysis_end_to_end(tmp_path):
    folder = tmp_path / "appdata" / "thesis-microsleep" / "eval" / "spoof"
    folder.mkdir(parents=True)
    header = ["time", "subject", "attack", "lighting", "challenge_passed", "passive_median", "passive_min", "passive_all", "match", "frames", "failure"]
    rows = []
    for _ in range(6):  # the real person
        rows.append(["t", "P01", "bonafide", "normal", 1, 0.85, 0.7, "[]", 0.9, 12, ""])
    for kind in ATTACKS:
        for _ in range(5):  # attacks: they match the face but cannot do the live action and look fake
            rows.append(["t", "P01", kind, "normal", 0, 0.12, 0.02, "[]", 0.88, 12, "challenge failed"])
    rows.append(["t", "P01", "phone_video", "normal", 1, 0.7, 0.5, "[]", 0.9, 12, ""])  # one video that got through every rule
    with (folder / "trials.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    r = run("analyze_spoof.py", tmp_path)
    assert r.returncode == 0, r.stderr[-500:]
    assert "bonafide 6" in r.stdout and "phone_video 6" in r.stdout
    assert "match only (no liveness at all)" in r.stdout
    for f in ("attack_rates.csv", "attack_acceptance.png", "passive_scores.png"):
        assert (tmp_path / "results" / "spoof" / f).is_file(), f
    table = list(csv.DictReader((tmp_path / "results" / "spoof" / "attack_rates.csv").open()))
    deployed = {x["presentation"]: x for x in table if x["rule"].startswith("sign-in as deployed")}
    assert deployed["print"]["accepted_or_rejected"] == "0" and deployed["phone_video"]["accepted_or_rejected"] == "1"
    matchonly = {x["presentation"]: x for x in table if x["rule"].startswith("match only")}
    assert matchonly["print"]["accepted_or_rejected"] == "5"  # with no liveness at all, every photo gets in


def test_the_report_shows_what_is_done_and_what_is_still_to_do(tmp_path):
    # only the spoof analysis has data: the others must say so rather than disappear
    folder = tmp_path / "appdata" / "thesis-microsleep" / "eval" / "spoof"
    folder.mkdir(parents=True)
    header = ["time", "subject", "attack", "lighting", "challenge_passed", "passive_median", "passive_min", "passive_all", "match", "frames", "failure"]
    with (folder / "trials.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows([["t", "P1", "bonafide", "normal", 1, 0.8, 0.7, "[]", 0.9, 12, ""], ["t", "P1", "print", "normal", 0, 0.1, 0.0, "[]", 0.9, 12, ""]])
    assert run("analyze_spoof.py", tmp_path).returncode == 0
    r = run("make_report.py", tmp_path)
    assert r.returncode == 0, r.stderr[-400:]
    text = (tmp_path / "results" / "REPORT.md").read_text(encoding="utf-8")
    assert "## 3. Spoofing attacks" in text and "sign-in as deployed" in text and "attack_acceptance.png" in text
    assert "No recordings scored yet" in text and "No identity trials yet" in text and "Run `bench_pipeline.py`" in text
