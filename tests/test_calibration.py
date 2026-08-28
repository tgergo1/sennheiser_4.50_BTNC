import math

import pytest

from opencaptune import eq

RATE = 48000

AUTOEQ_SAMPLE = """Preamp: -6.2 dB
Filter 1: ON LSC Fc 105 Hz Gain 0.5 dB Q 0.70
Filter 2: ON PK Fc 2008 Hz Gain -7.2 dB Q 1.15
Filter 3: ON HSC Fc 10000 Hz Gain 6.1 dB Q 0.70
"""


def response(section, frequency, rate=RATE):
    (b0, b1, b2), (_, a1, a2) = eq.coefficients(section, rate)
    z = complex(math.cos(2 * math.pi * frequency / rate), math.sin(2 * math.pi * frequency / rate))
    return 20 * math.log10(abs((b0 + b1 / z + b2 / z**2) / (1.0 + a1 / z + a2 / z**2)))


def test_a_low_shelf_lifts_below_the_corner_and_not_above():
    section = eq.Filter(eq.LOW_SHELF, 1000, 6.0, 0.7)
    assert response(section, 20) == pytest.approx(6.0, abs=0.05)
    assert response(section, 20000) == pytest.approx(0.0, abs=0.05)
    assert response(section, 1000) == pytest.approx(3.0, abs=0.3)


def test_a_high_shelf_lifts_above_the_corner_and_not_below():
    section = eq.Filter(eq.HIGH_SHELF, 1000, 6.0, 0.7)
    assert response(section, 20000) == pytest.approx(6.0, abs=0.05)
    assert response(section, 20) == pytest.approx(0.0, abs=0.05)


def test_shelves_cut_as_well_as_boost():
    assert response(eq.Filter(eq.LOW_SHELF, 1000, -6.0, 0.7), 20) == pytest.approx(-6.0, abs=0.05)


def test_a_filter_rejects_a_bad_kind_or_q():
    with pytest.raises(ValueError, match="unknown filter kind"):
        eq.Filter("BANDPASS", 1000, 3.0, 1.0)
    with pytest.raises(ValueError, match="Q must be positive"):
        eq.Filter(eq.PEAKING, 1000, 3.0, 0.0)


def test_parsing_autoeq_text():
    calibration = eq.parse_parametric(AUTOEQ_SAMPLE, "Sample")
    assert [f.kind for f in calibration.filters] == [eq.LOW_SHELF, eq.PEAKING, eq.HIGH_SHELF]
    assert calibration.filters[1].frequency == 2008
    assert calibration.filters[1].gain_db == -7.2
    assert calibration.filters[1].q == 1.15


def test_parsing_ignores_the_stated_preamp():
    # It is recomputed from the cascade, and has to be once a preset is stacked on.
    assert len(eq.parse_parametric(AUTOEQ_SAMPLE, "Sample").filters) == 3


def test_parsing_rejects_a_malformed_filter_line():
    with pytest.raises(ValueError, match="could not parse filter line"):
        eq.parse_parametric("Filter 1: ON PK Fc banana Hz Gain 3 dB Q 1", "Bad")


def test_parsing_rejects_text_with_no_filters():
    with pytest.raises(ValueError, match="no filters found"):
        eq.parse_parametric("Preamp: -6.2 dB\n", "Empty")


def test_the_shipped_calibration_matches_the_published_preamp():
    # AutoEq states -6.2 dB for this profile. Computing it from our own filter
    # implementation is an independent check that the biquads match theirs.
    calibration = eq.calibration("HD 4.50 BTNC")
    assert eq.preamp_db(calibration, RATE) == pytest.approx(-6.2, abs=0.15)


def test_calibration_lookup_is_case_insensitive_and_reports_unknown_names():
    assert eq.calibration("hd 4.50 btnc").name == "HD 4.50 BTNC"
    with pytest.raises(KeyError, match="unknown calibration"):
        eq.calibration("HD 800")


def test_the_chain_puts_correction_before_taste():
    calibration = eq.calibration("HD 4.50 BTNC")
    chain = eq.chain(eq.preset("Rock"), calibration)
    assert chain[: len(calibration.filters)] == list(calibration.filters)
    assert len(chain) == len(calibration.filters) + len(eq.preset_filters(eq.preset("Rock")))


def test_a_chain_with_no_preset_or_no_calibration_still_works():
    calibration = eq.calibration("HD 4.50 BTNC")
    assert len(eq.chain(None, calibration)) == len(calibration.filters)
    assert eq.chain(eq.preset("Neutral"), None) == []


def test_stacking_a_preset_on_a_calibration_adds_their_responses():
    calibration = eq.calibration("HD 4.50 BTNC")
    rock = eq.preset("Rock")
    for frequency in (100, 1000, 5000):
        combined = eq.response_db(eq.chain(rock, calibration), frequency, RATE)
        separate = (
            eq.response_db(calibration, frequency, RATE)
            + eq.response_db(rock, frequency, RATE)
        )
        assert combined == pytest.approx(separate, abs=1e-9)


def test_a_calibration_needs_more_headroom_once_a_preset_is_stacked_on_it():
    calibration = eq.calibration("HD 4.50 BTNC")
    alone = eq.preamp_db(calibration, RATE)
    with_boost = eq.preamp_db(eq.chain(eq.preset("Rock"), calibration), RATE)
    assert with_boost < alone
