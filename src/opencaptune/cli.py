"""Command line interface."""

from __future__ import annotations

import argparse
import json
import sys

from . import eq as equaliser
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


def _curve(gains: tuple[float, ...], width: int = 21) -> list[str]:
    """A small ASCII column per band, centred on 0 dB."""
    middle = width // 2
    rows = []
    for gain in gains:
        offset = int(round(gain / equaliser.MAX_GAIN_DB * middle))
        cells = [" "] * width
        cells[middle] = "|"
        low, high = sorted((middle, middle + offset))
        for index in range(low, high + 1):
            cells[index] = "#"
        rows.append("".join(cells))
    return rows


def _print_preset(preset) -> None:
    print(f"{preset.name}\n")
    rows = _curve(preset.gains_db)
    for frequency, gain, row in zip(equaliser.bands(), preset.gains_db, rows):
        label = f"{frequency} Hz" if frequency < 1000 else f"{frequency / 1000:.3g} kHz"
        print(f"  {label:>9}  {gain:+6.2f} dB  {row}")
    print(f"\n  Q {equaliser.default_q():.2f} ({equaliser.band_width_octaves():.2f} octave bands)")


def _export(preset, style: str, sample_rate: int) -> str:
    if style == "json":
        return json.dumps(
            {
                "name": preset.name,
                "q": round(equaliser.default_q(), 4),
                "bands": [
                    {"frequency_hz": frequency, "gain_db": gain}
                    for frequency, gain in zip(equaliser.bands(), preset.gains_db)
                ],
            },
            indent=2,
        )
    if style == "apo":
        # EqualizerAPO / AutoEQ parametric format, also read by many players.
        lines = ["Preamp: -6.0 dB"]
        q = equaliser.default_q()
        for index, (frequency, gain) in enumerate(zip(equaliser.bands(), preset.gains_db), 1):
            lines.append(
                f"Filter {index}: ON PK Fc {frequency} Hz Gain {gain:.2f} dB Q {q:.3f}"
            )
        return "\n".join(lines)
    if style == "biquad":
        lines = []
        for numerator, denominator in equaliser.filter_chain(preset, sample_rate):
            b0, b1, b2 = numerator
            _, a1, a2 = denominator
            lines.append(f"{b0:.10f} {b1:.10f} {b2:.10f} {a1:.10f} {a2:.10f}")
        return "\n".join(lines)
    raise ValueError(f"unknown format {style!r}")


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

    eq_parser = subcommands.add_parser("eq", help="CapTune's equaliser presets")
    eq_commands = eq_parser.add_subparsers(dest="eq_command", required=True)
    eq_commands.add_parser("list", help="list the presets CapTune shipped")

    for name, help_text in (("show", "display a preset"), ("export", "write a preset out")):
        command = eq_commands.add_parser(name, help=help_text)
        command.add_argument("preset", help="preset name, e.g. Rock")
        command.add_argument("--bass", type=int, default=0, metavar="0-100",
                             help="bass boost strength, as CapTune's slider")
        command.add_argument("--treble", type=int, default=0, metavar="0-100",
                             help="treble boost strength, as CapTune's slider")
        if name == "export":
            command.add_argument("--format", default="apo", choices=("apo", "json", "biquad"),
                                 help="apo: EqualizerAPO/AutoEQ parametric text (default)")
            command.add_argument("--sample-rate", type=int, default=48000,
                                 help="sample rate for biquad coefficients")

    arguments = parser.parse_args(argv)

    try:
        if arguments.command == "eq":
            if arguments.eq_command == "list":
                for preset in equaliser.presets().values():
                    span = f"{min(preset.gains_db):+.1f} to {max(preset.gains_db):+.1f} dB"
                    print(f"  {preset.name:<12} {span}")
                return 0
            preset = equaliser.preset(arguments.preset).with_boosts(
                bass=arguments.bass, treble=arguments.treble
            )
            if arguments.eq_command == "show":
                _print_preset(preset)
            else:
                print(_export(preset, arguments.format, arguments.sample_rate))
            return 0

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
    except (HostAppError, NotImplementedError, KeyError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
