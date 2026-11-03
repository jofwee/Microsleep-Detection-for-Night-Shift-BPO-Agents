"""MediaPipe Face Landmarker (Tasks API) wrapper.

Runs in VIDEO mode, so timestamps passed to detect() must increase from frame to frame.
num_faces defaults to 3 so bystanders behind the agent are seen (and later ignored by
the identity lock) instead of being mistaken for the agent.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions, vision

DEFAULT_MODEL = Path(__file__).resolve().parents[1] / "models" / "face_landmarker.task"


@dataclass
class FaceResult:
    landmarks: np.ndarray  # (478, 3), x and y normalised to [0, 1]
    blendshapes: dict[str, float]
    matrix: np.ndarray | None  # 4x4 face-to-camera transform

    def pixels(self, width: int, height: int) -> np.ndarray:
        """(478, 2) landmark positions in pixels."""
        return self.landmarks[:, :2] * np.array([width, height], dtype=float)

    def box(self) -> tuple[float, float, float, float]:
        """Normalised bounding box (x1, y1, x2, y2)."""
        xs, ys = self.landmarks[:, 0], self.landmarks[:, 1]
        return float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())

    def area(self) -> float:
        """Normalised bounding-box area, used to pick the main face."""
        xs, ys = self.landmarks[:, 0], self.landmarks[:, 1]
        return float((xs.max() - xs.min()) * (ys.max() - ys.min()))


def primary_face(faces: list[FaceResult]) -> FaceResult | None:
    """The largest face. Until the identity lock exists, this stands in for "the agent"."""
    return max(faces, key=lambda f: f.area(), default=None)


class FaceLandmarker:
    def __init__(self, model_path: str | Path = DEFAULT_MODEL, num_faces: int = 3) -> None:
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(
                f"{path} not found. Run: python scripts/download_models.py"
            )
        options = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(path)),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=num_faces,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)
        self._last_ts = -1

    def detect(self, frame_bgr: np.ndarray, ts_ms: int) -> list[FaceResult]:
        ts_ms = max(int(ts_ms), self._last_ts + 1)  # VIDEO mode needs strictly increasing timestamps
        self._last_ts = ts_ms
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        result = self._landmarker.detect_for_video(image, ts_ms)

        faces: list[FaceResult] = []
        for i, lms in enumerate(result.face_landmarks):
            arr = np.array([(p.x, p.y, p.z) for p in lms], dtype=float)
            shapes = {}
            if result.face_blendshapes and i < len(result.face_blendshapes):
                shapes = {c.category_name: float(c.score) for c in result.face_blendshapes[i]}
            matrix = None
            if result.facial_transformation_matrixes and i < len(result.facial_transformation_matrixes):
                matrix = np.asarray(result.facial_transformation_matrixes[i], dtype=float)
            faces.append(FaceResult(arr, shapes, matrix))
        return faces

    def close(self) -> None:
        self._landmarker.close()

    def __enter__(self) -> "FaceLandmarker":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
