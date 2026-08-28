"""Reading and writing CoreAudio device volume.

This exists because of a trap in the virtual-device routing. Your volume keys
act on whichever device is the *system output*, so once that becomes the
virtual device the real output device's own volume is stranded at whatever it
happened to be — unreachable, and silently attenuating everything the
equaliser sends. On this machine it sat at 0.56, throwing away nearly half the
available level with no control able to reach it.

So the equaliser takes its output device to unity while it runs and puts it
back when it stops. Volume then lives entirely on the virtual device, where
the keyboard can reach it.

Everything here is best-effort: not every device exposes a settable volume,
and failing to adjust one is never a reason to refuse to play audio.
"""

from __future__ import annotations

import struct

MASTER_ELEMENT = 0
CHANNEL_ELEMENTS = (1, 2)


def _core_audio():
    import CoreAudio  # noqa: PLC0415 - only available inside the helper bundle

    return CoreAudio


def _address(selector, scope, element):
    CoreAudio = _core_audio()
    return CoreAudio.AudioObjectPropertyAddress(selector, scope, element)


def _output_scope():
    return _core_audio().kAudioObjectPropertyScopeOutput


def _read_float(device: int, element: int) -> float | None:
    CoreAudio = _core_audio()
    address = _address(
        CoreAudio.kAudioDevicePropertyVolumeScalar, _output_scope(), element
    )
    if not CoreAudio.AudioObjectHasProperty(device, address):
        return None
    status, _, blob = CoreAudio.AudioObjectGetPropertyData(
        device, address, 0, b"", 4, None
    )
    if status != 0:
        return None
    return float(struct.unpack("f", bytes(blob))[0])


def _write_float(device: int, element: int, value: float) -> bool:
    CoreAudio = _core_audio()
    address = _address(
        CoreAudio.kAudioDevicePropertyVolumeScalar, _output_scope(), element
    )
    if not CoreAudio.AudioObjectHasProperty(device, address):
        return False
    settable = CoreAudio.AudioObjectIsPropertySettable(device, address, None)
    if not (settable[1] if isinstance(settable, tuple) else settable):
        return False
    status = CoreAudio.AudioObjectSetPropertyData(
        device, address, 0, b"", 4, struct.pack("f", value)
    )
    return status == 0


def _device_ids() -> tuple[int, ...]:
    CoreAudio = _core_audio()
    address = _address(
        CoreAudio.kAudioHardwarePropertyDevices,
        CoreAudio.kAudioObjectPropertyScopeGlobal,
        CoreAudio.kAudioObjectPropertyElementMain,
    )
    status, size = CoreAudio.AudioObjectGetPropertyDataSize(
        CoreAudio.kAudioObjectSystemObject, address, 0, b"", None
    )
    if status != 0 or not size:
        return ()
    status, _, blob = CoreAudio.AudioObjectGetPropertyData(
        CoreAudio.kAudioObjectSystemObject, address, 0, b"", size, None
    )
    if status != 0:
        return ()
    return struct.unpack(f"{size // 4}I", bytes(blob))


def _name(device: int) -> str | None:
    import objc  # noqa: PLC0415

    CoreAudio = _core_audio()
    address = _address(
        CoreAudio.kAudioObjectPropertyName,
        CoreAudio.kAudioObjectPropertyScopeGlobal,
        CoreAudio.kAudioObjectPropertyElementMain,
    )
    status, size = CoreAudio.AudioObjectGetPropertyDataSize(device, address, 0, b"", None)
    if status != 0 or size != 8:
        return None
    status, _, blob = CoreAudio.AudioObjectGetPropertyData(device, address, 0, b"", 8, None)
    if status != 0:
        return None
    pointer = struct.unpack("Q", bytes(blob))[0]
    if not pointer:
        return None
    # Copy the text out now: the wrapper must not outlive this call or PyObjC
    # will release a string it does not own.
    return str(objc.objc_object(c_void_p=pointer))


def find_output_device(name: str) -> int | None:
    """The CoreAudio device with this name that has an output volume control.

    A Bluetooth headset appears twice, once as its microphone and once as its
    output, under the same name. Requiring a volume control in the output
    scope is what tells them apart.
    """
    for device in _device_ids():
        if _name(device) != name:
            continue
        if any(_read_float(device, e) is not None for e in (MASTER_ELEMENT, *CHANNEL_ELEMENTS)):
            return device
    return None


def get_volume(device: int) -> float | None:
    """Device volume as 0.0-1.0, from the master control or the channels."""
    master = _read_float(device, MASTER_ELEMENT)
    if master is not None:
        return master
    channels = [v for v in (_read_float(device, e) for e in CHANNEL_ELEMENTS) if v is not None]
    return sum(channels) / len(channels) if channels else None


def set_volume(device: int, value: float) -> bool:
    """Set device volume. Returns whether anything was actually changed."""
    value = min(1.0, max(0.0, value))
    if _write_float(device, MASTER_ELEMENT, value):
        return True
    # Every channel must be written. `any(...)` over a generator would stop at
    # the first success and leave the other channel where it was, which on a
    # stereo device means one ear quieter than the other.
    written = [_write_float(device, element, value) for element in CHANNEL_ELEMENTS]
    return any(written)
