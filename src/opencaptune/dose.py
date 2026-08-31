"""Listening exposure, tracked the way hearing damage actually works.

Damage depends on loudness *and* time together, not on either alone. The
occupational standard treats 80 dB(A) for 40 hours a week as a full dose, and
trades 3 dB against half the time: 83 dB for 20 hours is the same dose, 86 dB
for 10 hours the same again. An hour of something loud can cost more than a
week of something quiet.

So this accumulates energy rather than sampling loudness, in hourly buckets
over a rolling week.

**On the accuracy of the number.** We know the signal level exactly and the
sound pressure at your ear not at all — that depends on the headphone's
sensitivity and on the amplifier behind it, and nothing reports either. The
figure here is therefore an estimate resting on one assumption, stated as
:data:`FULL_SCALE_SPL`: what full-scale audio comes to in dB SPL at these
headphones at full volume. Calibrate it against a sound level meter and the
number becomes real; leave it and the trend is still honest even if the
absolute value is not.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

#: A full weekly dose: 80 dB(A) for 40 hours.
REFERENCE_DB = 80.0
REFERENCE_HOURS = 40.0

#: Halving the time for every 3 dB is the exchange rate the standards use.
EXCHANGE_RATE_DB = 3.0

#: Assumed sound pressure of a full-scale signal at full volume. A guess, and
#: the single thing to calibrate if you want the absolute figure to mean
#: something. 100 dB is typical for a closed-back headphone driven hard.
FULL_SCALE_SPL = 100.0

WINDOW_HOURS = 24 * 7


@dataclass(frozen=True)
class Exposure:
    """A week of listening, summarised."""

    hours: float
    average_db: float
    dose_fraction: float
    #: The loudest interval recorded, not the loudest hour: a short burst
    #: averaged over an hour disappears, and it is the burst that matters.
    loudest_db: float

    @property
    def percentage(self) -> float:
        return self.dose_fraction * 100.0

    @property
    def is_over(self) -> bool:
        return self.dose_fraction >= 1.0

    def allowance_hours_at(self, level_db: float) -> float:
        """How long a full dose would take at some level."""
        return REFERENCE_HOURS / (2 ** ((level_db - REFERENCE_DB) / EXCHANGE_RATE_DB))


def path() -> Path:
    return Path.home() / "Library" / "Application Support" / "OpenCapTune" / "exposure.json"


def _now_bucket() -> int:
    return int(time.time() // 3600)


def _load() -> dict[int, list[float]]:
    location = path()
    if not location.exists():
        return {}
    try:
        raw = json.loads(location.read_text())["hours"]
    except (json.JSONDecodeError, KeyError, OSError, TypeError):
        return {}
    buckets = {}
    for key, value in raw.items():
        try:
            # Energy and seconds, plus the loudest single interval within the
            # hour — an average over a whole hour hides a short loud burst,
            # which is exactly the thing worth seeing.
            peak = float(value[2]) if len(value) > 2 else 0.0
            buckets[int(key)] = [float(value[0]), float(value[1]), peak]
        except (TypeError, ValueError, IndexError):
            continue
    return buckets


def _save(buckets: dict[int, list[float]]) -> None:
    cutoff = _now_bucket() - WINDOW_HOURS
    kept = {str(k): v for k, v in buckets.items() if k > cutoff}
    location = path()
    location.parent.mkdir(parents=True, exist_ok=True)
    location.write_text(json.dumps({"hours": kept}))


def record(energy: float, seconds: float) -> None:
    """Add listening time.

    ``energy`` is the summed mean square of the audio over ``seconds``, which
    is what the engine already computes; keeping it as energy rather than a
    level is what makes the buckets addable.
    """
    if seconds <= 0 or energy < 0:
        return
    buckets = _load()
    bucket = _now_bucket()
    existing = buckets.get(bucket, [0.0, 0.0, 0.0])
    buckets[bucket] = [
        existing[0] + energy,
        existing[1] + seconds,
        max(existing[2], energy / seconds),
    ]
    _save(buckets)


def _to_spl(mean_square: float) -> float:
    if mean_square <= 0:
        return -math.inf
    return 10.0 * math.log10(mean_square) + FULL_SCALE_SPL


def summary() -> Exposure:
    """Exposure over the last seven days."""
    buckets = _load()
    cutoff = _now_bucket() - WINDOW_HOURS
    recent = [v for k, v in buckets.items() if k > cutoff]
    total_energy = sum(v[0] for v in recent)
    total_seconds = sum(v[1] for v in recent)
    if total_seconds <= 0:
        return Exposure(hours=0.0, average_db=-math.inf, dose_fraction=0.0, loudest_db=-math.inf)

    average = _to_spl(total_energy / total_seconds)
    loudest = max(
        (_to_spl(v[2]) for v in recent if len(v) > 2 and v[2] > 0), default=-math.inf
    )
    hours = total_seconds / 3600.0

    # Dose is time weighted by level against the reference, doubling per 3 dB.
    allowed = REFERENCE_HOURS / (2 ** ((average - REFERENCE_DB) / EXCHANGE_RATE_DB))
    return Exposure(
        hours=hours,
        average_db=average,
        dose_fraction=hours / allowed if allowed > 0 else float("inf"),
        loudest_db=loudest,
    )


def reset() -> None:
    path().unlink(missing_ok=True)
