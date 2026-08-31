"""What can actually be exchanged with the headset over standard profiles.

This model has no vendor control channel, but that does not mean nothing
reaches it. Two standard paths carry real information:

**AVRCP Absolute Volume, host to headset.** Writing a CoreAudio output
device's volume makes macOS transmit an AVRCP ``SetAbsoluteVolume`` over the
air, which the headset applies in its own amplifier. The evidence is in the
numbers: readbacks land on exact multiples of 1/128 — 0.5625 is 72/128,
0.78125 is 100/128 — because the scalar is mapped onto Absolute Volume's
7-bit range. It is genuinely a command, not a local gain.

**Battery level, headset to host.** The headset reports its charge over HFP
using Apple's ``AT+IPHONEACCEV`` extension, and macOS surfaces it. We cannot
write to that channel — the system owns it — but we can read what it carries.
"""

from __future__ import annotations

import json
import subprocess


def _bluetooth_report() -> dict:
    try:
        raw = subprocess.run(
            ["system_profiler", "SPBluetoothDataType", "-json"],
            capture_output=True, text=True, timeout=15,
        )
        if raw.returncode != 0:
            return {}
        return json.loads(raw.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}


def connected_headsets() -> list[dict]:
    """Connected Bluetooth devices, with whatever they report about themselves."""
    report = _bluetooth_report()
    controllers = report.get("SPBluetoothDataType") or []
    found = []
    for controller in controllers:
        for entry in controller.get("device_connected", []) or []:
            for name, info in entry.items():
                found.append(
                    {
                        "name": name,
                        "address": info.get("device_address"),
                        "battery": _percent(info.get("device_batteryLevelMain")),
                        "firmware": info.get("device_firmwareVersion"),
                        "vendor_id": info.get("device_vendorID"),
                        "product_id": info.get("device_productID"),
                        "services": info.get("device_services"),
                    }
                )
    return found


def _percent(value) -> int | None:
    if not value:
        return None
    try:
        return int(str(value).strip().rstrip("%"))
    except ValueError:
        return None


def find(name: str) -> dict | None:
    """One connected headset by name, or None."""
    for entry in connected_headsets():
        if entry["name"] == name:
            return entry
    return None


def battery_percent(name: str) -> int | None:
    entry = find(name)
    return entry["battery"] if entry else None
