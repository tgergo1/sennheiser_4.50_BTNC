import pytest

from opencaptune import settings


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "path", lambda: tmp_path / "settings.json")


def test_defaults_apply_when_nothing_is_saved(store):
    assert settings.get("follow_device") is False
    assert settings.get("auto_profile") is None


def test_setting_then_reading(store):
    settings.set("follow_device", True)
    settings.set("auto_profile", "Music")
    assert settings.get("follow_device") is True
    assert settings.get("auto_profile") == "Music"


def test_unknown_keys_are_refused(store):
    with pytest.raises(KeyError, match="unknown setting"):
        settings.get("nope")
    with pytest.raises(KeyError, match="unknown setting"):
        settings.set("nope", 1)


def test_a_corrupt_file_falls_back_to_defaults(store, tmp_path):
    (tmp_path / "settings.json").write_text("not json")
    assert settings.get("follow_device") is False


# --- Sound Check convergence ------------------------------------------------

pytest.importorskip("AppKit", reason="window module is macOS only")


def test_level_matching_removes_the_average():
    from opencaptune.window import level_matched

    matched = level_matched([6.0, 2.0, -2.0, 2.0])
    assert sum(matched) == pytest.approx(0.0)


def test_a_pair_differs_in_tone_but_not_in_level():
    from opencaptune.window import candidates

    a, b = candidates([0.0] * 14, range(0, 4), 4.0)
    # Both average zero, so neither is simply louder than the other.
    assert sum(a) == pytest.approx(0.0)
    assert sum(b) == pytest.approx(0.0)
    # A lifts the bass, B lowers it, by the same amount.
    assert a[0] > 0 > b[0]
    assert a[0] == pytest.approx(-b[0])


def test_the_pair_only_moves_the_band_group_under_test():
    from opencaptune.window import candidates

    a, _ = candidates([0.0] * 14, range(0, 4), 4.0)
    assert len(set(round(v, 6) for v in a[0:4])) == 1
    assert len(set(round(v, 6) for v in a[4:])) == 1
    assert a[0] != a[4]


def test_the_step_halves_each_cycle_so_it_converges():
    from opencaptune.window import CYCLES, DIMENSIONS, START_STEP_DB

    steps = [START_STEP_DB / (2 ** cycle) for cycle in range(CYCLES)]
    assert steps == [4.0, 2.0, 1.0]
    assert len(DIMENSIONS) * CYCLES == 12
