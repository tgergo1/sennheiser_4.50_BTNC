import json

import pytest

from opencaptune import eq


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "calibrations.json"
    monkeypatch.setattr(eq, "user_calibrations_path", lambda: path)
    return path


def make(name, gain=3.0):
    return eq.Calibration(name=name, filters=(eq.Filter(eq.PEAKING, 1000, gain, 1.0),),
                          source="test")


def test_saving_then_loading_round_trips(store):
    eq.save_calibration(make("My Headphones"))
    loaded = eq.calibrations()["My Headphones"]
    assert loaded.filters[0].gain_db == 3.0
    assert loaded.source == "test"


def test_the_shipped_calibration_is_still_there_alongside(store):
    eq.save_calibration(make("My Headphones"))
    assert "HD 4.50 BTNC" in eq.calibrations()


def test_saving_the_same_name_replaces_rather_than_duplicates(store):
    eq.save_calibration(make("Same", gain=1.0))
    eq.save_calibration(make("Same", gain=9.0))
    assert len(json.loads(store.read_text())["calibrations"]) == 1
    assert eq.calibrations()["Same"].filters[0].gain_db == 9.0


def test_deleting_an_imported_calibration(store):
    eq.save_calibration(make("Temporary"))
    assert eq.delete_calibration("temporary") is True
    assert "Temporary" not in eq.calibrations()


def test_deleting_something_that_is_not_there_reports_so(store):
    assert eq.delete_calibration("Never Existed") is False


def test_a_shipped_calibration_cannot_be_deleted(store):
    assert eq.delete_calibration("HD 4.50 BTNC") is False
    assert "HD 4.50 BTNC" in eq.calibrations()


def test_a_corrupt_user_file_does_not_hide_the_shipped_ones(store):
    store.write_text("{ this is not json")
    assert "HD 4.50 BTNC" in eq.calibrations()


def test_an_imported_calibration_works_in_a_chain(store):
    eq.save_calibration(make("My Headphones"))
    chain = eq.chain(eq.preset("Rock"), eq.calibration("my headphones"))
    assert len(chain) == 1 + len(eq.preset_filters(eq.preset("Rock")))
