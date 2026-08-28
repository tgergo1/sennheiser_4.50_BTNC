# OpenCapTune

Sennheiser retired CapTune, and Smart Control never supported the hardware
CapTune did. This repository is an attempt to give those headphones their
features back.

It starts by answering the question that decides what is even possible: **does
the headphone expose a control channel?**

```
$ captune devices
Bogcifüles             00-16-94-41-89-d8  connected

$ captune survey 00:16:94:41:89:D8
Advertised services
  0x1000     Service Discovery Server
  0x1108     Headset
  0x110B     Audio Sink
  ...
Verdict: No control channel. This device advertises only standard audio
profiles, so it cannot be configured over Bluetooth by any app
```

For the **HD 4.50 BTNC** the answer is no, and [docs/FINDINGS.md](docs/FINDINGS.md)
records the evidence. CapTune's equaliser, SoundCheck wizard and Sound Profiles
ran on the phone and processed audio before it was sent to the headphones —
nothing was ever stored on the headset. Rebuilding them is therefore an audio
problem, not a Bluetooth one.

Other Sennheiser models *do* have a control channel, and for those the same
survey will say so.

## Install

```
uv venv && uv pip install -e ".[macos]"
```

Python 3.10+. The Bluetooth backend is macOS-only today; on Linux use BlueZ's
`sdptool browse` for the equivalent of `captune survey`.

## Commands

| Command | Purpose |
| --- | --- |
| `captune devices` | list paired Bluetooth devices |
| `captune survey ADDR` | report which services a device exposes, and the verdict |
| `captune survey ADDR --rfcomm-sweep` | additionally try every RFCOMM channel |
| `captune bundle` | rebuild the macOS helper bundle |

`--json` emits the full survey for scripting; `-v` includes raw SDP records.

## Why there is a helper bundle

macOS refuses Bluetooth to a bare interpreter twice over: the binary must sit
in a bundle declaring `NSBluetoothAlwaysUsageDescription`, *and* it must be its
own TCC-responsible process, which only a LaunchServices launch achieves. A
process spawned from a terminal is denied even inside a valid bundle.

So `captune` stages a small bundle around a copy of the running interpreter in
`~/Library/Application Support/OpenCapTune`, ad-hoc signs it, and launches it
with `open -W`. macOS will ask once for Bluetooth permission. Since `open`
detaches stdio, results come back through a temporary file rather than a pipe.
The bundle is only rebuilt when the interpreter moves — rebuilding changes the
code signature, which makes macOS forget the grant and ask again.

See `src/opencaptune/hostapp.py`.

## GAIA

`opencaptune.gaia` implements the CSR/Qualcomm vendor protocol used by their
Bluetooth audio ADK: frame codec, command table and status codes. Qualcomm
never published a specification, so the command table is transcribed from their
own reference Android implementation. It is exercised by the test suite but has
not been driven against hardware — the HD 4.50 BTNC does not speak it.

```
pytest
```

## Licence

MIT. Not affiliated with, endorsed by, or derived from Sennheiser or Sonova.
