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


SWITCH_AT = 6 * 512


def _switch_output(fade, blocks=12, block=512, at=SWITCH_AT // 512):
    """Change preset partway through a steady signal and return the output.

    The signal is DC on purpose. A tone's own slew between samples is far
    larger than the step a preset change causes, so it would hide exactly what
    this is measuring; with DC the filters settle flat and any step is the
    switch.
    """
    equaliser = dsp.Equaliser(eq.preset("Loudness"), RATE, fade_ms=20.0)
    signal = np.ones((blocks * block, 2), dtype=np.float32)
    pieces = []
    for index in range(blocks):
        if index == at:
            equaliser.set_preset(eq.preset("Neutral"), fade=fade)
        pieces.append(equaliser.process(signal[index * block : (index + 1) * block]))
    return np.concatenate(pieces), equaliser


def _largest_step(signal, start=SWITCH_AT - 2, length=2048):
    """Largest sample-to-sample jump in a window.

    Defaults to the window around the preset change. The filter's own start-up
    transient at sample zero is larger than the switch step and would mask it,
    so it is deliberately outside the window.
    """
    window = np.abs(np.diff(signal, axis=0))[start : start + length]
    return float(window.max())


def test_crossfading_removes_the_step_a_preset_change_would_otherwise_cause():
    faded, _ = _switch_output(fade=True)
    abrupt, _ = _switch_output(fade=False)
    # Loudness carries a -6.4 dB preamp and Neutral none, so swapping them
    # abruptly jumps the level by about half of full scale in one sample.
    assert _largest_step(abrupt) > 0.4
    assert _largest_step(faded) < _largest_step(abrupt) / 100


def test_a_crossfade_stays_between_the_two_curves_and_never_overshoots():
    faded, _ = _switch_output(fade=True)
    abrupt, _ = _switch_output(fade=False)
    # A linear mix of the two filter outputs cannot leave their range; an
    # equal-power mix of these correlated signals would exceed it.
    assert np.abs(faded).max() <= max(np.abs(abrupt).max(), 1.0) + 1e-6


def test_the_crossfade_finishes_and_leaves_the_new_curve_running():
    _, equaliser = _switch_output(fade=True)
    assert not equaliser.fading
    assert equaliser.preset.name == "Neutral"
    # Neutral is a pass-through, so once the fade is done the output is the input.
    block = tone(1000, frames=512).astype(np.float32)
    assert np.allclose(equaliser.process(block), block)


def test_the_first_preset_is_not_faded_in_from_silence():
    equaliser = dsp.Equaliser(eq.preset("Rock"), RATE)
    assert not equaliser.fading


def test_a_second_change_during_a_fade_does_not_stack_filters():
    equaliser = dsp.Equaliser(eq.preset("Rock"), RATE, fade_ms=50.0)
    equaliser.process(tone(1000, frames=256).astype(np.float32))
    equaliser.set_preset(eq.preset("Jazz"))
    assert equaliser.fading
    equaliser.set_preset(eq.preset("Voice"))
    equaliser.process(tone(1000, frames=4096).astype(np.float32))
    assert not equaliser.fading
    assert equaliser.preset.name == "Voice"


def test_swapping_the_calibration_keeps_the_preset_and_rebuilds_the_chain():
    calibration = eq.calibration("HD 4.50 BTNC")
    equaliser = dsp.Equaliser(eq.preset("Rock"), RATE)
    plain = equaliser._sections.shape[0]

    equaliser.set_calibration(calibration)
    assert equaliser.preset.name == "Rock"
    assert equaliser.calibration is calibration
    assert equaliser._sections.shape[0] == plain + len(calibration.filters)

    equaliser.set_calibration(None)
    assert equaliser.calibration is None
    assert equaliser._sections.shape[0] == plain


def test_swapping_the_loudness_keeps_the_preset_and_the_calibration():
    from opencaptune.eq import loudness as loudness_module

    calibration = eq.calibration("HD 4.50 BTNC")
    equaliser = dsp.Equaliser(eq.preset("Rock"), RATE, calibration=calibration)
    before = equaliser._sections.shape[0]

    compensation = loudness_module.compensation(60.0)
    equaliser.set_loudness(compensation)
    assert equaliser.preset.name == "Rock"
    assert equaliser.calibration is calibration
    assert equaliser.loudness == compensation
    assert equaliser._sections.shape[0] == before + len(compensation)

    equaliser.set_loudness(None)
    assert equaliser.loudness == ()
    assert equaliser._sections.shape[0] == before


def test_more_correction_means_more_headroom_is_taken():
    from opencaptune.eq import loudness as loudness_module

    equaliser = dsp.Equaliser(eq.preset("Neutral"), RATE)
    assert equaliser.preamp_db == 0.0
    equaliser.set_loudness(loudness_module.compensation(50.0))
    assert equaliser.preamp_db < -5.0
