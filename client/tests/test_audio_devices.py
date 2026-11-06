"""The saved microphone choice: kept by name, resolved to a device number at call time."""
from bpo.audio import devices

LIST = [(1, "Headset Microphone (USB)"), (3, "Microphone Array")]


def test_choice_is_saved_by_name_and_read_back(tmp_path):
    p = tmp_path / "audio.json"
    assert devices.saved_name(p) is None
    devices.save_name("Microphone Array", p)
    assert devices.saved_name(p) == "Microphone Array"
    devices.save_name(None, p)
    assert devices.saved_name(p) is None


def test_a_broken_settings_file_means_the_default(tmp_path):
    p = tmp_path / "audio.json"
    p.write_text("{not json", encoding="utf-8")
    assert devices.saved_name(p) is None


def test_name_becomes_a_number_even_if_numbers_moved():
    assert devices.device_number("microphone array", LIST) == 3
    assert devices.device_number("Microphone Array", [(7, "Microphone Array")]) == 7


def test_unplugged_or_unset_microphone_falls_back_to_the_default():
    assert devices.device_number("Unplugged headset", LIST) is None
    assert devices.device_number(None, LIST) is None
    assert devices.device_number("", LIST) is None
