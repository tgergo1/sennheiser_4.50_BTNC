"""Named setups: a whole signal chain saved under one name.

CapTune called these Sound Profiles and stored a device alongside the curve,
which is the useful part — settings that suit headphones are wrong for
speakers, and the device is what tells them apart. A profile here captures
everything the engine needs, so applying one is a single action from the menu
bar rather than four.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from . import eq as equaliser

DEFAULT_INPUT = "BlackHole 2ch"


@dataclass(frozen=True)
class Profile:
    name: str
    preset: str = "Neutral"
    calibration: str | None = None
    crossfeed: int = 0
    loudness_phon: float | None = None
    bass: int = 0
    treble: int = 0
    output_device: str | None = None
    capture: str = "auto"
    input_device: str | None = DEFAULT_INPUT

    def validate(self) -> None:
        """Fail loudly at save time rather than quietly at apply time."""
        if not self.name.strip():
            raise ValueError("a profile needs a name")
        equaliser.preset(self.preset)
        if self.calibration is not None:
            equaliser.calibration(self.calibration)
        if not 0 <= self.crossfeed <= 100:
            raise ValueError("crossfeed is a percentage in the range 0-100")
        if self.loudness_phon is not None:
            from .eq import loudness

            if not loudness.MIN_PHON <= self.loudness_phon <= loudness.MAX_PHON:
                raise ValueError(
                    f"listening level must be between {loudness.MIN_PHON:.0f} "
                    f"and {loudness.MAX_PHON:.0f} phon"
                )
        for value, label in ((self.bass, "bass"), (self.treble, "treble")):
            if not 0 <= value <= 100:
                raise ValueError(f"{label} boost is a percentage in the range 0-100")

    def summary(self) -> str:
        parts = [self.preset]
        if self.calibration:
            parts.append(self.calibration)
        if self.crossfeed:
            parts.append(f"crossfeed {self.crossfeed}%")
        if self.loudness_phon is not None:
            parts.append(f"{self.loudness_phon:g} phon")
        if self.bass:
            parts.append(f"bass +{self.bass}%")
        if self.treble:
            parts.append(f"treble +{self.treble}%")
        return " + ".join(parts)


def path() -> Path:
    return Path.home() / "Library" / "Application Support" / "OpenCapTune" / "profiles.json"


def _read() -> list[dict]:
    location = path()
    if not location.exists():
        return []
    try:
        return json.loads(location.read_text())["profiles"]
    except (json.JSONDecodeError, KeyError, OSError, TypeError):
        return []


def profiles() -> dict[str, Profile]:
    found = {}
    for entry in _read():
        try:
            found[entry["name"]] = Profile(**entry)
        except (TypeError, KeyError):
            continue  # a profile written by a different version
    return found


def profile(name: str) -> Profile:
    available = profiles()
    for candidate, value in available.items():
        if candidate.lower() == name.lower():
            return value
    raise KeyError(
        f"unknown profile {name!r}"
        + (f"; available: {', '.join(available)}" if available else "; none saved yet")
    )


def save(value: Profile) -> None:
    value.validate()
    location = path()
    location.parent.mkdir(parents=True, exist_ok=True)
    entries = [e for e in _read() if (e.get("name") or "").lower() != value.name.lower()]
    entries.append(asdict(value))
    location.write_text(json.dumps({"profiles": entries}, indent=2))


def delete(name: str) -> bool:
    entries = _read()
    remaining = [e for e in entries if (e.get("name") or "").lower() != name.lower()]
    if len(remaining) == len(entries):
        return False
    path().write_text(json.dumps({"profiles": remaining}, indent=2))
    return True


def from_status(name: str, status: dict, input_device: str | None = None) -> Profile:
    """Capture what the engine is doing right now as a named profile."""
    return Profile(
        name=name,
        preset=status.get("preset", "Neutral"),
        calibration=status.get("calibration"),
        crossfeed=int(status.get("crossfeed", 0)),
        loudness_phon=status.get("loudness_phon"),
        output_device=status.get("output"),
        capture=status.get("capture", "auto"),
        input_device=input_device,
    )


def to_config(value: Profile):
    """Turn a profile into the engine configuration it describes."""
    from .audio.engine import EngineConfig

    if not value.output_device:
        raise ValueError(
            f"profile {value.name!r} has no output device; save it while the "
            "equaliser is running, or set one explicitly"
        )
    return EngineConfig(
        capture=value.capture,
        input_device=value.input_device,
        output_device=value.output_device,
        preset=value.preset,
        calibration=value.calibration,
        crossfeed=value.crossfeed,
        loudness_phon=value.loudness_phon,
        bass=value.bass,
        treble=value.treble,
    )


def with_name(value: Profile, name: str) -> Profile:
    return replace(value, name=name)
