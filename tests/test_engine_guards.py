import pytest

from opencaptune.audio import devices as devices_module
from opencaptune.audio.devices import Device
from opencaptune.audio.engine import Engine, EngineConfig

# A Bluetooth headset appears twice under one name: its hands-free microphone
# and its A2DP output.
FIXTURE = [
    Device(0, "Bogcifüles", 1, 0, 16000.0, "Core Audio"),
    Device(1, "Bogcifüles", 0, 2, 44100.0, "Core Audio"),
    Device(2, "BlackHole 2ch", 2, 2, 48000.0, "Core Audio"),
    Device(3, "MacBook Pro Speakers", 0, 2, 48000.0, "Core Audio"),
]


@pytest.fixture(autouse=True)
def fixed_devices(monkeypatch):
    monkeypatch.setattr(devices_module, "devices", lambda: FIXTURE)


def test_the_output_device_is_resolved_by_name():
    engine = Engine(EngineConfig(output_device="Bogcifüles"))
    assert engine.output.name == "Bogcifüles"
    assert engine.output.output_channels == 2


def test_a_different_output_still_works():
    engine = Engine(EngineConfig(output_device="MacBook Pro Speakers"))
    assert engine.output.name == "MacBook Pro Speakers"


def test_nothing_is_captured_from_an_input_device():
    # There is no input device at all now: the tap is the only source, which
    # is why macOS has no reason to treat any of this as a microphone.
    engine = Engine(EngineConfig(output_device="Bogcifüles"))
    assert not hasattr(engine, "input") or engine.input is None
