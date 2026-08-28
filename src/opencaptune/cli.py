"""Command line interface."""

from __future__ import annotations

import argparse
import json
import sys

from .bluetooth import uuids as uuid_table
from .hostapp import HostAppError, ensure_bundle, run_helper
from .survey import survey


def _print_devices(devices: list[dict]) -> None:
    if not devices:
        print("No paired Bluetooth devices.")
        return
    width = max(len(device["name"] or "") for device in devices)
    for device in devices:
        state = "connected" if device["connected"] else "not connected"
        print(f"{(device['name'] or '?'):<{width}}  {device['address']}  {state}")


def _print_survey(result, verbose: bool) -> None:
    print(f"Device {result.address}\n")

    print("Advertised services")
    for service in result.advertised_services:
        short = uuid_table.short_uuid(service["uuid"])
        label = f"0x{short:04X}" if short is not None else service["uuid"]
        print(f"  {label:<10} {service['name']}")

    channels = [
        (record["rfcomm_channel"], record["service_name"] or "?")
        for record in result.records
        if record["rfcomm_channel"] is not None
    ]
    if channels:
        print("\nRFCOMM channels")
        for number, name in sorted(set(channels)):
            print(f"  {number:<3} {name}")

    if result.peripherals:
        named = [p for p in result.peripherals if p["name"]]
        print(f"\nBLE scan: {len(result.peripherals)} peripherals, {len(named)} named")
        for peripheral in result.ble_control_peripherals:
            print(f"  control service on {peripheral['name'] or peripheral['identifier']}")

    if result.channels:
        print("\nRFCOMM sweep")
        if result.sweep_was_blocked:
            code = result.channels[0]["io_return"]
            print(f"  inconclusive: every channel failed with {code} (macOS blocked the sweep)")
        else:
            for entry in result.channels:
                if entry["opened"]:
                    print(f"  channel {entry['channel']}: open, MTU {entry['mtu']}")

    print(f"\nVerdict: {result.verdict}")

    if verbose:
        print("\nFull SDP records:")
        print(json.dumps(result.records, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="captune",
        description="Open-source tooling for Sennheiser headphones orphaned by CapTune.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("devices", help="list paired Bluetooth devices")

    survey_parser = subcommands.add_parser(
        "survey", help="report whether a device exposes any control channel"
    )
    survey_parser.add_argument("address", help="Bluetooth address, e.g. 00:16:94:41:89:D8")
    survey_parser.add_argument(
        "--ble-seconds", type=float, default=10.0, help="how long to scan for BLE advertisements"
    )
    survey_parser.add_argument(
        "--rfcomm-sweep",
        action="store_true",
        help="also try opening every RFCOMM channel (often blocked by macOS)",
    )
    survey_parser.add_argument("--json", action="store_true", help="emit raw JSON")
    survey_parser.add_argument("-v", "--verbose", action="store_true", help="include SDP records")

    subcommands.add_parser("bundle", help="rebuild the macOS Bluetooth helper bundle")

    arguments = parser.parse_args(argv)

    try:
        if arguments.command == "devices":
            _print_devices(run_helper({"action": "list_devices"})["devices"])
            return 0

        if arguments.command == "bundle":
            print(f"Helper bundle ready at {ensure_bundle(force=True)}")
            return 0

        result = survey(
            arguments.address,
            ble_seconds=arguments.ble_seconds,
            rfcomm_sweep=arguments.rfcomm_sweep,
        )
        if arguments.json:
            print(
                json.dumps(
                    {
                        "address": result.address,
                        "verdict": result.verdict,
                        "records": result.records,
                        "peripherals": result.peripherals,
                        "channels": result.channels,
                    },
                    indent=2,
                )
            )
        else:
            _print_survey(result, arguments.verbose)
        return 0
    except (HostAppError, NotImplementedError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
