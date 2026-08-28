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
