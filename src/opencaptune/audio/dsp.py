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
        fade_ms: float = 20.0,
    ) -> None:
        if channels < 1:
            raise ValueError("an equaliser needs at least one channel")
        self.sample_rate = sample_rate
        self.channels = channels
        self._q = q
        self._auto_headroom = auto_headroom
        self._fade_frames = max(1, int(sample_rate * fade_ms / 1000.0))
        self._state = np.zeros((0, 2, channels))
        self._fade = None
        self.set_preset(preset, fade=False)

    @property
    def preset(self) -> eq.Preset:
        return self._preset

    @property
    def preamp_db(self) -> float:
        return self._preamp_db

    def set_preset(self, preset: eq.Preset, fade: bool = True) -> None:
        """Swap the curve, crossfading from the old one so it does not click.

        Changing coefficients under a running filter leaves its state in the
        wrong terms for the new curve, and a change in section count discards
        the state entirely. Either way the output steps. So the old filter is
        kept alive for a few milliseconds and the two are mixed.
        """
        previous = (
            {"sections": self._sections, "state": self._state, "preamp": self._preamp}
            if fade and self._fade is None
            else None
        )

        sections = sections_for(preset, self.sample_rate, self._q)
        if sections.shape[0] != self._state.shape[0]:
            self._state = np.zeros((sections.shape[0], 2, self.channels))
        elif previous is not None:
            # The outgoing filter needs its own copy to keep running.
            self._state = self._state.copy()
        self._sections = sections
        self._preset = preset
        self._preamp_db = (
            eq.preamp_db(preset, self.sample_rate, self._q) if self._auto_headroom else 0.0
        )
        self._preamp = 10.0 ** (self._preamp_db / 20.0)

        if previous is not None:
            self._fade = {**previous, "done": 0}

    def reset(self) -> None:
        """Clear filter memory, e.g. after a gap in the audio."""
        self._state = np.zeros_like(self._state)
        self._fade = None

    @property
    def fading(self) -> bool:
        return self._fade is not None

    def _run(self, sections, state, preamp, block):
        """Apply one filter, returning its output and its advanced state."""
        scaled = block * preamp
        if sections.shape[0] == 0:
            return scaled, state
        return sosfilt(sections, scaled, axis=0, zi=state)

    def process(self, block: np.ndarray) -> np.ndarray:
        """Filter one block of shape (frames, channels)."""
        if block.ndim != 2 or block.shape[1] != self.channels:
            raise ValueError(
                f"expected a block of shape (frames, {self.channels}), got {block.shape}"
            )
        incoming, self._state = self._run(self._sections, self._state, self._preamp, block)

        if self._fade is not None:
            outgoing, self._fade["state"] = self._run(
                self._fade["sections"], self._fade["state"], self._fade["preamp"], block
            )
            done = self._fade["done"]
            position = np.arange(done, done + len(block))
            ramp = np.clip(position / self._fade_frames, 0.0, 1.0)[:, None]
            # Linear, not equal-power: both sides are the same audio through
            # different filters, so they are strongly correlated. An
            # equal-power mix of correlated signals peaks at sqrt(2) and
            # overshoots; a linear mix stays between the two.
            incoming = incoming * ramp + outgoing * (1.0 - ramp)
            self._fade["done"] = done + len(block)
            if self._fade["done"] >= self._fade_frames:
                self._fade = None

        return incoming.astype(np.float32, copy=False)
