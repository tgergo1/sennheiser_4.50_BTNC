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

## The menu bar app

```
captune ui
```

A sliders icon appears in the menu bar. The menu opens onto what the equaliser
is currently doing — the preset and calibration in force, the device, its
battery, the preamp — over a live spectrum of whatever is playing. Crossfeed,
loudness and the headset's own volume are continuous sliders that take effect
as you drag them; preset, calibration, profiles and output device are below,
along with the equaliser window and Sound Check.

The spectrum is only measured while the menu is open, so nothing is computed
for a display nobody is looking at.

It holds no state of its own — everything it shows is read back from the
daemon every two seconds, so the menu and the audio can never disagree, and
the CLI and the menu can be used interchangeably.

### Profiles

A profile is a whole setup under one name — preset, calibration, crossfeed,
loudness and the output device it belongs to. Settings that suit headphones
are wrong for speakers, and the device is what tells them apart.

```
captune profile save "Music"      # captures what is running right now
captune profile apply "Music"
captune profile list
```

**Profiles → Save current as…** does the same from the menu bar.

**Start when … connects** in the menu names the device your applied profile
plays to. Switch it on and the equaliser starts by itself when that device
appears and stops when it goes away, so plugging in the headphones is the only
thing you have to do.

```
captune autostart enable          # and start the menu bar app at login
```

### The equaliser window

**Equaliser window…** opens a live view: the fourteen bands as a curve you
drag directly, drawn over a running spectrum of whatever is playing. Dragging a
handle changes the sound immediately.

### Sound Check

CapTune's best feature, rebuilt. **Sound Check…** runs twelve blind
comparisons — bass, lower mid, upper mid, treble, three passes each with the
step halving every pass — and converges on the curve you actually prefer.

Both options in every pair are **matched for loudness**: the average level is
removed from each, so a comparison is about tone. Without that the louder
option wins almost every time regardless of how it sounds, which is the
classic way listening tests lie to you.

Save the result with **Profiles → Save current as…**.

## System-wide equaliser

Everything the Mac plays goes through the filter — Spotify, YouTube, anything.

```
captune eq start
captune eq set Voice --bass 30     # change curve without interrupting playback
captune eq status
captune eq stop
```

It plays to whatever your Mac is already playing to, so there is nothing to
pick. `--output` overrides that if you want the equalised audio to come out
somewhere else.

No virtual audio device, no driver to install, and nothing to change in System
Settings. A CoreAudio process tap asks the system directly for the audio it is
already playing, muted at the far end so you hear only the equalised copy. The
tap excludes this app, so its own output cannot be captured and fed back round.

Requires macOS 14.2 or later.

**Latency** is about 12 ms at the default 512-frame block, on top of
Bluetooth's own. `captune eq status` counts anything that goes wrong: blocks
that arrived late, audio dropped because playback fell behind, and silence
played because it got ahead.

### The microphone

It does not use one, and it cannot: the bundle asks for no microphone
permission at all, and there is no input device anywhere in the path. macOS
gates the tap under audio capture instead, so nothing here appears as a
microphone or lights the indicator.

An earlier design read a virtual loopback device, and that could not be made
true of it — reading a virtual device is audio input as far as macOS is
concerned, however virtual the device is. Being rid of that is most of why the
tap exists.

### Volume

Your volume keys act on whichever device is the system output, so once that is
BlackHole they control BlackHole — and the headphones' own device volume is
left wherever it happened to be, silently attenuating everything the equaliser
sends, with no control able to reach it.

So the equaliser raises its output device to full while it runs and puts it
back when it stops. Volume then lives entirely on BlackHole, where your keys
are. `captune eq status` says when it has done this. Pass
`--no-manage-volume` to leave the device alone.

Corrections cost level on top of that, and unavoidably: you cannot boost
3.9 kHz by 8 dB without making room for it first. The calibration alone takes
6.2 dB. If you want the loudest possible output, run `Neutral` with no
calibration, which needs no preamp at all.

**To hear your Mac normally again**, `captune eq stop`. Nothing else needs
undoing: the system output never moved.

## Headphone calibration

Presets are taste. A calibration is measurement: what this particular model
gets wrong, from a rig. oratory1990 measured the HD 4.50 BTNC and AutoEq
publishes the correction to the Harman over-ear target; it ships here.

```
captune eq calibrations
captune eq start --preset Neutral --calibration "HD 4.50 BTNC"
```

### Any other headphones

The same machinery works for anything you own. AutoEq collects measurements
from two dozen independent measurers covering thousands of models:

```
captune eq fetch "HD 650"              # search, download, save
captune eq fetch "K371" --list         # just show what matches
captune eq import curve.txt --name "…" # a local AutoEq/EqualizerAPO file
captune eq forget "…"                  # remove an imported one
```

