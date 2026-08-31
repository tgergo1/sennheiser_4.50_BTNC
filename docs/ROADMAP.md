# What can actually be done with an HD 4.50 BTNC

The headset exposes no control channel ([FINDINGS.md](FINDINGS.md)), so the
work splits cleanly in two: everything reachable from the host, and everything
that needs a screwdriver.

Each item is marked with how confident I am that it works, and roughly what it
costs. Nothing below is speculative about the *headphones* — the uncertainty is
in effort, not feasibility, unless it says otherwise.

## Tier 1 — host side, no hardware

### 1. Measured headphone calibration — **done**

oratory1990 measured this exact model on a rig; AutoEq publishes the correction
to the Harman over-ear target. It is now shipped and verified on hardware to
within 0.3%.

```
captune eq start --calibration "HD 4.50 BTNC" --preset Neutral \
  --input "BlackHole 2ch" --output "YourHeadphones"
```

This is the single largest sound improvement available. The correction is not
subtle: −7.2 dB at 2 kHz and +8.2 dB at 3.9 kHz. Presets stack on top —
calibration fixes the headphones, the preset is taste.

### 2. Rebuild SoundCheck — **done**

CapTune's best feature: a blind A/B wizard that converged on a personal curve
by asking which of two renderings you preferred. Nothing about it needed the
headphones. A paired-comparison procedure over a few tilt and band-gain
parameters converges in 15–20 comparisons.

Worth doing properly: randomise presentation order, level-match the pair (an
uncompensated level difference is heard as "better"), and allow "no
preference".

### 3. Crossfeed, and speaker virtualisation — **done**

CapTune had `setVirtualizerStrength`. On headphones each ear hears only its own
channel, which is why hard-panned mixes feel like they are inside your head.
Crossfeed mixes a delayed, low-passed copy of each channel into the other.
Bauer and Meier designs are well documented and are a handful of biquads plus a
delay line — it fits the existing engine directly.

### 4. System audio capture without a virtual device — **done**

macOS 14.2+ has CoreAudio process taps, which would remove BlackHole entirely
and allow a different curve per application.

**What works from Python, verified on this machine:**

- Translating a PID to the `AudioObjectID` CoreAudio uses for a process, via
  `kAudioHardwarePropertyTranslatePIDToProcessObject`. The qualifier argument
  must be `b""`; passing `None` raises "converting to a C array".
- `CATapDescription.initStereoGlobalTapButExcludeProcesses_([self])`, which
  taps everything *except* us — that exclusion is what stops the equaliser's
  own output feeding back into its input.
- `setMuteBehavior_(1)`, so the untreated audio is muted on the device while we
  play the processed copy.
- `AudioHardwareCreateProcessTap` — **returns status 0 and a live tap object**.
- Reading a device's UID, though the CFString comes back as a raw pointer that
  must be copied to a Python string immediately: keeping the wrapper alive
  makes PyObjC release a string it does not own, which crashes later.

**The segfault is solved.** It was not a marshalling problem needing native
code. PyObjC hands the aggregate dictionary key constants back as `bytes`, not
`str`, so the description was built with keys of the wrong type and CoreAudio
dereferenced something that was not a string. One `.decode()` per key fixes it,
and the aggregate now builds: status 0, with the tap on its input side and the
real output device on its output. `opencaptune.audio.tap` implements it.

**The silence is solved too.** A tap created without
`NSAudioCaptureUsageDescription` in the bundle's Info.plist is created happily
and then delivers nothing at all — no error, no prompt, just zeros. With the
key present, captured audio matches the source exactly.

**The glitching is solved too, and the cause was the shape of the device.**
The first design put the tap *and* the headphones in one aggregate and ran a
duplex stream across it. That reported a late block almost every time — 259 in
259 — because an aggregate spanning a system-clocked tap and a jittery
Bluetooth output has two clock domains, and drift compensation did not rescue
it. Larger blocks made it worse: at 1024 and 2048 the callback never fired.

Splitting them fixes it completely. The tap gets an aggregate of its own and is
opened as a pure input; the headphones are opened as an ordinary output; a
small ring buffer takes up the difference. Measured over three seconds, idle
and playing: **258 blocks, zero late, zero dropped, zero starved**, with
captured audio exact. Setting the tap aggregate's nominal rate to the output's
avoids resampling between them.

