"""Command line interface."""

from __future__ import annotations

import argparse
import json
import sys

from . import daemon
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
        lines = [f"Preamp: {equaliser.preamp_db(preset):.1f} dB"]
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


def _print_status(report: dict) -> None:
    print(f"  preset      {report['preset']}  (preamp {report['preamp_db']:+.1f} dB)")
    print(f"  routing     {report['input']} -> {report['output']}")
    print(f"  format      {report['sample_rate']} Hz, {report['channels']} ch, "
          f"{report['block_size']} frame blocks (~{report['latency_ms']} ms)")
    hours = report["frames"] / report["sample_rate"] / 3600
    print(f"  processed   {report['frames']} frames ({hours:.2f} h), "
          f"{report['glitches']} glitches, peak {report['peak']:.3f}")


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

    audio_parser = subcommands.add_parser("audio", help="audio device inspection")
    audio_commands = audio_parser.add_subparsers(dest="audio_command", required=True)
    audio_commands.add_parser("devices", help="list CoreAudio devices")

    eq_parser = subcommands.add_parser("eq", help="CapTune's equaliser presets")
    eq_commands = eq_parser.add_subparsers(dest="eq_command", required=True)
    eq_commands.add_parser("list", help="list the presets CapTune shipped")

    start = eq_commands.add_parser("start", help="start the always-on equaliser")
    start.add_argument("--input", default="BlackHole",
                       help="device to read from, normally a virtual output device")
    start.add_argument("--output", required=True, help="device to play to, e.g. your headphones")
    start.add_argument("--preset", default="Neutral", help="preset to start with")
    start.add_argument("--bass", type=int, default=0, metavar="0-100")
    start.add_argument("--treble", type=int, default=0, metavar="0-100")
    start.add_argument("--block-size", type=int, default=512,
                       help="frames per block; lower is less latency, more risk of glitches")
    start.add_argument("--sample-rate", type=int, default=None,
                       help="defaults to the output device's own rate")

    eq_commands.add_parser("stop", help="stop the equaliser")
    show_status = eq_commands.add_parser("status", help="show what the equaliser is doing")
    show_status.add_argument("--reset", action="store_true",
                             help="clear the frame, glitch and peak counters afterwards")

    live = eq_commands.add_parser("set", help="change preset while running")
    live.add_argument("preset")
    live.add_argument("--bass", type=int, default=0, metavar="0-100")
    live.add_argument("--treble", type=int, default=0, metavar="0-100")

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
        if arguments.command == "audio":
            from .audio.devices import devices as audio_devices

            for device in audio_devices():
                capability = []
                if device.is_input:
                    capability.append(f"in {device.input_channels}")
                if device.is_output:
                    capability.append(f"out {device.output_channels}")
                print(
                    f"  {device.index:>3}  {device.name:<30} {', '.join(capability):<12} "
                    f"{device.default_sample_rate:>6.0f} Hz"
                )
            return 0

        if arguments.command == "eq":
            if arguments.eq_command == "start":
                from .audio.engine import EngineConfig

                report = daemon.start(
                    EngineConfig(
                        input_device=arguments.input,
                        output_device=arguments.output,
                        preset=arguments.preset,
                        bass=arguments.bass,
                        treble=arguments.treble,
                        sample_rate=arguments.sample_rate,
                        block_size=arguments.block_size,
                    )
                )
                print("Equaliser running.")
                _print_status(report)
                return 0

            if arguments.eq_command == "stop":
                daemon.stop()
                print("Equaliser stopped.")
                return 0

            if arguments.eq_command == "status":
                if not daemon.is_running():
                    print("Equaliser is not running.")
                    return 1
                print("Equaliser running.")
                _print_status(daemon.status())
                if arguments.reset:
                    daemon.reset_stats()
                    print("  counters cleared")
                return 0

            if arguments.eq_command == "set":
                report = daemon.set_preset(arguments.preset, arguments.bass, arguments.treble)
                print(f"Preset is now {report['preset']} (preamp {report['preamp_db']:+.1f} dB).")
                return 0

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
    except (HostAppError, NotImplementedError, KeyError, ValueError, LookupError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
