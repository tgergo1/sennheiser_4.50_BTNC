# Does the HD 4.50 BTNC have a control channel?

**No.** Measured on 2026-08-28 against a connected HD 4.50 BTNC
(`00:16:94:41:89:D8`, OUI registered to Sennheiser Communications A/S).

Reproduce with:

```
captune survey 00:16:94:41:89:D8 --rfcomm-sweep
```

## What the headphones advertise

A full SDP browse returns ten records, all of them standard audio profiles:

| UUID | Service |
| --- | --- |
| 0x1000 | Service Discovery Server |
| 0x1108 | Headset |
| 0x110B | Audio Sink |
| 0x110C | A/V Remote Control Target |
| 0x110E | A/V Remote Control |
| 0x110F | A/V Remote Control Controller |
| 0x111E | Handsfree |
| 0x1131 | Headset - HS |
| 0x1200 | PnP Information |
| 0x1203 | Generic Audio |

Two RFCOMM channels exist: 1 (Hands-Free) and 2 (Headset). Both are standard
telephony profiles that macOS itself drives.

Absent, and specifically looked for:

- **Serial Port (0x1101).** Not advertised. This is what a vendor protocol
  normally rides on.
- **GAIA over RFCOMM** (`00001107-D102-11E1-9B23-00025B00A5A5`). Not advertised.
  GAIA is the CSR/Qualcomm vendor protocol; the PnP record confirms a CSR
  chipset (vendor ID `0x0A12`, Bluetooth SIG assigned number for Cambridge
  Silicon Radio), so this is the protocol the device would use if it had one.
- **GAIA over BLE GATT** (`00001100-D102-11E1-9B23-00025B00A5A5`). A 10 second
  BLE scan found 41 peripherals, none advertising this service and none
  identifying as Sennheiser. The headphones do not appear as a BLE peripheral
  at all.

## What this means

A companion app can only change settings a device is willing to expose. These
headphones expose audio transport and standard AVRCP transport controls, and
nothing else. There is no endpoint for an equaliser, a noise-cancelling mode, a
button remap, or a firmware update.

This matches Sennheiser's own position: Smart Control supports no model
launched before 2018 apart from the PXC 550, and the HD 4.50 BTNC is a 2017
model. It also matches what CapTune actually was — a *media player* whose
equaliser, SoundCheck wizard and Sound Profiles processed audio inside the app,
on the phone, before it ever reached the headphones. Nothing was stored on the
headset. That is why the features died with the app, and why they can be
rebuilt entirely host-side.

## One caveat, stated plainly

An unadvertised RFCOMM channel is possible in principle. The sweep that would
rule it out is inconclusive on macOS: every channel from 1 to 30, including the
two that demonstrably exist, fails identically with `kIOReturnError`
(`0xE00002BC`). macOS refuses third-party RFCOMM opens to a device it holds as
an audio endpoint, so the sweep proves nothing either way.

To settle it, run the sweep from Linux, where BlueZ gives an honest answer:

```
sdptool browse 00:16:94:41:89:D8      # raw SDP, no OS-side caching
rfcomm connect /dev/rfcomm0 00:16:94:41:89:D8 <channel>
```

The absence of *any* vendor service in SDP already makes a hidden channel
unlikely: a device that hides its control channel still has to tell its own
companion app where to find it.

---

# What CapTune actually did — from the app itself

Settled by static analysis of CapTune 1.8.1 (`com.sennheiser.captune`, build
722). The APK is signed by `C=DE, L=Wedemark, O=Sennheiser electronic GmbH &
Co. KG, OU=Consumer Division`, a certificate issued 2014-03-27; a repackaged
build would carry a different signing key. It was decompiled and read, never
executed.

## The app sorted headphones into families

`SupportedDeviceHelper` holds the device table. Every model carries a
`FamilyType`, and the HD 4.50BTNC is entry 17:

```java
SUPPORTED_DEVICES.put(17, new SupportedDeviceModel(
    17, 12, SupportedDeviceModel.FamilyType.OTHER, "HD 4.50BTNC", ...));
```

`FamilyType` has three values, and only one of them talks to hardware:

| Family | Models | Device stack |
| --- | --- | --- |
| `EVEREST` | PXC 550, MB 660, MB 660 MS, Dior 550 | `EverestStack` |
| `MOMENTUM` | MOMENTUM, MOMENTUM Free, HD1 range | `GenericDevice` |
| `OTHER` | **HD 4.50BTNC**, HD 4.40BT, CX 7.00BT | `GenericDevice` |

`DeviceFactory` is the whole decision:

```java
static IDevice getAudioSource(SupportedDevice supportedDevice) {
    if (supportedDevice.isEverestDevice()) {
        return new EverestStack();
    }
    return new GenericDevice();
}
```

## `GenericDevice` sends nothing

The class the HD 4.50BTNC is handed implements the device interface as a set of
empty methods:

```java
class GenericDevice implements IDevice {
    public boolean isSPPConnected()                            { return false; }
    public void parse(byte[] bArr)                             { }
    public void send(byte[] bArr)                              { }
    public void setBTConnection(IRemoteDevice.IDeviceConnection c) { }
```

There is no protocol, no handshake and no write path. **CapTune never sent
these headphones a single byte.** Its equaliser methods call
`AudioWeaverLibrary`, a bundled native DSP that processed audio on the phone
before it was streamed to the headset.

By contrast `EverestStack` — the PXC 550 and MB 660 — carries a real protocol
(`EverestTransmission` / `EverestReception` over an SPP socket on UUID
`1ddce62a-ecb1-4455-8153-0743c87aec9f`), with opcodes for noise cancellation
level, EQ mode, battery voltage and audio prompts. That is the app-facing
control channel the HD 4.50 BTNC never had.

This independently confirms the SDP evidence above, from the other end: the
headphones advertise no control service, and the app never looked for one.

## What this means for the rebuild

Everything CapTune did for these headphones happened host-side, so all of it
can be rebuilt host-side — and the tuning does not have to be guessed, because
the curves shipped as data. They are extracted verbatim into
`src/opencaptune/eq/presets.json`:

- **14 bands**, centred at 35, 57, 92, 148, 238, 384, 620, 1000, 1613, 2601,
  4196, 6767, 10195 and 17605 Hz — a constant 1.613 ratio, 0.69 octaves apart.
- **9 presets**: Neutral, Loudness, Pop, Rock, Hip Hop, Electro, Jazz,
  Classical, Voice. (A tenth entry, "Custom", is the user's scratch slot.)
- **Bass and treble boost offset curves**, added on top of a preset and scaled
  by a 0-100 slider, then clamped to ±12 dB.

The only part that cannot be recovered is the filter design: the DSP was a
closed native library. `opencaptune.eq` uses standard peaking biquads with a Q
derived from the band spacing, which reproduces the intended response curve.
