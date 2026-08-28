"""Windows: the curve editor with a live spectrum, and the Sound Check wizard.

Both are driven from the menu bar app and talk to the daemon over the control
socket, so nothing here holds audio state either.
"""

from __future__ import annotations

import objc
from AppKit import (
    NSApplication,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSButton,
    NSColor,
    NSFont,
    NSMakePoint,
    NSMakeRect,
    NSTextField,
    NSTitledWindowMask,
    NSView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSObject, NSTimer

from . import daemon
from . import eq as equaliser
from . import profiles as profile_store

#: Windows are owned here: an NSWindow with no strong reference is deallocated
#: out from under you the moment the function that made it returns.
_open_windows: dict[str, object] = {}

SPECTRUM_FLOOR_DB = -72.0
MARGIN = 46


class EqualiserView(NSView):
    """Fourteen draggable band handles over a live spectrum."""

    def initWithFrame_(self, frame):
        self = objc.super(EqualiserView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.gains = [0.0] * len(equaliser.bands())
        self.spectrum = [SPECTRUM_FLOOR_DB] * len(equaliser.bands())
        self.dragging = None
        return self

    @objc.python_method
    def _band_x(self, index, width):
        count = len(equaliser.bands())
        span = width - 2 * MARGIN
        return MARGIN + span * index / max(1, count - 1)

    @objc.python_method
    def _gain_y(self, gain, height):
        span = height - 2 * MARGIN
        position = (gain - equaliser.MIN_GAIN_DB) / (
            equaliser.MAX_GAIN_DB - equaliser.MIN_GAIN_DB
        )
        return MARGIN + span * position

    @objc.python_method
    def _y_to_gain(self, y, height):
        span = height - 2 * MARGIN
        position = (y - MARGIN) / span
        gain = equaliser.MIN_GAIN_DB + position * (
            equaliser.MAX_GAIN_DB - equaliser.MIN_GAIN_DB
        )
        return min(equaliser.MAX_GAIN_DB, max(equaliser.MIN_GAIN_DB, gain))

    def drawRect_(self, rect):
        bounds = self.bounds()
        width, height = bounds.size.width, bounds.size.height
        NSColor.controlBackgroundColor().set()
        NSBezierPath.fillRect_(bounds)

        # Horizontal grid every 6 dB, with 0 dB emphasised.
        for gain in range(int(equaliser.MIN_GAIN_DB), int(equaliser.MAX_GAIN_DB) + 1, 6):
            y = self._gain_y(gain, height)
            (NSColor.separatorColor() if gain else NSColor.tertiaryLabelColor()).set()
            line = NSBezierPath.bezierPath()
            line.moveToPoint_(NSMakePoint(MARGIN, y))
            line.lineToPoint_(NSMakePoint(width - MARGIN, y))
            line.setLineWidth_(1.0 if gain else 1.5)
            line.stroke()
            self._label(f"{gain:+d}", 6, y - 7, NSColor.tertiaryLabelColor())

        # Spectrum behind the curve.
        NSColor.systemBlueColor().colorWithAlphaComponent_(0.22).set()
        for index, level in enumerate(self.spectrum):
            fraction = max(0.0, min(1.0, (level - SPECTRUM_FLOOR_DB) / -SPECTRUM_FLOOR_DB))
            if fraction <= 0.001:
                continue
            x = self._band_x(index, width)
            bar_height = (height - 2 * MARGIN) * fraction
            NSBezierPath.fillRect_(NSMakeRect(x - 7, MARGIN, 14, bar_height))

        # The curve itself.
        NSColor.controlAccentColor().set()
        curve = NSBezierPath.bezierPath()
        for index, gain in enumerate(self.gains):
            point = NSMakePoint(self._band_x(index, width), self._gain_y(gain, height))
            if index == 0:
                curve.moveToPoint_(point)
            else:
                curve.lineToPoint_(point)
        curve.setLineWidth_(2.0)
        curve.stroke()

        for index, gain in enumerate(self.gains):
            x = self._band_x(index, width)
            y = self._gain_y(gain, height)
            NSColor.controlAccentColor().set()
            NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x - 5, y - 5, 10, 10)).fill()
            frequency = equaliser.bands()[index]
            label = f"{frequency}" if frequency < 1000 else f"{frequency / 1000:.3g}k"
            self._label(label, x - 12, 18, NSColor.secondaryLabelColor())

    @objc.python_method
    def _label(self, text, x, y, colour):
        from AppKit import NSAttributedString, NSFontAttributeName, NSForegroundColorAttributeName

        attributes = {
            NSFontAttributeName: NSFont.systemFontOfSize_(9),
            NSForegroundColorAttributeName: colour,
        }
        NSAttributedString.alloc().initWithString_attributes_(text, attributes).drawAtPoint_(
            NSMakePoint(x, y)
        )

    @objc.python_method
    def _nearest_band(self, point):
        width = self.bounds().size.width
        distances = [
            (abs(self._band_x(index, width) - point.x), index)
            for index in range(len(self.gains))
        ]
        distance, index = min(distances)
        return index if distance < 24 else None

    def mouseDown_(self, event):
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        self.dragging = self._nearest_band(point)
        if self.dragging is not None:
            self.mouseDragged_(event)

    def mouseDragged_(self, event):
        if self.dragging is None:
            return
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        self.gains[self.dragging] = self._y_to_gain(point.y, self.bounds().size.height)
        self.setNeedsDisplay_(True)
        try:
            daemon.set_curve(self.gains)
        except Exception:  # noqa: BLE001 - dragging must not raise dialogs
            pass

    def mouseUp_(self, event):
        self.dragging = None


