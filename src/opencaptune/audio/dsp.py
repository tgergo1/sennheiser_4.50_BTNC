"""The equaliser as a real-time filter.

A cascade of peaking biquads, one per band, run as second-order sections.  The
filter state is carried between blocks so the audio is continuous, and it is
preserved across preset changes wherever the section count allows, which keeps
switching presets from clicking.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import sosfilt

from .. import eq


def sections_for(preset: eq.Preset, sample_rate: int, q: float | None = None) -> np.ndarray:
    """Second-order sections for a preset, in SciPy's ``sos`` layout."""
    chain = eq.filter_chain(preset, sample_rate, q)
    if not chain:
        return np.zeros((0, 6), dtype=np.float64)
    return np.array(
        [[b0, b1, b2, 1.0, a1, a2] for (b0, b1, b2), (_, a1, a2) in chain],
        dtype=np.float64,
    )


class Equaliser:
    """Stateful, block-based equaliser for interleaved float32 audio."""

    def __init__(
        self,
        preset: eq.Preset,
        sample_rate: int,
        channels: int = 2,
        q: float | None = None,
        auto_headroom: bool = True,
    ) -> None:
        if channels < 1:
            raise ValueError("an equaliser needs at least one channel")
        self.sample_rate = sample_rate
        self.channels = channels
        self._q = q
        self._auto_headroom = auto_headroom
        self._state = np.zeros((0, 2, channels))
        self.set_preset(preset)

    @property
    def preset(self) -> eq.Preset:
        return self._preset

    @property
    def preamp_db(self) -> float:
        return self._preamp_db

    def set_preset(self, preset: eq.Preset) -> None:
        """Swap the curve, keeping filter state where the shape still matches."""
        sections = sections_for(preset, self.sample_rate, self._q)
        if sections.shape[0] != self._state.shape[0]:
            self._state = np.zeros((sections.shape[0], 2, self.channels))
        self._sections = sections
        self._preset = preset
        self._preamp_db = (
            eq.preamp_db(preset, self.sample_rate, self._q) if self._auto_headroom else 0.0
        )
        self._preamp = 10.0 ** (self._preamp_db / 20.0)

    def reset(self) -> None:
        """Clear filter memory, e.g. after a gap in the audio."""
        self._state = np.zeros_like(self._state)

    def process(self, block: np.ndarray) -> np.ndarray:
        """Filter one block of shape (frames, channels)."""
        if block.ndim != 2 or block.shape[1] != self.channels:
            raise ValueError(
                f"expected a block of shape (frames, {self.channels}), got {block.shape}"
            )
        if self._sections.shape[0] == 0:
            return (block * self._preamp).astype(np.float32, copy=False)

        filtered, self._state = sosfilt(
            self._sections, block * self._preamp, axis=0, zi=self._state
        )
        return filtered.astype(np.float32, copy=False)
