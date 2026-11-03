"""The demo tool must import and parse arguments, and its drawing must not crash (headless)."""
import numpy as np

from test_enroll import make_face
from tools.identity_demo import DEFAULT_PROFILE, banner, main
from tools.overlay import draw
from vision.calibration import Calibration
from vision.drowsiness import DrowsinessEngine
from vision.pipeline import Measurement


def test_draw_shows_the_identity_lock_status_for_every_state():
    for status in ("VERIFIED", "SEARCHING", "ABSENT", "IDENTITY_MISMATCH", "SPOOF_SUSPECTED"):
        frame = np.zeros((480, 640, 3), np.uint8)
        face = make_face()
        m = Measurement(0, 1, face, face.pixels(640, 480), 0.3, 0.3, 0.03, 0.0, 0.0, 0.0, 0.1, False, status, 0.62)
        draw(frame, m, DrowsinessEngine(Calibration()), [], 30.0)
        assert frame.any()


def test_banner_draws_text():
    frame = np.zeros((100, 400, 3), np.uint8)
    banner(frame, "Look straight at the camera")
    assert frame.any()


def test_login_and_watch_refuse_to_run_without_a_profile(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["identity_demo", "login", "--profile", str(tmp_path / "missing.npz")])
    assert main() == 1
    assert "enroll" in capsys.readouterr().out
    assert DEFAULT_PROFILE.name == "profile.local.npz"