This is now the default. Per-application curves are the remaining prize — the
tap already knows which process each stream belongs to.

### 5. Real loudness compensation — **done**

Human hearing loses bass and treble at low volume (ISO 226 equal-loudness
contours). CapTune's "Loudness" preset is a fixed approximation of this. A
correct version tracks the actual playback level and interpolates between
contours, so it stops applying the boost as you turn it up.

### 6. Battery and status — *trivial, not done*

macOS already knows the charge level over HFP; `captune devices` could surface
it, along with connection state and the negotiated codec.

## What can actually be sent to the headset

The headset has no vendor control channel, but "no vendor channel" is not "no
channel". Two standard paths carry real traffic, and one more is advertised by
the headset but blocked by macOS.

### AVRCP Absolute Volume — works, and is already in use

Writing a CoreAudio output device's volume makes macOS transmit an AVRCP
`SetAbsoluteVolume` over the air, which the headset applies in its **own
amplifier**. This is not a local gain: it is a command the headset obeys.

The evidence is in the quantisation. Readbacks land on exact multiples of
1/128 — 0.5625 is 72/128, 0.78125 is exactly 100/128 — because the scalar is
mapped onto Absolute Volume's 7-bit field. Nothing else would quantise that
way.

So the volume takeover has been sending the headset commands all along.
`captune headset volume "Name" 100` now does it deliberately.

Worth knowing this is a *different gain stage* from the equaliser's preamp.
The preamp attenuates digitally, before the audio is encoded and transmitted;
this sets the analogue gain at the other end. For a digital chain, keeping the
headset high and attenuating digitally is usually right.

### Battery — readable, in the direction we cannot write

The headset reports its charge over HFP using Apple's `AT+IPHONEACCEV`
extension. We cannot write to that channel, but macOS surfaces what it
carries, so `captune headset status` and the menu bar can show it.

### HFP AT commands — advertised by the headset, blocked by macOS

The headset's own HFP service record says what it accepts: HFP 1.6, features
`0x3A`, which decodes as three-way calling, **voice recognition activation**,
**remote volume control**, and enhanced call status.

That means these are in scope *for this device*:

| Command | Effect |
| --- | --- |
| `+VGS: n` | set the headset's speaker gain, 0–15 — a volume path independent of AVRCP |
| `+VGM: n` | set microphone gain |
| `AT+BVRA=1` | activate voice recognition — the headset advertises support |
| `+CIEV: i,v` | push indicators: service, signal, roam, battery, call state |
| `RING` / `+CLIP:` | make it ring, and announce a caller |
| vendor `AT` | the actual unexplored surface |

macOS even ships the API for it: `IOBluetoothHandsFreeAudioGateway` has
`sendResponse:`, `setOutputVolume:`, `setIndicator:value:`, `setCodecID:`
(CVSD / mSBC / AAC-ELD) and `setVendorID:`.

**It does not work here.** The gateway resolves the service record correctly —
channel 1, features 58 — and then `openRFCOMMChannel` returns nothing and the
service level connection never comes up. It is the same wall as the RFCOMM
sweep: the system holds that channel and will not share it. The API exists;
the channel does not open.

A Linux host owns its own HFP channel, so this is where a Raspberry Pi stops
being optional. Everything in that table becomes testable there, and probing
for vendor `AT` commands is the one genuinely unexplored surface left on this
headset.

## Tier 2 — protocol probing, still no soldering

macOS owns HFP and AVRCP and will not share them, so all of this needs a Linux
host — a Raspberry Pi is enough.

### 7. The HFP AT command channel — *unexplored, genuinely worth trying*

HFP is a serial protocol carrying AT commands, and vendors add their own. Apple
devices send `AT+XAPL` and receive `AT+IPHONEACCEV` for battery. Sennheiser may
well have vendor commands here — this is the one plausible remaining control
surface on a headset with no SPP, and nobody appears to have looked.

On Linux, take over the HFP channel with BlueZ and try. Low cost, real chance
of a surprise.

