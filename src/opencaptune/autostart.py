"""Starting the menu bar app at login.

A LaunchAgent rather than a Login Item, because launchd starts the process as
its own responsible process — which is what macOS requires before it will hand
out microphone access. A process inheriting a terminal's identity is refused
even from inside a correctly signed bundle.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from .hostapp import EXECUTABLE_NAME, ensure_bundle

LABEL = "org.opencaptune.menubar"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def is_enabled() -> bool:
    return plist_path().exists()


def _definition() -> dict:
    bundle = ensure_bundle()
    executable = bundle / "Contents" / "MacOS" / EXECUTABLE_NAME
    return {
        "Label": LABEL,
        "ProgramArguments": [str(executable), "-m", "opencaptune.menubar"],
        "EnvironmentVariables": {
            "PYTHONHOME": sys.base_prefix,
            "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
        },
        "RunAtLoad": True,
        # Not KeepAlive: quitting from the menu should mean quit, not respawn.
        "ProcessType": "Interactive",
    }


def enable() -> Path:
    location = plist_path()
    location.parent.mkdir(parents=True, exist_ok=True)
    location.write_bytes(plistlib.dumps(_definition()))
    subprocess.run(["launchctl", "unload", str(location)], capture_output=True)
    result = subprocess.run(["launchctl", "load", str(location)], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"launchctl refused the agent: {result.stderr.strip()}")
    return location


def disable() -> bool:
    location = plist_path()
    if not location.exists():
        return False
    subprocess.run(["launchctl", "unload", str(location)], capture_output=True)
    location.unlink()
    return True
