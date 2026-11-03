"""The overlay's drawing code must not crash on any state (it runs headless here)."""
import numpy as np
import pytest

from test_pipeline import FRAME, make_face
from tools.overlay import describe, draw
from vision.calibration import Calibration
from vision.drowsiness import DrowsinessEngine, Event, EventType, FatigueState, Sample
from vision.pipeline import Measurement


def test_draw_with_a_face_changes_the_frame():
    face = make_face()
    px = face.pixels(640, 480)
    m = Measurement(0, 1, face, px, 0.4, 0.4, 0.03, -2.0, 1.0, 0.5, 0.1, False)
    frame = FRAME.copy()
    draw(frame, m, DrowsinessEngine(Calibration()), ["12:00:00  YAWN 2100 ms"], fps=29.7, banner="Close your eyes 3s")
    assert frame.any()


def test_draw_without_a_face_and_with_missing_values():
    frame = FRAME.copy()
    draw(frame, Measurement(0, 0), DrowsinessEngine(Calibration()), [], fps=0.0)
    assert frame.any()  # the text panel is still drawn


def test_draw_microsleep_state_adds_the_red_border():
    engine = DrowsinessEngine(Calibration())
    for t in range(0, 1600, 33):
        engine.update(Sample(t, ear=0.30 if t < 1000 else 0.10))
    assert engine.state is FatigueState.MICROSLEEP
    frame = FRAME.copy()
    draw(frame, Measurement(1600, 1), engine, [], fps=30.0)
    assert tuple(frame[2, 300]) == (40, 40, 230)  # border pixel, BGR


def test_describe_event():
    assert "MICROSLEEP 650 ms" in describe(Event(EventType.MICROSLEEP, 0, 650))
