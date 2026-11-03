"""Word/character error rates used by Spike S1 (simulation/spikes/_common.py)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "simulation" / "spikes"))
from _common import cer, edit_distance, normalise, wer  # noqa: E402


def test_normalise_ignores_case_punctuation_hyphens_and_accents():
    assert normalise("Okay po, i-check ko lang!") == "okay po i check ko lang"
    assert normalise("  Café   Señor ") == "cafe senor"


def test_edit_distance():
    assert edit_distance(list("kitten"), list("sitting")) == 3
    assert edit_distance([], list("abc")) == 3
    assert edit_distance(list("abc"), list("abc")) == 0


def test_wer_counts_substitutions_deletions_and_insertions():
    assert wer("the cat sat", "the cat sat") == 0
    assert wer("the cat sat", "the dog sat") == pytest.approx(1 / 3)
    assert wer("the cat sat", "the sat") == pytest.approx(1 / 3)
    assert wer("the cat sat", "the big cat sat") == pytest.approx(1 / 3)
    assert wer("one two", "three four five six") == pytest.approx(2.0)  # can exceed 1


def test_spelling_variants_are_cheap_in_cer_but_costly_in_wer():
    ref, hyp = "chine-check ko yung billing", "chi ni check ko yung billing"  # one word heard as two
    assert wer(ref, hyp) > 0.3
    assert cer(ref, hyp) < 0.10


def test_empty_cases():
    assert wer("", "") == 0 and wer("", "x") == 1
    assert cer("", "") == 0 and cer("", "x") == 1
    assert wer("hello there", "") == 1
