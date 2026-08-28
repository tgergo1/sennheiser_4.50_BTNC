"""The menu bar app, tested as far as it can be without a window server.

PyObjC builds the Objective-C class at import time and rejects any plain
Python helper it cannot turn into a selector, so simply importing the module
catches a whole class of mistake — including the one that broke this app the
first time it ran.
"""

import sys

import pytest

pytest.importorskip("AppKit", reason="menu bar app is macOS only")

if sys.platform != "darwin":
    pytest.skip("macOS only", allow_module_level=True)


def test_the_module_imports_and_the_objc_class_builds():
    from opencaptune import menubar

    assert menubar.MenuBarController is not None


def test_every_menu_action_the_app_wires_up_actually_exists():
    from opencaptune import menubar

    for selector in ("togglePower_", "choosePreset_", "chooseCalibration_",
                     "chooseCrossfeed_", "chooseLoudness_", "chooseOutput_",
                     "quit_", "refresh_"):
        assert hasattr(menubar.MenuBarController, selector), selector


def test_the_discrete_steps_are_valid_settings():
    from opencaptune import menubar
    from opencaptune.audio.crossfeed import Crossfeed
    from opencaptune.eq import loudness

    for value in menubar.CROSSFEED_STEPS:
        Crossfeed(strength=value, sample_rate=48000)
    for value in menubar.LOUDNESS_STEPS:
        if value is not None:
            loudness.compensation(value)


def test_loudness_labels_read_sensibly():
    from opencaptune import menubar

    assert menubar._label_for_loudness(None) == "Off"
    assert menubar._label_for_loudness(60.0) == "60 phon"
