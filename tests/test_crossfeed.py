import numpy as np
import pytest

from opencaptune.audio.crossfeed import MAX_DEPTH_DB, Crossfeed

RATE = 48000


def stereo(left, right):
    return np.stack([left, right], axis=1).astype(np.float32)


def tone(frequency, frames=RATE):
    return np.sin(2 * np.pi * frequency * np.arange(frames) / RATE)


def rms(signal):
    return float(np.sqrt(np.mean(signal[RATE // 4 :] ** 2)))


def test_mono_passes_through_untouched():
    # The whole point of working on mid/side: nothing happens to mono, so
    # crossfeed cannot colour it or build up bass.
    crossfeed = Crossfeed(strength=100, sample_rate=RATE)
    block = stereo(tone(80), tone(80))
    assert np.allclose(crossfeed.process(block), block, atol=1e-6)


def test_strength_zero_is_an_identity():
    crossfeed = Crossfeed(strength=0, sample_rate=RATE)
    block = stereo(tone(200), -tone(200))
    assert crossfeed.process(block) is block


def test_low_frequency_side_content_is_narrowed():
    crossfeed = Crossfeed(strength=100, sample_rate=RATE)
    # Out of phase is pure side signal.
    block = stereo(tone(80), -tone(80))
    out = crossfeed.process(block)
    before = rms(block[:, 0] - block[:, 1])
    after = rms(out[:, 0] - out[:, 1])
    assert 20 * np.log10(after / before) == pytest.approx(-MAX_DEPTH_DB, abs=0.5)


def test_high_frequency_stereo_image_is_left_alone():
    crossfeed = Crossfeed(strength=100, sample_rate=RATE)
    block = stereo(tone(8000), -tone(8000))
    out = crossfeed.process(block)
    before = rms(block[:, 0] - block[:, 1])
    after = rms(out[:, 0] - out[:, 1])
    assert 20 * np.log10(after / before) == pytest.approx(0.0, abs=0.5)


def test_strength_scales_the_effect():
    depths = []
    for strength in (25, 50, 100):
        crossfeed = Crossfeed(strength=strength, sample_rate=RATE)
        out = crossfeed.process(stereo(tone(80), -tone(80)))
        depths.append(rms(out[:, 0] - out[:, 1]))
    assert depths[0] > depths[1] > depths[2]


def test_processing_in_blocks_matches_processing_in_one_go():
    block = stereo(tone(300), -tone(300))
    whole = Crossfeed(strength=70, sample_rate=RATE).process(block)
    chunked = Crossfeed(strength=70, sample_rate=RATE)
    pieces = [chunked.process(part) for part in np.array_split(block, 13)]
    assert np.allclose(whole, np.concatenate(pieces), atol=1e-6)


def test_mono_streams_are_left_alone_entirely():
    crossfeed = Crossfeed(strength=100, sample_rate=RATE, channels=1)
    assert not crossfeed.active
    block = np.ones((256, 1), dtype=np.float32)
    assert crossfeed.process(block) is block


def test_rejects_an_out_of_range_strength_or_corner():
    with pytest.raises(ValueError, match="0-100"):
        Crossfeed(strength=101, sample_rate=RATE)
    with pytest.raises(ValueError, match="Nyquist"):
        Crossfeed(strength=50, sample_rate=RATE, corner_hz=30000)
    with pytest.raises(ValueError, match="0-100"):
        Crossfeed(sample_rate=RATE).set_strength(-1)


def test_output_shape_and_dtype_are_preserved():
    crossfeed = Crossfeed(strength=50, sample_rate=RATE)
    out = crossfeed.process(stereo(tone(440, 512), -tone(440, 512)))
    assert out.shape == (512, 2)
    assert out.dtype == np.float32
