"""Crossfeed: stop hard-panned mixes sitting inside your head.

On speakers each ear hears both channels, the far one attenuated, delayed and
dulled by the head. On headphones each ear hears only its own channel, which is
a cue the brain never gets naturally, and hard-panned material ends up
localised inside the skull rather than out in front. Crossfeed puts some of
that missing path back.

The usual construction mixes a low-passed copy of each channel into the other.
That works, but it sums correlated content: mono material gains bass and level,
so the design then needs a compensating shelf to undo damage it just did.

This works on mid and side instead. Low frequencies carry the interaural cues
that headphones render unnaturally, so the side signal is shelved down below a
corner frequency while mid is left strictly alone. Mono content is therefore
bit-identical, no compensation is needed, and the stereo image stays intact
above the corner where it belongs.

It does omit the interaural delay of a full head-related model. That trade buys
exactness: there is nothing here that needs correcting afterwards.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import sosfilt

from .. import eq

#: Side-channel attenuation at full strength. Crossfeed designs in the wild sit
#: between about 4.5 dB (bs2b's default) and 9.5 dB (Meier); 10 dB at strength
#: 100 puts the usual range comfortably inside the slider.
MAX_DEPTH_DB = 10.0
DEFAULT_CORNER_HZ = 700.0


class Crossfeed:
    """Narrows the stereo image below a corner frequency, leaving mono alone."""

    def __init__(
        self,
        strength: int = 0,
        sample_rate: int = 48000,
        channels: int = 2,
        corner_hz: float = DEFAULT_CORNER_HZ,
    ) -> None:
        if not 0 <= strength <= 100:
            raise ValueError("crossfeed strength is a percentage in the range 0-100")
        if corner_hz <= 0 or corner_hz >= sample_rate / 2:
            raise ValueError(
                f"corner frequency {corner_hz} Hz is not below the Nyquist limit "
                f"of {sample_rate / 2} Hz"
            )
        self.sample_rate = sample_rate
        self.channels = channels
        self.corner_hz = corner_hz
        self._state = np.zeros((1, 2, 1))
        self.set_strength(strength)

    @property
    def strength(self) -> int:
        return self._strength

    @property
    def depth_db(self) -> float:
        """How far the side channel is pulled down below the corner."""
        return -MAX_DEPTH_DB * self._strength / 100.0

    @property
    def active(self) -> bool:
        # Mono has no side channel to work on, so there is nothing to do.
        return self._strength > 0 and self.channels == 2

    def set_strength(self, strength: int) -> None:
        if not 0 <= strength <= 100:
            raise ValueError("crossfeed strength is a percentage in the range 0-100")
        self._strength = strength
        if strength == 0:
            self._sections = np.zeros((0, 6))
            return
        numerator, denominator = eq.shelf_coefficients(
            eq.LOW_SHELF, self.corner_hz, self.depth_db, self.sample_rate, q=0.707
        )
        b0, b1, b2 = numerator
        _, a1, a2 = denominator
        self._sections = np.array([[b0, b1, b2, 1.0, a1, a2]])
        self._state = np.zeros((1, 2, 1))

    def reset(self) -> None:
        self._state = np.zeros_like(self._state)

    def process(self, block: np.ndarray) -> np.ndarray:
        """Filter one block of shape (frames, channels)."""
        if not self.active:
            return block

        left, right = block[:, 0], block[:, 1]
        mid = (left + right) * 0.5
        side = (left - right) * 0.5

        shelved, self._state = sosfilt(
            self._sections, side.reshape(-1, 1), axis=0, zi=self._state
        )
        shelved = shelved[:, 0]

        out = np.empty_like(block)
        out[:, 0] = mid + shelved
        out[:, 1] = mid - shelved
        return out