### 8. AVRCP vendor-dependent PDUs — *unexplored, lower odds*

The headset advertises A/V Remote Control **Target** *and* **Controller**, so it
both sends and receives. AVRCP allows vendor-dependent commands carrying a
company ID. Worth a probe once you own the channel.

### 9. A raw SDP browse from Linux — *cheap, closes a gap*

`sdptool browse` reports what the device actually returns rather than what
macOS chose to cache, and an unrestricted RFCOMM channel sweep is possible
there. This closes the one caveat in FINDINGS.md.

### 10. The USB port — *unknown, costs nothing to check*

The micro-USB port is documented for charging. CSR BlueCore parts do have USB
support, and if the port enumerates at all it is a control surface. Plug it
into the Mac and look:

```
system_profiler SPUSBDataType | grep -iA6 "sennheiser\|CSR\|Cambridge"
```

Most likely it is charge-only. It takes ten seconds to find out.

## Tier 3 — hardware, where custom firmware lives

This voids the warranty and can brick the headphones. It is also where the real
control is.

### 11. Identify the chip

The PnP record reports Bluetooth SIG vendor `0x0A12` — Cambridge Silicon Radio
— so it is a BlueCore part. Given a 2017 ANC headset with aptX, CSR8670 is the
strong expectation, with CSR8675 possible. **This is an inference, not a
measurement**; the FCC internal photos (FCC ID `DMOSCBT6`) would settle it, and
so would simply reading the chip once the case is open.

Either way both are BlueCore and both work with the tooling below.

### 12. Get in

iFixit has a motherboard replacement guide for the HD 4.50, so the teardown is
documented. You are looking for the SPI debug pads: `SPI_CLK`, `SPI_MOSI`,
`SPI_MISO`, `SPI_CS#` and ground, usually exposed as test points.

### 13. Build the programmer — about $5

CSR BlueCore chips have an SPI debug interface in ROM. [csr-spi-ftdi](https://github.com/lorf/csr-spi-ftdi)
is an open-source driver that lets the official CSR BlueSuite tools talk to the
chip through a cheap FT232RL breakout board. No proprietary programmer needed.

### 14. Dump everything, before writing anything

Read the full PS Key store and firmware to a file and keep it somewhere safe.
This is the difference between an experiment and an expensive mistake. The SPI
interface lives in ROM, so recovery is usually possible even after a bad write
— but only if you have something to write back.

### 15. PS Key editing — the realistic ceiling

CSR designs put a surprising amount of behaviour in the persistent store rather
than in code, and PSTool can edit it:

| Key | What it controls |
| --- | --- |
| `PSKEY_DEVICE_NAME` | the Bluetooth name, permanently — not a host-side alias |
| `PSKEY_DEVICE_CLASS` | how the headset presents itself to hosts |
| `PSKEY_USR0`–`USR49` | application-specific: this is where a vendor puts ANC parameters, DSP settings, button behaviour and timers |
| charger keys | battery thresholds and charge current |
| radio keys | transmit power, and so range |

Plausible wins: change the auto-power-off timeout, silence or change the voice
prompts, re-tune the ANC, remap the buttons, extend range.

**The `USR` keys are where the interesting stuff is, and they are undocumented
by definition** — they mean whatever Sennheiser's firmware decides. Finding out
means changing one, listening, and writing down what happened. That is the
actual DIY project.

### 16. Custom firmware — the honest limit

The chip runs a VM application built with the CSR ADK, which is NDA-locked, and
the audio DSP is Kalimba with its own proprietary toolchain. Writing genuinely
new firmware is not realistically on the table without those.

What *is* on the table: flashing a different stock image, and reconfiguring
behaviour through PS Keys — which on CSR designs covers more than you would
expect.

## Suggested order

1. Use the calibration. It is done and it is the biggest audible change.
2. Check the USB port. Ten seconds, small chance of a real find.
3. Build crossfeed and loudness compensation. Cheap, and they make the
   headphones nicer every day.
4. Put a Linux box on the HFP channel. This is the most likely place for an
   undiscovered control surface.
5. Then, if the itch remains, open it up — dump first, edit second.
