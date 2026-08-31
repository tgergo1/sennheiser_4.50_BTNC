import math

import pytest

from opencaptune import dose


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(dose, "path", lambda: tmp_path / "exposure.json")


def at_level(db, seconds):
    """Record `seconds` of audio at `db` SPL."""
    mean_square = 10 ** ((db - dose.FULL_SCALE_SPL) / 10.0)
    dose.record(mean_square * seconds, seconds)


def test_nothing_listened_to_is_no_dose():
    summary = dose.summary()
    assert summary.hours == 0.0
    assert summary.dose_fraction == 0.0
    assert not summary.is_over


def test_the_reference_exposure_is_exactly_one_full_dose():
    at_level(dose.REFERENCE_DB, dose.REFERENCE_HOURS * 3600)
    summary = dose.summary()
    assert summary.average_db == pytest.approx(dose.REFERENCE_DB, abs=0.01)
    assert summary.dose_fraction == pytest.approx(1.0, rel=0.01)
    assert summary.is_over


def test_half_the_time_at_the_reference_is_half_a_dose():
    at_level(dose.REFERENCE_DB, dose.REFERENCE_HOURS * 3600 / 2)
    assert dose.summary().dose_fraction == pytest.approx(0.5, rel=0.01)
    assert not dose.summary().is_over


def test_three_decibels_louder_costs_twice_as_much():
    # The exchange rate is the whole point: loudness and time trade off.
    at_level(dose.REFERENCE_DB + 3.0, 3600)
    louder = dose.summary().dose_fraction
    dose.reset()
    at_level(dose.REFERENCE_DB, 3600)
    assert louder == pytest.approx(dose.summary().dose_fraction * 2, rel=0.02)


def test_quiet_listening_barely_registers():
    at_level(60.0, 40 * 3600)
    assert dose.summary().dose_fraction < 0.02


def test_exposure_accumulates_across_separate_sessions():
    at_level(dose.REFERENCE_DB, 3600)
    at_level(dose.REFERENCE_DB, 3600)
    summary = dose.summary()
    assert summary.hours == pytest.approx(2.0, rel=0.01)
    assert summary.average_db == pytest.approx(dose.REFERENCE_DB, abs=0.01)


def test_a_short_loud_burst_is_not_averaged_away():
    # Both land in the same hour. Averaged, the burst vanishes; it should not.
    at_level(70.0, 3600)
    at_level(95.0, 60)
    summary = dose.summary()
    assert summary.loudest_db == pytest.approx(95.0, abs=0.1)
    # That one minute carries more energy than the whole hour before it, so it
    # pulls the average up by nearly 8 dB — which is the point of measuring
    # energy rather than sampling loudness.
    assert 70.0 < summary.average_db < summary.loudest_db
    assert summary.average_db > 75.0


def test_the_allowance_halves_every_three_decibels():
    summary = dose.summary()
    assert summary.allowance_hours_at(dose.REFERENCE_DB) == pytest.approx(40.0)
    assert summary.allowance_hours_at(83.0) == pytest.approx(20.0)
    assert summary.allowance_hours_at(86.0) == pytest.approx(10.0)


def test_silence_and_nonsense_are_ignored():
    dose.record(0.0, 0.0)
    dose.record(-1.0, 10.0)
    dose.record(1.0, -5.0)
    assert dose.summary().hours == 0.0


def test_hours_older_than_the_window_fall_out(monkeypatch):
    at_level(dose.REFERENCE_DB, 3600)
    assert dose.summary().hours == pytest.approx(1.0, rel=0.01)
    # A week and a day later, that hour is no longer counted.
    real = dose._now_bucket()
    monkeypatch.setattr(dose, "_now_bucket", lambda: real + dose.WINDOW_HOURS + 24)
    assert dose.summary().hours == 0.0


def test_a_corrupt_file_reads_as_no_history():
    dose.path().parent.mkdir(parents=True, exist_ok=True)
    dose.path().write_text("{ not json")
    assert dose.summary().hours == 0.0
