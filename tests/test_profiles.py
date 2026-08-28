import json

import pytest

from opencaptune import profiles


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "profiles.json"
    monkeypatch.setattr(profiles, "path", lambda: path)
    return path


def test_saving_and_loading_round_trips(store):
    profiles.save(profiles.Profile(name="Music", preset="Rock", crossfeed=40,
                                   output_device="Headphones"))
    loaded = profiles.profile("music")
    assert loaded.preset == "Rock"
    assert loaded.crossfeed == 40


def test_saving_the_same_name_replaces_it(store):
    profiles.save(profiles.Profile(name="Music", preset="Rock", output_device="X"))
    profiles.save(profiles.Profile(name="Music", preset="Jazz", output_device="X"))
    assert len(json.loads(store.read_text())["profiles"]) == 1
    assert profiles.profile("Music").preset == "Jazz"


def test_deleting(store):
    profiles.save(profiles.Profile(name="Gone", output_device="X"))
    assert profiles.delete("gone") is True
    assert profiles.delete("gone") is False


def test_an_unknown_profile_says_what_is_available(store):
    profiles.save(profiles.Profile(name="Music", output_device="X"))
    with pytest.raises(KeyError, match="Music"):
        profiles.profile("Podcasts")


def test_an_unknown_profile_with_none_saved_says_so(store):
    with pytest.raises(KeyError, match="none saved yet"):
        profiles.profile("Anything")


def test_validation_rejects_nonsense_before_it_reaches_the_engine(store):
    with pytest.raises(KeyError, match="unknown preset"):
        profiles.save(profiles.Profile(name="Bad", preset="Dubstep"))
    with pytest.raises(ValueError, match="crossfeed"):
        profiles.save(profiles.Profile(name="Bad", crossfeed=140))
    with pytest.raises(ValueError, match="phon"):
        profiles.save(profiles.Profile(name="Bad", loudness_phon=200.0))
    with pytest.raises(ValueError, match="needs a name"):
        profiles.save(profiles.Profile(name="  "))


def test_a_profile_written_by_another_version_is_skipped_not_fatal(store):
    store.write_text(json.dumps({"profiles": [
        {"name": "Good", "preset": "Rock"},
        {"name": "Future", "preset": "Rock", "unknown_field": 1},
    ]}))
    found = profiles.profiles()
    assert "Good" in found and "Future" not in found


def test_a_corrupt_file_reads_as_empty(store):
    store.write_text("{{{ not json")
    assert profiles.profiles() == {}


def test_capturing_what_is_running(store):
    status = {"preset": "Voice", "calibration": "HD 4.50 BTNC", "crossfeed": 25,
              "loudness_phon": 60.0, "output": "Headphones"}
    entry = profiles.from_status("Evening", status)
    assert entry.preset == "Voice"
    assert entry.output_device == "Headphones"
    assert "Voice" in entry.summary() and "25%" in entry.summary()


def test_turning_a_profile_into_engine_configuration(store):
    entry = profiles.Profile(name="Music", preset="Rock", calibration="HD 4.50 BTNC",
                             crossfeed=30, output_device="Headphones")
    config = profiles.to_config(entry)
    assert config.preset == "Rock"
    assert config.crossfeed == 30
    assert config.output_device == "Headphones"


def test_a_profile_with_no_device_cannot_be_applied(store):
    with pytest.raises(ValueError, match="no output device"):
        profiles.to_config(profiles.Profile(name="Music"))
