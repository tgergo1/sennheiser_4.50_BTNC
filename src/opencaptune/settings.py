"""A tiny key/value store for the app's own preferences."""

from __future__ import annotations

import json
from pathlib import Path

DEFAULTS = {
    # Profile applied when the equaliser starts itself.
    "auto_profile": None,
    # Start and stop automatically as the profile's output device comes and goes.
    "follow_device": False,
}


def path() -> Path:
    return Path.home() / "Library" / "Application Support" / "OpenCapTune" / "settings.json"


def load() -> dict:
    location = path()
    values = dict(DEFAULTS)
    if location.exists():
        try:
            stored = json.loads(location.read_text())
            values.update({k: v for k, v in stored.items() if k in DEFAULTS})
        except (json.JSONDecodeError, OSError, TypeError, AttributeError):
            pass
    return values


def get(key: str):
    if key not in DEFAULTS:
        raise KeyError(f"unknown setting {key!r}")
    return load()[key]


def set(key: str, value) -> None:  # noqa: A001 - reads naturally at the call site
    if key not in DEFAULTS:
        raise KeyError(f"unknown setting {key!r}")
    values = load()
    values[key] = value
    location = path()
    location.parent.mkdir(parents=True, exist_ok=True)
    location.write_text(json.dumps(values, indent=2))
