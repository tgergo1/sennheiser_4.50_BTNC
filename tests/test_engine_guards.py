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


def test_no_output_device_means_whatever_the_mac_is_playing_to(monkeypatch):
    monkeypatch.setattr(devices_module, "default_output", lambda: FIXTURE[1])
    import opencaptune.audio.engine as engine_module

    monkeypatch.setattr(engine_module, "default_output", lambda: FIXTURE[1])
    engine = Engine(EngineConfig())
    assert engine.output.name == "Bogcifüles"


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


def test_exposure_is_handed_over_in_seconds_not_samples(monkeypatch):
    """Regression: energy per sample divided by seconds is not a mean square.

    Accumulating mean-square weighted by frames and then dividing by seconds
    inflates every level by 10*log10(sample_rate) — about 46 dB at 44.1 kHz,
    which turned a quiet tone into a reported 125 dB.
    """
    import opencaptune.audio.engine as engine_module

    engine = Engine(EngineConfig(output_device="Bogcifüles"))
    recorded = []
    monkeypatch.setattr(
        engine_module.dose_tracker, "record", lambda e, s: recorded.append((e, s))
    )

    # Exactly one second of audio whose mean square is 0.25.
    engine._dose_energy = 0.25 * engine.sample_rate
    engine._dose_frames = engine.sample_rate
    engine.flush_exposure()

    energy, seconds = recorded[0]
    assert seconds == pytest.approx(1.0)
    assert energy / seconds == pytest.approx(0.25), "must come back as a mean square"


def test_flushing_with_nothing_recorded_writes_nothing(monkeypatch):
    import opencaptune.audio.engine as engine_module

    engine = Engine(EngineConfig(output_device="Bogcifüles"))
    recorded = []
    monkeypatch.setattr(
        engine_module.dose_tracker, "record", lambda e, s: recorded.append((e, s))
    )
    engine.flush_exposure()
    assert recorded == []
