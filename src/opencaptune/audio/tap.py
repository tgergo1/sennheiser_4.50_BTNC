"""Capturing system audio with a CoreAudio process tap.

The alternative to this is a virtual loopback device such as BlackHole, which
works but has two costs: it must be installed, and reading it is *audio input*
as far as macOS is concerned, so the microphone indicator lights up and the
system serves the recording from whatever the default input device happens to
be. Neither is acceptable for something that only ever wants the audio the
machine is already playing.

A process tap asks CoreAudio directly for that audio. The tap is global but
excludes this process, which is what stops our own output being captured and
fed back round. It is created muted, so the untreated audio does not reach the
headphones alongside the processed copy.

The tap is then wrapped in a private aggregate device together with the real
output device, giving one device with the tap on its input side and the
headphones on its output — which any ordinary audio library can open.

Requires macOS 14.2 or later.
"""

from __future__ import annotations

import os
import struct

#: CATapDescription mute behaviours.
UNMUTED = 0
MUTED = 1
MUTED_WHEN_TAPPED = 2

AGGREGATE_NAME = "OpenCapTune Tap"
AGGREGATE_UID = "org.opencaptune.tap"


class TapError(RuntimeError):
    pass


def _core_audio():
    import CoreAudio  # noqa: PLC0415

    return CoreAudio


def _address(selector, scope=None, element=None):
    CoreAudio = _core_audio()
    return CoreAudio.AudioObjectPropertyAddress(
        selector,
        scope or CoreAudio.kAudioObjectPropertyScopeGlobal,
        CoreAudio.kAudioObjectPropertyElementMain if element is None else element,
    )


def _read(obj, address, size, qualifier=b""):
    CoreAudio = _core_audio()
    status, _, blob = CoreAudio.AudioObjectGetPropertyData(
        obj, address, len(qualifier), qualifier, size, None
    )
    return status, bytes(blob)


def _cfstring(obj, address) -> str | None:
    import objc  # noqa: PLC0415

    CoreAudio = _core_audio()
    status, size = CoreAudio.AudioObjectGetPropertyDataSize(obj, address, 0, b"", None)
    if status != 0 or size != 8:
        return None
    status, blob = _read(obj, address, 8)
    if status != 0:
        return None
    pointer = struct.unpack("Q", blob)[0]
    # Copy the text out immediately; the wrapper must not outlive this call.
    return str(objc.objc_object(c_void_p=pointer)) if pointer else None


def _key(constant):
    """CoreAudio dictionary keys.

    PyObjC hands these back as ``bytes``. Passing them straight into the
    description dictionary produces keys of the wrong type, and CoreAudio then
    dereferences something that is not a string and crashes the process — with
    no error, no exception, just SIGSEGV inside the framework.
    """
    return constant.decode() if isinstance(constant, bytes) else constant


def available() -> bool:
    """Whether this macOS has the process tap API."""
    try:
        CoreAudio = _core_audio()
    except ImportError:
        return False
    return hasattr(CoreAudio, "AudioHardwareCreateProcessTap") and hasattr(
        CoreAudio, "CATapDescription"
    )


def _own_process_object() -> int:
    CoreAudio = _core_audio()
    status, blob = _read(
        CoreAudio.kAudioObjectSystemObject,
        _address(CoreAudio.kAudioHardwarePropertyTranslatePIDToProcessObject),
        4,
        struct.pack("i", os.getpid()),
    )
    if status != 0:
        raise TapError("could not identify this process to CoreAudio")
    return struct.unpack("I", blob)[0]


def device_uid(device: int) -> str | None:
    CoreAudio = _core_audio()
    return _cfstring(device, _address(CoreAudio.kAudioDevicePropertyDeviceUID))


class SystemCapture:
    """A tap plus the aggregate device that exposes it.

    Both are torn down by :meth:`close`, and leaving one behind would leave a
    stray device in the system's list, so callers should use it as a context
    manager.
    """

    def __init__(
        self,
        output_device: int | None = None,
        mute_original: bool = True,
        sample_rate: float | None = None,
    ) -> None:
        # A tap-only aggregate: combining the tap and a Bluetooth output in one
        # aggregate and running duplex across it makes nearly every block
        # arrive late, because they are separate clock domains and the
        # Bluetooth side is jittery. As a pure input it is exact.
        self.sample_rate = sample_rate
        self.mute_original = mute_original
        self.tap = None
        self.aggregate = None
        self.name = AGGREGATE_NAME

    def __enter__(self) -> "SystemCapture":
        self.open()
        return self

    def __exit__(self, *exception) -> None:
        self.close()

    def open(self) -> None:
        CoreAudio = _core_audio()
        if not available():
            raise TapError("process taps need macOS 14.2 or later")

        description = CoreAudio.CATapDescription.alloc().initStereoGlobalTapButExcludeProcesses_(
            [_own_process_object()]
        )
        description.setName_("OpenCapTune system tap")
        description.setPrivate_(True)
        description.setMuteBehavior_(MUTED if self.mute_original else UNMUTED)

        status, tap = CoreAudio.AudioHardwareCreateProcessTap(description, None)
        if status != 0:
            raise TapError(f"could not create the audio tap (status {status})")
        self.tap = tap

        tap_uid = _cfstring(tap, _address(CoreAudio.kAudioTapPropertyUID))
        if not tap_uid:
            self.close()
            raise TapError("the tap did not report a UID")

        configuration = {
            _key(CoreAudio.kAudioAggregateDeviceNameKey): AGGREGATE_NAME,
            _key(CoreAudio.kAudioAggregateDeviceUIDKey): AGGREGATE_UID,
            _key(CoreAudio.kAudioAggregateDeviceIsPrivateKey): 1,
            _key(CoreAudio.kAudioAggregateDeviceIsStackedKey): 0,
            _key(CoreAudio.kAudioAggregateDeviceTapAutoStartKey): 1,
            _key(CoreAudio.kAudioAggregateDeviceTapListKey): [
                {_key(CoreAudio.kAudioSubTapUIDKey): tap_uid}
            ],
        }
        status, aggregate = CoreAudio.AudioHardwareCreateAggregateDevice(configuration, None)
        if status != 0:
            self.close()
            raise TapError(f"could not create the capture device (status {status})")
        self.aggregate = aggregate
        if self.sample_rate:
            self._match_sample_rate(self.sample_rate)

    def _match_sample_rate(self, rate: float) -> bool:
        """Ask the tap device to run at the output's rate, to avoid resampling."""
        CoreAudio = _core_audio()
        address = _address(CoreAudio.kAudioDevicePropertyNominalSampleRate)
        status = CoreAudio.AudioObjectSetPropertyData(
            self.aggregate, address, 0, b"", 8, struct.pack("d", float(rate))
        )
        return status == 0

    def nominal_sample_rate(self) -> float | None:
        CoreAudio = _core_audio()
        status, blob = _read(
            self.aggregate, _address(CoreAudio.kAudioDevicePropertyNominalSampleRate), 8
        )
        return struct.unpack("d", blob)[0] if status == 0 else None

    def close(self) -> None:
        CoreAudio = _core_audio()
        if self.aggregate is not None:
            CoreAudio.AudioHardwareDestroyAggregateDevice(self.aggregate)
            self.aggregate = None
        if self.tap is not None:
            CoreAudio.AudioHardwareDestroyProcessTap(self.tap)
            self.tap = None
