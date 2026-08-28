import math

import pytest

from opencaptune import eq


def test_ships_captunes_own_band_centres():
    assert eq.bands() == (35, 57, 92, 148, 238, 384, 620, 1000, 1613, 2601, 4196, 6767, 10195, 17605)


def test_every_preset_covers_every_band_within_the_gain_limits():
    for preset in eq.presets().values():
        assert len(preset.gains_db) == len(eq.bands()), preset.name
        assert all(eq.MIN_GAIN_DB <= g <= eq.MAX_GAIN_DB for g in preset.gains_db), preset.name


def test_neutral_is_flat():
    assert set(eq.preset("Neutral").gains_db) == {0.0}


def test_preset_lookup_is_case_insensitive():
    assert eq.preset("hip hop").name == "Hip Hop"
    with pytest.raises(KeyError, match="unknown preset"):
        eq.preset("Dubstep")


def test_boosts_reproduce_captunes_arithmetic():
    # Neutral is flat, so the result is the offset curve scaled by the slider.
    boosted = eq.preset("Neutral").with_boosts(bass=50)
    bass = eq.boost_offsets()["bass"]
    assert boosted.gains_db[0] == pytest.approx(bass[0] * 0.5)
    assert boosted.gains_db[-1] == pytest.approx(0.0)
    assert "bass +50%" in boosted.name


def test_boosts_clamp_to_the_gain_limits():
    loud = eq.Preset("Test", tuple([11.0] * len(eq.bands())))
    assert max(loud.with_boosts(bass=100, treble=100).gains_db) == eq.MAX_GAIN_DB


def test_boosts_reject_out_of_range_sliders():
    with pytest.raises(ValueError, match="0-100"):
        eq.preset("Neutral").with_boosts(bass=101)


def test_zero_boost_leaves_the_curve_and_name_untouched():
    original = eq.preset("Rock")
    assert original.with_boosts() == original


def test_band_spacing_is_a_constant_fraction_of_an_octave():
    assert eq.band_width_octaves() == pytest.approx(0.69, abs=0.02)
    assert eq.default_q() == pytest.approx(2.07, abs=0.05)


def test_a_zero_gain_peaking_filter_is_a_pass_through():
    numerator, denominator = eq.peaking_coefficients(1000, 0.0, 48000)
    assert numerator == pytest.approx(denominator)


def _response_db(sections, frequency, sample_rate):
    z = complex(math.cos(2 * math.pi * frequency / sample_rate),
                math.sin(2 * math.pi * frequency / sample_rate))
    total = 1.0 + 0j
    for (b0, b1, b2), (_, a1, a2) in sections:
        total *= (b0 + b1 / z + b2 / z**2) / (1.0 + a1 / z + a2 / z**2)
    return 20 * math.log10(abs(total))


def test_a_peaking_filter_hits_its_gain_at_the_centre_frequency():
    section = eq.peaking_coefficients(1000, 6.0, 48000)
    assert _response_db([section], 1000, 48000) == pytest.approx(6.0, abs=0.01)


def test_the_chain_reproduces_the_requested_curve_at_each_band():
    preset = eq.preset("Rock")
    sections = eq.filter_chain(preset, 48000)
    for frequency, gain in zip(eq.bands(), preset.gains_db):
        if frequency >= 24000:
            continue
        # Cascaded constant-Q sections overlap, so adjacent boosts add a little
        # on top of each other. Around 2 dB of overshoot is inherent to the design.
        assert _response_db(sections, frequency, 48000) == pytest.approx(gain, abs=2.0)


def test_the_chain_drops_bands_above_nyquist_and_flat_bands():
    assert eq.filter_chain(eq.preset("Neutral"), 48000) == []
    at_44k = eq.filter_chain(eq.preset("Rock"), 44100)
    at_32k = eq.filter_chain(eq.preset("Rock"), 32000)
    assert len(at_32k) < len(at_44k)


def test_peaking_rejects_a_centre_frequency_above_nyquist():
    with pytest.raises(ValueError, match="Nyquist"):
        eq.peaking_coefficients(17605, 3.0, 32000)
