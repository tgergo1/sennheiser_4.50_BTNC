import numpy as np
import pytest

from opencaptune.audio.spatial import (
    DEFAULT_ANGLE,
    MAX_FEED,
    Virtualiser,
    interaural_delay,
)

RATE = 48000


def stereo(left, right):
    return np.stack([left, right], axis=1).astype(np.float32)


def tone(frequency, frames=RATE):
    return np.sin(2 * np.pi * frequency * np.arange(frames) / RATE)


def rms(signal):
    return float(np.sqrt(np.mean(signal[RATE // 4 :] ** 2)))


def test_the_interaural_delay_matches_the_textbook_figure():
    # About 0.26 ms for a source 30 degrees off centre.
    assert interaural_delay(DEFAULT_ANGLE) == pytest.approx(0.00026, abs=0.00002)
    assert interaural_delay(0) == 0.0
    # Further round the head means a longer wait, up to abeam.
    assert interaural_delay(90) > interaural_delay(30) > interaural_delay(10)


def test_the_delay_in_samples_follows_the_sample_rate():
    seconds = interaural_delay(DEFAULT_ANGLE)
    for rate in (44100, 48000, 96000):
        delay = Virtualiser(50, sample_rate=rate).delay_samples
        assert delay == pytest.approx(seconds * rate, abs=1)
    # The same delay in time is more samples at a higher rate.
    assert (Virtualiser(50, sample_rate=48000).delay_samples
            > Virtualiser(50, sample_rate=44100).delay_samples)


def test_strength_zero_is_an_identity():
    virtualiser = Virtualiser(strength=0, sample_rate=RATE)
    block = stereo(tone(440), -tone(440))
    assert virtualiser.process(block) is block


def test_centred_content_keeps_its_level():
    # Without the normalisation both ears would simply get louder as strength
    # rises, which reads as "more spatial" while only being "more".
    virtualiser = Virtualiser(strength=100, sample_rate=RATE)
    steady = np.ones((2048, 2), dtype=np.float32)
    out = virtualiser.process(steady)
    assert out[-1, 0] == pytest.approx(1.0, abs=0.02)
    assert out[-1, 1] == pytest.approx(1.0, abs=0.02)


def test_each_ear_receives_the_other_channel():
    virtualiser = Virtualiser(strength=100, sample_rate=RATE)
    # Only the left channel carries anything.
    block = stereo(tone(300), np.zeros(RATE))
    out = virtualiser.process(block)
    assert rms(out[:, 1]) > 0.01, "the right ear should hear the left speaker"
    assert rms(out[:, 0]) > rms(out[:, 1]), "but less than the near ear does"


def test_the_crossed_path_is_dulled_more_than_it_is_darkened():
    # The head shadows high frequencies far more than low ones.
    low = Virtualiser(strength=100, sample_rate=RATE)
    high = Virtualiser(strength=100, sample_rate=RATE)
    crossed_low = rms(low.process(stereo(tone(200), np.zeros(RATE)))[:, 1])
    crossed_high = rms(high.process(stereo(tone(8000), np.zeros(RATE)))[:, 1])
    assert crossed_low > crossed_high * 1.5


def test_the_crossed_path_arrives_late():
    virtualiser = Virtualiser(strength=100, sample_rate=RATE)
    impulse = np.zeros((64, 2), dtype=np.float32)
    impulse[0, 0] = 1.0
    out = virtualiser.process(impulse)
    assert out[0, 0] != 0.0, "the near ear hears it immediately"
    assert np.all(out[: virtualiser.delay_samples, 1] == 0.0), "the far ear waits"
    assert np.any(out[virtualiser.delay_samples :, 1] != 0.0)


def test_strength_scales_the_effect():
    crossed = []
    for strength in (25, 60, 100):
        virtualiser = Virtualiser(strength=strength, sample_rate=RATE)
        out = virtualiser.process(stereo(tone(300), np.zeros(RATE)))
        crossed.append(rms(out[:, 1]))
    assert crossed[0] < crossed[1] < crossed[2]
    assert Virtualiser(100, sample_rate=RATE).feed == MAX_FEED


def test_processing_in_blocks_matches_processing_in_one_go():
    block = stereo(tone(440), -tone(440))
    whole = Virtualiser(70, sample_rate=RATE).process(block)
    chunked = Virtualiser(70, sample_rate=RATE)
    pieces = [chunked.process(part) for part in np.array_split(block, 11)]
    assert np.allclose(whole, np.concatenate(pieces), atol=1e-6)


def test_mono_streams_are_left_alone():
    virtualiser = Virtualiser(100, sample_rate=RATE, channels=1)
    assert not virtualiser.active
    block = np.ones((128, 1), dtype=np.float32)
    assert virtualiser.process(block) is block


def test_out_of_range_strength_is_refused():
    with pytest.raises(ValueError, match="0-100"):
        Virtualiser(strength=101, sample_rate=RATE)
    with pytest.raises(ValueError, match="0-100"):
        Virtualiser(sample_rate=RATE).set_strength(-5)
