import numpy as np
import pytest

from opencaptune import eq
from opencaptune.audio import dsp

RATE = 48000


def tone(frequency, frames=RATE, channels=2):
    t = np.arange(frames) / RATE
    wave = np.sin(2 * np.pi * frequency * t)
    return np.repeat(wave[:, None], channels, axis=1)


def level_db(signal):
    # Measured over the tail, so the filter's start-up transient is excluded.
    return 20 * np.log10(np.sqrt(np.mean(signal[RATE // 2 :] ** 2)) + 1e-30)


def test_neutral_only_applies_the_preamp_and_stays_bit_exact():
    equaliser = dsp.Equaliser(eq.preset("Neutral"), RATE)
    block = tone(1000).astype(np.float32)
    assert equaliser.preamp_db == 0.0
    assert np.allclose(equaliser.process(block), block)


def test_headroom_accounts_for_overlap_not_just_the_largest_band():
    # Adjacent peaking sections sum, so the cascade peaks above any single band.
    rock = eq.preset("Rock")
    assert -eq.preamp_db(rock) > max(rock.gains_db)
    assert eq.preamp_db(eq.preset("Neutral")) == 0.0
    only_cuts = eq.Preset("Cuts", tuple([-3.0] * len(eq.bands())))
    assert eq.preamp_db(only_cuts) == 0.0


def test_a_boosted_preset_cannot_clip_a_full_scale_signal():
    preset = eq.preset("Loudness")
    equaliser = dsp.Equaliser(preset, RATE)
    boosted_band = eq.bands()[preset.gains_db.index(max(preset.gains_db))]
    output = equaliser.process(tone(boosted_band).astype(np.float32))
    assert np.max(np.abs(output[RATE // 2 :])) <= 1.0


def test_gain_at_a_band_matches_the_preset_relative_to_the_preamp():
    preset = eq.preset("Rock")
    equaliser = dsp.Equaliser(preset, RATE)
    for index, frequency in enumerate(eq.bands()):
        if frequency >= RATE / 2:
            continue
        reference = tone(frequency).astype(np.float32)
        measured = level_db(equaliser.process(reference)) - level_db(reference)
        expected = preset.gains_db[index] + equaliser.preamp_db
        assert measured == pytest.approx(expected, abs=2.0), frequency
        equaliser.reset()


def test_processing_in_blocks_matches_processing_in_one_go():
    preset = eq.preset("Voice")
    whole = dsp.Equaliser(preset, RATE).process(tone(440).astype(np.float32))

    blocked = dsp.Equaliser(preset, RATE)
    pieces = [blocked.process(chunk) for chunk in np.array_split(tone(440).astype(np.float32), 17)]
    assert np.allclose(whole, np.concatenate(pieces), atol=1e-6)


def test_reset_clears_filter_memory():
    equaliser = dsp.Equaliser(eq.preset("Rock"), RATE)
    equaliser.process(tone(100).astype(np.float32))
    equaliser.reset()
    after = equaliser.process(np.zeros((256, 2), dtype=np.float32))
    assert np.all(after == 0.0)


def test_changing_preset_keeps_state_when_the_section_count_matches():
    bands = len(eq.bands())
    equaliser = dsp.Equaliser(eq.Preset("A", tuple([2.0] * bands)), RATE)
    equaliser.process(tone(100, frames=1024).astype(np.float32))
    before = equaliser._state.copy()
    equaliser.set_preset(eq.Preset("B", tuple([-2.0] * bands)))
    assert equaliser._state.shape == before.shape
    assert np.array_equal(equaliser._state, before)


def test_changing_to_a_different_shape_resets_state_rather_than_crashing():
    equaliser = dsp.Equaliser(eq.preset("Rock"), RATE)
    equaliser.process(tone(100, frames=1024).astype(np.float32))
    equaliser.set_preset(eq.preset("Neutral"))
    assert equaliser._state.shape[0] == 0
    assert equaliser.process(tone(100, frames=64).astype(np.float32)).shape == (64, 2)


def test_output_is_float32_and_shaped_like_the_input():
    equaliser = dsp.Equaliser(eq.preset("Jazz"), RATE)
    output = equaliser.process(tone(440, frames=512).astype(np.float32))
    assert output.dtype == np.float32
    assert output.shape == (512, 2)


def test_rejects_a_block_with_the_wrong_channel_count():
    equaliser = dsp.Equaliser(eq.preset("Jazz"), RATE, channels=2)
    with pytest.raises(ValueError, match="expected a block of shape"):
        equaliser.process(np.zeros((256, 1), dtype=np.float32))


def test_supports_mono():
    equaliser = dsp.Equaliser(eq.preset("Pop"), RATE, channels=1)
    assert equaliser.process(np.zeros((256, 1), dtype=np.float32)).shape == (256, 1)
