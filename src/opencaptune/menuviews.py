"""Custom views for the menu bar app.

An NSMenu is not limited to rows of text: any item can host a view. That is
worth using here, because the two things this app should show — what it is
doing to the sound, and how loud that sound currently is — are both much
clearer drawn than written.
"""

from __future__ import annotations

import objc
from AppKit import (
    NSAttributedString,
    NSBezierPath,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMakePoint,
    NSMakeRect,
    NSSlider,
    NSTextField,
    NSView,
)

METER_FLOOR_DB = -66.0
WIDTH = 300
CAPTION_WIDTH = 124


def _text(value, size, colour, bold=False):
    font = NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size)
    return NSAttributedString.alloc().initWithString_attributes_(
        value, {NSFontAttributeName: font, NSForegroundColorAttributeName: colour}
    )


class HeaderView(NSView):
    """Title, subtitle, and a live spectrum of what is playing."""

    def initWithFrame_(self, frame):
        self = objc.super(HeaderView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.title = "…"
        self.subtitle = ""
        self.spectrum = []
        self.active = False
        return self

    @objc.python_method
    def update(self, title, subtitle, spectrum, active):
        self.title = title
        self.subtitle = subtitle
        self.spectrum = spectrum or []
        self.active = active
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        bounds = self.bounds()
        width, height = bounds.size.width, bounds.size.height

        _text(self.title, 13, NSColor.labelColor(), bold=True).drawAtPoint_(
            NSMakePoint(14, height - 22)
        )
        if self.subtitle:
            _text(self.subtitle, 11, NSColor.secondaryLabelColor()).drawAtPoint_(
                NSMakePoint(14, height - 38)
            )

        # The spectrum sits along the bottom as a row of slim bars. Drawn even
        # when idle, flat, so the row does not appear and disappear.
        bars = self.spectrum if self.spectrum else [METER_FLOOR_DB] * 14
        left, right = 14.0, width - 14.0
        span = right - left
        slot = span / len(bars)
        base = 10.0
        top = height - 52.0
        for index, level in enumerate(bars):
            fraction = max(0.0, min(1.0, (level - METER_FLOOR_DB) / -METER_FLOOR_DB))
            bar = max(1.5, fraction * (top - base))
            colour = (
                NSColor.controlAccentColor() if self.active else NSColor.tertiaryLabelColor()
            )
            colour.colorWithAlphaComponent_(0.85 if fraction > 0.02 else 0.25).set()
            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(left + index * slot + 1, base, slot - 3, bar), 1.5, 1.5
            )
            path.fill()


class SliderRow(NSView):
    """A labelled slider, live-updating, for use as a menu item's view."""

    def initWithFrame_(self, frame):
        self = objc.super(SliderRow, self).initWithFrame_(frame)
        if self is None:
            return None
        self._handler = None
        self._format = "{:.0f}"
        self.caption = ""

        self.slider = NSSlider.alloc().initWithFrame_(
            NSMakeRect(CAPTION_WIDTH, 4, WIDTH - CAPTION_WIDTH - 58, 20)
        )
        self.slider.setTarget_(self)
        self.slider.setAction_("changed:")
        self.slider.setContinuous_(True)
        self.addSubview_(self.slider)

        self.readout = NSTextField.alloc().initWithFrame_(NSMakeRect(WIDTH - 52, 4, 46, 18))
        self.readout.setBezeled_(False)
        self.readout.setDrawsBackground_(False)
        self.readout.setEditable_(False)
        self.readout.setSelectable_(False)
        self.readout.setAlignment_(2)  # right
        # Monospaced digits keep the readout from jittering as it counts.
        monospaced = getattr(NSFont, "monospacedDigitSystemFontOfSize_weight_", None)
        self.readout.setFont_(
            monospaced(11, 0) if monospaced else NSFont.systemFontOfSize_(11)
        )
        self.readout.setTextColor_(NSColor.secondaryLabelColor())
        self.addSubview_(self.readout)
        return self

    @objc.python_method
    def configure(self, caption, minimum, maximum, handler, formatter="{:.0f}"):
        self.caption = caption
        self.slider.setMinValue_(minimum)
        self.slider.setMaxValue_(maximum)
        self._handler = handler
        self._format = formatter
        return self

    @objc.python_method
    def set_value(self, value, enabled=True):
        # Never fight the user: leave the knob alone while it is being dragged.
        if not self.slider.isHighlighted():
            self.slider.setDoubleValue_(value)
        self.slider.setEnabled_(enabled)
        self.readout.setStringValue_(self._format.format(value) if enabled else "—")
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        _text(self.caption, 12, NSColor.labelColor()).drawAtPoint_(NSMakePoint(14, 6))

    @objc.IBAction
    def changed_(self, sender):
        value = sender.doubleValue()
        self.readout.setStringValue_(self._format.format(value))
        if self._handler is not None:
            self._handler(value)
