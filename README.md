# OpenCapTune

Sennheiser retired CapTune, and Smart Control never supported the hardware
CapTune did. This project gives those headphones their features back.

Two things came out of taking the problem apart:

1. **The HD 4.50 BTNC has no control channel, and never did.** It advertises
   only standard audio profiles, and CapTune's own code confirms it: the app
   sorted headphones into families, and this one mapped to a class whose
   `send()` and `parse()` methods are empty. CapTune never sent these
   headphones a single byte. [docs/FINDINGS.md](docs/FINDINGS.md) has the
   evidence from both ends.
2. **So everything it did is rebuildable, exactly.** CapTune's equaliser ran on
   the phone, and its curves shipped as data inside the app. They are extracted
   here verbatim — the real band centres, the real presets, the real bass and
   treble offset tables.

```
$ captune eq show Rock

      35 Hz   +4.00 dB            ####
      57 Hz   +4.00 dB            ####
      92 Hz   +3.50 dB            ####
     148 Hz   +2.50 dB            ###
     238 Hz   +1.50 dB            ##
     384 Hz   +0.00 dB            #
     620 Hz   -0.50 dB            #
       1 kHz  -0.50 dB            #
    1.61 kHz  -0.50 dB            #
     2.6 kHz  +1.00 dB            ##
     4.2 kHz  +2.50 dB            ###
    6.77 kHz  +3.50 dB            ####
    10.2 kHz  +4.00 dB            ####
    17.6 kHz  +4.50 dB            #####
```

## Install

```
uv tool install --editable ".[macos]"
```

This puts `captune` on your `PATH`, so it works from any directory. `--editable`
points the installed tool at this checkout, so edits here take effect
immediately — but moving or deleting the checkout breaks the command. Drop
`--editable` for a standalone copy, and `uv tool uninstall opencaptune` to
remove it.

To work on the code instead, `uv venv && uv pip install -e ".[macos]"` — but
note that this leaves `captune` inside `.venv/bin`, reachable only after
`source .venv/bin/activate`.

Python 3.10+, macOS. The equaliser itself is portable; the Bluetooth survey and
the permission plumbing are macOS-specific.

## System-wide equaliser

CapTune could only equalise music it played itself. This does better: with a
virtual output device in front of it, everything on the Mac goes through the
filter — Spotify, YouTube, anything.

Install the virtual device once (this asks for your password, and macOS will
ask you to allow the driver in System Settings → Privacy & Security):

```
brew install blackhole-2ch
```

Then send the Mac's audio into it and run the equaliser from it to your
headphones:

```
captune audio devices
captune eq start --input "BlackHole 2ch" --output "YourHeadphones" --preset Rock
```

Set the Mac's **output device to BlackHole 2ch** (System Settings → Sound). Audio
now flows: apps → BlackHole → OpenCapTune → headphones.

```
captune eq set Voice --bass 30     # change curve without interrupting playback
captune eq status
captune eq stop
```

Switching preset crossfades over 20 ms rather than swapping filters outright,
which would step the output — audibly, since presets carry different preamps.
The two filters run side by side for the fade and are mixed linearly. Measured
on hardware, the step at a switch drops by about three orders of magnitude and
the output never exceeds the louder of the two presets.

**Latency** is about 11 ms at the default 512-frame block, on top of Bluetooth's
own. Fine for music, noticeable for video; `--block-size 256` halves it at some
risk of glitches, which `captune eq status` counts. Those counters — frames,
glitches and peak — accumulate since the last reset, and `peak` is a running
maximum, so use `captune eq status --reset` before measuring anything.

**To hear your Mac normally again**, set the output device back and
`captune eq stop`.

## Headphone calibration

Presets are taste. A calibration is measurement: what this particular model
gets wrong, from a rig. oratory1990 measured the HD 4.50 BTNC and AutoEq
publishes the correction to the Harman over-ear target; it ships here.

```
captune eq calibrations
captune eq start --input "BlackHole 2ch" --output "YourHeadphones" \
  --preset Neutral --calibration "HD 4.50 BTNC"
```

It is not a subtle correction — −7.2 dB at 2 kHz and +8.2 dB at 3.9 kHz — and
it is the single biggest improvement available to these headphones. Presets
stack on top of it: the calibration fixes the headphones, the preset is taste.
`captune eq calibrate off` removes it while running, which is the honest way to
hear what it does.

Verified against the hardware at six frequencies, exercising both shelves and
the largest cut and boost, to within 0.3% of the predicted response.

## Crossfeed

On speakers each ear hears both channels; on headphones each hears only its
own, which is a cue the brain never gets naturally, and hard-panned mixes end
up localised inside your head.

```
captune eq crossfeed 60
```

