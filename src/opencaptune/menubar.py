"""A menu bar app for the equaliser.

Runs inside the helper bundle as an accessory app — no dock icon, just an item
in the menu bar — and drives the daemon over the same control socket the CLI
uses. It holds no audio state of its own: everything it shows is read back
from the daemon, so the two can never disagree.

PyObjC turns every method on an NSObject subclass into an Objective-C
selector, so helpers that are not menu actions must be marked
``@objc.python_method`` or the class will not build.
"""

from __future__ import annotations

import sys
import traceback

import objc
from AppKit import (
    NSAlert,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSImage,
    NSMakeRect,
    NSMenu,
    NSMenuItem,
    NSStatusBar,
    NSTextField,
    NSVariableStatusItemLength,
)
from Foundation import NSObject, NSTimer

from . import daemon
from . import eq as equaliser
from . import profiles as profile_store
from . import settings as app_settings
from .audio.engine import EngineConfig
from .hostapp import HostAppError

REFRESH_SECONDS = 2.0

CROSSFEED_STEPS = [0, 25, 50, 75, 100]
LOUDNESS_STEPS = [None, 50.0, 60.0, 70.0, 80.0]

def _label_for_loudness(phon: float | None) -> str:
    return "Off" if phon is None else f"{phon:g} phon"


