"""Face embeddings behind a small interface.

ArcFaceOnnx runs InsightFace's w600k_r50 recognition model through onnxruntime and
aligns faces from MediaPipe's landmarks, so the `insightface` package (which needs a C++
compiler on Windows) is not required. Anything that implements FaceEmbedder can replace it,
for example OpenCV's SFace for a commercial build.

Embeddings are L2-normalised, so cosine similarity is a plain dot product.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
import onnxruntime as ort

from vision.landmarks import FaceResult

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"

# ArcFace's 112x112 reference positions: image-left eye, image-right eye, nose tip,
# image-left mouth corner, image-right mouth corner.
ARCFACE_TEMPLATE = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float64,
)
ALIGNED_SIZE = 112


class FaceEmbedder(Protocol):
    name: str
    version: str

    def embed(self, frame_bgr: np.ndarray, face: FaceResult) -> np.ndarray:
        """L2-normalised embedding of the face."""
        ...


def five_points(px: np.ndarray) -> np.ndarray:
    """The five ArcFace anchor points from (478, 2) pixel landmarks.

    Eye centres are the midpoints of the eye corners, which do not move with gaze.
    "Left" means image-left (the subject's right side) in a non-mirrored frame.
    """
    return np.array(
        [
            (px[33] + px[133]) / 2,  # subject's right eye -> image-left
            (px[362] + px[263]) / 2,  # subject's left eye  -> image-right
            px[1],  # nose tip
            px[61],  # subject's right mouth corner -> image-left
            px[291],  # subject's left mouth corner  -> image-right
        ],
        dtype=np.float64,
    )


def similarity_transform(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Least-squares 2x3 similarity transform (rotation, uniform scale, shift): Umeyama 1991."""
    n = src.shape[0]
    mu_s, mu_d = src.mean(axis=0), dst.mean(axis=0)
    s, d = src - mu_s, dst - mu_d
    cov = d.T @ s / n
    u, sv, vt = np.linalg.svd(cov)
    sign = np.ones(2)
    if np.linalg.det(cov) < 0:
        sign[-1] = -1
    rot = u @ np.diag(sign) @ vt
    scale = (sv * sign).sum() / s.var(axis=0).sum()
    m = np.zeros((2, 3))
    m[:, :2] = scale * rot
    m[:, 2] = mu_d - scale * rot @ mu_s
    return m


def align_face(frame_bgr: np.ndarray, face: FaceResult) -> np.ndarray:
    """112x112 BGR crop with the eyes, nose and mouth at ArcFace's reference positions."""
    h, w = frame_bgr.shape[:2]
    m = similarity_transform(five_points(face.pixels(w, h)), ARCFACE_TEMPLATE)
    return cv2.warpAffine(frame_bgr, m, (ALIGNED_SIZE, ALIGNED_SIZE), flags=cv2.INTER_LINEAR)


class ArcFaceOnnx:
    def __init__(self, model_path: str | Path = MODELS_DIR / "w600k_r50.onnx", name: str = "arcface_w600k_r50") -> None:
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(f"{path} not found. Run: python scripts/download_models.py")
        self.name = name
        self.version = "insightface-buffalo_l-v0.7"
        self._session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        self._input = self._session.get_inputs()[0].name

    def embed_aligned(self, aligned_bgr: np.ndarray) -> np.ndarray:
        rgb = cv2.cvtColor(aligned_bgr, cv2.COLOR_BGR2RGB).astype(np.float32)
        blob = ((rgb - 127.5) / 127.5).transpose(2, 0, 1)[None]
        vec = self._session.run(None, {self._input: blob})[0][0].astype(np.float64)
        return vec / max(np.linalg.norm(vec), 1e-12)

    def embed(self, frame_bgr: np.ndarray, face: FaceResult) -> np.ndarray:
        return self.embed_aligned(align_face(frame_bgr, face))
