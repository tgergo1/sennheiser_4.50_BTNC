"""Speaker virtualisation: move the image out of your head.

Headphones put each channel directly into one ear, which never happens in life
and is why stereo on headphones sits between your ears rather than in front of
you. With real speakers each ear also hears the *other* one, arriving slightly
later because it travelled further, and duller because your head was in the
way. Those two cues — the delay and the shadow — are most of what tells you a
sound is out there.

This puts both back. A pair of virtual speakers at ±30°: each ear gets its own
channel directly, plus a delayed and dulled copy of the other.

It is a structural model, not a measured head-related transfer function. The
delay comes from Woodworth's formula for a spherical head and the shadow from
a shelf filter; there is no pinna modelling and no elevation. A measured HRTF
would be more accurate and would need a dataset, a listener it was measured on,
and far more machinery. This is the part that does most of the work.

Unlike :mod:`opencaptune.audio.crossfeed`, which only narrows the image at low
frequencies and leaves mono untouched, this deliberately changes mono too —
because two speakers in a room change mono at your ears as well.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.signal import sosfilt

from .. import eq

#: Radius of an average human head, in metres.
HEAD_RADIUS = 0.0875
SPEED_OF_SOUND = 343.0

#: Where the virtual speakers sit, in degrees off centre.
DEFAULT_ANGLE = 30.0

#: How much of the opposite channel reaches each ear at full strength.
MAX_FEED = 0.5

#: The head shadow: the crossed path loses its highs.
SHADOW_CORNER_HZ = 900.0
SHADOW_DB = -9.0


def interaural_delay(angle_degrees: float = DEFAULT_ANGLE) -> float:
    """Extra time the far ear waits, in seconds (Woodworth's formula).

    About 0.26 ms at 30°, which matches the textbook figure for a source that
    far off centre.
    """
    angle = math.radians(min(90.0, abs(angle_degrees)))
    return (HEAD_RADIUS / SPEED_OF_SOUND) * (angle + math.sin(angle))


class Virtualiser:
    """Renders stereo as two virtual speakers in front of the listener."""

    def __init__(
        self,
        strength: int = 0,
        sample_rate: int = 48000,
        channels: int = 2,
        angle_degrees: float = DEFAULT_ANGLE,
    ) -> None:
        if not 0 <= strength <= 100:
            raise ValueError("spatial strength is a percentage in the range 0-100")
        self.sample_rate = sample_rate
        self.channels = channels
        self.angle_degrees = angle_degrees
        self.delay_samples = max(1, round(interaural_delay(angle_degrees) * sample_rate))

        numerator, denominator = eq.shelf_coefficients(
            eq.HIGH_SHELF, SHADOW_CORNER_HZ, SHADOW_DB, sample_rate, q=0.707
        )
        b0, b1, b2 = numerator
        _, a1, a2 = denominator
        self._shadow = np.array([[b0, b1, b2, 1.0, a1, a2]])

        self._tail = np.zeros((self.delay_samples, channels), dtype=np.float32)
        self._state = np.zeros((1, 2, channels))
        self.set_strength(strength)

    @property
    def strength(self) -> int:
        return self._strength

    @property
    def feed(self) -> float:
        """Level of the crossed path, 0 to :data:`MAX_FEED`."""
        return MAX_FEED * self._strength / 100.0

    @property
    def active(self) -> bool:
        return self._strength > 0 and self.channels == 2

    def set_strength(self, strength: int) -> None:
        if not 0 <= strength <= 100:
            raise ValueError("spatial strength is a percentage in the range 0-100")
        self._strength = strength

    def reset(self) -> None:
        self._tail = np.zeros_like(self._tail)
        self._state = np.zeros_like(self._state)

    def process(self, block: np.ndarray) -> np.ndarray:
        if not self.active:
            return block

        frames = len(block)
        # The crossed path is the *other* channel, delayed by the extra
        # distance around the head.
        padded = np.concatenate([self._tail, block], axis=0)
        delayed = padded[:frames]
        self._tail = padded[frames:].copy()

        shadowed, self._state = sosfilt(self._shadow, delayed, axis=0, zi=self._state)

        feed = self.feed
        out = np.empty_like(block)
        out[:, 0] = block[:, 0] + feed * shadowed[:, 1]
        out[:, 1] = block[:, 1] + feed * shadowed[:, 0]
        # Both ears now receive more energy than they started with; normalise so
        # centred content keeps its level instead of growing with strength.
        return (out / (1.0 + feed)).astype(np.float32, copy=False)
