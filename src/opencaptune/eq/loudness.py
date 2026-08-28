"""Loudness compensation from the ISO 226 equal-loudness contours.

Hearing is not equally sensitive at all frequencies, and the shape of that
insensitivity changes with level: quiet music genuinely has less audible bass
and treble, not as a matter of taste but of physiology. Music is mixed at one
level and usually played back at another, so the tonal balance you get is not
the one that was intended.

CapTune's "Loudness" preset is a fixed curve, which is the same correction no
matter how loud you are actually listening — right at exactly one level and
wrong everywhere else. This computes the correction for the level you are at,
as the difference between two equal-loudness contours.

The contours come from ISO 226:2003, whose analytical formula gives the sound
pressure needed at each frequency to match a reference tone at 1 kHz.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from importlib import resources

from . import MAX_GAIN_DB, MIN_GAIN_DB, PEAKING, Filter, bands, default_q

#: ISO 226:2003 defines its contours over this range only.
MIN_PHON = 0.0
MAX_PHON = 90.0

#: The level music is assumed to have been balanced at.
DEFAULT_REFERENCE_PHON = 80.0


@lru_cache(maxsize=1)
def _tables() -> tuple[tuple[float, ...], ...]:
    data = json.loads(resources.files(__package__).joinpath("iso226.json").read_text())
    return (
        tuple(data["frequency"]),
        tuple(data["alpha_f"]),
        tuple(data["L_U"]),
        tuple(data["T_f"]),
    )


def frequencies() -> tuple[float, ...]:
    """The frequencies ISO 226 tabulates, 20 Hz to 12.5 kHz."""
    return _tables()[0]


def contour(phon: float) -> tuple[float, ...]:
    """Sound pressure level at each tabulated frequency for one loudness level.

    This is the ISO 226:2003 clause 4.1 formula. By definition the result at
    1 kHz equals ``phon``.
    """
    if not MIN_PHON <= phon <= MAX_PHON:
        raise ValueError(
            f"ISO 226 defines contours from {MIN_PHON:.0f} to {MAX_PHON:.0f} phon; got {phon}"
        )
    _, alpha_f, l_u, t_f = _tables()
    levels = []
    for alpha, offset, threshold in zip(alpha_f, l_u, t_f):
        a_f = 4.47e-3 * (10.0 ** (0.025 * phon) - 1.15) + (
            0.4 * 10.0 ** ((threshold + offset) / 10.0 - 9.0)
        ) ** alpha
        levels.append((10.0 / alpha) * math.log10(a_f) - offset + 94.0)
    return tuple(levels)


def _relative_to_1k(levels: tuple[float, ...]) -> list[float]:
    """A contour's shape, with its 1 kHz point moved to zero."""
    index = frequencies().index(1000.0)
    return [level - levels[index] for level in levels]


def correction_db(
    playback_phon: float, reference_phon: float = DEFAULT_REFERENCE_PHON
) -> list[tuple[float, float]]:
    """The gain needed at each tabulated frequency, as (frequency, dB).

    Positive below and above the midrange when playing quieter than the
    reference, which is the familiar bass-and-treble lift. Zero everywhere when
    the two levels match.
    """
    playback = _relative_to_1k(contour(playback_phon))
    reference = _relative_to_1k(contour(reference_phon))
    return [
        (frequency, quiet - loud)
        for frequency, quiet, loud in zip(frequencies(), playback, reference)
    ]


def _interpolate(curve: list[tuple[float, float]], frequency: float) -> float:
    """Linear interpolation in log frequency, holding the ends flat.

    Holding matters at the top: ISO 226 stops at 12.5 kHz but the equaliser has
    bands above it, and extrapolating a curve that steep would invent numbers.
    """
    if frequency <= curve[0][0]:
        return curve[0][1]
    if frequency >= curve[-1][0]:
        return curve[-1][1]
    for (low_f, low_g), (high_f, high_g) in zip(curve, curve[1:]):
        if low_f <= frequency <= high_f:
            span = math.log(high_f / low_f)
            position = math.log(frequency / low_f) / span
            return low_g + position * (high_g - low_g)
    return curve[-1][1]


def compensation(
    playback_phon: float,
    reference_phon: float = DEFAULT_REFERENCE_PHON,
    q: float | None = None,
) -> tuple[Filter, ...]:
    """Filters that restore the reference tonal balance at a quieter level.

    Sampled onto the equaliser's own band centres, and clipped to the
    equaliser's gain range — at very low levels the true correction exceeds
    what the filters can deliver.
    """
    curve = correction_db(playback_phon, reference_phon)
    width = default_q() if q is None else q
    sections = []
    for frequency in bands():
        gain = _interpolate(curve, float(frequency))
        gain = min(MAX_GAIN_DB, max(MIN_GAIN_DB, gain))
        if abs(gain) < 0.05:
            continue
        sections.append(Filter(PEAKING, float(frequency), gain, width))
    return tuple(sections)