class EqualiserWindow(NSObject):
    def init(self):
        self = objc.super(EqualiserWindow, self).init()
        if self is None:
            return None
        rect = NSMakeRect(0, 0, 720, 420)
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, NSWindowStyleMaskTitled | NSWindowStyleMaskClosable, NSBackingStoreBuffered, False
        )
        self.window.setTitle_("OpenCapTune — Equaliser")
        self.window.setReleasedWhenClosed_(False)
        self.view = EqualiserView.alloc().initWithFrame_(rect)
        self.window.setContentView_(self.view)
        self.window.center()

        try:
            daemon.watch_spectrum(True)
            self.view.gains = list(daemon.status().get("gains", self.view.gains))
        except Exception:  # noqa: BLE001
            pass

        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.1, self, "tick:", None, True
        )
        return self

    def tick_(self, timer):
        try:
            reading = daemon.spectrum()
        except Exception:  # noqa: BLE001 - the daemon may have stopped
            return
        self.view.spectrum = reading.get("spectrum", self.view.spectrum)
        if self.view.dragging is None:
            self.view.gains = list(reading.get("gains", self.view.gains))
        self.view.setNeedsDisplay_(True)

    @objc.python_method
    def show(self):
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)


#: Sound Check adjusts these groups of bands rather than all fourteen at once:
#: a listener can hear "more bass" reliably, but not one band in isolation.
DIMENSIONS = [
    ("bass", range(0, 4)),
    ("lower mid", range(4, 7)),
    ("upper mid", range(7, 10)),
    ("treble", range(10, 14)),
]
CYCLES = 3
START_STEP_DB = 4.0


def level_matched(gains: list[float]) -> list[float]:
    """Remove the average level so a comparison is about tone, not loudness.

    Without this the louder of two options wins almost every time, whatever it
    sounds like.
    """
    average = sum(gains) / len(gains)
    return [g - average for g in gains]


def candidates(current: list[float], group: range, step: float):
    """The two options for one comparison: this band group up, and down."""
    def shifted(direction):
        moved = list(current)
        for index in group:
            moved[index] += direction * step
        return level_matched(moved)

    return shifted(+1), shifted(-1)


