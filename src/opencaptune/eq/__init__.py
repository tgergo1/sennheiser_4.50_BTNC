"""CapTune's equaliser, reimplemented from the data the app shipped.

CapTune applied its equaliser on the phone, to its own playback, through a
closed native DSP library.  The *curves* were plain data, so they survive: the
band centres and preset gains here are lifted verbatim from the app's own
``presetsdefault.json`` and its bass/treble offset tables.

The filter design is ours, since the original DSP is a binary blob.  Fourteen
bands span 35 Hz to 17.6 kHz at a constant ratio of about 1.613, which is
0.69 octaves per band; the default Q follows from that spacing.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

MIN_GAIN_DB = -12.0
MAX_GAIN_DB = 12.0


@dataclass(frozen=True)
class Preset:
    name: str
    gains_db: tuple[float, ...]

    def with_boosts(self, bass: int = 0, treble: int = 0) -> "Preset":
        """Apply CapTune's bass and treble boost sliders, each 0-100.

        The app added a fixed offset curve scaled by the slider percentage, then
        clamped to the equaliser's range — reproduced here exactly.
        """
        if not 0 <= bass <= 100 or not 0 <= treble <= 100:
            raise ValueError("boost strengths are percentages in the range 0-100")
        offsets = boost_offsets()
        gains = [
            min(
                MAX_GAIN_DB,
                max(
                    MIN_GAIN_DB,
                    gain
                    + offsets["bass"][index] * (bass / 100.0)
                    + offsets["treble"][index] * (treble / 100.0),
                ),
            )
            for index, gain in enumerate(self.gains_db)
        ]
        suffix = []
        if bass:
            suffix.append(f"bass +{bass}%")
        if treble:
            suffix.append(f"treble +{treble}%")
        name = f"{self.name} ({', '.join(suffix)})" if suffix else self.name
        return Preset(name=name, gains_db=tuple(gains))


@lru_cache(maxsize=1)
def _data() -> dict:
    text = resources.files(__package__).joinpath("presets.json").read_text()
    return json.loads(text)


def bands() -> tuple[int, ...]:
    """Band centre frequencies in Hz."""
    return tuple(_data()["bands_hz"])


def boost_offsets() -> dict[str, list[float]]:
    return _data()["boost_offsets_db"]


@lru_cache(maxsize=1)
def presets() -> dict[str, Preset]:
    return {
        entry["name"]: Preset(name=entry["name"], gains_db=tuple(entry["gains_db"]))
        for entry in _data()["presets"]
    }


def preset(name: str) -> Preset:
    """Look a preset up by name, case-insensitively."""
    available = presets()
    for candidate, value in available.items():
        if candidate.lower() == name.lower():
            return value
    raise KeyError(f"unknown preset {name!r}; available: {', '.join(available)}")


def band_width_octaves() -> float:
    """Spacing between adjacent bands, in octaves, from the band centres."""
    centres = bands()
    ratios = [
        math.log2(high / low) for low, high in zip(centres, centres[1:])
    ]
    return sum(ratios) / len(ratios)


def default_q() -> float:
    """Q that makes adjacent peaking filters meet cleanly at their -3 dB points."""
    span = 2 ** band_width_octaves()
    return math.sqrt(span) / (span - 1)


def peaking_coefficients(
    frequency: float, gain_db: float, sample_rate: int, q: float | None = None
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """RBJ peaking EQ biquad, returned as normalised (b0, b1, b2), (1, a1, a2)."""
    if frequency <= 0 or frequency >= sample_rate / 2:
        raise ValueError(
            f"centre frequency {frequency} Hz is not below the Nyquist limit "
            f"of {sample_rate / 2} Hz"
        )
    if q is None:
        q = default_q()

    amplitude = 10 ** (gain_db / 40.0)
    omega = 2.0 * math.pi * frequency / sample_rate
    alpha = math.sin(omega) / (2.0 * q)
    cosine = math.cos(omega)

    b0 = 1.0 + alpha * amplitude
    b1 = -2.0 * cosine
    b2 = 1.0 - alpha * amplitude
    a0 = 1.0 + alpha / amplitude
    a1 = -2.0 * cosine
    a2 = 1.0 - alpha / amplitude

    return (b0 / a0, b1 / a0, b2 / a0), (1.0, a1 / a0, a2 / a0)


def response_db(
    values: Preset, frequency: float, sample_rate: int = 48000, q: float | None = None
) -> float:
    """Magnitude response of the whole cascade at one frequency, in dB."""
    omega = 2.0 * math.pi * frequency / sample_rate
    z = complex(math.cos(omega), math.sin(omega))
    total = complex(1.0, 0.0)
    for (b0, b1, b2), (_, a1, a2) in filter_chain(values, sample_rate, q):
        total *= (b0 + b1 / z + b2 / z**2) / (1.0 + a1 / z + a2 / z**2)
    return 20.0 * math.log10(abs(total)) if abs(total) > 0 else -math.inf


def preamp_db(
    values: Preset, sample_rate: int = 48000, q: float | None = None, points: int = 1024
) -> float:
    """Attenuation that stops a boosted curve clipping a full-scale signal.

    Not simply the largest band gain: neighbouring peaking sections overlap, so
    two adjacent boosts sum to more than either alone. This measures the actual
    peak of the cascade across the spectrum instead.
    """
    chain = filter_chain(values, sample_rate, q)
    if not chain:
        return 0.0
    lowest, highest = 10.0, min(sample_rate / 2.0 * 0.999, 22000.0)
    step = (math.log10(highest) - math.log10(lowest)) / (points - 1)
    peak = max(
        response_db(values, 10.0 ** (math.log10(lowest) + step * index), sample_rate, q)
        for index in range(points)
    )
    return -max(0.0, peak)


def filter_chain(
    values: Preset, sample_rate: int, q: float | None = None
) -> list[tuple[tuple[float, float, float], tuple[float, float, float]]]:
    """Biquad sections for a preset, skipping bands above the Nyquist limit."""
    sections = []
    for frequency, gain in zip(bands(), values.gains_db):
        if frequency >= sample_rate / 2:
            continue
        if gain == 0.0:
            continue
        sections.append(peaking_coefficients(frequency, gain, sample_rate, q))
    return sections
