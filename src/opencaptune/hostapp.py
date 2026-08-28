"""Run Bluetooth work from inside a macOS application bundle.

macOS gates Bluetooth behind TCC, and the check has two teeth:

1. The executing binary must live in a bundle whose Info.plist carries
   ``NSBluetoothAlwaysUsageDescription``.  A bare interpreter aborts with
   SIGABRT the moment it touches IOBluetooth — not an exception, a crash.
2. The process must be *responsible* for itself.  A binary spawned from a
   terminal inherits the terminal's TCC identity and is refused even when it
   does sit inside a valid bundle; only a LaunchServices launch (``open``)
   makes it its own responsible process.

So the CLI stages a tiny bundle around a copy of the running interpreter, hands
it a request file, launches it with ``open -W``, and reads back a response file.
``open`` detaches stdio, which is why results travel through the filesystem
rather than a pipe.
"""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BUNDLE_NAME = "OpenCapTune.app"
BUNDLE_ID = "org.opencaptune.host"
EXECUTABLE_NAME = "OpenCapTune"
BLUETOOTH_USAGE = (
    "OpenCapTune talks to your Sennheiser headphones over Bluetooth to read "
    "their capabilities and settings."
)
# Reading from a virtual output device such as BlackHole counts as microphone
# input as far as macOS is concerned, so the equaliser needs this too.
MICROPHONE_USAGE = (
    "OpenCapTune reads the audio you are playing so it can equalise it before "
    "it reaches your headphones."
)


class HostAppError(RuntimeError):
    pass


def support_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "OpenCapTune"


def bundle_path() -> Path:
    return support_dir() / BUNDLE_NAME


def _interpreter() -> Path:
    """The real interpreter binary, looking through a virtualenv's shim."""
    base = getattr(sys, "_base_executable", None) or sys.executable
    return Path(base).resolve()


def _info_plist() -> dict:
    return {
        "CFBundleExecutable": EXECUTABLE_NAME,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleName": "OpenCapTune",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.1.0",
        # No dock icon: this bundle exists only to own a TCC identity.
        "LSBackgroundOnly": True,
        "NSBluetoothAlwaysUsageDescription": BLUETOOTH_USAGE,
        "NSBluetoothPeripheralUsageDescription": BLUETOOTH_USAGE,
        "NSMicrophoneUsageDescription": MICROPHONE_USAGE,
    }


def ensure_bundle(force: bool = False) -> Path:
    """Create (or refresh) the helper bundle and return its path.

    Rebuilding changes the code signature, which makes macOS forget the
    Bluetooth grant and ask again, so the bundle is left alone unless the
    interpreter it wraps has actually moved.
    """
    bundle = bundle_path()
    executable = bundle / "Contents" / "MacOS" / EXECUTABLE_NAME
    stamp = support_dir() / "bundle-stamp.json"
    interpreter = _interpreter()
    want = {"interpreter": str(interpreter), "plist": _info_plist()}

    if not force and executable.exists() and stamp.exists():
        try:
            if json.loads(stamp.read_text()) == want:
                return bundle
        except (json.JSONDecodeError, OSError):
            pass

    if bundle.exists():
        shutil.rmtree(bundle)
    executable.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(interpreter, executable)
    (bundle / "Contents" / "Info.plist").write_bytes(plistlib.dumps(_info_plist()))

    signed = subprocess.run(
        ["codesign", "--force", "--sign", "-", str(bundle)],
        capture_output=True,
        text=True,
    )
    if signed.returncode != 0:
        raise HostAppError(f"could not sign the helper bundle: {signed.stderr.strip()}")

    # Written only after signing succeeds, so a failed build is retried.
    stamp.write_text(json.dumps(want))
    return bundle


def _launch_arguments(module: str, arguments: list[str]) -> list[str]:
    # The interpreter runs outside its own prefix, so point it at its standard
    # library and at wherever this package was imported from.
    return [
        "--env",
        f"PYTHONHOME={sys.base_prefix}",
        "--env",
        f"PYTHONPATH={os.pathsep.join(p for p in sys.path if p)}",
        "--args",
        "-m",
        module,
        *arguments,
    ]


def launch_detached(module: str, arguments: list[str]) -> None:
    """Start a long-running helper inside the bundle and return immediately."""
    if sys.platform != "darwin":
        raise HostAppError("the helper bundle is a macOS-only mechanism")
    bundle = ensure_bundle()
    result = subprocess.run(
        ["open", "-n", str(bundle), *_launch_arguments(module, arguments)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise HostAppError(f"could not launch the helper: {result.stderr.strip()}")


def run_helper(request: dict, timeout: float = 120.0) -> dict:
    """Execute one request inside the bundle and return its response."""
    if sys.platform != "darwin":
        raise HostAppError("the helper bundle is a macOS-only mechanism")

    bundle = ensure_bundle()
    workdir = Path(tempfile.mkdtemp(prefix="opencaptune-"))
    try:
        request_file = workdir / "request.json"
        response_file = workdir / "response.json"
        request_file.write_text(json.dumps(request))

        command = [
            "open",
            "-W",
            "-n",
            str(bundle),
            *_launch_arguments("opencaptune._helper", [str(request_file), str(response_file)]),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        if not response_file.exists():
            detail = (result.stderr or result.stdout or "").strip()
            raise HostAppError(
                "the Bluetooth helper produced no response"
                + (f": {detail}" if detail else ". It may have been denied Bluetooth access.")
            )
        response = json.loads(response_file.read_text())
        if response.get("error"):
            raise HostAppError(response["error"])
        return response
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
