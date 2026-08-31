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
                     "chooseOutput_", "chooseProfile_", "saveProfile_",
                     "toggleAutostart_", "toggleFollowDevice_", "openWindow_",
                     "openSoundCheck_", "quit_", "refresh_",
                     "menuWillOpen_", "menuDidClose_", "pollSpectrum_"):
        assert hasattr(menubar.MenuBarController, selector), selector


def test_the_slider_ranges_are_valid_settings():
    from opencaptune import menubar
    from opencaptune.audio.crossfeed import Crossfeed
    from opencaptune.eq import loudness

    for value in (0, 50, 100):
        Crossfeed(strength=value, sample_rate=48000)
    # Anything at or above the cut-off must be a level the compensator accepts.
    for value in (menubar.LOUDNESS_OFF_BELOW, 70.0, 90.0):
        loudness.compensation(value)


def test_the_custom_menu_views_build():
    from opencaptune.menuviews import HeaderView, SliderRow

    assert HeaderView is not None and SliderRow is not None