The usual construction mixes a low-passed copy of each channel into the other,
which sums correlated content and builds up bass on mono material, then needs a
compensating shelf to undo the damage. This works on mid and side instead:
the side signal is shelved down below 700 Hz and mid is left strictly alone, so
mono passes through bit-identically and the image above the corner is
untouched. Measured on hardware at full strength: −10.05 dB on side content at
80 Hz, 0.00 dB on mono, 0.00 dB on side at 8 kHz.

It omits the interaural delay a full head-related model would have. That trade
buys exactness — there is nothing here that needs correcting afterwards.

## Loudness compensation

Hearing loses bass and treble at low volume — physiology, not taste. Music is
balanced at one level and usually played at another, so quiet listening is
genuinely thinner than intended.

```
captune eq loudness 60      # roughly how loud you are actually listening, in phon
captune eq loudness off
```

CapTune's "Loudness" preset was a fixed curve: right at exactly one level and
wrong everywhere else. This computes the correction for the level you are at,
as the difference between two ISO 226:2003 equal-loudness contours, and applies
nothing at all when playback matches the reference. At 60 phon against an
80 phon reference it lifts 50 Hz by about 10 dB relative to 1 kHz.

It cannot know your actual sound pressure level — that needs the headphone's
sensitivity and the amplifier gain — so you tell it roughly where you are.
Verified against the hardware to within 0.07 dB.

## Using the presets somewhere else

```
captune eq export Rock --format apo
```

Emits AutoEq/EqualizerAPO parametric text, which most equaliser software
imports — including iOS players, which is the only way to get these curves onto
a phone, since iOS gives no app the system audio path. `--format json` and
`--format biquad` (raw coefficients for a given `--sample-rate`) are also
available.

## Commands

| Command | Purpose |
| --- | --- |
| `captune eq list` | the presets CapTune shipped |
| `captune eq calibrations` | measured headphone corrections |
| `captune eq calibrate NAME\|off` | apply or remove a correction while running |
| `captune eq crossfeed 0-100` | narrow the stereo image at low frequencies |
| `captune eq loudness PHON\|off` | equal-loudness compensation for your listening level |
| `captune eq show NAME [--bass N] [--treble N]` | display a curve |
| `captune eq export NAME [--format apo\|json\|biquad]` | write a curve out |
| `captune eq start --input DEV --output DEV` | start the always-on equaliser |
| `captune eq set NAME` / `stop` / `status` | control it while it runs |
| `captune eq status --reset` | show it, then clear the counters |
| `captune audio devices` | list CoreAudio devices |
| `captune devices` | list paired Bluetooth devices |
| `captune survey ADDR` | report a headphone's control channel, if any |
| `captune bundle` | rebuild the macOS helper bundle |

## What else is possible

[docs/ROADMAP.md](docs/ROADMAP.md) inventories what can still be built — from
crossfeed and a rebuilt SoundCheck wizard, through probing the HFP channel from
Linux for an undiscovered control surface, to opening the headphones and
editing the CSR chip's persistent store over SPI.

## How the presets were recovered

From CapTune 1.8.1, verified as genuinely Sennheiser-signed, decompiled and
read but never executed. 14 bands from 35 Hz to 17.6 kHz spaced a constant 0.69
octaves apart, nine presets, and the bass/treble offset curves with the app's
own arithmetic — scale by the slider, add to the preset, clamp to ±12 dB.

Verified end to end against the real hardware: a 1 kHz tone through BlackHole
into the headphones lands within 0.1% of the predicted level on every preset.

The filter design is ours, because the original DSP was a closed native
library. Peaking biquads with a Q derived from the band spacing reproduce the
intended response. The preamp is measured from the actual cascade rather than
the largest band gain, because adjacent boosts overlap and sum: "Voice" peaks
at +6.7 dB even though no single band exceeds +4.5 dB.

## Why there is a helper bundle

macOS refuses Bluetooth and microphone access to a bare interpreter twice over:
the binary must sit in a bundle declaring the usage descriptions, *and* it must
be its own TCC-responsible process, which only a LaunchServices launch
achieves. A process spawned from a terminal is denied even inside a valid
bundle.

So `captune` stages a small bundle around a copy of the running interpreter in
`~/Library/Application Support/OpenCapTune`, ad-hoc signs it, and launches it
with `open`. macOS asks once for each permission. Since `open` detaches stdio,
one-shot results come back through a file and the equaliser daemon is driven
over a Unix socket. See `src/opencaptune/hostapp.py`.

Reading from BlackHole counts as microphone input to macOS, which is why the
equaliser needs microphone permission despite never touching a microphone.

## GAIA

`opencaptune.gaia` implements the CSR/Qualcomm vendor protocol used by their
Bluetooth audio ADK — frame codec, command table, status codes — for models
that *do* have a control channel. Qualcomm never published a specification, so
the command table is transcribed from their reference Android implementation.
It is covered by tests but has not been driven against hardware.

```
pytest
```

## Licence

MIT. Not affiliated with, endorsed by, or derived from Sennheiser or Sonova.
