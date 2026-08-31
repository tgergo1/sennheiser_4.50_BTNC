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


def test_capturing_from_the_headsets_own_microphone_is_refused():
    # Opening it drags the headset out of A2DP into hands-free mode: mono,
    # narrow band, microphone live. Refuse rather than do that silently.
    with pytest.raises(ValueError, match="own microphone"):
        Engine(EngineConfig(capture="device", input_device="Bogcifüles",
                            output_device="Bogcifüles"))


def test_the_normal_loopback_routing_is_accepted():
    engine = Engine(EngineConfig(capture="device", input_device="BlackHole 2ch",
                                 output_device="Bogcifüles"))
    assert engine.input.name == "BlackHole 2ch"
    assert engine.output.name == "Bogcifüles"


def test_a_different_output_still_works():
    engine = Engine(EngineConfig(capture="device", input_device="BlackHole 2ch",
                                 output_device="MacBook Pro Speakers"))
    assert engine.output.name == "MacBook Pro Speakers"


def test_device_capture_needs_an_input_device():
    with pytest.raises(ValueError, match="needs an input device"):
        Engine(EngineConfig(capture="device", input_device=None,
                            output_device="Bogcifüles"))


def test_tap_capture_needs_no_input_device_at_all():
    engine = Engine(EngineConfig(capture="tap", output_device="Bogcifüles"))
    assert engine.capture_mode == "tap"
    assert engine.input is None
