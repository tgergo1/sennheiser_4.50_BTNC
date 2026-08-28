import pytest

from opencaptune.audio import devices as devices_module
from opencaptune.audio.devices import Device, resolve

# Bluetooth headsets appear twice: once as an HFP microphone, once as an A2DP
# sink. Both entries carry the same name, so `kind` is what disambiguates them.
FIXTURE = [
    Device(0, "Bogcifüles", 1, 0, 16000.0, "Core Audio"),
    Device(1, "Bogcifüles", 0, 2, 44100.0, "Core Audio"),
    Device(2, "MacBook Pro Microphone", 1, 0, 48000.0, "Core Audio"),
    Device(3, "MacBook Pro Speakers", 0, 2, 48000.0, "Core Audio"),
    Device(4, "BlackHole 2ch", 2, 2, 48000.0, "Core Audio"),
    Device(5, "BlackHole 16ch", 16, 16, 48000.0, "Core Audio"),
]


@pytest.fixture(autouse=True)
def _fixed_devices(monkeypatch):
    monkeypatch.setattr(devices_module, "devices", lambda: FIXTURE)


def test_the_same_name_resolves_differently_for_input_and_output():
    assert resolve("Bogcifüles", "input").index == 0
    assert resolve("Bogcifüles", "output").index == 1


def test_partial_names_match():
    assert resolve("BlackHole 2", "input").index == 4
    assert resolve("Speakers", "output").index == 3


def test_partial_names_that_match_several_devices_are_rejected():
    with pytest.raises(LookupError, match="matches several"):
        resolve("BlackHole", "input")


def test_resolves_by_index_as_int_or_string():
    assert resolve(4, "output").name == "BlackHole 2ch"
    assert resolve("4", "output").name == "BlackHole 2ch"


def test_an_index_of_the_wrong_direction_is_rejected():
    with pytest.raises(LookupError, match="not an output device"):
        resolve(2, "output")


def test_an_unknown_name_lists_what_is_available():
    with pytest.raises(LookupError, match="no input device matches"):
        resolve("Soundflower", "input")


def test_kind_must_be_input_or_output():
    with pytest.raises(ValueError, match="input.*output"):
        resolve("BlackHole", "sideways")
