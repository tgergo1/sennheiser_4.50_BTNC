"""Command line interface."""

from __future__ import annotations

import argparse
import json
import sys

from . import daemon
from . import eq as equaliser
from . import profiles as profile_store
from . import settings as app_settings
from .bluetooth import uuids as uuid_table
from .hostapp import HostAppError, ensure_bundle, launch_detached, run_helper
from .survey import survey


def _headset_volume(name: str):
    """The headset's own gain, via the helper (CoreAudio needs the bundle)."""
    try:
        response = run_helper({"action": "headset_volume", "name": name})
        return response.get("volume")
    except HostAppError:
        return None


def _set_headset_volume(name: str, level: float) -> bool:
    try:
        response = run_helper({"action": "set_headset_volume", "name": name, "volume": level})
        return bool(response.get("changed"))
    except HostAppError:
        return False


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


def _print_preset(preset, calibration=None) -> None:
    title = preset.name if calibration is None else f"{preset.name} + {calibration.name}"
    print(f"{title}\n")

    if calibration is None:
        gains = preset.gains_db
    else:
        # With a calibration in the chain the preset's own numbers no longer
        # describe what you hear, so show the measured response instead.
        chain = equaliser.chain(preset, calibration)
        gains = tuple(equaliser.response_db(chain, f, 48000) for f in equaliser.bands())

    for frequency, gain, row in zip(equaliser.bands(), gains, _curve(gains)):
        label = f"{frequency} Hz" if frequency < 1000 else f"{frequency / 1000:.3g} kHz"
        print(f"  {label:>9}  {gain:+6.2f} dB  {row}")

    print(f"\n  Q {equaliser.default_q():.2f} ({equaliser.band_width_octaves():.2f} octave bands)")
    if calibration is not None:
        preamp = equaliser.preamp_db(equaliser.chain(preset, calibration), 48000)
        print(f"  preamp {preamp:+.1f} dB")
        print(f"  calibration: {calibration.description}")


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
    correction = report.get("calibration")
    print(f"  preset      {report['preset']}  (preamp {report['preamp_db']:+.1f} dB)")
    print(f"  calibration {correction or 'off'}")
    phon = report.get("loudness_phon")
    print(f"  loudness    {'off' if phon is None else f'{phon:g} phon'}"
          f"     crossfeed {report.get('crossfeed', 0)}%")
    raised = report.get("output_volume_raised_from")
    if raised is not None:
        print(f"  volume      output device raised to full from {raised:.0%} "
              f"(restored on stop)")
    print(f"  routing     system audio -> {report['output']}")
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
    subcommands.add_parser("ui", help="open the menu bar app")

    headset = subcommands.add_parser(
        "headset", help="what can be read from and sent to the headset itself"
    )
    headset_commands = headset.add_subparsers(dest="headset_command", required=True)
    headset_commands.add_parser("status", help="battery and volume of connected headsets")
    headset_volume = headset_commands.add_parser(
        "volume", help="set the headset's own amplifier gain over AVRCP"
    )
    headset_volume.add_argument("device", help="device name, e.g. your headphones")
    headset_volume.add_argument("percent", type=int, metavar="0-100")

    profile_parser = subcommands.add_parser("profile", help="named setups")
    profile_commands = profile_parser.add_subparsers(dest="profile_command", required=True)
    profile_commands.add_parser("list", help="list saved profiles")
    for name, help_text in (("save", "save what is running now"),
                            ("apply", "start or reconfigure to a profile"),
                            ("show", "describe a profile"),
                            ("delete", "remove a profile")):
        command = profile_commands.add_parser(name, help=help_text)
        command.add_argument("name")

    auto = subcommands.add_parser("autostart", help="start the menu bar app at login")
    auto_commands = auto.add_subparsers(dest="autostart_command", required=True)
    auto_commands.add_parser("enable")
    auto_commands.add_parser("disable")
    auto_commands.add_parser("status")

    audio_parser = subcommands.add_parser("audio", help="audio device inspection")
    audio_commands = audio_parser.add_subparsers(dest="audio_command", required=True)
    audio_commands.add_parser("devices", help="list CoreAudio devices")

    eq_parser = subcommands.add_parser("eq", help="CapTune's equaliser presets")
    eq_commands = eq_parser.add_subparsers(dest="eq_command", required=True)
    eq_commands.add_parser("list", help="list the presets CapTune shipped")
    eq_commands.add_parser("calibrations", help="list measured headphone corrections")

    fetch = eq_commands.add_parser(
        "fetch", help="download a headphone correction from the AutoEq database"
    )
    fetch.add_argument("model", help='headphone model, e.g. "HD 650"')
    fetch.add_argument("--source", default=None,
                       help="restrict to one measurer, e.g. oratory1990")
    fetch.add_argument("--name", default=None, help="save it under a different name")
    fetch.add_argument("--list", action="store_true",
                       help="only show what matches, without saving")

    import_parser = eq_commands.add_parser(
        "import", help="import a correction from an AutoEq/EqualizerAPO text file"
    )
    import_parser.add_argument("file")
    import_parser.add_argument("--name", required=True, help="what to call it")

    forget = eq_commands.add_parser("forget", help="delete an imported correction")
    forget.add_argument("name")

    crossfeed_parser = eq_commands.add_parser(
        "crossfeed", help="set the crossfeed strength while running"
    )
    crossfeed_parser.add_argument("strength", type=int, metavar="0-100")

    loudness_parser = eq_commands.add_parser(
        "loudness", help="set the listening level for equal-loudness compensation"
    )
    loudness_parser.add_argument("phon", help="listening level in phon (0-90), or 'off'")

    calibrate = eq_commands.add_parser(
        "calibrate", help="apply or remove a headphone correction while running"
    )
    calibrate.add_argument("calibration", help="calibration name, or 'off'")

    start = eq_commands.add_parser("start", help="start the always-on equaliser")
    start.add_argument("--output", required=True, help="device to play to, e.g. your headphones")
    start.add_argument("--preset", default="Neutral", help="preset to start with")
    start.add_argument("--calibration", default=None,
                       help="measured headphone correction to apply first, e.g. 'HD 4.50 BTNC'")
    start.add_argument("--crossfeed", type=int, default=0, metavar="0-100",
                       help="narrow the stereo image at low frequencies")
    start.add_argument("--loudness", type=float, default=None, metavar="PHON",
                       help="listening level, for equal-loudness compensation (0-90)")
    start.add_argument("--reference-phon", type=float, default=80.0, metavar="PHON",
                       help="level the music was balanced at (default 80)")
    start.add_argument("--bass", type=int, default=0, metavar="0-100")
    start.add_argument("--treble", type=int, default=0, metavar="0-100")
    start.add_argument("--block-size", type=int, default=512,
                       help="frames per block; lower is less latency, more risk of glitches")
    start.add_argument("--no-manage-volume", action="store_true",
                       help="leave the output device's own volume alone")
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
        command.add_argument("--calibration", default=None,
                             help="stack the preset on a measured headphone correction")
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
                        output_device=arguments.output,
                        preset=arguments.preset,
                        calibration=arguments.calibration,
                        crossfeed=arguments.crossfeed,
                        loudness_phon=arguments.loudness,
                        reference_phon=arguments.reference_phon,
                        bass=arguments.bass,
                        treble=arguments.treble,
                        sample_rate=arguments.sample_rate,
                        block_size=arguments.block_size,
                        manage_volume=not arguments.no_manage_volume,
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

            if arguments.eq_command == "calibrations":
                found = equaliser.calibrations()
                if not found:
                    print("No calibrations are shipped.")
                    return 0
                for entry in found.values():
                    print(f"  {entry.name}  ({len(entry.filters)} filters)")
                    print(f"    {entry.description}")
                return 0

            if arguments.eq_command == "fetch":
                from .eq import autoeq

                sources = (arguments.source,) if arguments.source else autoeq.DEFAULT_SOURCES
                matches = autoeq.search(arguments.model, sources)
                if not matches:
                    print(f"Nothing in AutoEq matches {arguments.model!r}.")
                    print(f"Searched: {', '.join(sources)}. Try --source to widen it.")
                    return 1
                for source, category, model in matches:
                    print(f"  {model}  ({source}, {category})")
                if arguments.list:
                    return 0
                chosen = matches[0]
                if len(matches) > 1:
                    print(f"\nTaking the closest match: {chosen[2]} ({chosen[0]})")
                correction = autoeq.fetch(*chosen)
                if arguments.name:
                    correction = equaliser.Calibration(
                        name=arguments.name, filters=correction.filters,
                        source=correction.source)
                equaliser.save_calibration(correction)
                print(f"\nSaved {correction.name!r} — {len(correction.filters)} filters, "
                      f"preamp {equaliser.preamp_db(correction):.1f} dB")
                print(f'Use it with: captune eq calibrate "{correction.name}"')
                return 0

            if arguments.eq_command == "import":
                from pathlib import Path

                text = Path(arguments.file).read_text()
                correction = equaliser.parse_parametric(
                    text, name=arguments.name, source=f"imported from {arguments.file}")
                equaliser.save_calibration(correction)
                print(f"Imported {correction.name!r} — {len(correction.filters)} filters, "
                      f"preamp {equaliser.preamp_db(correction):.1f} dB")
                return 0

            if arguments.eq_command == "forget":
                if equaliser.delete_calibration(arguments.name):
                    print(f"Removed {arguments.name!r}.")
                    return 0
                print(f"No imported correction called {arguments.name!r}. "
                      "Corrections shipped with the app cannot be removed.")
                return 1

            if arguments.eq_command == "crossfeed":
                report = daemon.set_crossfeed(arguments.strength)
                print(f"Crossfeed is now {report['crossfeed']}%.")
                return 0

            if arguments.eq_command == "loudness":
                phon = None if arguments.phon.lower() == "off" else float(arguments.phon)
                report = daemon.set_loudness(phon)
                print(
                    f"Loudness compensation is now {'off' if phon is None else f'{phon:g} phon'} "
                    f"(preamp {report['preamp_db']:+.1f} dB)."
                )
                return 0

            if arguments.eq_command == "calibrate":
                name = None if arguments.calibration.lower() == "off" else arguments.calibration
                daemon.set_calibration(name)
                print(f"Calibration is now {name or 'off'}.")
                return 0

            if arguments.eq_command == "list":
                for preset in equaliser.presets().values():
                    span = f"{min(preset.gains_db):+.1f} to {max(preset.gains_db):+.1f} dB"
                    print(f"  {preset.name:<12} {span}")
                return 0
            preset = equaliser.preset(arguments.preset).with_boosts(
                bass=arguments.bass, treble=arguments.treble
            )
            correction = (
                equaliser.calibration(arguments.calibration) if arguments.calibration else None
            )
            if arguments.eq_command == "show":
                _print_preset(preset, correction)
            else:
                print(_export(preset, arguments.format, arguments.sample_rate, correction))
            return 0

        if arguments.command == "devices":
            _print_devices(run_helper({"action": "list_devices"})["devices"])
            return 0

        if arguments.command == "headset":
            from .bluetooth import headset as headset_info

            if arguments.headset_command == "status":
                found = headset_info.connected_headsets()
                if not found:
                    print("No Bluetooth devices connected.")
                    return 0
                for entry in found:
                    print(f"  {entry['name']}  ({entry['address']})")
                    if entry["battery"] is not None:
                        print(f"    battery   {entry['battery']}%  "
                              "(reported by the headset over HFP)")
                    level = _headset_volume(entry["name"])
                    if level is not None:
                        print(f"    volume    {level:.0%}  "
                              f"= step {round(level * 128)} of 128, "
                              "sent to the headset over AVRCP")
                    if entry["vendor_id"]:
                        print(f"    chipset   vendor {entry['vendor_id']}")
                return 0

            level = max(0, min(100, arguments.percent)) / 100.0
            if not _set_headset_volume(arguments.device, level):
                print(f"error: {arguments.device!r} has no settable volume",
                      file=sys.stderr)
                return 1
            print(f"Sent absolute volume step {round(level * 128)} of 128 "
                  f"to {arguments.device} over AVRCP.")
            return 0

        if arguments.command == "profile":
            if arguments.profile_command == "list":
                saved = profile_store.profiles()
                if not saved:
                    print("No profiles saved. Run `captune profile save NAME` "
                          "while the equaliser is running.")
                    return 0
                auto = app_settings.get("auto_profile")
                for entry in saved.values():
                    marker = " (auto)" if entry.name == auto else ""
                    print(f"  {entry.name}{marker}")
                    print(f"    {entry.summary()}")
                    if entry.output_device:
                        print(f"    -> {entry.output_device}")
                return 0

            if arguments.profile_command == "save":
                if not daemon.is_running():
                    print("error: start the equaliser first, so there is something to save",
                          file=sys.stderr)
                    return 1
                entry = profile_store.from_status(arguments.name, daemon.status())
                profile_store.save(entry)
                print(f"Saved {entry.name!r}: {entry.summary()}")
                return 0

            if arguments.profile_command == "show":
                entry = profile_store.profile(arguments.name)
                print(f"{entry.name}\n  {entry.summary()}")
                print(f"  -> {entry.output_device or '(none set)'}")
                return 0

            if arguments.profile_command == "delete":
                if profile_store.delete(arguments.name):
                    print(f"Removed {arguments.name!r}.")
                    return 0
                print(f"No profile called {arguments.name!r}.", file=sys.stderr)
                return 1

            entry = profile_store.profile(arguments.name)
            if daemon.is_running():
                daemon.stop()
            report = daemon.start(profile_store.to_config(entry))
            print(f"Applied {entry.name!r}.")
            _print_status(report)
            return 0

        if arguments.command == "autostart":
            from . import autostart

            if arguments.autostart_command == "enable":
                print(f"Enabled. The menu bar app will start at login.\n  {autostart.enable()}")
                return 0
            if arguments.autostart_command == "disable":
                print("Disabled." if autostart.disable() else "It was not enabled.")
                return 0
            print("Starts at login." if autostart.is_enabled() else "Does not start at login.")
            return 0

        if arguments.command == "ui":
            launch_detached("opencaptune.menubar", [])
            print("Menu bar app started — look for the slider icon in the menu bar.")
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
    except (HostAppError, NotImplementedError, KeyError, ValueError, LookupError,
            OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
