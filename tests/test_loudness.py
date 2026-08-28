import pytest

from opencaptune import eq
from opencaptune.eq import loudness


def test_a_contour_equals_its_own_phon_value_at_1_kHz():
    # This is the definition of the phon, so it is an independent check that
    # the ISO 226 tables and formula are right. The standard's analytical
    # approximation leaves a hair of residual.
    index = loudness.frequencies().index(1000.0)
    for phon in (20, 40, 60, 80):
        assert loudness.contour(phon)[index] == pytest.approx(phon, abs=0.02)


def test_quiet_contours_demand_much_more_bass():
    frequencies = loudness.frequencies()
    low = frequencies.index(50.0)
    quiet, loud = loudness.contour(20), loudness.contour(80)
    # At 20 phon the ear needs far more sound pressure at 50 Hz, relative to
    # 1 kHz, than it does at 80 phon.
    assert (quiet[low] - 20) > (loud[low] - 80) + 15


def test_contours_outside_the_standards_range_are_refused():
    with pytest.raises(ValueError, match="0 to 90 phon"):
        loudness.contour(95)
    with pytest.raises(ValueError, match="0 to 90 phon"):
        loudness.contour(-1)


def test_no_correction_when_playback_matches_the_reference():
    for frequency, gain in loudness.correction_db(80.0, 80.0):
        assert gain == pytest.approx(0.0, abs=1e-9)
    assert loudness.compensation(80.0, 80.0) == ()


def test_playing_quieter_lifts_the_bass_and_leaves_1_kHz_alone():
    curve = dict(loudness.correction_db(60.0, 80.0))
    assert curve[1000.0] == pytest.approx(0.0, abs=1e-9)
    assert curve[50.0] > 5.0
    assert curve[20.0] > curve[50.0] > curve[125.0] > curve[500.0]


def test_the_lift_grows_the_quieter_you_listen():
    at_70 = dict(loudness.correction_db(70.0))[50.0]
    at_50 = dict(loudness.correction_db(50.0))[50.0]
    assert at_50 > at_70 > 0


def test_playing_louder_than_the_reference_cuts_the_bass():
    assert dict(loudness.correction_db(90.0, 70.0))[50.0] < 0


def test_compensation_lands_on_the_equalisers_own_bands():
    sections = loudness.compensation(60.0)
    assert sections
    assert all(section.frequency in [float(b) for b in eq.bands()] for section in sections)
    assert all(section.kind == eq.PEAKING for section in sections)


def test_compensation_is_clipped_to_the_equalisers_range():
    # The true correction at very low levels exceeds what the filters can do.
    for section in loudness.compensation(0.0):
        assert eq.MIN_GAIN_DB <= section.gain_db <= eq.MAX_GAIN_DB


def test_bands_above_the_tables_top_hold_rather_than_extrapolate():
    curve = loudness.correction_db(60.0)
    top = curve[-1]
    assert top[0] == 12500.0
    # 17605 Hz is a real band and ISO 226 stops at 12.5 kHz.
    assert loudness._interpolate(curve, 17605.0) == pytest.approx(top[1])
    assert loudness._interpolate(curve, 5.0) == pytest.approx(curve[0][1])


def test_compensation_composes_into_the_chain_after_the_calibration():
    calibration = eq.calibration("HD 4.50 BTNC")
    compensation = loudness.compensation(60.0)
    chain = eq.chain(eq.preset("Rock"), calibration, compensation)
    assert chain[: len(calibration.filters)] == list(calibration.filters)
    assert chain[len(calibration.filters) : len(calibration.filters) + len(compensation)] == list(
        compensation
    )