class MenuBarController(NSObject):
    def init(self):
        self = objc.super(MenuBarController, self).init()
        if self is None:
            return None
        self.status = None
        self.output_device = None
        self._headset_name = None
        # Set before the first refresh: device enumeration can fail, and
        # togglePower_ reads this.
        self._outputs = []
        self._profiles = []
        self._build()
        self.refresh_()
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            REFRESH_SECONDS, self, "refresh:", None, True
        )
        return self

    # ---- construction ---------------------------------------------------

    @objc.python_method
    def _build(self):
        bar = NSStatusBar.systemStatusBar()
        self.item = bar.statusItemWithLength_(NSVariableStatusItemLength)
        button = self.item.button()
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "slider.horizontal.3", "OpenCapTune"
        )
        if image is not None:
            button.setImage_(image)
        else:
            button.setTitle_("EQ")

        self.menu = NSMenu.alloc().init()
        self.menu.setAutoenablesItems_(False)

        self.state_item = self._add(self.menu, "…", None)
        self.routing_item = self._add(self.menu, "", None)
        self.menu.addItem_(NSMenuItem.separatorItem())

        self.power_item = self._add(self.menu, "Start", "togglePower:")
        self.menu.addItem_(NSMenuItem.separatorItem())

        self.profile_menu = self._submenu("Profiles")
        self.menu.addItem_(NSMenuItem.separatorItem())

        self.preset_menu = self._submenu("Preset")
        self.calibration_menu = self._submenu("Calibration")
        self.crossfeed_menu = self._submenu("Crossfeed")
        self.loudness_menu = self._submenu("Loudness")
        self.output_menu = self._submenu("Output device")
        self.headset_menu = self._submenu("Headset volume")
        self.menu.addItem_(NSMenuItem.separatorItem())

        self.window_item = self._add(self.menu, "Equaliser window…", "openWindow:")
        self.soundcheck_item = self._add(self.menu, "Sound Check…", "openSoundCheck:")
        self.menu.addItem_(NSMenuItem.separatorItem())

        self.login_item = self._add(self.menu, "Start at login", "toggleAutostart:")
        self.follow_item = self._add(self.menu, "Follow device", "toggleFollowDevice:")
        self.menu.addItem_(NSMenuItem.separatorItem())
        self._add(self.menu, "Quit", "quit:")
        self.item.setMenu_(self.menu)

        for index, preset in enumerate(equaliser.presets()):
            self._add(self.preset_menu, preset, "choosePreset:", tag=index)
        for index, value in enumerate(CROSSFEED_STEPS):
            self._add(self.crossfeed_menu, "Off" if value == 0 else f"{value}%",
                      "chooseCrossfeed:", tag=index)
        for index, value in enumerate(LOUDNESS_STEPS):
            self._add(self.loudness_menu, _label_for_loudness(value), "chooseLoudness:", tag=index)

    @objc.python_method
    def _add(self, menu, title, action, tag=None):
        entry = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
        if action:
            entry.setTarget_(self)
            entry.setEnabled_(True)
        else:
            entry.setEnabled_(False)
        if tag is not None:
            entry.setTag_(tag)
        menu.addItem_(entry)
        return entry

    @objc.python_method
    def _submenu(self, title):
        parent = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
        submenu = NSMenu.alloc().init()
        submenu.setAutoenablesItems_(False)
        parent.setSubmenu_(submenu)
        self.menu.addItem_(parent)
        return submenu

    # ---- state ----------------------------------------------------------

    def refresh_(self, timer=None):
        try:
            self.status = daemon.status()
        except (HostAppError, OSError):
            self.status = None
        self._follow_device()
        self._apply_state()

    @objc.python_method
    def _follow_device(self):
        """Start and stop as the profile's output device comes and goes."""
        try:
            if not app_settings.get("follow_device"):
                return
            name = app_settings.get("auto_profile")
            if not name:
                return
            entry = profile_store.profiles().get(name)
            if entry is None or not entry.output_device:
                return

            from .audio.devices import devices

            present = any(
                d.is_output and d.name == entry.output_device for d in devices()
            )
            if present and self.status is None:
                daemon.start(profile_store.to_config(entry))
                self.status = daemon.status()
            elif not present and self.status is not None:
                daemon.stop()
                self.status = None
        except Exception:  # noqa: BLE001 - a background rule must never crash the app
            pass

    @objc.python_method
    def _headset_info(self):
        from .bluetooth import headset as headset_info

        try:
            found = headset_info.connected_headsets()
        except Exception:  # noqa: BLE001
            return None
        target = self.status["output"] if self.status else self.output_device
        return next((e for e in found if e["name"] == target), None) or (
            found[0] if found else None
        )

    @objc.python_method
    def _apply_state(self):
        running = self.status is not None
        headset = self._headset_info()
        self._headset_name = headset["name"] if headset else None
        battery = f" · battery {headset['battery']}%" if headset and headset["battery"] is not None else ""
        self.power_item.setTitle_("Stop" if running else "Start")

        if not running:
            self.state_item.setTitle_("Not running")
            self.routing_item.setHidden_(not battery)
            self.routing_item.setTitle_(f"{headset['name']}{battery}" if headset else "")
        else:
            preset = self.status["preset"]
            calibration = self.status.get("calibration")
            self.state_item.setTitle_(preset if not calibration else f"{preset} + {calibration}")
            self.routing_item.setHidden_(False)
            self.routing_item.setTitle_(
                f"{self.status['output']}{battery} · {self.status['preamp_db']:+.1f} dB"
                + (f" · {self.status['glitches']} glitches" if self.status["glitches"] else "")
            )

        presets = list(equaliser.presets())
        self._tick(self.preset_menu,
                   presets.index(self.status["preset"])
                   if running and self.status["preset"] in presets else None)

        # Rebuilt each refresh so imported corrections appear without a restart.
        names = list(equaliser.calibrations())
        self.calibration_menu.removeAllItems()
        self._add(self.calibration_menu, "Off", "chooseCalibration:", tag=-1)
        for index, name in enumerate(names):
            self._add(self.calibration_menu, name, "chooseCalibration:", tag=index)
        current = self.status.get("calibration") if running else None
        self._tick(self.calibration_menu,
                   0 if current is None else (names.index(current) + 1 if current in names else None),
                   enabled=running)

        crossfeed = self.status.get("crossfeed", 0) if running else None
        self._tick(self.crossfeed_menu,
                   CROSSFEED_STEPS.index(crossfeed) if crossfeed in CROSSFEED_STEPS else None,
                   enabled=running)

        phon = self.status.get("loudness_phon") if running else None
        self._tick(self.loudness_menu,
                   LOUDNESS_STEPS.index(phon) if phon in LOUDNESS_STEPS else None,
                   enabled=running)

        self._fill_profiles()
        self._fill_outputs()
        self._fill_headset(headset)

        button = self.item.button()
        if headset and headset["battery"] is not None:
            button.setToolTip_(f"{headset['name']} — battery {headset['battery']}%")
            # Shown beside the icon only when it is worth noticing.
            button.setTitle_(f" {headset['battery']}%" if headset["battery"] <= 30 else "")
        else:
            button.setToolTip_("OpenCapTune")
            button.setTitle_("")

        from . import autostart

        self.login_item.setState_(1 if autostart.is_enabled() else 0)
        self.follow_item.setState_(1 if app_settings.get("follow_device") else 0)
        self.window_item.setEnabled_(running)
        self.soundcheck_item.setEnabled_(running)

    @objc.python_method
    def _tick(self, menu, index, enabled=True):
        for position in range(menu.numberOfItems()):
            entry = menu.itemAtIndex_(position)
            entry.setState_(1 if index is not None and position == index else 0)
            entry.setEnabled_(enabled)

    @objc.python_method
    def _fill_profiles(self):
        self.profile_menu.removeAllItems()
        self._profiles = list(profile_store.profiles().values())
        auto = app_settings.get("auto_profile")
        if not self._profiles:
            self._add(self.profile_menu, "No profiles saved", None)
        for index, entry in enumerate(self._profiles):
            item = self._add(self.profile_menu, entry.name, "chooseProfile:", tag=index)
            item.setState_(1 if entry.name == auto else 0)
            item.setToolTip_(entry.summary())
        self.profile_menu.addItem_(NSMenuItem.separatorItem())
        save = self._add(self.profile_menu, "Save current as…", "saveProfile:")
        save.setEnabled_(self.status is not None)

    @objc.python_method
    def _fill_headset(self, entry):
        """The headset's own amplifier gain.

        A different thing from the equaliser's preamp: the preamp attenuates
        digitally before the audio is transmitted, while this is an AVRCP
        command the headset applies in its own amplifier at the far end.
        """
        self.headset_menu.removeAllItems()
        if entry is None:
            self._add(self.headset_menu, "No headset connected", None)
            return
        for percent in (100, 75, 50, 25):
            self._add(self.headset_menu, f"{percent}%", "chooseHeadsetVolume:", tag=percent)

    @objc.python_method
    def _fill_outputs(self):
        from .audio.devices import devices

        try:
            outputs = [d for d in devices() if d.is_output]
        except Exception:  # noqa: BLE001 - a menu must never take the app down
            return
        self.output_menu.removeAllItems()
        chosen = self.output_device or (self.status["output"] if self.status else None)
        for index, device in enumerate(outputs):
            entry = self._add(self.output_menu, device.name, "chooseOutput:", tag=index)
            entry.setState_(1 if device.name == chosen else 0)
            entry.setEnabled_(self.status is None)
        self._outputs = outputs

    # ---- actions --------------------------------------------------------

    @objc.python_method
    def _guard(self, work):
        try:
            work()
        except Exception as error:  # noqa: BLE001 - surface it, never crash
            alert = NSAlert.alloc().init()
            alert.setMessageText_("OpenCapTune")
            alert.setInformativeText_(f"{type(error).__name__}: {error}")
            alert.runModal()
        self.refresh_()

    @objc.python_method
    def _ask(self, question, default=""):
        alert = NSAlert.alloc().init()
        alert.setMessageText_(question)
        alert.addButtonWithTitle_("Save")
        alert.addButtonWithTitle_("Cancel")
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 240, 24))
        field.setStringValue_(default)
        alert.setAccessoryView_(field)
        alert.window().setInitialFirstResponder_(field)
        if alert.runModal() != 1000:  # NSAlertFirstButtonReturn
            return None
        return field.stringValue().strip() or None

    @objc.IBAction
    def togglePower_(self, sender):
        def work():
            if self.status is not None:
                daemon.stop()
                return
            output = self.output_device or (self._outputs[0].name if self._outputs else None)
            if output is None:
                raise HostAppError("no output device is available")
            daemon.start(EngineConfig(output_device=output))

        self._guard(work)

    @objc.IBAction
    def choosePreset_(self, sender):
        names = list(equaliser.presets())
        self._guard(lambda: daemon.set_preset(names[sender.tag()]))

    @objc.IBAction
    def chooseCalibration_(self, sender):
        names = list(equaliser.calibrations())
        tag = sender.tag()
        self._guard(lambda: daemon.set_calibration(None if tag < 0 else names[tag]))

    @objc.IBAction
    def chooseCrossfeed_(self, sender):
        self._guard(lambda: daemon.set_crossfeed(CROSSFEED_STEPS[sender.tag()]))

    @objc.IBAction
    def chooseLoudness_(self, sender):
        self._guard(lambda: daemon.set_loudness(LOUDNESS_STEPS[sender.tag()]))

    @objc.IBAction
    def chooseOutput_(self, sender):
        self.output_device = self._outputs[sender.tag()].name
        self.refresh_()

    @objc.IBAction
    def chooseHeadsetVolume_(self, sender):
        from .hostapp import run_helper

        name = getattr(self, "_headset_name", None)
        if not name:
            return
        self._guard(
            lambda: run_helper(
                {"action": "set_headset_volume", "name": name,
                 "volume": sender.tag() / 100.0}
            )
        )

    @objc.IBAction
    def chooseProfile_(self, sender):
        entry = self._profiles[sender.tag()]

        def work():
            if daemon.is_running():
                daemon.stop()
            daemon.start(profile_store.to_config(entry))
            app_settings.set("auto_profile", entry.name)

        self._guard(work)

    @objc.IBAction
    def saveProfile_(self, sender):
        name = self._ask("Name this profile")
        if not name:
            return
        self._guard(
            lambda: profile_store.save(profile_store.from_status(name, daemon.status()))
        )

    @objc.IBAction
    def toggleAutostart_(self, sender):
        from . import autostart

        self._guard(lambda: autostart.disable() if autostart.is_enabled() else autostart.enable())

    @objc.IBAction
    def toggleFollowDevice_(self, sender):
        self._guard(
            lambda: app_settings.set("follow_device", not app_settings.get("follow_device"))
        )

    @objc.IBAction
    def openWindow_(self, sender):
        from .window import show_equaliser_window

        self._guard(show_equaliser_window)

    @objc.IBAction
    def openSoundCheck_(self, sender):
        from .window import show_soundcheck

        self._guard(show_soundcheck)

    @objc.IBAction
    def quit_(self, sender):
        NSApplication.sharedApplication().terminate_(self)


def main(argv: list[str] | None = None) -> int:
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    controller = MenuBarController.alloc().init()
    if controller is None:
        return 1
    app.run()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception:
        traceback.print_exc()
        raise