Imported corrections sit alongside the shipped one and appear in the menu bar
without a restart.

### About the shipped profile

It is not a subtle correction — −7.2 dB at 2 kHz and +8.2 dB at 3.9 kHz — and
it is the single biggest improvement available to these headphones. Presets
stack on top of it: the calibration fixes the headphones, the preset is taste.
`captune eq calibrate off` removes it while running, which is the honest way to
hear what it does.

Verified against the hardware at six frequencies, exercising both shelves and
the largest cut and boost, to within 0.3% of the predicted response.

## Spatial audio

```
captune eq spatial 60
```

Headphones put each channel straight into one ear, which never happens in life
and is why stereo sits between your ears instead of in front of you. With
speakers each ear also hears the *other* one — later, because it travelled
further around your head, and duller, because your head was in the way. Those
two cues are most of what tells you a sound is out there.

This puts both back, as a pair of virtual speakers at ±30°: the delay comes
from Woodworth's formula for a spherical head (0.261 ms at that angle) and the
shadow from a shelf filter on the crossed path.

It is a structural model, not a measured head-related transfer function. There
is no pinna modelling and no elevation, and without a head tracker the image
does not stay put when you turn — a measured HRTF would need a dataset and a
listener it was measured on. This is the part that does most of the work.

Verified against the hardware: the level at 0%, 50% and 100% strength matched
prediction to 0.0%.

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

## Listening exposure

```
captune listening dose
```

```
  Last 7 days   6.2 hours
  Average       78 dB
  Loudest       91 dB
  Weekly dose   [########....................] 29%
```

Hearing damage depends on loudness and time *together*. The occupational
standard treats 80 dB for 40 hours a week as a full dose and trades 3 dB
against half the time, so 86 dB for 10 hours costs the same as 80 dB for 40.
An hour of something loud can outweigh a week of something quiet — which is
why this accumulates energy rather than sampling loudness, in hourly buckets
over a rolling week. The loudest single interval is kept separately, because
averaging a short loud burst over an hour hides exactly the thing worth
seeing.

The menu bar shows the same figure, and `captune listening reset` clears it.

**On the accuracy of the number.** The signal level is known exactly and the
pressure at your ear is not: that depends on the headphone's sensitivity and
the amplifier behind it, and nothing reports either. The figure rests on one
stated assumption — that full-scale audio at full volume comes to 100 dB SPL
at these headphones. Calibrate that against a sound level meter and the number
becomes real; leave it and the trend is still honest even when the absolute
value is not.

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
| `captune eq spatial 0-100` | render as virtual speakers in front of you |
| `captune eq crossfeed 0-100` | narrow the stereo image at low frequencies |
| `captune listening dose\|reset` | how much you have listened to, and how loud |
| `captune eq loudness PHON\|off` | equal-loudness compensation for your listening level |
| `captune eq show NAME [--bass N] [--treble N]` | display a curve |
| `captune eq export NAME [--format apo\|json\|biquad]` | write a curve out |
| `captune eq start` | start the always-on equaliser |
| `captune eq set NAME` / `stop` / `status` | control it while it runs |
| `captune eq status --reset` | show it, then clear the counters |
| `captune ui` | open the menu bar app |
| `captune profile list\|save\|apply\|delete NAME` | named setups |
| `captune autostart enable\|disable\|status` | start the app at login |
| `captune audio devices` | list CoreAudio devices |
| `captune devices` | list paired Bluetooth devices |
| `captune survey ADDR` | report a headphone's control channel, if any |
| `captune bundle` | rebuild the macOS helper bundle |

## Talking to the headset itself

```
captune headset status
captune headset volume "YourHeadphones" 100
```

The HD 4.50 BTNC has no vendor control channel, but standard profiles still
carry traffic in both directions. Setting its volume transmits an AVRCP
`SetAbsoluteVolume` that the headset applies in its own amplifier — a command,
not a local gain — and the headset reports its battery back over HFP. Both are
in the menu bar under **Headset volume**.

[docs/ROADMAP.md](docs/ROADMAP.md) has the evidence, and the table of HFP
commands this headset advertises that macOS will not let us send.

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

The bundle is an `LSUIElement` accessory app rather than `LSBackgroundOnly`,
which is what lets the same bundle both run the daemon in the background and
put an item in the menu bar.

### A note on stacking corrections

Each correction takes headroom, and they add up. A boosted preset over the
calibration over loudness compensation can reach a preamp of −17 dB, which is
correct — it is what stops the combination clipping — but it will be quiet.
`captune eq status` always shows the preamp in force.

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