class SoundCheckWindow(NSObject):
    def init(self):
        self = objc.super(SoundCheckWindow, self).init()
        if self is None:
            return None
        self.current = [0.0] * len(equaliser.bands())
        self.round = 0
        self.step = START_STEP_DB
        self.option_a = None
        self.option_b = None

        rect = NSMakeRect(0, 0, 460, 240)
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, NSWindowStyleMaskTitled | NSWindowStyleMaskClosable, NSBackingStoreBuffered, False
        )
        self.window.setTitle_("Sound Check")
        self.window.setReleasedWhenClosed_(False)
        content = NSView.alloc().initWithFrame_(rect)

        self.heading = self._text(content, NSMakeRect(24, 186, 412, 24), 15, True)
        self.detail = self._text(content, NSMakeRect(24, 150, 412, 34), 11, False)

        self._button(content, NSMakeRect(24, 96, 200, 32), "Listen to A", "playA:")
        self._button(content, NSMakeRect(236, 96, 200, 32), "Listen to B", "playB:")
        self._button(content, NSMakeRect(24, 56, 200, 32), "A sounds better", "chooseA:")
        self._button(content, NSMakeRect(236, 56, 200, 32), "B sounds better", "chooseB:")
        self._button(content, NSMakeRect(24, 16, 200, 28), "No preference", "skip:")
        self._button(content, NSMakeRect(236, 16, 200, 28), "Finish now", "finish:")

        self.window.setContentView_(content)
        self.window.center()
        self._next_round()
        return self

    @objc.python_method
    def _text(self, parent, frame, size, bold):
        field = NSTextField.alloc().initWithFrame_(frame)
        field.setBezeled_(False)
        field.setDrawsBackground_(False)
        field.setEditable_(False)
        field.setSelectable_(False)
        field.setFont_(
            NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size)
        )
        parent.addSubview_(field)
        return field

    @objc.python_method
    def _button(self, parent, frame, title, action):
        button = NSButton.alloc().initWithFrame_(frame)
        button.setTitle_(title)
        button.setBezelStyle_(1)
        button.setTarget_(self)
        button.setAction_(action)
        parent.addSubview_(button)
        return button

    @objc.python_method
    def _total_rounds(self):
        return len(DIMENSIONS) * CYCLES

    @objc.python_method
    def _next_round(self):
        if self.round >= self._total_rounds():
            self._apply(self.current)
            self.heading.setStringValue_("Done")
            self.detail.setStringValue_(
                "Your curve is applied. Save it from the menu bar: Profiles → Save current as…"
            )
            return
        name, group = DIMENSIONS[self.round % len(DIMENSIONS)]
        self.step = START_STEP_DB / (2 ** (self.round // len(DIMENSIONS)))
        self.option_a, self.option_b = candidates(self.current, group, self.step)
        self.heading.setStringValue_(
            f"Comparison {self.round + 1} of {self._total_rounds()} — {name}"
        )
        self.detail.setStringValue_(
            "Play music, listen to each, and pick the one you prefer. "
            "They are matched for loudness, so judge tone only."
        )
        self._apply(self.option_a)

    @objc.python_method
    def _apply(self, gains):
        try:
            daemon.set_curve(gains)
        except Exception:  # noqa: BLE001
            pass

    @objc.python_method
    def _choose(self, winner):
        self.current = level_matched(winner)
        self.round += 1
        self._next_round()

    @objc.IBAction
    def playA_(self, sender):
        self._apply(self.option_a)

    @objc.IBAction
    def playB_(self, sender):
        self._apply(self.option_b)

    @objc.IBAction
    def chooseA_(self, sender):
        self._choose(self.option_a)

    @objc.IBAction
    def chooseB_(self, sender):
        self._choose(self.option_b)

    @objc.IBAction
    def skip_(self, sender):
        # Neither was better: keep what we had and move on.
        self.round += 1
        self._next_round()

    @objc.IBAction
    def finish_(self, sender):
        self.round = self._total_rounds()
        self._next_round()

    @objc.python_method
    def show(self):
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)


def show_equaliser_window():
    if "equaliser" not in _open_windows:
        _open_windows["equaliser"] = EqualiserWindow.alloc().init()
    _open_windows["equaliser"].show()


def show_soundcheck():
    _open_windows["soundcheck"] = SoundCheckWindow.alloc().init()
    _open_windows["soundcheck"].show()
