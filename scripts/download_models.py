"""Download the model files the client needs into client/models/.

    python scripts/download_models.py            # everything below
    python scripts/download_models.py --only face_landmarker.task

SHA-256 values are pinned after the first download (trust on first use). A mismatch
means the file changed upstream or the download was corrupted, and the file is removed.

Licences: the InsightFace ArcFace weights are for NON-COMMERCIAL research only (fine for
the thesis; swap in SFace behind FaceEmbedder for anything commercial). MiniFASNet is
Apache-2.0 (Minivision's Silent-Face-Anti-Spoofing, ONNX export by yakhyo).
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
import zipfile
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parents[1] / "client" / "models"

MODELS: dict[str, dict] = {
    "face_landmarker.task": {
        "url": "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
        "min_bytes": 1_000_000,
        "sha256": "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff",
    },
    # ArcFace recognition model (ResNet-50, 512-d), taken out of InsightFace's buffalo_l pack.
    "w600k_r50.onnx": {
        "url": "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
        "zip_member": "w600k_r50.onnx",
        "min_bytes": 100_000_000,
        "sha256": "4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43",
    },
    # Lighter ArcFace (MobileFaceNet) from buffalo_s, for weak PCs. Only fetched with --with-small.
    "w600k_mbf.onnx": {
        "url": "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_s.zip",
        "zip_member": "w600k_mbf.onnx",
        "min_bytes": 5_000_000,
        "sha256": None,
        "optional": True,
    },
    "MiniFASNetV2.onnx": {
        "url": "https://github.com/yakhyo/face-anti-spoofing/releases/download/weights/MiniFASNetV2.onnx",
        "min_bytes": 1_000_000,
        "sha256": "b32929adc2d9c34b9486f8c4c7bc97c1b69bc0ea9befefc380e4faae4e463907",
    },
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch(url: str, dest: Path) -> None:
    def progress(blocks: int, block: int, total: int) -> None:
        if total > 50_000_000 and blocks % 400 == 0:
            print(f"       {min(blocks * block, total) / 1e6:6.0f} / {total / 1e6:.0f} MB", flush=True)

    urllib.request.urlretrieve(url, dest, progress)


def download(name: str, spec: dict) -> bool:
    dest = MODELS_DIR / name
    if dest.is_file() and dest.stat().st_size >= spec["min_bytes"]:
        print(f"[skip] {name} already present ({dest.stat().st_size:,} bytes)")
        return True
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"[get ] {name} <- {spec['url']}")
    try:
        _fetch(spec["url"], tmp)
        if "zip_member" in spec:
            with zipfile.ZipFile(tmp) as z:
                with z.open(spec["zip_member"]) as src, dest.open("wb") as out:
                    while chunk := src.read(1 << 20):
                        out.write(chunk)
            tmp.unlink()
        else:
            tmp.replace(dest)
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        print(f"[fail] {name}: {exc}", file=sys.stderr)
        tmp.unlink(missing_ok=True)
        dest.unlink(missing_ok=True)
        return False

    if dest.stat().st_size < spec["min_bytes"]:
        print(f"[fail] {name}: only {dest.stat().st_size} bytes, not a model", file=sys.stderr)
        dest.unlink()
        return False
    digest = sha256(dest)
    if spec["sha256"] and digest != spec["sha256"]:
        print(f"[fail] {name}: sha256 {digest} does not match the pinned {spec['sha256']}", file=sys.stderr)
        dest.unlink()
        return False
    pinned = "pinned ok" if spec["sha256"] else "NOT PINNED YET - copy this into MODELS"
    print(f"[ ok ] {name}: {dest.stat().st_size:,} bytes, sha256 {digest} ({pinned})")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="+", choices=sorted(MODELS), help="fetch just these files")
    ap.add_argument("--with-small", action="store_true", help="also fetch the lighter ArcFace for weak PCs")
    args = ap.parse_args()
    names = args.only or [n for n, s in MODELS.items() if args.with_small or not s.get("optional")]
    results = [download(n, MODELS[n]) for n in names]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
