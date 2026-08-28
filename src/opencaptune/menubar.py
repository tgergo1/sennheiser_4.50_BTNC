"""A menu bar app for the equaliser.

Runs inside the helper bundle as an accessory app — no dock icon, just an item
in the menu bar — and drives the daemon over the same control socket the CLI
uses. It holds no audio state of its own: everything it shows is read back from
the daemon, so the two can never disagree.
"""

from __future__ import annotations

import sys
import traceback

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSAlert,
    NSImage,
    NSMenu,
    NSMenuItem,
    NSStatusBar,
    NSVariableStatusItemLength,
)
from Foundation import NSObject, NSTimer

from . import daemon
from . import eq as equaliser
from .audio.engine import EngineConfig
from .hostapp import HostAppError

REFRESH_SECONDS = 2.0

CROSSFEED_STEPS = [0, 25, 50, 75, 100]
LOUDNESS_STEPS = [None, 50.0, 60.0, 70.0, 80.0]

#: Remembered so "Start" can be a single click rather than a form.
DEFAULT_INPUT = "BlackHole 2ch"


def _label_for_loudness(phon: float | None) -> str:
    return "Off" if phon is None else f"{phon:g} phon"


class MenuBarController(NSObject):
    def init(self):
        self = objc.super(MenuBarController, self).init()
        if self is None:
            return None
        self.status = None
        self.output_device = None
        # Set before the first refresh: device enumeration can fail, and
        # togglePower_ reads this.
        self._outputs = []
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

        self.preset_menu = self._submenu("Preset")
        self.calibration_menu = self._submenu("Calibration")
        self.crossfeed_menu = self._submenu("Crossfeed")
        self.loudness_menu = self._submenu("Loudness")
        self.output_menu = self._submenu("Output device")

        self.menu.addItem_(NSMenuItem.separatorItem())
        self._add(self.menu, "Quit", "quit:")
        self.item.setMenu_(self.menu)

        self._fill_static_menus()

    @objc.python_method
    def _add(self, menu, title, action, target=None, tag=None, indent=False):
        entry = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title, action, ""
        )
        if action:
            entry.setTarget_(target or self)
            entry.setEnabled_(True)
        else:
            entry.setEnabled_(False)
        if tag is not None:
            entry.setTag_(tag)
        if indent:
            entry.setIndentationLevel_(1)
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

    @objc.python_method
    def _fill_static_menus(self):
        for index, preset in enumerate(equaliser.presets()):
            self._add(self.preset_menu, preset, "choosePreset:", tag=index)

        self._add(self.calibration_menu, "Off", "chooseCalibration:", tag=-1)
        for index, name in enumerate(equaliser.calibrations()):
            self._add(self.calibration_menu, name, "chooseCalibration:", tag=index)

        for index, value in enumerate(CROSSFEED_STEPS):
            title = "Off" if value == 0 else f"{value}%"
            self._add(self.crossfeed_menu, title, "chooseCrossfeed:", tag=index)

        for index, value in enumerate(LOUDNESS_STEPS):
            self._add(
                self.loudness_menu, _label_for_loudness(value), "chooseLoudness:", tag=index
            )

    # ---- state ----------------------------------------------------------

    def refresh_(self, timer=None):
        try:
            self.status = daemon.status()
        except (HostAppError, OSError):
            self.status = None
        self._apply_state()

    @objc.python_method
    def _apply_state(self):
        running = self.status is not None
        self.power_item.setTitle_("Stop" if running else "Start")

        if not running:
            self.state_item.setTitle_("Not running")
            self.routing_item.setTitle_("")
            self.routing_item.setHidden_(True)
        else:
            preset = self.status["preset"]
            calibration = self.status.get("calibration")
            summary = preset if not calibration else f"{preset} + {calibration}"
            self.state_item.setTitle_(summary)
            self.routing_item.setHidden_(False)
            self.routing_item.setTitle_(
                f"{self.status['output']} · {self.status['preamp_db']:+.1f} dB"
                + (f" · {self.status['glitches']} glitches" if self.status["glitches"] else "")
            )

        self._tick(self.preset_menu, list(equaliser.presets()).index(self.status["preset"])
                   if running and self.status["preset"] in equaliser.presets() else None)

        names = list(equaliser.calibrations())
        current = self.status.get("calibration") if running else None
        self._tick(self.calibration_menu,
                   0 if current is None else names.index(current) + 1 if current in names else None,
                   enabled=running)

        crossfeed = self.status.get("crossfeed", 0) if running else None
        self._tick(self.crossfeed_menu,
                   CROSSFEED_STEPS.index(crossfeed) if crossfeed in CROSSFEED_STEPS else None,
                   enabled=running)

        phon = self.status.get("loudness_phon") if running else None
        self._tick(self.loudness_menu,
                   LOUDNESS_STEPS.index(phon) if phon in LOUDNESS_STEPS else None,
                   enabled=running)

        self._fill_outputs()

    @objc.python_method
    def _tick(self, menu, index, enabled=True):
        for position in range(menu.numberOfItems()):
            entry = menu.itemAtIndex_(position)
            entry.setState_(1 if index is not None and position == index else 0)
            entry.setEnabled_(enabled)

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

    @objc.IBAction
    def togglePower_(self, sender):
        def work():
            if self.status is not None:
                daemon.stop()
                return
            output = self.output_device or (self._outputs[0].name if self._outputs else None)
            if output is None:
                raise HostAppError("no output device is available")
            daemon.start(EngineConfig(input_device=DEFAULT_INPUT, output_device=output))

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
