"""The always-on equaliser: read one device, filter, write another.

Paired with a virtual output device such as BlackHole this gives system-wide
equalisation.  macOS plays into the virtual device, this reads from it, and the
filtered result goes to the headphones.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field

import numpy as np

from .. import eq
from ..eq import loudness as loudness_module
from .crossfeed import Crossfeed
from . import tap as system_tap
from . import volume as volume_control
from .devices import Device, resolve
from .dsp import Equaliser


@dataclass
class EngineConfig:
    output_device: str | int
    #: "tap" captures system audio directly with a CoreAudio process tap: no
    #: virtual device, no output-device switching. "device" reads a loopback
    #: such as BlackHole. "auto" takes the tap where the OS supports it.
    capture: str = "auto"
    input_device: str | int | None = None
    preset: str = "Neutral"
    calibration: str | None = None
    loudness_phon: float | None = None
    reference_phon: float = loudness_module.DEFAULT_REFERENCE_PHON
    crossfeed: int = 0
    manage_volume: bool = True
    manage_input: bool = True
    bass: int = 0
    treble: int = 0
    sample_rate: int | None = None
    block_size: int = 512
    channels: int = 2
    q: float | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class _Ring:
    """A small circular buffer bridging the capture and playback callbacks.

    The tap and the headphones are separate devices, so they run on separate
    clocks even when both are nominally at the same rate. This absorbs the
    jitter between them, drops the oldest audio if playback falls behind, and
    plays silence rather than stale audio if it gets ahead.
    """

    def __init__(self, frames: int, channels: int) -> None:
        self._buffer = np.zeros((frames, channels), dtype=np.float32)
        self._size = frames
        self._read = 0
        self._write = 0
        self._count = 0
        self._lock = threading.Lock()
        self.dropped = 0
        self.starved = 0

    @property
    def filled(self) -> int:
        with self._lock:
            return self._count

    def write(self, block: np.ndarray) -> None:
        frames = len(block)
        with self._lock:
            spare = self._size - self._count
            if frames > spare:
                discard = frames - spare
                self._read = (self._read + discard) % self._size
                self._count -= discard
                self.dropped += discard
            first = min(frames, self._size - self._write)
            self._buffer[self._write : self._write + first] = block[:first]
            if first < frames:
                self._buffer[: frames - first] = block[first:]
            self._write = (self._write + frames) % self._size
            self._count += frames

    def read(self, frames: int) -> np.ndarray:
        out = np.zeros((frames, self._buffer.shape[1]), dtype=np.float32)
        with self._lock:
            available = min(frames, self._count)
            if available < frames:
                self.starved += frames - available
            first = min(available, self._size - self._read)
            out[:first] = self._buffer[self._read : self._read + first]
            if first < available:
                out[first:available] = self._buffer[: available - first]
            self._read = (self._read + available) % self._size
            self._count -= available
        return out


@dataclass
class Stats:
    frames: int = 0
    glitches: int = 0
    peak: float = 0.0


class Engine:
    """Streams audio from one device through the equaliser to another."""

    def __init__(self, config: EngineConfig) -> None:
        self.config = config
        self.stats = Stats()
        self._stream = None
        self._input_stream = None
        self._ring = None
        self._lock = threading.Lock()
        self._restore_volume: tuple[int, float] | None = None
        # Held by name, not by id: CoreAudio reassigns device ids when a
        # device re-enumerates, and a Bluetooth headset does that whenever it
        # reconnects. Restoring a stale id would point the default input at
        # whatever inherited the number.
        self._restore_input: str | None = None
        self._spectrum = np.full(len(eq.bands()), -90.0)
        self._bins = None
        # Only measured while something is displaying it.
        self._watching_spectrum = False

        self.output = resolve(config.output_device, "output")

        self.capture_mode = config.capture
        if self.capture_mode == "auto":
            self.capture_mode = "tap" if system_tap.available() else "device"
        if self.capture_mode == "tap" and not system_tap.available():
            raise ValueError("process taps need macOS 14.2 or later")
        if self.capture_mode not in ("tap", "device"):
            raise ValueError(f"unknown capture mode {config.capture!r}")

        self.input = None
        if self.capture_mode == "device":
            if not config.input_device:
                raise ValueError("device capture needs an input device, e.g. 'BlackHole 2ch'")
            self.input = resolve(config.input_device, "input")
            if self.input.index == self.output.index:
                raise ValueError(
                    f"input and output are the same device ({self.input.name!r}); "
                    "the point of the loop is that they differ"
                )
            # Never capture from a headset's own microphone: opening it forces
            # the headset out of A2DP into hands-free mode — mono, narrow band,
            # microphone live. We only ever need the loopback.
            if self.input.name == self.output.name:
                raise ValueError(
                    f"{self.input.name!r} is the headset's own microphone. Capturing "
                    "from it would switch the headset into call mode. Use a virtual "
                    "loopback device such as BlackHole as the input."
                )

        self.sample_rate = int(config.sample_rate or self.output.default_sample_rate)
        self.channels = min(config.channels, self.output.output_channels)
        if self.input is not None:
            self.channels = min(self.channels, self.input.input_channels)
        self._capture = None
        if self.channels < 1:
            raise ValueError("the chosen devices have no usable channels in common")

        self._crossfeed = Crossfeed(
            strength=config.crossfeed, sample_rate=self.sample_rate, channels=self.channels
        )
        self._equaliser = Equaliser(
            self._preset_from(config.preset, config.bass, config.treble),
            sample_rate=self.sample_rate,
            channels=self.channels,
            q=config.q,
            calibration=eq.calibration(config.calibration) if config.calibration else None,
            loudness=self._loudness_filters(config.loudness_phon, config.reference_phon),
        )

    @staticmethod
    def _preset_from(name: str, bass: int, treble: int) -> eq.Preset:
        if name == "Custom":
            return eq.Preset("Custom", tuple(0.0 for _ in eq.bands()))
        return eq.preset(name).with_boosts(bass=bass, treble=treble)

    @staticmethod
    def _loudness_filters(phon: float | None, reference: float):
        if phon is None:
            return ()
        return loudness_module.compensation(phon, reference)

    def set_loudness(self, phon: float | None) -> None:
        """Change the level compensation while streaming."""
        filters = self._loudness_filters(phon, self.config.reference_phon)
        with self._lock:
            self._equaliser.set_loudness(filters)
        self.config.loudness_phon = phon

    def set_curve(self, gains) -> None:
        """Apply an arbitrary 14-band curve, for the editor and Sound Check."""
        bands = eq.bands()
        if len(gains) != len(bands):
            raise ValueError(f"expected {len(bands)} gains, got {len(gains)}")
        clamped = tuple(
            min(eq.MAX_GAIN_DB, max(eq.MIN_GAIN_DB, float(g))) for g in gains
        )
        with self._lock:
            self._equaliser.set_preset(eq.Preset("Custom", clamped))
        self.config.preset = "Custom"

    def watch_spectrum(self, enabled: bool) -> None:
        self._watching_spectrum = bool(enabled)

    def spectrum(self) -> list[float]:
        return [round(float(v), 1) for v in self._spectrum]

    def set_crossfeed(self, strength: int) -> None:
        """Change the crossfeed strength while streaming."""
        with self._lock:
            self._crossfeed.set_strength(strength)
        self.config.crossfeed = strength

    @property
    def preset(self) -> eq.Preset:
        return self._equaliser.preset

    @property
    def running(self) -> bool:
        return self._stream is not None and self._stream.active

    def reset_stats(self) -> None:
        """Clear the counters, e.g. after fixing a source of glitches."""
        self.stats = Stats()

    def set_calibration(self, name: str | None) -> None:
        """Change the headphone correction while streaming."""
        replacement = eq.calibration(name) if name else None
        with self._lock:
            self._equaliser.set_calibration(replacement)
        self.config.calibration = name

    def set_preset(self, name: str, bass: int = 0, treble: int = 0) -> None:
        """Change curve while streaming."""
        replacement = self._preset_from(name, bass, treble)
        with self._lock:
            self._equaliser.set_preset(replacement)
        self.config.preset, self.config.bass, self.config.treble = name, bass, treble

    def _band_bins(self, frames: int):
        """Which FFT bins belong to each equaliser band.

        Cached per block size: the mapping only changes if the block does.
        """
        if self._bins is not None and self._bins[0] == frames:
            return self._bins[1]
        frequencies = np.fft.rfftfreq(frames, 1.0 / self.sample_rate)
        centres = np.array(eq.bands(), dtype=float)
        # Split between neighbours geometrically, which is how the bands are spaced.
        edges = np.concatenate(
            [[centres[0] / 1.3], np.sqrt(centres[:-1] * centres[1:]), [centres[-1] * 1.3]]
        )
        groups = [
            np.where((frequencies >= low) & (frequencies < high))[0]
            for low, high in zip(edges, edges[1:])
        ]
        self._bins = (frames, groups)
        return groups

    def _measure_spectrum(self, block) -> None:
        mono = block.mean(axis=1)
        spectrum = np.abs(np.fft.rfft(mono * np.hanning(len(mono)))) / (len(mono) / 2)
        levels = []
        for group in self._band_bins(len(mono)):
            energy = float(np.sqrt(np.mean(spectrum[group] ** 2))) if len(group) else 0.0
            levels.append(20.0 * np.log10(energy + 1e-9))
        measured = np.array(levels)
        # Fast attack, slow release, so the display is readable rather than jittery.
        rising = measured > self._spectrum
        self._spectrum = np.where(
            rising, measured, self._spectrum * 0.8 + measured * 0.2
        )

    def _callback(self, indata, outdata, frames, time_info, status) -> None:
        if status:
            self.stats.glitches += 1
        with self._lock:
            filtered = self._equaliser.process(self._crossfeed.process(indata))
            if self._watching_spectrum and frames:
                self._measure_spectrum(filtered)
        outdata[:] = filtered
        self.stats.frames += frames
        peak = float(abs(filtered).max()) if frames else 0.0
        if peak > self.stats.peak:
            self.stats.peak = peak

    def _take_output_volume(self) -> None:
        """Put the output device at unity so nothing attenuates behind our back.

        Once the system output is the virtual device, the volume keys act on
        that and can no longer reach the real output device, whose own volume
        then silently scales everything we send.
        """
        if not self.config.manage_volume:
            return
        try:
            device = volume_control.find_output_device(self.output.name)
            if device is None:
                return
            current = volume_control.get_volume(device)
            if current is None or current >= 0.999:
                return
            if volume_control.set_volume(device, 1.0):
                self._restore_volume = (device, current)
        except Exception:  # noqa: BLE001 - never block playback over this
            self._restore_volume = None

    def _steer_default_input_away(self) -> None:
        """Stop macOS reaching for the headset's microphone while we record.

        Reading the loopback device counts as microphone use, and macOS serves
        that from whichever device is the *default* input. When that default is
        the headset, it gets pulled out of A2DP into hands-free mode: mono,
        narrow band, microphone live. Pointing the default at anything else for
        the duration avoids it.
        """
        if not self.config.manage_input:
            return
        try:
            current = volume_control.default_input_device()
            if current is None:
                return
            if volume_control.device_name(current) != self.output.name:
                return  # the default is not the headset; nothing to do

            alternatives = [
                (device, name)
                for device, name in volume_control.input_devices()
                if name != self.output.name and name != self.input.name
            ]
            # Prefer the machine's own microphone over another accessory.
            alternatives.sort(key=lambda entry: 0 if "MacBook" in entry[1] else 1)
            if not alternatives:
                return
            previous_name = volume_control.device_name(current)
            if volume_control.set_default_input_device(alternatives[0][0]):
                self._restore_input = previous_name
        except Exception:  # noqa: BLE001 - never block playback over this
            self._restore_input = None

    def _give_default_input_back(self) -> None:
        if self._restore_input is None:
            return
        try:
            device = volume_control.find_input_device(self._restore_input)
            if device is not None:
                volume_control.set_default_input_device(device)
        except Exception:  # noqa: BLE001
            pass
        self._restore_input = None

    def _give_output_volume_back(self) -> None:
        if self._restore_volume is None:
            return
        device, previous = self._restore_volume
        try:
            volume_control.set_volume(device, previous)
        except Exception:  # noqa: BLE001
            pass
        self._restore_volume = None

    def _open_tap(self):
        """Create the tap device and return the index PortAudio knows it by."""
        import time  # noqa: PLC0415

        import sounddevice  # noqa: PLC0415

        # Matched to the output's rate so nothing has to be resampled between
        # the two devices.
        capture = system_tap.SystemCapture(sample_rate=float(self.sample_rate))
        capture.open()
        self._capture = capture

        try:
            # PortAudio enumerates devices when it initialises, and the
            # aggregate did not exist then. Re-initialise so that it appears —
            # and give CoreAudio a moment, because publishing a new device is
            # not instantaneous and the first look can miss it.
            for attempt in range(10):
                sounddevice._terminate()
                sounddevice._initialize()
                for index, entry in enumerate(sounddevice.query_devices()):
                    if entry["name"] == capture.name and entry["max_input_channels"] > 0:
                        return index
                time.sleep(0.15)
            raise system_tap.TapError("the capture device did not appear to the audio layer")
        except Exception:
            # Leaving the aggregate behind would leave a stray device in the
            # system's list and block the next attempt.
            self._close_capture()
            raise

    def _start_tap_streams(self) -> None:
        """Capture and playback as two independent streams.

        One duplex stream across a device holding both the tap and a Bluetooth
        output reports a late block almost every time: two clock domains, one
        of them jittery. Kept apart, each side runs exactly on time and a ring
        buffer takes up the difference.
        """
        import sounddevice  # noqa: PLC0415

        index = self._open_tap()
        block = self.config.block_size
        self._ring = _Ring(block * 8, self.channels)
        try:
            self._input_stream = sounddevice.InputStream(
                device=index,
                samplerate=self.sample_rate,
                blocksize=block,
                channels=self.channels,
                dtype="float32",
                callback=self._capture_callback,
            )
            self._stream = sounddevice.OutputStream(
                device=self.output.index,
                samplerate=self.sample_rate,
                blocksize=block,
                channels=self.channels,
                dtype="float32",
                callback=self._playback_callback,
            )
            self._stream.start()
            self._input_stream.start()
        except Exception:
            self.stop()
            raise

    def _capture_callback(self, indata, frames, time_info, status) -> None:
        if status:
            self.stats.glitches += 1
        with self._lock:
            filtered = self._equaliser.process(self._crossfeed.process(indata))
            if self._watching_spectrum and frames:
                self._measure_spectrum(filtered)
        self._ring.write(filtered)
        self.stats.frames += frames
        peak = float(abs(filtered).max()) if frames else 0.0
        if peak > self.stats.peak:
            self.stats.peak = peak

    def _playback_callback(self, outdata, frames, time_info, status) -> None:
        if status:
            self.stats.glitches += 1
        outdata[:] = self._ring.read(frames)

    def start(self) -> None:
        import sounddevice  # noqa: PLC0415

        if self.running:
            return
        self._take_output_volume()

        if self.capture_mode == "tap":
            self._start_tap_streams()
            return

        else:
            # Reading a loopback counts as recording, and macOS serves that
            # from the default input — which must therefore not be the headset.
            self._steer_default_input_away()
            devices = (self.input.index, self.output.index)

        try:
            self._stream = sounddevice.Stream(
                device=devices,
                samplerate=self.sample_rate,
                blocksize=self.config.block_size,
                channels=self.channels,
                dtype="float32",
                callback=self._callback,
            )
            self._stream.start()
        except Exception:
            self._close_capture()
            raise

    def stop(self) -> None:
        for attribute in ("_input_stream", "_stream"):
            stream = getattr(self, attribute, None)
            if stream is not None:
                stream.stop()
                stream.close()
                setattr(self, attribute, None)
        self._ring = None
        self._close_capture()
        self._give_output_volume_back()
        self._give_default_input_back()

    def _close_capture(self) -> None:
        if self._capture is not None:
            self._capture.close()
            self._capture = None

    def status(self) -> dict:
        return {
            "running": self.running,
            "capture": self.capture_mode,
            "input": self.input.name if self.input else system_tap.AGGREGATE_NAME,
            "output": self.output.name,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "block_size": self.config.block_size,
            "preset": self.preset.name,
            "calibration": (
                self._equaliser.calibration.name if self._equaliser.calibration else None
            ),
            "loudness_phon": self.config.loudness_phon,
            "crossfeed": self._crossfeed.strength,
            "moved_default_input": self._restore_input is not None,
            "output_volume_raised_from": (
                round(self._restore_volume[1], 3) if self._restore_volume else None
            ),
            "preamp_db": round(self._equaliser.preamp_db, 2),
            "gains": [round(g, 2) for g in self.preset.gains_db],
            "latency_ms": round(self.config.block_size / self.sample_rate * 1000, 1),
            "frames": self.stats.frames,
            "glitches": self.stats.glitches,
            "peak": round(self.stats.peak, 4),
            "dropped": self._ring.dropped if self._ring else 0,
            "starved": self._ring.starved if self._ring else 0,
        }
