import json

import pytest

from opencaptune import eq
from opencaptune.eq import autoeq

PARAMETRIC = """Preamp: -6.3 dB
Filter 1: ON LSC Fc 105 Hz Gain 1.0 dB Q 0.70
Filter 2: ON PK Fc 2000 Hz Gain -5.0 dB Q 1.10
"""

TREE = {
    "results/oratory1990": ["over-ear", "in-ear"],
    "results/oratory1990/over-ear": ["Sennheiser HD 650", "Sennheiser HD 6XX", "AKG K371"],
    "results/oratory1990/in-ear": ["Etymotic ER2XR"],
    "results/crinacle": ["harman_over-ear_2018"],
    "results/crinacle/harman_over-ear_2018": ["Sennheiser HD 650"],
}


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(autoeq, "_directories", lambda path: TREE.get(path, []))


def test_search_finds_a_model_across_sources(offline):
    found = autoeq.search("HD 650", ("oratory1990", "crinacle"))
    assert ("oratory1990", "over-ear", "Sennheiser HD 650") in found
    assert ("crinacle", "harman_over-ear_2018", "Sennheiser HD 650") in found


def test_search_ranks_an_exact_name_first(offline):
    found = autoeq.search("AKG K371", ("oratory1990",))
    assert found[0][2] == "AKG K371"


def test_search_ranks_a_prefix_above_a_mention(offline):
    found = autoeq.search("Sennheiser", ("oratory1990",))
    assert all(name.startswith("Sennheiser") for _, _, name in found)


def test_search_returns_nothing_for_an_unknown_model(offline):
    assert autoeq.search("Nonexistent Headphone", ("oratory1990",)) == []


def test_search_needs_something_to_search_for(offline):
    with pytest.raises(ValueError, match="model name"):
        autoeq.search("   ")


def test_a_source_that_fails_does_not_sink_the_whole_search(monkeypatch):
    def flaky(path):
        if path.startswith("results/crinacle"):
            raise autoeq.AutoEqError("boom")
        return TREE.get(path, [])

    monkeypatch.setattr(autoeq, "_directories", flaky)
    found = autoeq.search("HD 650", ("oratory1990", "crinacle"))
    assert found == [("oratory1990", "over-ear", "Sennheiser HD 650")]


def test_fetch_parses_what_it_downloads(monkeypatch):
    class Response:
        def read(self):
            return PARAMETRIC.encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(autoeq.urllib.request, "urlopen", lambda url, timeout=0: Response())
    calibration = autoeq.fetch("oratory1990", "over-ear", "Sennheiser HD 650")
    assert calibration.name == "Sennheiser HD 650"
    assert [f.kind for f in calibration.filters] == [eq.LOW_SHELF, eq.PEAKING]
    assert "oratory1990" in calibration.source
